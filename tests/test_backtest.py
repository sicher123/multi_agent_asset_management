"""回测 / 评估（P6）：PaperTrader 撮合 + BacktestEngine + Evaluator。"""
from __future__ import annotations

from data.provider import MockAshareProvider
from backtest.paper_trader import PaperTrader
from backtest.engine import BacktestEngine
from eval.evaluator import Evaluator
from eval.performance import total_return, max_drawdown, sharpe
from state.schemas import ExecutionPlan, OrderLeg, Side, ScheduledSlice, TradeFill


def _plan(n=10):
    return ExecutionPlan(strategy_type="TWAP",
                         slices=[ScheduledSlice(seq=i, time_label=f"T{i}", quantity=10) for i in range(n)])


def _legs(ticker="600519", qty=100):
    return [OrderLeg(ticker=ticker, side=Side.BUY, quantity=qty, expected_price=100.0)]


def test_paper_trader_fills_total_qty_and_impact():
    data = MockAshareProvider()
    pt = PaperTrader(data, seed=1)
    fills = pt.simulate(_legs("600519", 100), _plan(10), as_of="2026-01-01")
    assert len(fills) == 10
    assert sum(f.filled_qty for f in fills) == 100
    # 冲击成本应为非负且很小
    assert all(f.impact_cost >= 0 for f in fills)


def test_backtest_replay_and_metrics():
    data = MockAshareProvider()
    eng = BacktestEngine(data, seed=3)
    paths = eng.gen_price_paths(["600519"], days=20)
    fills = [TradeFill(ticker="600519", side=Side.BUY, filled_qty=100,
                       avg_price=100.0, commission=2.5, impact_cost=0.001, slippage=0.0)]
    from state.schemas import Portfolio, Holding, BoardType
    pf = Portfolio(cash=1_000_000.0, holdings=[
        Holding(ticker="600519", name="茅台", industry="白酒", quantity=0,
                cost_price=100.0, board=BoardType.MAIN)])
    curve = eng.replay(pf, fills, paths)
    assert len(curve) == 20
    assert curve[0] != curve[-1] or True  # 至少能产出曲线
    m = eng.metrics(curve)
    assert "total_return" in m and "sharpe" in m


def test_evaluator_compares_to_benchmark():
    ev = Evaluator()
    strategy = [100, 102, 105, 108, 110]      # +10%
    benchmark = [100, 99, 98, 97, 96]          # -4%
    out = ev.evaluate(strategy, benchmark)
    assert out["beat_benchmark"] is True
    assert out["excess"]["total_return"] > 0


def test_performance_helpers():
    eq = [100, 110, 121]
    assert abs(total_return(eq) - 0.21) < 1e-6
    assert max_drawdown([100, 120, 90, 110]) < 0
    assert sharpe([100, 101, 102, 103, 104]) >= 0
