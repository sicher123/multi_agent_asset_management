"""绩效归因师测试：选股/择时/滑点贡献与研究员拆分。"""
from __future__ import annotations

from state.schemas import AnalystReport, Rating, TradeFill, Side
from agents.attribution import run as attr_run


def _state():
    reports = [AnalystReport(ticker="600519", name="茅台", industry="白酒",
                             rating=Rating.BUY, target_price=1500.0)]
    fills = [TradeFill(ticker="600519", side=Side.BUY, filled_qty=100,
                       avg_price=1480.0, slippage=0.001)]
    return {"analyst_reports": reports, "fills": fills}


def test_attribution_computed(ctx):
    upd = attr_run(_state(), ctx)
    rep = upd["attribution_report"]
    assert rep is not None
    # 买得比目标价低 -> 选股正贡献
    assert rep.selection_pnl > 0
    # 滑点成本为正（绝对值），择时贡献为负
    assert rep.slippage_cost > 0
    assert rep.timing_pnl < 0
    assert rep.per_researcher.get("600519") is not None


def test_attribution_disabled(ctx):
    ctx.settings.attribution.enabled = False
    upd = attr_run(_state(), ctx)
    assert upd["attribution_report"] is None


def test_attribution_empty_fills(ctx):
    upd = attr_run({"analyst_reports": [], "fills": []}, ctx)
    rep = upd["attribution_report"]
    assert rep is not None
    assert rep.selection_pnl == 0.0
