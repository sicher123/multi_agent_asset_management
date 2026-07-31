"""
绩效指标（纯标准库，无 numpy）：权益曲线 -> 收益/回撤/夏普等。
供 Evaluator 与 Monitor 复用。
"""
from __future__ import annotations

import math
from typing import List


def daily_returns(equity: List[float]) -> List[float]:
    return [equity[i] / equity[i - 1] - 1 for i in range(1, len(equity)) if equity[i - 1] != 0]


def total_return(equity: List[float]) -> float:
    return (equity[-1] / equity[0] - 1) if len(equity) >= 2 and equity[0] else 0.0


def max_drawdown(equity: List[float]) -> float:
    peak = equity[0] if equity else 0.0
    mdd = 0.0
    for v in equity:
        peak = max(peak, v)
        dd = (v - peak) / peak if peak else 0.0
        mdd = min(mdd, dd)
    return mdd


def annualized_return(equity: List[float], periods_per_year: int = 252) -> float:
    if len(equity) < 2 or equity[0] == 0:
        return 0.0
    n = len(equity) - 1
    return (equity[-1] / equity[0]) ** (periods_per_year / n) - 1


def sharpe(equity: List[float], rf: float = 0.0, periods_per_year: int = 252) -> float:
    rets = daily_returns(equity)
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    std = math.sqrt(var)
    if std == 0:
        return 0.0
    return (mean - rf) / std * math.sqrt(periods_per_year)


def win_rate(equity: List[float]) -> float:
    rets = daily_returns(equity)
    if not rets:
        return 0.0
    return sum(1 for r in rets if r > 0) / len(rets)
