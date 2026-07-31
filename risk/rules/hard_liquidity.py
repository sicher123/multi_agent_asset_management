"""硬约束③：流动性约束——拟买入额不超过过去 N 日日均成交额 ADV 的 X%。"""
from __future__ import annotations

from risk.rules.base import RiskRule, RiskContext, RuleResult
from state.schemas import RiskMetric


class LiquidityAdvRule(RiskRule):
    name = "流动性/ADV 约束"
    severity = "HARD"

    def check(self, ctx: RiskContext) -> RuleResult:
        messages = []
        worst_ratio = 0.0
        for leg in ctx.decision.rebalance_orders:
            if leg.side.value != "BUY":
                continue
            adv = ctx.data.get_adv(leg.ticker)
            if adv <= 0:
                messages.append(f"{leg.ticker} 无法获取 ADV，流动性未知")
                continue
            ratio = (leg.quantity * leg.expected_price) / adv
            worst_ratio = max(worst_ratio, ratio)
            if ratio > ctx.limits.adv_liquidity_pct + 1e-9:
                messages.append(
                    f"{leg.ticker} 买入额占 ADV {ratio:.1%} 超过上限 {ctx.limits.adv_liquidity_pct:.0%}，交易员难以执行")
        passed = not messages
        metric = RiskMetric(name="最大买入/ADV", value=round(worst_ratio, 4),
                            limit=round(ctx.limits.adv_liquidity_pct, 4), passed=passed)
        return RuleResult(
            rule=self.name, severity=self.severity, violated=not passed,
            message=("；".join(messages) if messages else "买入额均在 ADV 限额内"),
            metric=metric,
        )
