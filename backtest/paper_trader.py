"""
PaperTrader：模拟撮合（降低市场冲击，产出真实感成交回报）。

- 按 ExecutionPlan 的拆单时间表逐段撮合，价格围绕预期价做随机游走。
- 市场冲击成本 ∝ 名义金额 / ADV（日均成交额）：大单冲击更大，体现「降冲击」目标。
- 卖出方向叠加卖压（价格略向下），符合 A 股盘口微观结构。
- 离线确定性（固定 seed），可复现；真实环境替换为实盘/历史回放即可。
"""
from __future__ import annotations

import random
from typing import List

from data.provider import BaseDataProvider
from state.schemas import ExecutionPlan, OrderLeg, Side, TradeFill


class PaperTrader:
    def __init__(self, data: BaseDataProvider, seed: int = 7):
        self.data = data
        self._rng = random.Random(seed)

    def simulate(self, legs: List[OrderLeg], plan: ExecutionPlan, as_of: str = "") -> List[TradeFill]:
        rng = self._rng
        adv = self.data.get_adv(legs[0].ticker) if legs else 0.0
        fills: List[TradeFill] = []
        slices = plan.slices or [None]
        n = max(len(slices), 1)
        for leg in legs:
            per = max(1, leg.quantity // n)
            remaining = leg.quantity
            for i, _ in enumerate(slices):
                qty = per if i < n - 1 else remaining
                if qty <= 0:
                    break
                remaining -= qty
                impact = min(0.02, (leg.quantity * leg.expected_price) / (adv + 1e-9) * 0.5) if adv else 0.0
                drift = rng.uniform(-impact, impact * 0.3)
                if leg.side == Side.SELL:
                    drift = -abs(drift)
                avg = round(leg.expected_price * (1 + drift), 2)
                fills.append(TradeFill(
                    ticker=leg.ticker, side=leg.side, filled_qty=qty,
                    avg_price=avg, commission=round(qty * avg * 0.00025, 2),
                    impact_cost=round(impact, 4), slippage=round(drift, 4),
                    timestamp=as_of,
                ))
        return fills
