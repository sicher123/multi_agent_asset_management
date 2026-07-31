"""硬约束①：单票仓位上限 & 总仓位上限（熊市自动降档）。"""
from __future__ import annotations

from risk.rules.base import RiskRule, RiskContext, RuleResult
from state.schemas import RiskMetric


class PositionLimitRule(RiskRule):
    name = "单票/总仓位上限"
    severity = "HARD"

    def check(self, ctx: RiskContext) -> RuleResult:
        pf = ctx.portfolio
        tv = pf.total_value
        # 计划买入名义额（按腿汇总）
        buy_notional = sum(l.quantity * l.expected_price for l in ctx.decision.rebalance_orders
                           if l.side.value == "BUY")
        messages = []

        # 单票
        def _cur_mv(tkr: str) -> float:
            for h in pf.holdings:
                if h.ticker == tkr:
                    return h.market_value
            return 0.0

        for leg in ctx.decision.rebalance_orders:
            if leg.side.value != "BUY":
                continue
            add = leg.quantity * leg.expected_price
            new_weight = (_cur_mv(leg.ticker) + add) / tv if tv else 1
            if new_weight > ctx.limits.single_position_max + 1e-9:
                messages.append(
                    f"{leg.ticker} 计划后仓位 {new_weight:.1%} 超过单票上限 {ctx.limits.single_position_max:.0%}")

        # 总仓位（含计划买入）
        eff_max = ctx.limits.effective_total_position_max(ctx.trend)
        post_position = (pf.holdings_value + buy_notional) / tv if tv else 1
        total_ok = post_position <= eff_max + 1e-9
        metric = RiskMetric(name="总仓位(含计划买入)", value=round(post_position, 4),
                            limit=round(eff_max, 4), passed=total_ok)
        if not total_ok:
            messages.append(f"总仓位将达 {post_position:.1%}，超过上限 {eff_max:.0%}（{'熊市' if ctx.trend.value=='BEAR' else '常态'}档）")

        return RuleResult(
            rule=self.name, severity=self.severity,
            violated=bool(messages),
            message="；".join(messages) if messages else "单票/总仓位均在限额内",
            metric=metric,
        )
