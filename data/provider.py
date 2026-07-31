"""
数据层抽象：仅 A 股。

- BaseDataProvider：统一接口，后期可替换为 tdx-connector / westock / wind 真实实现。
- MockAshareProvider：离线确定性数据，覆盖 watchlist 几个标的，供端到端验证。
- TdxProvider：tdx-connector 的桩（接口已对齐，待接入真实 MCP）。
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import List, Optional

from state.schemas import MacroReport, Trend, Fundamentals


@dataclass
class Quote:
    ticker: str
    name: str
    last: float
    prev_close: float
    industry: str


class BaseDataProvider:
    def get_quote(self, ticker: str) -> Quote: raise NotImplementedError
    def get_fundamentals(self, ticker: str) -> Fundamentals: raise NotImplementedError
    def get_macro(self) -> MacroReport: raise NotImplementedError
    def get_adv(self, ticker: str, days: int = 20) -> float: raise NotImplementedError
    def get_news(self, ticker: str) -> List[str]: raise NotImplementedError


class MockAshareProvider(BaseDataProvider):
    def __init__(self, seed: int = 42):
        self._rng = random.Random(seed)
        # 内置几个示例标的的「真实感」基础数据
        self._base = {
            "600519": ("贵州茅台", 1480.0, "白酒"),
            "000001": ("平安银行", 11.5, "银行"),
            "300750": ("宁德时代", 185.0, "新能源"),
        }

    def _meta(self, ticker: str):
        name, price, ind = self._base.get(ticker, (ticker, 50.0, "未知"))
        return name, price, ind

    def get_quote(self, ticker: str) -> Quote:
        name, price, ind = self._meta(ticker)
        last = round(price * (1 + self._rng.uniform(-0.03, 0.03)), 2)
        prev = round(price, 2)
        return Quote(ticker=ticker, name=name, last=last, prev_close=prev, industry=ind)

    def get_fundamentals(self, ticker: str) -> Fundamentals:
        name, price, ind = self._meta(ticker)
        net_profit_ttm = round(price * 1.2, 1)
        # 模拟三年一致预期净利润（TTM 基础上逐年 +8% 增长），供 DCF 真实反算
        g = 0.08
        e1 = round(net_profit_ttm * (1 + g), 1)
        e2 = round(e1 * (1 + g), 1)
        e3 = round(e2 * (1 + g), 1)
        return Fundamentals(
            ticker=ticker, market_cap=round(price * 12.5, 1),
            net_profit_ttm=net_profit_ttm,
            revenue_ttm=round(price * 5.0, 1),
            dividend_yield=round(self._rng.uniform(0.01, 0.03), 4),
            e1=e1, e2=e2, e3=e3,
        )

    def get_macro(self) -> MacroReport:
        return MacroReport(
            trend=Trend.SIDEWAYS, risk_appetite="中性偏谨慎",
            suggested_max_position=0.70, liquidity_view="流动性合理",
            summary="（模拟）宏观窄幅震荡，无系统性风险信号。",
        )

    def get_adv(self, ticker: str, days: int = 20) -> float:
        name, price, ind = self._meta(ticker)
        # 模拟日均成交额（元）：价格 * 流通股近似 * 换手
        return price * 1_000_000.0 * self._rng.uniform(0.8, 1.5)

    def get_news(self, ticker: str) -> List[str]:
        name, _, _ = self._meta(ticker)
        return [f"{name}：近期无重大负面事件（模拟）"]


class TdxProvider(BaseDataProvider):
    """tdx-connector 接入桩：接口已对齐，真实实现读取 MCP 返回。"""

    def __init__(self, connector=None):
        self._connector = connector

    def get_quote(self, ticker: str):
        raise NotImplementedError("TdxProvider 待接入 tdx-connector MCP（wenda_news / quotes / kline）")

    def get_fundamentals(self, ticker: str):
        raise NotImplementedError("TdxProvider 待接入")

    def get_macro(self):
        raise NotImplementedError("TdxProvider 待接入 wenda_macro")

    def get_adv(self, ticker: str, days: int = 20):
        raise NotImplementedError("TdxProvider 待接入 kline 计算 ADV")

    def get_news(self, ticker: str):
        raise NotImplementedError("TdxProvider 待接入 wenda_news")


def get_provider(settings) -> BaseDataProvider:
    if settings.data.provider == "tdx":
        return TdxProvider()
    return MockAshareProvider()
