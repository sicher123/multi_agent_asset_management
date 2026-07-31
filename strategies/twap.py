"""TWAP 策略：在交易窗口内均匀拆单，降低市场冲击。"""
from __future__ import annotations

from typing import List

from state.schemas import ExecutionPlan, OrderLeg, ScheduledSlice, StrategyType
from strategies.base import OrderStrategy, StrategyContext
from strategies.registry import register_strategy


@register_strategy
class TWAPStrategy(OrderStrategy):
    strategy_type = StrategyType.TWAP
    label = "TWAP 时间加权"

    def build(self, legs: List[OrderLeg], ctx: StrategyContext) -> ExecutionPlan:
        total = sum(l.quantity for l in legs)
        n = max(1, ctx.slices_count)
        base, rem = divmod(total, n)
        slices = [
            ScheduledSlice(seq=i + 1, time_label=f"T{i + 1}/{n}", quantity=base + (1 if i < rem else 0))
            for i in range(n)
        ]
        impact = round(min(0.01, (ctx.notional / ctx.adv) * 0.005), 4) if ctx.adv else 0.0
        return ExecutionPlan(
            strategy_type=self.strategy_type,
            slices=slices,
            estimated_market_impact_pct=impact,
            notes=f"均分为 {n} 段执行，平滑市场冲击。",
        )
