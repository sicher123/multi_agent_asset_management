"""投委会/终审委员（IC Gate，HITL）测试：门槛判定、节点、路由、全链路、防死锁。"""
from __future__ import annotations

from state.schemas import ManagerDecision, OrderLeg, Side, RiskCheckResult, RiskDecision
from core.config import Settings, ICGateConfig
from graph.builder import _ic_materiality, _ic_gate, _route_ic, LocalRunner


# --------------------------- 门槛判定 ---------------------------
def _md(targets):
    return ManagerDecision(worth_trading=True, stop_loss_pct=0.08, take_profit_pct=0.2,
                           target_portfolio_weights=targets,
                           rebalance_orders=[OrderLeg(ticker=t, side=Side.BUY, quantity=1, expected_price=1.0)
                                             for t in targets])


def test_materiality_single_weight(ctx):
    st = {"manager_decision": _md({"600519": 0.10}), "portfolio": None}
    material, _ = _ic_materiality(st, ctx)
    assert material  # 单票 10% 超 5% 门槛


def test_materiality_not_triggered(ctx):
    st = {"manager_decision": _md({"600519": 0.03}), "portfolio": None}
    material, _ = _ic_materiality(st, ctx)
    assert not material


def test_materiality_exception(ctx):
    st = {"manager_decision": _md({"600519": 0.03}),
          "risk_result": RiskCheckResult(decision=RiskDecision.EXCEPTION)}
    material, _ = _ic_materiality(st, ctx)
    assert material  # EXCEPTION 一律重大


# --------------------------- 节点 ---------------------------
def test_ic_gate_auto_pass_when_not_material(monkeypatch):
    monkeypatch.setattr("graph.builder.human_approve", lambda *a, **k: False)  # 即便拒绝，非重大也自动过会
    ctx = _ctx(ic_on_reject="rejudge")
    st = {"manager_decision": _md({"600519": 0.03}), "portfolio": None, "loop_count": 0}
    upd = _ic_gate(st, ctx)
    assert upd["ic_gate_approved"] is True
    assert upd["audit_log"][0].actor == "system"


def test_ic_gate_human_approve_material(monkeypatch):
    monkeypatch.setattr("graph.builder.human_approve", lambda *a, **k: True)
    ctx = _ctx(ic_on_reject="rejudge")
    st = {"manager_decision": _md({"600519": 0.10}), "portfolio": None, "loop_count": 0}
    upd = _ic_gate(st, ctx)
    assert upd["ic_gate_approved"] is True


def test_ic_gate_deny_increments_loop_on_rejudge(monkeypatch):
    monkeypatch.setattr("graph.builder.human_approve", lambda *a, **k: False)
    ctx = _ctx(ic_on_reject="rejudge")
    st = {"manager_decision": _md({"600519": 0.10}), "portfolio": None, "loop_count": 1}
    upd = _ic_gate(st, ctx)
    assert upd["ic_gate_approved"] is False
    assert upd["loop_count"] == 2


def test_ic_gate_deny_no_increment_on_terminate(monkeypatch):
    monkeypatch.setattr("graph.builder.human_approve", lambda *a, **k: False)
    ctx = _ctx(ic_on_reject="terminate")
    st = {"manager_decision": _md({"600519": 0.10}), "portfolio": None, "loop_count": 1}
    upd = _ic_gate(st, ctx)
    assert upd["ic_gate_approved"] is False
    assert "loop_count" not in upd


# --------------------------- 路由 ---------------------------
class _FakeCtx:
    """轻量假上下文：_ic_gate / _route_ic 只需 ctx.settings.{ic_gate,max_loop,hitl_enabled}。"""

    def __init__(self, ic_on_reject="rejudge", hitl=True, max_loop=3):
        self.settings = Settings(hitl_enabled=hitl, max_loop=max_loop)
        self.settings.ic_gate = ICGateConfig(on_reject=ic_on_reject)


def _ctx(ic_on_reject="rejudge"):
    return _FakeCtx(ic_on_reject=ic_on_reject)


def _st(approved, loop=0):
    return {"ic_gate_approved": approved, "loop_count": loop}


def test_route_ic_approved_goes_trader():
    assert _route_ic(_st(True), _ctx()) == "trader"


def test_route_ic_deny_terminate():
    assert _route_ic(_st(False), _ctx("terminate")) == "reviewer"


def test_route_ic_deny_rejudge_manager():
    assert _route_ic(_st(False, loop=0), _ctx("rejudge")) == "manager"


def test_route_ic_deny_rejudge_overloop_reviewer():
    assert _route_ic(_st(False, loop=3), _ctx("rejudge")) == "reviewer"


# --------------------------- 全链路（LocalRunner）---------------------------
def test_full_pipeline_runs_all_new_roles(monkeypatch, ctx, init_state):
    """默认自动放行：完整闭环应产出全部新角色结果。"""
    monkeypatch.setattr("graph.builder.human_approve", lambda *a, **k: True)
    runner = LocalRunner(ctx)
    final = runner.invoke(init_state)
    # 入口与新增角色均产出
    assert final.get("market_events") is not None
    assert final.get("quant_signals")
    assert final.get("compliance_result") is not None
    assert final.get("ic_gate_approved") is True
    assert final.get("attribution_report") is not None
    assert final.get("execution_memo")
    # 交易仍落地
    assert len(final.get("fills", [])) > 0
    # 全链路审计含各 HITL 节点
    stages = [a.stage for a in final.get("audit_log", [])]
    assert "hitl_risk_review" in stages
    assert "hitl_ic" in stages
