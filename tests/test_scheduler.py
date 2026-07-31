"""
冲刺项⑤ Scheduler 钩子实体化 —— 单测。

覆盖：
- run_due_jobs 按时触发（盘前 09:00 / 周度周五 17:00）+ 同日去重
- notify_event 重大事件分类 + 入队 + event 推送；非重大事件不入队
- drain_research 真实执行 runner 并清空队列
- post_market / pre_market / weekly 真实钩子体（写库 / 推送 / 台账 / 清单 / 衰减）
- run_research_for_ticker 单标的真实复跑行业研究员并回写 KG
"""
from __future__ import annotations

import os
import json
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

from scheduler.scheduler import Scheduler, SchedContext, ScheduleSpec, PushSink
from scheduler.jobs import make_default_jobs, run_research_for_ticker

from state.schemas import (OrderLeg, Side, ManagerDecision, Portfolio, Holding,
                           AnalystReport)
from kg.store import KnowledgeGraph
from kg.updater import KGUpdater
from kg.schema import Entity, Relation, eid
from data.provider import MockAshareProvider

from core.config import Settings
from core.llm import get_llm
from core.context import AppContext
from data.provider import get_provider
from kg.extractor import KGExtractor
from kg.retrieval import GraphRAGRetriever
from harness.prompthub import PromptHub
from harness.injection_guard import InjectionGuard
from harness.datavalidator import DataValidator
from harness.cost_guard import CostGuard
from harness.observability import Metrics
from risk.engine import RiskEngine
from backtest.paper_trader import PaperTrader
from backtest.engine import BacktestEngine
from eval.evaluator import Evaluator


class RecordPushSink(PushSink):
    def __init__(self):
        self.records = []

    def push(self, channel: str, title: str, message: str) -> None:
        self.records.append((channel, title, message))


def _make_fake_ctx():
    """覆盖 post_market/pre_market/weekly 钩子所需的最小 ctx。"""
    kg = KnowledgeGraph()
    old_ts = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    kg.add_entity(Entity(id=eid("Company", "600519"), type="Company", name="贵州茅台",
                         confidence=1.0, sources=["seed"], ts=old_ts))
    # 一条旧关系，用于验证 weekly 衰减
    kg.add_relation(Relation(src=eid("Company", "600519"), dst=eid("Price", "target"),
                             type="HAS_TARGET", confidence=1.0, ts=old_ts, sources=["seed"]))
    return SimpleNamespace(
        kg=kg,
        kg_updater=KGUpdater(kg),
        settings=SimpleNamespace(
            watchlist=[
                {"ticker": "600519", "name": "贵州茅台", "industry": "白酒"},
                {"ticker": "000001", "name": "平安银行", "industry": "银行"},
                {"ticker": "300750", "name": "宁德时代", "industry": "新能源"},
            ],
            kg_decay_half_life=30.0,
        ),
        data=MockAshareProvider(),
    )


def _build_full_ctx(tmp_dir: str) -> AppContext:
    """完整 ctx（镜像 main），供 run_research_for_ticker 真实复跑行业研究员。"""
    settings = Settings.load("config/settings.yaml")
    settings.hitl_enabled = False
    llm = get_llm(settings)
    data = get_provider(settings)
    catalog = {w["ticker"]: {"name": w.get("name", w["ticker"]),
                             "industry": w.get("industry", "未知")}
               for w in settings.watchlist}
    kg = KnowledgeGraph()
    return AppContext(
        settings=settings, llm=llm, data=data, risk_engine=RiskEngine(),
        strategy_name=settings.default_strategy, kg=kg,
        extractor=KGExtractor(catalog=catalog), retriever=GraphRAGRetriever(kg),
        kg_updater=KGUpdater(kg), prompthub=PromptHub(), metrics=Metrics(),
        cost_guard=CostGuard(model="mock", budget=5.0), injection=InjectionGuard(),
        validator=DataValidator(), paper_trader=PaperTrader(data),
        backtest=BacktestEngine(data), evaluator=Evaluator(),
    )


# --------------------------- 定时触发 ---------------------------
def test_run_due_jobs_fires_pre_market_at_0900():
    fired = []
    sched = Scheduler(sctx=None)
    sched.every_pre_market(lambda p: fired.append("pre"), schedule=ScheduleSpec(9, 0))
    # 周一 09:00 触发
    due = sched.run_due_jobs(datetime(2026, 7, 13, 9, 0), payload={})
    assert [d["job_id"] for d in due] == ["pre_market.default"]
    # 同日再次调用应去重（不重复触发）
    due2 = sched.run_due_jobs(datetime(2026, 7, 13, 9, 0), payload={})
    assert due2 == []
    # 非 09:00 不触发
    due3 = sched.run_due_jobs(datetime(2026, 7, 13, 10, 0), payload={})
    assert due3 == []


def test_weekly_only_fires_on_friday():
    fired = []
    sched = Scheduler(sctx=None)
    sched.every_weekly(lambda p: fired.append("wk"), schedule=ScheduleSpec(17, 0, weekdays=(4,)))
    # 周一 17:00 不应触发
    assert sched.run_due_jobs(datetime(2026, 7, 13, 17, 0), payload={}) == []
    # 周五 17:00 应触发
    due = sched.run_due_jobs(datetime(2026, 7, 17, 17, 0), payload={})
    assert [d["job_id"] for d in due] == ["weekly.default"]


