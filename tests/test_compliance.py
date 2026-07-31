"""合规（与风控合并岗位）测试：合规引擎 + 风控节点合并回灌。"""
from __future__ import annotations

from state.schemas import (
    ManagerDecision, OrderLeg, Side, Portfolio, RiskLimits, AnalystReport, Rating, RiskDecision,
)
from core.config import ComplianceConfig, Settings
from risk.compliance import ComplianceEngine, ComplianceContext
from agents.risk_controller import run as risk_run
from data.provider import MockAshareProvider


def _dec(legs):
    return ManagerDecision(worth_trading=True, stop_loss_pct=0.08, take_profit_pct=0.2, rebalance_orders=legs)


def _ctx_with_restricted(restricted):
    s = Settings()
    s.compliance = ComplianceConfig(restricted_list=restricted)
    eng = ComplianceEngine()
    cc = ComplianceContext(
        decision=_dec([OrderLeg(ticker="600519", side=Side.BUY, quantity=1, expected_price=1.0)]),
        portfolio=Portfolio(cash=10_000_000.0, holdings=[]),
        limits=RiskLimits(), compliance_cfg=s.compliance,
        data=MockAshareProvider(), analyst_reports=[], as_of="",
    )
    return eng, cc


def test_restricted_list_hard_violation():
    eng, cc = _ctx_with_restricted(["600519"])
    res = eng.check(cc)
    assert not res.passed
    assert any("600519" in h for h in res.restricted_hits)


def test_no_restricted_passes():
    eng, cc = _ctx_with_restricted([])
    res = eng.check(cc)
    assert res.passed
    assert res.restricted_hits == []


def test_disclosure_5pct_flagged():
    """举牌披露：计划后权重 >5% 触发披露提示（软，不阻断）。"""
    eng = ComplianceEngine()
    # 大额买入使权重超 5%
    cc = ComplianceContext(
        decision=_dec([OrderLeg(ticker="000001", side=Side.BUY, quantity=1_000_000, expected_price=10.0)]),
        portfolio=Portfolio(cash=10_000_000.0, holdings=[]),
        limits=RiskLimits(), compliance_cfg=ComplianceConfig(),
        data=MockAshareProvider(), analyst_reports=[], as_of="",
    )
    res = eng.check(cc)
    assert res.disclosure_hits  # 披露提示存在
    assert res.passed           # 披露不阻断流程


def test_risk_controller_merges_compliance_rejection():
    """风控节点：合规硬违规回灌风控结论，升级为 REJECTED。"""
    settings = Settings()
    settings.compliance = ComplianceConfig(restricted_list=["600519"])
    # 构造最小 ctx（复刻 _build_context 的所需字段）
    from core.llm import get_llm
    from harness.observability import Tracer
    from harness.cost_guard import CostGuard
    from harness.traced_llm import TracedLLM
    from kg.store import KnowledgeGraph
    from main import _build_context, _build_initial_portfolio
    tracer = Tracer(silent=True)
    cost = CostGuard(model="mock", budget=5.0)
    llm = TracedLLM(get_llm(settings), tracer, cost, actor="system")
    ctx = _build_context(settings, llm, MockAshareProvider(), tracer, cost, KnowledgeGraph())
    state = {
        "manager_decision": _dec([OrderLeg(ticker="600519", side=Side.BUY, quantity=1, expected_price=1.0)]),
        "portfolio": _build_initial_portfolio(settings),
        "risk_limits": settings.risk_limits,
        "analyst_reports": [AnalystReport(ticker="600519", name="X", industry="Y",
                                          rating=Rating.BUY, target_price=1.0)],
        "as_of_date": "2026-01-01",
    }
    upd = risk_run(state, ctx)
    assert upd["compliance_result"] is not None
    assert not upd["compliance_result"].passed
    assert upd["risk_result"].decision == RiskDecision.REJECTED
    assert "600519" in ";".join(upd["risk_result"].hard_violations)
