"""
调度器（Scheduler）：把「盘前 / 收盘后 / 周度 / 重大事件」触发做成可注册、可定时、可持久化的实体。

冲刺项⑤「钩子实体化」目标：
- 钩子体真实落地：post_market → 写 KG sqlite + 追加运行台账 + 推送；pre_market → 生成盘前关注
  清单(涨跌停/ST 标记)落盘 + 推送；weekly → KG 置信度衰减 + 周报落盘 + 推送；event → 重大事件即时
  推送。
- 定时层：每个 slot 可绑定 ScheduleSpec（hour/minute/weekday），由 run_due_jobs(now) 在进程内按时触发，
  无需外部 cron 即可演示；真实部署也可由 OS cron / Celery 直接调用 trigger()。last_fired 状态持久化，
  同一天同任务不重复触发。
- 事件层：notify_event(ticker, kind, detail) 解析「重大事件」（业绩/评级/涨跌停/停牌复牌/宏观政策等），
  命中则入队 re-research 并即时推送；drain_reresearch(runner) 用注入的 runner 真实执行重研。
- 推送抽象：PushSink（控制台 / 文件 / 可选 Webhook / 组合），离线可跑、真实环境可插拔。

全部动作离线可运行（Mock 数据 + 本地 sqlite/jsonl），无外部依赖。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple

# ----------------------------- 重大事件分类 -----------------------------
# 列入即为「重大事件」，触发重研；其余视为常规噪声，不触发。
MATERIAL_EVENT_KINDS = {
    "earnings",            # 业绩披露 / 业绩预告
    "rating_change",       # 评级上调/下调
    "limit_up",            # 涨停
    "limit_down",          # 跌停
    "trading_halt_resume", # 停牌 / 复牌
    "macro_policy",        # 宏观政策（降准/加息/产业扶持）
    "major_announcement",  # 重大公告（并购/定增/减持）
    "guidance_change",     # 业绩指引修正
}

_EVENT_LABELS = {
    "earnings": "业绩披露",
    "rating_change": "评级变动",
    "limit_up": "涨停",
    "limit_down": "跌停",
    "trading_halt_resume": "停牌/复牌",
    "macro_policy": "宏观政策",
    "major_announcement": "重大公告",
    "guidance_change": "业绩指引修正",
}


def is_material_event(kind: str) -> bool:
    return kind in MATERIAL_EVENT_KINDS


# ----------------------------- 定时规格 -----------------------------
@dataclass
class ScheduleSpec:
    """简化 cron：到 hour:minute 且 weekday 命中即触发（weekday 0=周一..6=周日）。"""
    hour: int
    minute: int
    weekdays: Tuple[int, ...] = (0, 1, 2, 3, 4, 5, 6)

    def is_due(self, now: datetime) -> bool:
        return (now.hour == self.hour and now.minute == self.minute
                and now.weekday() in self.weekdays)


@dataclass
class Job:
    id: str
    slot: str                       # pre_market | post_market | weekly | event
    fn: Callable[[dict], None]
    enabled: bool = True
    schedule: Optional[ScheduleSpec] = None


@dataclass
class MaterialEvent:
    ticker: str
    kind: str
    detail: str
    ts: str
    material: bool
    payload: dict = field(default_factory=dict)


# ----------------------------- 推送抽象 -----------------------------
class PushSink:
    """推送通道基类。真实环境可派生为企微/钉钉/邮件；此处提供离线实现。"""

    def push(self, channel: str, title: str, message: str) -> None:
        raise NotImplementedError


class NullPushSink(PushSink):
    def push(self, channel: str, title: str, message: str) -> None:
        return None


class ConsolePushSink(PushSink):
    def push(self, channel: str, title: str, message: str) -> None:
        print(f"  [推送:{channel}] {title} — {message}")


class FilePushSink(PushSink):
    """把推送追加写入 jsonl 文件（离线「写库」代理）。"""

    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    def push(self, channel: str, title: str, message: str) -> None:
        rec = {"ts": datetime.now(timezone.utc).isoformat(), "channel": channel,
               "title": title, "message": message}
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


class WebhookPushSink(PushSink):
    """可选 Webhook 推送（真实环境接 IM/告警）。无 url 时静默 no-op。"""

    def __init__(self, url: Optional[str] = None, timeout: float = 3.0):
        self.url = url
        self.timeout = timeout

    def push(self, channel: str, title: str, message: str) -> None:
        if not self.url:
            return
        try:
            import urllib.request
            data = json.dumps({"channel": channel, "title": title, "message": message},
                              ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(self.url, data=data,
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=self.timeout)
        except Exception as e:  # 推送失败不应中断主流程
            print(f"  [warn] webhook 推送失败: {e}")


class MultiPushSink(PushSink):
    def __init__(self, sinks: List[PushSink]):
        self.sinks = sinks

    def push(self, channel: str, title: str, message: str) -> None:
        for s in self.sinks:
            try:
                s.push(channel, title, message)
            except Exception:
                continue


# ----------------------------- 调度上下文 -----------------------------
@dataclass
class SchedContext:
    """钩子体执行所需的共享依赖（上下文 + 产物目录 + 推送通道）。"""
    ctx: object                       # AppContext
    run_dir: str = "run_artifacts"
    push: PushSink = field(default_factory=NullPushSink)


# ----------------------------- 调度器 -----------------------------
class Scheduler:
    SLOTS = ("pre_market", "post_market", "weekly", "event")

    def __init__(self, sctx: Optional[SchedContext] = None,
                 state_path: Optional[str] = None):
        self.sctx = sctx
        self.state_path = state_path or (
            os.path.join(sctx.run_dir, "scheduler_state.json") if sctx else None)
        self._jobs: Dict[str, List[Job]] = {s: [] for s in self.SLOTS}
        self._reresearch_queue: List[MaterialEvent] = []
        self._last_fired: Dict[str, str] = {}        # job_id -> date(YYYY-MM-DD)
        self._last_runs: List[dict] = []              # 最近触发记录（供打印）
        self._load_state()

    # --------------------------- 注册 ---------------------------
    def register_job(self, job: Job) -> None:
        if job.slot not in self._jobs:
            raise ValueError(f"未知 slot: {job.slot}")
        self._jobs[job.slot].append(job)

    def every_pre_market(self, fn: Callable[[dict], None], schedule: Optional[ScheduleSpec] = None,
                         id: str = "pre_market.default") -> None:
        self.register_job(Job(id=id, slot="pre_market", fn=fn, schedule=schedule))

    def every_post_market(self, fn: Callable[[dict], None], schedule: Optional[ScheduleSpec] = None,
                          id: str = "post_market.default") -> None:
        self.register_job(Job(id=id, slot="post_market", fn=fn, schedule=schedule))

    def every_weekly(self, fn: Callable[[dict], None], schedule: Optional[ScheduleSpec] = None,
                     id: str = "weekly.default") -> None:
        self.register_job(Job(id=id, slot="weekly", fn=fn, schedule=schedule))

    def on_event(self, fn: Callable[[dict], None], id: str = "event.default") -> None:
        # 事件槽不由定时驱动，只由 notify_event 触发
        self.register_job(Job(id=id, slot="event", fn=fn))

    # --------------------------- 触发 ---------------------------
    def trigger(self, slot: str, payload: dict) -> List[dict]:
        results = []
        for job in self._jobs.get(slot, []):
            if not job.enabled:
                continue
            rec = {"job_id": job.id, "slot": slot, "ts": datetime.now(timezone.utc).isoformat(),
                   "ok": True, "error": None}
            try:
                job.fn(payload)
            except Exception as e:  # 单钩子失败不中断整轮
                rec["ok"] = False
                rec["error"] = str(e)
                print(f"  [warn] 调度钩子 {job.id} 执行异常: {e}")
            self._last_runs.append(rec)
            results.append(rec)
        # post_market/weekly 通过 trigger 显式触发时也记 last_fired，避免同日重复
        if slot in ("post_market", "weekly"):
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            for job in self._jobs.get(slot, []):
                if job.enabled:
                    self._last_fired[job.id] = today
            self._persist_state()
        return results

    def run_due_jobs(self, now: datetime, payload: Optional[dict] = None) -> List[dict]:
        """按 ScheduleSpec 触发当日到点的任务，同日同任务只触发一次。"""
        payload = payload or {}
        today = now.strftime("%Y-%m-%d")
        fired: List[dict] = []
        for slot, jobs in self._jobs.items():
            if slot == "event":
                continue  # 事件槽不定时
            for job in jobs:
                if not job.enabled or job.schedule is None:
                    continue
                if not job.schedule.is_due(now):
                    continue
                if self._last_fired.get(job.id) == today:
                    continue  # 同日已触发
                rec = {"job_id": job.id, "slot": slot, "ts": now.isoformat(), "ok": True, "error": None}
                try:
                    job.fn(payload)
                except Exception as e:
                    rec["ok"] = False
                    rec["error"] = str(e)
                    print(f"  [warn] 定时钩子 {job.id} 执行异常: {e}")
                self._last_fired[job.id] = today
                self._last_runs.append(rec)
                fired.append(rec)
        if fired:
            self._persist_state()
        return fired

    # --------------------------- 事件 / 重研 ---------------------------
    def notify_event(self, ticker: str, kind: str, detail: str,
                     payload: Optional[dict] = None, now: Optional[datetime] = None) -> MaterialEvent:
        """上报一个市场事件；若是重大事件则入队重研并推送告警。返回事件对象。"""
        now = now or datetime.now(timezone.utc)
        material = is_material_event(kind)
        ev = MaterialEvent(ticker=ticker, kind=kind, detail=detail,
                           ts=now.isoformat(), material=material, payload=payload or {})
        if material:
            self._reresearch_queue.append(ev)
            self.trigger("event", ev.__dict__)   # event 钩子即时推送
            self._persist_state()
        return ev

    def drain_reresearch(self, runner: Callable[[str], object]) -> List[dict]:
        """用注入的 runner 真实执行队列中的重研任务，清空队列。返回每只标的结果。"""
        queue = list(self._reresearch_queue)
        self._reresearch_queue.clear()
        self._persist_state()
        out: List[dict] = []
        for ev in queue:
            rec = {"ticker": ev.ticker, "kind": ev.kind, "material": True}
            try:
                rec["result"] = runner(ev.ticker)
            except Exception as e:
                rec["error"] = str(e)
                print(f"  [warn] 重研 {ev.ticker} 失败: {e}")
            out.append(rec)
        return out

    @property
    def reresearch_queue(self) -> List[MaterialEvent]:
        return list(self._reresearch_queue)

    @property
    def fired_jobs(self) -> List[dict]:
        return list(self._last_runs)

    # --------------------------- 状态持久化 ---------------------------
    def _persist_state(self) -> None:
        if not self.state_path:
            return
        try:
            os.makedirs(os.path.dirname(self.state_path) or ".", exist_ok=True)
            with open(self.state_path, "w", encoding="utf-8") as f:
                json.dump({"last_fired": self._last_fired,
                           "reresearch_queue": [e.__dict__ for e in self._reresearch_queue]},
                          f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _load_state(self) -> None:
        if not self.state_path or not os.path.exists(self.state_path):
            return
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._last_fired = data.get("last_fired", {})
            self._reresearch_queue = [
                MaterialEvent(**e) for e in data.get("reresearch_queue", [])
            ]
        except Exception:
            pass
