"""风控阶段人工审核确认（HITL）测试（冲刺项：风控人工审核）。

覆盖：
- 节点 _risk_review：默认自动放行（hitl 关闭）/ 强制否决时的 loop_count 自增（仅 rejudge）。
- 路由 _route_risk_review：确认后按原风控结论流转；否决后 terminate/rejudge 两种去向与超循环防死锁。
- LocalRunner 全链路：默认自动放行回归；强制否决 + terminate 直接终结；强制否决 + rejudge 循环重研后防死锁终结。
- 复用真实 AppContext 跑完整研究闭环，断言风控人工审核落库到 audit_log。
"""
from __future__ import annotations

import os

from state.schemas import (
    RiskCheckResult, RiskDecision, ManagerDecision, OrderLeg, Side, AnalystReport, Rating,
)
from core.config import Settings, RiskReviewConfig
from graph.builder import _risk_review, _route_risk_review, LocalRunner

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --------------------------- 轻量假 ctx ---------------------------
class _FakeCtx:
    def __init__(self, on_reject: str = "rejudge", hitl: bool = False, max_loop: int = 3):
        self.settings = Settings(hitl_enabled=hitl, max_loop=max_loop)
        self.settings.risk_review = RiskReviewConfig(on_reject=on_reject)


def _risk(decision: RiskDecision, hard=None, soft=None, feedback: str = "") -> RiskCheckResult:
    return RiskCheckResult(
        decision=decision,
        hard_violations=hard or [],
        soft_findings=soft or [],
        feedback=feedback,
    )


# --------------------------- 节点单测 ---------------------------
def test_risk_review_node_auto_approves_when_hitl_off(monkeypatch):
    monkeypatch.setattr("graph.builder.human_approve", lambda *a, **k: True)
    ctx = _FakeCtx(on_reject="rejudge", hitl=False)
    st = {"risk_result": _risk(RiskDecision.APPROVED), "loop_count": 0}
    upd = _risk_review(st, ctx)
    assert upd["risk_review_approved"] is True
    assert "loop_count" not in upd  # 仅在否决+rejudge 时自增
    assert upd["audit_log"][0].stage == "hitl_risk_review"


def test_risk_review_node_reject_increments_loop_on_rejudge(monkeypatch):
    monkeypatch.setattr("graph.builder.human_approve", lambda *a, **k: False)
    ctx = _FakeCtx(on_reject="rejudge", hitl=True)
    st = {"risk_result": _risk(RiskDecision.APPROVED), "loop_count": 1}
    upd = _risk_review(st, ctx)
    assert upd["risk_review_approved"] is False
    assert upd["loop_count"] == 2  # 否决 + rejudge => loop_count++


def test_risk_review_node_reject_no_increment_on_terminate(monkeypatch):
    monkeypatch.setattr("graph.builder.human_approve", lambda *a, **k: False)
    ctx = _FakeCtx(on_reject="terminate", hitl=True)
    st = {"risk_result": _risk(RiskDecision.APPROVED), "loop_count": 1}
    upd = _risk_review(st, ctx)
    assert upd["risk_review_approved"] is False
    assert "loop_count" not in upd  # terminate 不计数（直接终结）


# --------------------------- 路由单测 ---------------------------
def _st(decision, approved=True, loop=0):
    return {"risk_result": _risk(decision), "risk_review_approved": approved, "loop_count": loop}


def test_route_review_approved_follows_original_risk():
    ctx = _FakeCtx()
    assert _route_risk_review(_st(RiskDecision.APPROVED), ctx) == "confirm_trade"
    assert _route_risk_review(_st(RiskDecision.EXCEPTION), ctx) == "exception_human"
    assert _route_risk_review(_st(RiskDecision.REJECTED, loop=0), ctx) == "manager"
    assert _route_risk_review(_st(RiskDecision.REJECTED, loop=3), ctx) == "reviewer"


def test_route_review_reject_terminate_ends():
    ctx = _FakeCtx(on_reject="terminate")
    assert _route_risk_review(_st(RiskDecision.APPROVED, approved=False), ctx) == "reviewer"


