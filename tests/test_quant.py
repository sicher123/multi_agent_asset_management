"""量化/因子研究员测试：系统因子信号生成（确定性、离线）。"""
from __future__ import annotations

from state.schemas import AnalystReport, Rating
from agents.quant import run as quant_run


def _reports():
    return [
        AnalystReport(ticker="600519", name="茅台", industry="白酒", rating=Rating.BUY, target_price=1480.0),
        AnalystReport(ticker="000001", name="平安", industry="银行", rating=Rating.HOLD, target_price=11.5),
    ]


def test_quant_produces_signals_for_all_tickers(ctx):
    state = {"analyst_reports": _reports()}
    upd = quant_run(state, ctx)
    sig = upd["quant_signals"]
    assert set(sig.keys()) == {"600519", "000001"}
    for s in sig.values():
        assert s.quant_view in ("BULLISH", "NEUTRAL", "BEARISH")
        assert -1.0 <= s.factor_score <= 1.0


def test_quant_disabled_returns_empty(ctx):
    ctx.settings.quant.enabled = False
    upd = quant_run({"analyst_reports": _reports()}, ctx)
    assert upd["quant_signals"] == {}
