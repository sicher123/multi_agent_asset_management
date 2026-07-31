"""
真实钩子体（冲刺项⑤「钩子实体化」）：把调度 slot 接到真实动作。

- post_market：写 KG sqlite + 追加运行台账 schedule_runs.json + 推送收盘摘要。
- pre_market：生成盘前关注清单（报价/涨跌幅/涨跌停/ST 标记）落盘 + 推送。
- weekly：KG 置信度衰减 + 周报落盘 + 推送。
- event：重大事件即时推送。

另提供 run_research_for_ticker：对单标的真实复跑行业研究员并回写 KG（重大事件触发重研的执行体）。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

from data.ashare_rules import is_limit_up, is_limit_down, is_st, limit_pct
from state.schemas import UniverseEntry
from agents.industry import run as industry_run
from scheduler.scheduler import (SchedContext, Job, ScheduleSpec, MaterialEvent,
                                 _EVENT_LABELS)


# ----------------------------- 收盘后 -----------------------------
def post_market_job(sctx: SchedContext, payload: dict) -> None:
    ctx = sctx.ctx
    run_dir = sctx.run_dir
    os.makedirs(run_dir, exist_ok=True)

    # 1) 真实写库：KG 持久化（幂等）
    kg_path = os.path.join(run_dir, "kg.sqlite")
    ctx.kg.save_sqlite(kg_path)

    # 2) 运行台账（调度「写库」代理）：追加一条收盘记录
    ledger_path = os.path.join(run_dir, "schedule_runs.json")
    md = payload.get("manager_decision")
    ev = payload.get("monitor", {}).get("eval")
    rec = {
        "slot": "post_market",
        "ts": datetime.now(timezone.utc).isoformat(),
        "run_id": payload.get("run_id"),
        "as_of_date": payload.get("as_of_date"),
        "worth_trading": bool(md and md.worth_trading),
        "buys": sum(1 for o in (md.rebalance_orders if md else []) if o.side.value == "BUY"),
        "sells": sum(1 for o in (md.rebalance_orders if md else []) if o.side.value == "SELL"),
        "kg_entities": ctx.kg.stats()["entities"],
        "kg_relations": ctx.kg.stats()["relations"],
        "excess_return": (round(ev["excess"]["total_return"], 4) if ev else None),
    }
    _append_jsonl(ledger_path, rec)

    # 3) 推送
    sctx.push.push(
        "post_market",
        f"收盘后汇总 · {payload.get('as_of_date')}",
        f"值得交易={rec['worth_trading']} 买{rec['buys']}/卖{rec['sells']} "
        f"KG({rec['kg_entities']}实体/{rec['kg_relations']}关系) "
        f"超额={rec['excess_return']}",
    )


# ----------------------------- 盘前 -----------------------------
def pre_market_job(sctx: SchedContext, payload: dict) -> None:
    ctx = sctx.ctx
    run_dir = sctx.run_dir
    os.makedirs(run_dir, exist_ok=True)

    watch = ctx.settings.watchlist
    pf = payload.get("portfolio")
    held = {h.ticker: h for h in (pf.holdings if pf else [])}

    rows: List[Dict[str, Any]] = []
    for w in watch:
        t = w["ticker"]
        q = ctx.data.get_quote(t)
        chg = (q.last / q.prev_close - 1.0) if q.prev_close else 0.0
        flags: List[str] = []
        if is_limit_up(q.last, q.prev_close, t):
            flags.append(f"涨停(限{limit_pct(t):.0%})")
        if is_limit_down(q.last, q.prev_close, t):
            flags.append(f"跌停(限{limit_pct(t):.0%})")
        if is_st(q.name):
            flags.append("ST禁投")
        h = held.get(t)
        pnl = None
        if h and h.cost_price > 0:
            pnl = round(q.last / h.cost_price - 1.0, 4)
            if pnl <= -0.05:
                flags.append(f"持仓浮亏{pnl:.0%}关注")
        rows.append({
            "ticker": t, "name": q.name, "industry": q.industry,
            "last": q.last, "prev_close": q.prev_close, "chg_pct": round(chg, 4),
            "held": h.quantity if h else 0, "flags": flags,
        })

    out = {
        "slot": "pre_market",
        "ts": datetime.now(timezone.utc).isoformat(),
        "as_of_date": payload.get("as_of_date"),
        "watchlist_count": len(rows),
        "alerts": [r for r in rows if r["flags"]],
        "rows": rows,
    }
    path = os.path.join(run_dir, f"pre_market_{payload.get('as_of_date', 'na')}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    n_alert = len(out["alerts"])
    sctx.push.push(
        "pre_market",
        f"盘前关注 · {out['as_of_date']}",
        f"监控 {len(rows)} 只，触发关注 {n_alert} 只"
        + ("" if n_alert == 0 else "：" + "; ".join(
            f"{a['name']}({a['ticker']}){','.join(a['flags'])}" for a in out["alerts"][:5])),
    )


# ----------------------------- 周度 -----------------------------
def weekly_job(sctx: SchedContext, payload: dict) -> None:
    ctx = sctx.ctx
    run_dir = sctx.run_dir
    os.makedirs(run_dir, exist_ok=True)

    # KG 置信度随时间衰减（学习闭环的时效性）
    if ctx.kg_updater:
        ctx.kg_updater.decay(half_life_days=ctx.settings.kg_decay_half_life)

    stats = ctx.kg.stats()
    # 取置信度最高的若干实体做周报摘要
    top = sorted(ctx.kg.entities.values(), key=lambda e: e.confidence, reverse=True)[:10]
    top_entities = [{"id": e.id, "type": e.type, "confidence": e.confidence} for e in top]

    iso_week = datetime.now(timezone.utc).strftime("%G-W%V")
    out = {
        "slot": "weekly",
        "ts": datetime.now(timezone.utc).isoformat(),
        "iso_week": iso_week,
        "kg_stats": stats,
        "top_entities": top_entities,
    }
    path = os.path.join(run_dir, f"weekly_{iso_week}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    sctx.push.push(
        "weekly",
        f"周度复盘 · {iso_week}",
        f"KG 实体 {stats['entities']} / 关系 {stats['relations']}，已执行置信度衰减",
    )


# ----------------------------- 重大事件 -----------------------------
def event_job(sctx: SchedContext, payload: dict) -> None:
    # payload 是 MaterialEvent.__dict__
    ev = MaterialEvent(**{k: v for k, v in payload.items()
                          if k in ("ticker", "kind", "detail", "ts", "material", "payload")})
    label = _EVENT_LABELS.get(ev.kind, ev.kind)
    sctx.push.push(
        "event",
        f"重大事件 · {ev.ticker} {label}",
        f"{ev.detail}（已入队触发重研）",
    )


# ----------------------------- 重大事件触发重研的执行体 -----------------------------
def run_research_for_ticker(sctx: SchedContext, ticker: str) -> Dict[str, Any]:
    """对单标的真实复跑行业研究员（含真实 DCF）并回写 KG + 持久化。"""
    ctx = sctx.ctx
    q = ctx.data.get_quote(ticker)
    state = {
        "universe": [UniverseEntry(ticker=ticker, name=q.name, industry=q.industry,
                                   trigger="event")],
        "log": [], "audit_log": [], "analyst_reports": [], "kg_stats": {},
    }
    out = industry_run(state, ctx)
    ctx.kg.save_sqlite(os.path.join(sctx.run_dir, "kg.sqlite"))
    reports = out.get("analyst_reports", [])
    summary = {
        "ticker": ticker,
        "name": q.name,
        "reports": len(reports),
        "kg_writes": out.get("kg_stats", {}).get("industry_kg_writes", 0),
        "first_rating": (reports[0].rating.value if reports else None),
        "first_target": (reports[0].target_price if reports else None),
        "dcf_used": (reports[0].dcf_used if reports else None),
    }
    sctx.push.push(
        "reresearch",
        f"重研完成 · {q.name}({ticker})",
        f"评级={summary['first_rating']} 目标价={summary['first_target']} "
        f"DCF={summary['dcf_used']} KG写入={summary['kg_writes']}",
    )
    return summary


# ----------------------------- 默认任务注册 -----------------------------
def make_default_jobs(sctx: SchedContext) -> List[Job]:
    """注册四个真实钩子（含定时），供 main / 测试使用。"""
    return [
        Job(id="post_market.default", slot="post_market",
            fn=lambda p: post_market_job(sctx, p),
            schedule=ScheduleSpec(hour=15, minute=5)),       # 收盘后
        Job(id="pre_market.default", slot="pre_market",
            fn=lambda p: pre_market_job(sctx, p),
            schedule=ScheduleSpec(hour=9, minute=0)),        # 盘前
        Job(id="weekly.default", slot="weekly",
            fn=lambda p: weekly_job(sctx, p),
            schedule=ScheduleSpec(hour=17, minute=0, weekdays=(4,))),  # 周五收盘后
        Job(id="event.default", slot="event",
            fn=lambda p: event_job(sctx, p)),                # 仅由 notify_event 触发
    ]


# ----------------------------- 工具 -----------------------------
def _append_jsonl(path: str, rec: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
