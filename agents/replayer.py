"""
复盘 / 成交落地（学习闭环）。

P4 增强：成交后把「预测目标价 vs 实际价」回灌知识图谱，上调/下调 HAS_TARGET 置信度
（穿透叙事的贝叶斯回溯思想），使 KG 随实盘演进。
- 把成交回报应用到组合（更新现金与持仓）。
- 记录审计。
"""
from __future__ import annotations

from typing import Dict, Any

from core.context import AppContext
from state.schemas import AuditEntry, Holding, Portfolio, BoardType


def _apply(pf: Portfolio, fills) -> Portfolio:
    for f in fills:
        if f.side.value == "BUY":
            pf.cash -= f.filled_qty * f.avg_price + f.commission
            found = next((h for h in pf.holdings if h.ticker == f.ticker), None)
            if found:
                total_qty = found.quantity + f.filled_qty
                found.cost_price = (found.cost_price * found.quantity + f.avg_price * f.filled_qty) / total_qty
                found.quantity = total_qty
                found.last_price = f.avg_price
            else:
                pf.holdings.append(Holding(
                    ticker=f.ticker, name=f.ticker, industry="未知",
                    quantity=f.filled_qty, cost_price=f.avg_price, last_price=f.avg_price,
                    board=BoardType.MAIN))
        else:
            pf.cash += f.filled_qty * f.avg_price - f.commission
            found = next((h for h in pf.holdings if h.ticker == f.ticker), None)
            if found:
                found.quantity = max(0, found.quantity - f.filled_qty)
                if found.quantity == 0:
                    pf.holdings.remove(found)
    return pf


def run(state: Dict[str, Any], ctx: AppContext) -> Dict[str, Any]:
    pf = state["portfolio"]
    fills = state.get("fills", [])
    updated = _apply(pf, fills)

    # 复盘回灌 KG 置信度
    kg_updates = 0
    if ctx.kg_updater:
        target_of = {r.ticker: r.target_price for r in state.get("analyst_reports", [])}
        for f in fills:
            pred = target_of.get(f.ticker)
            if pred:
                actual = ctx.data.get_quote(f.ticker).last
                ctx.kg_updater.update_outcome(f.ticker, pred, actual)
                kg_updates += 1

    entry = AuditEntry(
        stage="replayer", actor="复盘", action="成交落地+KG回灌",
        detail=f"组合净值={updated.total_value:,.0f}，持仓 {len(updated.holdings)} 只，KG 回灌 {kg_updates}",
        timestamp=state.get("as_of_date", ""),
    )
    return {"portfolio": updated, "audit_log": [entry],
            "kg_stats": {"kg_outcome_updates": kg_updates}, "stage": "replayer",
            "log": [f"[replayer] 组合净值={updated.total_value:,.0f}，KG 回灌 {kg_updates}"]}
