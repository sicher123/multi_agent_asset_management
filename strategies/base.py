"""
下单策略 —— 可插拔核心（策略模式）。

新增一种策略只需：继承 OrderStrategy 实现 build()，并在模块导入时用
@register_strategy 注册（见 registry.py）。投资经理/交易员通过配置名取用，
不依赖具体实现 => 后期替换/增加策略零改动调用方。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List

from state.schemas import ExecutionPlan, OrderLeg, StrategyType


@dataclass
class StrategyContext:
    slices_count: int = 10
    window_label: str = "09:30-15:00"
    adv: float = 0.0               # 过去 N 日日均成交额（元），用于冲击预估
    notional: float = 0.0          # 本批订单名义金额（元）


class OrderStrategy(ABC):
    # 子类设置：策略类型与展示名
    strategy_type: StrategyType
    label: str

    @abstractmethod
    def build(self, legs: List[OrderLeg], ctx: StrategyContext) -> ExecutionPlan:
        """把调仓指令集转成带时间表的可执行计划。"""
        ...
