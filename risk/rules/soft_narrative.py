"""
软约束②：叙事脆弱性（直接复用穿透叙事方法论）。

用「目标价相对 DCF 合理价的偏离度」衡量：偏离越大，叙事越脆弱，
越可能在利空下回撤。超过阈值 => 触发例外（需人工确认该叙事）。
维度自洽：目标价与 DCF 合理价均为每股（元）。
"""
from __future__ import annotations

from risk.rules.base import RiskRule, RiskContext, RuleResult
from state.schemas import RiskMetric

NARRATIVE_FRAGILITY_THRESHOLD = 0.20  # 目标价超过 DCF 合理价 20% 视为脆弱


class NarrativeFragilityRule(RiskRule):
    name = "叙事脆弱性"
    severity = "SOFT"

    def check(self, ctx: RiskContext) -> RuleResult:
        worst = 0.0
        worst_tk = "-"
        for r in ctx.analyst_reports:
            # 仅对真实调用过 DCF 的报告评估（dcf_fair_price>0）
            if r.dcf_used and r.dcf_fair_price > 0:
                dev = (r.target_price - r.dcf_fair_price) / r.dcf_fair_price
                if dev > worst:
                    worst = dev
                    worst_tk = r.ticker
        passed = worst <= NARRATIVE_FRAGILITY_THRESHOLD + 1e-9
        metric = RiskMetric(name=f"叙事偏离({worst_tk})", value=round(worst, 4),
                            limit=NARRATIVE_FRAGILITY_THRESHOLD, passed=passed)
        return RuleResult(
            rule=self.name, severity=self.severity, violated=not passed,
            message=(f"{worst_tk} 目标价超 DCF 合理价 {worst:.0%}，叙事脆弱，需人工确认"
                     if not passed else "叙事与 DCF 合理价基本吻合"),
            metric=metric,
        )
