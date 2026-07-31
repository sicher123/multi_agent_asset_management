"""市价即时策略：一次性成交，冲击最大，适合小单/紧急。"""
from __future__ import annotations

from typing import List

from state.schemas import ExecutionPlan, OrderLeg, ScheduledSlice, StrategyType
from strategies.base import OrderStrategy, StrategyContext
from strategies.registry import register_strategy


@register_strategy
class MarketOrderStrategy(OrderStrategy):
    strategy_type = StrategyType.MARKET
    label = "市价即时"

    def build(self, legs: List[OrderLeg], ctx: StrategyContext) -> ExecutionPlan:
        total = sum(l.quantity for l in legs)
        impact = round(min(0.02, (ctx.notional / ctx.adv) * 0.01), 4) if ctx.adv else 0.0
        return ExecutionPlan(
            strategy_type=self.strategy_type,
            slices=[ScheduledSlice(seq=1, time_label="立即", quantity=total)],
            estimated_market_impact_pct=impact,
            notes="一次性市价成交，市场冲击最大；仅建议小单使用。",
        )
