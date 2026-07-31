"""
回测引擎：给定价格路径与成交序列，复现组合权益曲线与绩效（历史回放）。

- gen_price_paths：为标的生成合成日线（真实环境替换为历史 K 线）。
- replay：把成交（默认落在首日）应用到初始组合，随后按日线逐日盯市，得到权益曲线。
- 系统级评估见 eval.evaluator（对比基准）。
"""
from __future__ import annotations

import random
from typing import Dict, List

from data.provider import BaseDataProvider
from eval.performance import annualized_return, daily_returns, max_drawdown, sharpe, total_return, win_rate
from state.schemas import Portfolio, TradeFill


class BacktestEngine:
    def __init__(self, data: BaseDataProvider, seed: int = 42):
        self.data = data
        self._rng = random.Random(seed)

    def gen_price_paths(self, tickers: List[str], days: int = 20) -> Dict[str, List[float]]:
        paths: Dict[str, List[float]] = {}
        for t in tickers:
            start = self.data.get_quote(t).last or 10.0
            prices = [start]
            for _ in range(days - 1):
                prices.append(round(prices[-1] * (1 + self._rng.uniform(-0.02, 0.02)), 2))
            paths[t] = prices
        return paths

    def replay(self, portfolio: Portfolio, fills: List[TradeFill],
               price_paths: Dict[str, List[float]]) -> List[float]:
        """返回逐日组合净值曲线（首日应用成交，随后盯市）。"""
        # 复制组合
        cash = portfolio.cash
        holdings = {h.ticker: {"qty": h.quantity, "last": h.last_price or h.cost_price}
                    for h in portfolio.holdings}
        days = max((len(p) for p in price_paths.values()), default=1)
        # 应用成交（首日）
        for f in fills:
            if f.side.value == "BUY":
                cash -= f.filled_qty * f.avg_price + f.commission
                h = holdings.setdefault(f.ticker, {"qty": 0, "last": f.avg_price})
                h["qty"] += f.filled_qty
                h["last"] = f.avg_price
            else:
                cash += f.filled_qty * f.avg_price - f.commission
                if f.ticker in holdings:
                    holdings[f.ticker]["qty"] = max(0, holdings[f.ticker]["qty"] - f.filled_qty)

        curve: List[float] = []
        for d in range(days):
            mv = cash
            for tk, h in holdings.items():
                price = price_paths.get(tk, [h["last"]])[min(d, len(price_paths.get(tk, [h["last"]])) - 1)]
                mv += h["qty"] * price
            curve.append(round(mv, 2))
        return curve

    @staticmethod
    def metrics(equity: List[float]) -> Dict[str, float]:
        return {
            "total_return": round(total_return(equity), 4),
            "annualized": round(annualized_return(equity), 4),
            "max_drawdown": round(max_drawdown(equity), 4),
            "sharpe": round(sharpe(equity), 4),
            "win_rate": round(win_rate(equity), 4),
        }
