"""
绩效归因师：投后（交易员之后）对本次闭环做绩效归因。

- 选股贡献（selection）：成交价相对分析师目标价的优劣（买得比目标价低=正贡献）。
- 择时/执行贡献（timing）：以滑点成本近似（执行时机优劣）。
- 各研究员（标的）贡献拆分，用于双角色评审里评价研究员质量。
- 与 replayer 解耦：replayer 负责成交落地 + KG 回灌；本节点只做分析，不修改组合。
"""
from __future__ import annotations

from typing import Dict, Any

from core.context import AppContext
from state.schemas import AttributionReport, TradeFill, AnalystReport, Side


def run(state: Dict[str, Any], ctx: AppContext) -> Dict[str, Any]:
    if not ctx.settings.attribution.enabled:
        return {"attribution_report": None, "stage": "attribution",
                "log": ["[归因] 已禁用（attribution.enabled=false）"]}

    fills: list = state.get("fills", [])
    target_of = {r.ticker: r.target_price for r in state.get("analyst_reports", [])}

    selection = 0.0
    slippage_cost = 0.0
    per_researcher: Dict[str, float] = {}
    for f in fills:
        tg = target_of.get(f.ticker)
        if tg:
            if f.side.value == "BUY":
                contrib = (tg - f.avg_price) * f.filled_qty
            else:
                contrib = (f.avg_price - tg) * f.filled_qty
            selection += contrib
            per_researcher[f.ticker] = round(per_researcher.get(f.ticker, 0.0) + contrib, 2)
        slippage_cost += f.slippage * f.filled_qty * f.avg_price

    timing = -slippage_cost  # 执行择时贡献近似为负的滑点成本
    report = AttributionReport(
        selection_pnl=round(selection, 2),
        timing_pnl=round(timing, 2),
        slippage_cost=round(slippage_cost, 2),
        per_researcher={k: round(v, 2) for k, v in per_researcher.items()},
        summary=(f"选股贡献 {selection:+,.0f} 元，执行/择时贡献 {timing:+,.0f} 元，"
                 f"总滑点成本 {slippage_cost:,.0f} 元"
                 + (f"；研究员贡献：{per_researcher}" if per_researcher else "")),
    )
    return {"attribution_report": report, "stage": "attribution",
            "log": [f"[归因] {report.summary}"]}
