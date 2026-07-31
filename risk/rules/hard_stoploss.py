"""硬约束④：止损线设置合理 + 现有持仓未触发止损（触发则需减仓而非新开）。"""
from __future__ import annotations

from risk.rules.base import RiskRule, RiskContext, RuleResult
from state.schemas import RiskMetric


class StopLossRule(RiskRule):
    name = "止损线/持仓回撤"
    severity = "HARD"

    def check(self, ctx: RiskContext) -> RuleResult:
        msgs = []
        sl = ctx.decision.stop_loss_pct
        if not (0 < sl <= 0.15):
            msgs.append(f"止损线 {sl:.0%} 未设置或不合理（应 0~15%）")

        # 现有持仓回撤检查
        worst_dd = 0.0
        for h in ctx.portfolio.holdings:
            if h.cost_price > 0:
                dd = (h.cost_price - h.last_price) / h.cost_price
                worst_dd = max(worst_dd, dd)
                if dd >= ctx.limits.stop_loss_pct:
                    msgs.append(f"持仓 {h.ticker} 回撤 {dd:.1%} 已触及止损线，应减仓而非新开仓")
        metric = RiskMetric(name="最大持仓回撤", value=round(worst_dd, 4),
                            limit=round(ctx.limits.stop_loss_pct, 4),
                            passed=worst_dd < ctx.limits.stop_loss_pct)
        return RuleResult(
            rule=self.name, severity=self.severity, violated=bool(msgs),
            message=("；".join(msgs) if msgs else "止损设置合理，持仓未触发止损"),
            metric=metric,
        )
