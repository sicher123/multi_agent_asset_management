"""
审核者（in-system 业务质量审计节点）。

以「业务正确性」视角审查本轮产出：风控是否真硬卡、组合层是否覆盖、A 股规则是否落地、
循环是否防死锁。结果写入 audit_log，供合规留痕，也供人工（或外部评审）复核。
（外部「审核者角色」的详尽报告见 REVIEW_报告.md，由开发者交付后由审核者角色出具。）
"""
from __future__ import annotations

from typing import Dict, Any

from core.context import AppContext
from data.ashare_rules import can_buy, can_sell
from state.schemas import AuditEntry, RiskDecision

ISSUES = []


def run(state: Dict[str, Any], ctx: AppContext) -> Dict[str, Any]:
    issues = []
    md = state.get("manager_decision")
    risk = state.get("risk_result")
    loop = state.get("loop_count", 0)

    if md and md.worth_trading and not md.rebalance_orders:
        issues.append("worth_trading=True 但无调仓指令（组合层未产出订单）")
    if md and (md.stop_loss_pct <= 0 or md.take_profit_pct <= 0):
        issues.append("未设置止损/止盈线")
    if risk is None:
        issues.append("缺少风控结论")
    elif risk.decision == RiskDecision.REJECTED and loop == 0:
        issues.append("首轮即被驳回（策略可能在硬约束边界）")
    if loop > ctx.settings.max_loop:
        issues.append(f"循环次数 {loop} 超过上限 {ctx.settings.max_loop}（防死锁失败）")

    # A 股规则逐单复核
    if md:
        for leg in md.rebalance_orders:
            q = ctx.data.get_quote(leg.ticker)
            ok, reason = (can_buy if leg.side.value == "BUY" else
                          lambda *a: can_sell(*a, bought_today=False))(leg.ticker, q.name, q.last, q.prev_close)
            if not ok:
                issues.append(f"{leg.ticker} {leg.side.value} 违反 A 股规则：{reason}")

    verdict = "通过" if not issues else f"发现 {len(issues)} 项待核"
    entry = AuditEntry(
        stage="reviewer", actor="审核者", action="业务质量审计",
        detail=f"结论={verdict}；" + ("；".join(issues) if issues else "风控/组合/规则均自洽"),
        timestamp=state.get("as_of_date", ""),
    )
    return {"audit_log": [entry], "stage": "reviewer",
            "log": [f"[reviewer] {verdict}"]}
