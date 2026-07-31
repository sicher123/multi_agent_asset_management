"""硬约束②：单一行业集中度上限。"""
from __future__ import annotations

from risk.rules.base import RiskRule, RiskContext, RuleResult
from state.schemas import RiskMetric


class IndustryConcentrationRule(RiskRule):
    name = "行业集中度上限"
    severity = "HARD"

    def check(self, ctx: RiskContext) -> RuleResult:
        pf = ctx.portfolio
        tv = pf.total_value
        # 当前行业敞口
        exp = pf.industry_exposure()
        # 把计划买入按行业累加
        name_industry = {r.ticker: r.industry for r in ctx.analyst_reports}
        for leg in ctx.decision.rebalance_orders:
            if leg.side.value != "BUY":
                continue
            ind = name_industry.get(leg.ticker)
            if not ind:
                continue
            add = leg.quantity * leg.expected_price / tv if tv else 0
            exp[ind] = exp.get(ind, 0.0) + add

        worst = max(exp.items(), key=lambda kv: kv[1]) if exp else ("-", 0.0)
        passed = worst[1] <= ctx.limits.industry_concentration_max + 1e-9
        metric = RiskMetric(name=f"行业'{worst[0]}'敞口", value=round(worst[1], 4),
                            limit=round(ctx.limits.industry_concentration_max, 4), passed=passed)
        return RuleResult(
            rule=self.name, severity=self.severity, violated=not passed,
            message=(f"行业 {worst[0]} 计划后敞口 {worst[1]:.1%} 超过上限 {ctx.limits.industry_concentration_max:.0%}"
                     if not passed else "行业集中度在限额内"),
            metric=metric,
        )
