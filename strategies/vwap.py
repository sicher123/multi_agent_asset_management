"""VWAP 策略：按典型「U 型」成交量分布拆单，贴合市场真实成交量，冲击更小。"""
from __future__ import annotations

from typing import List

from state.schemas import ExecutionPlan, OrderLeg, ScheduledSlice, StrategyType
from strategies.base import OrderStrategy, StrategyContext
from strategies.registry import register_strategy


@register_strategy
class VWAPStrategy(OrderStrategy):
    strategy_type = StrategyType.VWAP
    label = "VWAP 量加权"

    def build(self, legs: List[OrderLeg], ctx: StrategyContext) -> ExecutionPlan:
        total = sum(l.quantity for l in legs)
        n = max(1, ctx.slices_count)
        # 开盘/收盘成交量大（U 型）
        raw = [1.6 if (i == 0 or i == n - 1) else 1.0 for i in range(n)]
        s = sum(raw)
        weights = [w / s for w in raw]
        slices: List[ScheduledSlice] = []
        assigned = 0
        for i in range(n):
            if i == n - 1:
                q = total - assigned
            else:
                q = round(total * weights[i])
                assigned += q
            slices.append(ScheduledSlice(seq=i + 1, time_label=f"T{i + 1}/{n}", quantity=max(0, q)))
        impact = round(min(0.006, (ctx.notional / ctx.adv) * 0.003), 4) if ctx.adv else 0.0
        return ExecutionPlan(
            strategy_type=self.strategy_type,
            slices=slices,
            estimated_market_impact_pct=impact,
            notes=f"按 U 型量分布拆为 {n} 段，贴合真实成交节奏。",
        )
