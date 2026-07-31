from state.schemas import OrderLeg, Side, StrategyType
from strategies.registry import get_strategy, list_strategies
from strategies.base import StrategyContext


def _legs(total=1000):
    return [OrderLeg(ticker="600519", side=Side.BUY, quantity=total, expected_price=100.0)]


def test_registry_has_defaults():
    names = list_strategies()
    assert "MARKET" in names and "TWAP" in names and "VWAP" in names


def test_market_single_slice():
    plan = get_strategy("MARKET").build(_legs(1000), StrategyContext())
    assert len(plan.slices) == 1
    assert plan.slices[0].quantity == 1000


def test_twap_sums_to_total():
    n = 7
    plan = get_strategy("TWAP").build(_legs(1000), StrategyContext(slices_count=n))
    assert len(plan.slices) == n
    assert sum(s.quantity for s in plan.slices) == 1000


def test_vwap_sums_to_total():
    n = 10
    plan = get_strategy("VWAP").build(_legs(1000), StrategyContext(slices_count=n))
    assert len(plan.slices) == n
    assert sum(s.quantity for s in plan.slices) == 1000
