"""
风控引擎：聚合硬约束 + 软约束，产出 RiskCheckResult。

- 硬约束任意 violated => 一票否决（REJECTED），反馈给投资经理优化。
- 无硬违规但软约束超阈 => EXCEPTION（需人工 HITL 审批例外）。
- 否则 APPROVED。

规则可插拔：register(rule) 即可加入，调用方（风控节点）不感知具体规则。
"""
from __future__ import annotations

from typing import List

from risk.rules.base import RiskRule, RiskContext, RuleResult
from state.schemas import RiskCheckResult, RiskDecision


class RiskEngine:
    def __init__(self):
        self.hard_rules: List[RiskRule] = []
        self.soft_rules: List[RiskRule] = []
        self._register_defaults()

    def register(self, rule: RiskRule) -> None:
        if rule.severity == "HARD":
            self.hard_rules.append(rule)
        else:
            self.soft_rules.append(rule)

    def _register_defaults(self):
        # 硬约束
        from risk.rules.hard_position import PositionLimitRule
        from risk.rules.hard_industry import IndustryConcentrationRule
        from risk.rules.hard_liquidity import LiquidityAdvRule
        from risk.rules.hard_stoploss import StopLossRule
        from risk.rules.hard_market import MarketRulesRule
        # 软约束
        from risk.rules.soft_var import VaRRule
        from risk.rules.soft_narrative import NarrativeFragilityRule
        for r in (PositionLimitRule(), IndustryConcentrationRule(), LiquidityAdvRule(),
                  StopLossRule(), MarketRulesRule(), VaRRule(), NarrativeFragilityRule()):
            self.register(r)

    def check(self, ctx: RiskContext) -> RiskCheckResult:
        hard: List[RuleResult] = [r.check(ctx) for r in self.hard_rules]
        soft: List[RuleResult] = [r.check(ctx) for r in self.soft_rules]

        violations = [h.message for h in hard if h.violated]
        soft_beyond = [s.message for s in soft if s.violated]
        metrics = [s.metric for s in soft if s.metric is not None]

        if violations:
            decision = RiskDecision.REJECTED
            requires_human = False
            feedback = "硬约束未通过，请优化下单策略：" + "；".join(violations)
        elif soft_beyond:
            decision = RiskDecision.EXCEPTION
            requires_human = True
            feedback = "无硬违规，但软约束触发例外，需人工审批：" + "；".join(soft_beyond)
        else:
            decision = RiskDecision.APPROVED
            requires_human = False
            feedback = "风控通过（硬约束+软约束均在限额内）。"

        return RiskCheckResult(
            decision=decision,
            hard_violations=violations,
            soft_findings=metrics,
            feedback=feedback,
            requires_human=requires_human,
        )
