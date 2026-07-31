"""算法交易/执行优化（合并进交易员）测试：执行策略选择。"""
from __future__ import annotations

from state.schemas import ManagerDecision, OrderLeg, Side, RiskCheckResult, RiskDecision
from agents.trader import run as trader_run, _select_strategy


def _state(quantity, price=1480.0):
    md = ManagerDecision(worth_trading=True, stop_loss_pct=0.08, take_profit_pct=0.2,
                         rebalance_orders=[OrderLeg(ticker="600519", side=Side.BUY,
                                                    quantity=quantity, expected_price=price)])
    return {"manager_decision": md, "risk_result": RiskCheckResult(decision=RiskDecision.APPROVED),
            "as_of_date": "2026-01-01"}


def test_select_vwap_for_large_notional(ctx):
    # 名义额 > large_notional(5e6)
    notional = 5000 * 1480.0  # 7.4M
    adv = ctx.data.get_adv("600519")
    assert _select_strategy(notional, adv, ctx) == "VWAP"


def test_select_twap_for_small_notional(ctx):
    notional = 10 * 1480.0  # 14.8K
    adv = ctx.data.get_adv("600519")
    assert _select_strategy(notional, adv, ctx) == "TWAP"


def test_trader_execution_memo_vwap(ctx):
    upd = trader_run(_state(5000), ctx)
    assert upd["execution_memo"]
    assert "VWAP" in upd["execution_memo"]
    assert len(upd["fills"]) > 0


def test_trader_execution_memo_twap(ctx):
    upd = trader_run(_state(10), ctx)
    assert "TWAP" in upd["execution_memo"]
    assert len(upd["fills"]) > 0


def test_trader_skips_when_risk_not_approved(ctx):
    md = ManagerDecision(worth_trading=True, stop_loss_pct=0.08, take_profit_pct=0.2,
                         rebalance_orders=[OrderLeg(ticker="600519", side=Side.BUY, quantity=1, expected_price=1.0)])
    st = {"manager_decision": md, "risk_result": RiskCheckResult(decision=RiskDecision.REJECTED)}
    upd = trader_run(st, ctx)
    assert upd["fills"] == []
