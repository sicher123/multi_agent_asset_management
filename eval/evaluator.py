"""
系统级评估（Evaluator）：策略 vs 基准（等权买入持有 Universe）。

不只评「报告写得好不好」，更评「组合赚没赚钱、控没控回撤」。
产出对比报告，供 Monitor 展示与审核者审计。
"""
from __future__ import annotations

from typing import Dict, List

from eval.performance import total_return, max_drawdown, sharpe, annualized_return, win_rate


class Evaluator:
    def evaluate(self, strategy_equity: List[float], benchmark_equity: List[float],
                 label: str = "strategy") -> Dict[str, object]:
        s = {
            "total_return": total_return(strategy_equity),
            "annualized": annualized_return(strategy_equity),
            "max_drawdown": max_drawdown(strategy_equity),
            "sharpe": sharpe(strategy_equity),
            "win_rate": win_rate(strategy_equity),
        }
        b = {
            "total_return": total_return(benchmark_equity),
            "annualized": annualized_return(benchmark_equity),
            "max_drawdown": max_drawdown(benchmark_equity),
            "sharpe": sharpe(benchmark_equity),
            "win_rate": win_rate(benchmark_equity),
        }
        excess = {k: round(s[k] - b[k], 4) for k in s}
        return {
            "label": label,
            "strategy": {k: round(v, 4) for k, v in s.items()},
            "benchmark": {k: round(v, 4) for k, v in b.items()},
            "excess": excess,
            "beat_benchmark": bool(excess["total_return"] > 0),
        }
