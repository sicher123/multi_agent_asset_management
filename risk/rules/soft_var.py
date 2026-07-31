"""软约束①：组合 1 日 95% VaR 估算（演示用历史波动率代理）。"""
from __future__ import annotations

from risk.rules.base import RiskRule, RiskContext, RuleResult
from state.schemas import RiskMetric


class VaRRule(RiskRule):
    name = "组合 VaR(95%,1d)"
    severity = "SOFT"

    def check(self, ctx: RiskContext) -> RuleResult:
        # 演示：以组合净值 1% 作为日 VaR 代理（真实应基于持仓收益协方差）
        pf = ctx.portfolio
        var_est = pf.total_value * 0.01
        limit = ctx.limits.var_1d_95_max * pf.total_value
        passed = var_est <= limit + 1e-9
        metric = RiskMetric(name="VaR(95%,1d)", value=round(var_est / pf.total_value, 4),
                            limit=round(ctx.limits.var_1d_95_max, 4), passed=passed)
        return RuleResult(
            rule=self.name, severity=self.severity, violated=not passed,
            message=(f"VaR {var_est / pf.total_value:.1%} 超过上限 {ctx.limits.var_1d_95_max:.0%}，需人工例外"
                     if not passed else "VaR 在限额内"),
            metric=metric,
        )