def test_route_review_reject_rejudge_routes_manager():
    ctx = _FakeCtx(on_reject="rejudge")
    assert _route_risk_review(_st(RiskDecision.APPROVED, approved=False, loop=0), ctx) == "manager"


def test_route_review_reject_rejudge_overloop_ends():
    ctx = _FakeCtx(on_reject="rejudge")
    assert _route_risk_review(_st(RiskDecision.APPROVED, approved=False, loop=3), ctx) == "reviewer"


# --------------------------- 全链路（LocalRunner）---------------------------
def _build_runner(on_reject="rejudge", approve=True):
    """构造真实 AppContext + LocalRunner，并按需强制 human_approve 返回值。"""
    from datetime import date
    from core.llm import get_llm
    from core.context import AppContext
    from data.provider import get_provider
    from harness.observability import Tracer
    from harness.cost_guard import CostGuard
    from harness.traced_llm import TracedLLM
    from kg.store import KnowledgeGraph
    from main import _build_context, _build_initial_portfolio

    settings = Settings.load(os.path.join(_ROOT, "config", "settings.yaml"))
    settings.risk_review = RiskReviewConfig(on_reject=on_reject)
    tracer = Tracer(silent=True)
    cost = CostGuard(model="mock", budget=5.0)
    llm = TracedLLM(get_llm(settings), tracer, cost, actor="system")
    data = get_provider(settings)
    kg = KnowledgeGraph()
    ctx = _build_context(settings, llm, data, tracer, cost, kg)
    runner = LocalRunner(ctx)
    init = {
        "run_id": "rr-test",
        "as_of_date": date.today().isoformat(),
        "market": "A_SHARE",
        "portfolio": _build_initial_portfolio(settings),
        "risk_limits": settings.risk_limits,
        "loop_count": 0,
    }
    return runner, init, settings


def test_full_pipeline_risk_review_automatic_approve_regression(monkeypatch):
    """默认（无人值守）自动放行：风控人工审核通过，流程仍可走到交易并产生成交（回归）。"""
    monkeypatch.setattr("graph.builder.human_approve", lambda *a, **k: True)
    runner, init, settings = _build_runner()
    final = runner.invoke(init)
    assert final.get("risk_review_approved") is True
    # 正常情形下风控 APPROVED -> 下单前确认 -> 交易落地
    assert final.get("pre_trade_approved") is True
    assert len(final.get("fills", [])) > 0
    stages = [a.stage for a in final.get("audit_log", [])]
    assert "hitl_risk_review" in stages


def test_full_pipeline_risk_review_reject_terminate(monkeypatch):
    """否决且 on_reject=terminate：首次审核即拒绝 -> 直接终结进审核者。"""
    monkeypatch.setattr("graph.builder.human_approve", lambda *a, **k: False)
    runner, init, settings = _build_runner(on_reject="terminate")
    final = runner.invoke(init)
    assert final.get("risk_review_approved") is False
    assert final.get("loop_count", 0) == 0  # terminate 不重研
    # 终结路径：审核者/复盘应已运行
    stages = [a.stage for a in final.get("audit_log", [])]
    assert "hitl_risk_review" in stages
    assert any(s.startswith("reviewer") for s in stages)


def test_full_pipeline_risk_review_reject_rejudge_then_deadlock(monkeypatch):
    """否决且 on_reject=rejudge：多次回落经理重研判，超循环后强制终结（防死锁）。"""
    monkeypatch.setattr("graph.builder.human_approve", lambda *a, **k: False)
    runner, init, settings = _build_runner(on_reject="rejudge")
    final = runner.invoke(init)
    assert final.get("risk_review_approved") is False
    # 防死锁：循环重研后必须终结，不得无限循环
    assert final.get("loop_count", 0) >= settings.max_loop
    denies = [a for a in final.get("audit_log", []) if a.stage == "hitl_risk_review" and a.action == "deny"]
    assert len(denies) >= 1
    stages = [a.stage for a in final.get("audit_log", [])]
    assert any(s.startswith("reviewer") for s in stages)
