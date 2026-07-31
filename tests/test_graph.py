"""图路由测试：风控条件边 + 驳回回落循环 + 防死锁 + HITL 断点（P4-P6）。"""
from graph.builder import _route_manager, _route_risk, _route_exception, _route_confirm
from state.schemas import ManagerDecision, RiskCheckResult, RiskDecision, OrderLeg, Side


def _md(worth: bool):
    return ManagerDecision(worth_trading=worth, stop_loss_pct=0.08, take_profit_pct=0.20,
                           rebalance_orders=[OrderLeg(ticker="600519", side=Side.BUY,
                                                      quantity=1, expected_price=1.0)] if worth else [])


def test_route_manager_worth():
    assert _route_manager({"manager_decision": _md(True)}) == "risk"


def test_route_manager_not_worth():
    assert _route_manager({"manager_decision": _md(False)}) == "reviewer"


def test_route_risk_approved():
    assert _route_risk({"risk_result": RiskCheckResult(decision=RiskDecision.APPROVED)}, 3) == "confirm_trade"


def test_route_risk_rejected_then_loop():
    # 未超循环上限 => 回落投资经理
    assert _route_risk({"risk_result": RiskCheckResult(decision=RiskDecision.REJECTED),
                        "loop_count": 0}, 3) == "manager"


def test_route_risk_rejected_maxloop_ends():
    # 超循环上限 => 强制终结（防死锁）
    assert _route_risk({"risk_result": RiskCheckResult(decision=RiskDecision.REJECTED),
                        "loop_count": 3}, 3) == "reviewer"


def test_route_risk_exception_to_human():
    # 软约束例外 => 进入人工审批节点
    assert _route_risk({"risk_result": RiskCheckResult(decision=RiskDecision.EXCEPTION)}, 3) == "exception_human"


def test_route_exception_approve_and_deny():
    assert _route_exception({"human_decision": True}) == "confirm_trade"
    assert _route_exception({"human_decision": False}) == "reviewer"


def test_route_confirm_approve_and_deny():
    # 下单前确认通过后进入投委会终审（ic_gate），再由 ic_gate 路由到 trader
    assert _route_confirm({"pre_trade_approved": True}) == "ic_gate"
    assert _route_confirm({"pre_trade_approved": False}) == "reviewer"
