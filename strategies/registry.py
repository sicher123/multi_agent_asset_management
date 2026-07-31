"""下单策略注册表：配置名 -> 策略实例。调用方只依赖此注册表，不依赖具体类。"""
from __future__ import annotations

from typing import Dict, List, Type

from strategies.base import OrderStrategy

STRATEGY_REGISTRY: Dict[str, OrderStrategy] = {}


def register_strategy(cls: Type[OrderStrategy]) -> Type[OrderStrategy]:
    STRATEGY_REGISTRY[cls.strategy_type.value] = cls()
    return cls


def get_strategy(name: str) -> OrderStrategy:
    if name not in STRATEGY_REGISTRY:
        raise KeyError(f"未知下单策略 '{name}'，可用：{list_strategies()}")
    return STRATEGY_REGISTRY[name]


def list_strategies() -> List[str]:
    return list(STRATEGY_REGISTRY.keys())


# 导入具体策略以触发注册（新增策略只需在此追加一行 + 实现文件）
from strategies.market import MarketOrderStrategy  # noqa: E402,F401
from strategies.twap import TWAPStrategy  # noqa: E402,F401
from strategies.vwap import VWAPStrategy  # noqa: E402,F401