def test_run_due_jobs_ignores_event_slot():
    fired = []
    sched = Scheduler(sctx=None)
    sched.on_event(lambda p: fired.append("ev"))
    # event 槽不应被定时驱动
    assert sched.run_due_jobs(datetime(2026, 7, 13, 9, 0), payload={}) == []
    assert fired == []


# --------------------------- 事件 / 重研 ---------------------------
def test_notify_event_material_enqueues_and_pushes(tmp_path):
    push = RecordPushSink()
    sctx = SchedContext(ctx=_make_fake_ctx(), run_dir=str(tmp_path), push=push)
    sched = Scheduler(sctx)
    for j in make_default_jobs(sctx):
        if j.slot == "event":
            sched.register_job(j)
    # 重大事件：业绩 → 入队 + event 推送
    ev = sched.notify_event("600519", "earnings", "半年报超预期")
    assert ev.material is True
    assert len(sched.reresearch_queue) == 1
    assert any(c == "event" for c, _, _ in push.records)
    # 非重大事件：不入队
    ev2 = sched.notify_event("000001", "minor_news", "日常新闻")
    assert ev2.material is False
    assert len(sched.reresearch_queue) == 1


def test_drain_reresearch_runs_runner_and_clears(tmp_path):
    push = RecordPushSink()
    sctx = SchedContext(ctx=_make_fake_ctx(), run_dir=str(tmp_path), push=push)
    sched = Scheduler(sctx)
    sched.notify_event("600519", "limit_up", "涨停")
    sched.notify_event("300750", "rating_change", "评级上调")
    calls = []
    results = sched.drain_reresearch(lambda t: calls.append(t) or {"ok": True})
    assert calls == ["600519", "300750"]
    assert len(results) == 2
    assert sched.reresearch_queue == []  # 已清空


# --------------------------- 真实钩子体 ---------------------------
def test_post_market_job_writes_db_and_ledger_and_pushes(tmp_path):
    push = RecordPushSink()
    sctx = SchedContext(ctx=_make_fake_ctx(), run_dir=str(tmp_path), push=push)
    sched = Scheduler(sctx)
    for j in make_default_jobs(sctx):
        if j.slot == "post_market":
            sched.register_job(j)
    md = ManagerDecision(
        worth_trading=True, stop_loss_pct=0.08, take_profit_pct=0.2,
        rebalance_orders=[
            OrderLeg(ticker="600519", side=Side.BUY, quantity=100, expected_price=1480.0, target_weight=0.08),
            OrderLeg(ticker="000001", side=Side.SELL, quantity=50, expected_price=11.5, target_weight=0.0),
        ],
    )
    payload = {"run_id": "run-001", "as_of_date": "2026-07-11",
               "manager_decision": md, "monitor": {"eval": {"excess": {"total_return": 0.0258}}}}
    sched.trigger("post_market", payload)

    assert os.path.exists(os.path.join(str(tmp_path), "kg.sqlite"))
    # 台账追加了一条 post_market 记录
    lines = [json.loads(l) for l in open(os.path.join(str(tmp_path), "schedule_runs.json"))]
    assert lines[-1]["slot"] == "post_market"
    assert lines[-1]["buys"] == 1 and lines[-1]["sells"] == 1
    assert any(c == "post_market" for c, _, _ in push.records)


def test_pre_market_job_writes_watchlist(tmp_path):
    push = RecordPushSink()
    sctx = SchedContext(ctx=_make_fake_ctx(), run_dir=str(tmp_path), push=push)
    sched = Scheduler(sctx)
    for j in make_default_jobs(sctx):
        if j.slot == "pre_market":
            sched.register_job(j)
    pf = Portfolio(cash=1_000_000, holdings=[
        Holding(ticker="600519", name="贵州茅台", industry="白酒", quantity=100,
                cost_price=1480.0, last_price=1480.0),
    ])
    sched.trigger("pre_market", {"as_of_date": "2026-07-11", "portfolio": pf})
    path = os.path.join(str(tmp_path), "pre_market_2026-07-11.json")
    assert os.path.exists(path)
    data = json.loads(open(path).read())
    assert data["watchlist_count"] == 3
    assert any(c == "pre_market" for c, _, _ in push.records)


def test_weekly_job_decays_kg(tmp_path):
    push = RecordPushSink()
    ctx = _make_fake_ctx()
    sctx = SchedContext(ctx=ctx, run_dir=str(tmp_path), push=push)
    sched = Scheduler(sctx)
    for j in make_default_jobs(sctx):
        if j.slot == "weekly":
            sched.register_job(j)
    rel = ctx.kg.neighbors(eid("Company", "600519"), "HAS_TARGET")[0]
    before = rel.confidence
    sched.trigger("weekly", {"as_of_date": "2026-07-11"})
    after = ctx.kg.neighbors(eid("Company", "600519"), "HAS_TARGET")[0].confidence
    assert after < before  # 置信度已衰减
    assert any(f.startswith("weekly_") for f in os.listdir(str(tmp_path)))


# --------------------------- 单标的真实重研 ---------------------------
def test_run_research_for_ticker_real(tmp_path):
    ctx = _build_full_ctx(str(tmp_path))
    sctx = SchedContext(ctx=ctx, run_dir=str(tmp_path), push=RecordPushSink())
    summary = run_research_for_ticker(sctx, "600519")
    assert summary["reports"] == 1
    assert summary["dcf_used"] is True       # Mock 数据可真实反算 DCF
    assert summary["kg_writes"] > 0
    assert os.path.exists(os.path.join(str(tmp_path), "kg.sqlite"))
