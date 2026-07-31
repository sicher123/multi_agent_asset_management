"""
A 股市场微观结构规则（硬编码的「硬事实」，风控与交易员都依赖它）。

仅 A 股：涨跌停板、T+1、ST 限制、禁止裸卖空。
注意：这些是交易所规则，不是可调的风控参数——属于「不可配置的事实层」。
"""
from __future__ import annotations

from state.schemas import BoardType, Side


def board_of(ticker: str) -> BoardType:
    if ticker.startswith("688"):
        return BoardType.STAR
    if ticker.startswith("300"):
        return BoardType.CHINEXT
    if ticker.startswith("60") or ticker.startswith("00") or ticker.startswith("30"):
        # 30/00 已在前面的创业板分支覆盖，这里兜底主板
        return BoardType.MAIN
    return BoardType.MAIN


def limit_pct(ticker: str) -> float:
    """当日涨跌幅上限（绝对值）。ST 由调用方另行禁止。"""
    b = board_of(ticker)
    if b == BoardType.CHINEXT or b == BoardType.STAR:
        return 0.20
    return 0.10


def is_limit_up(price: float, prev_close: float, ticker: str) -> bool:
    if prev_close <= 0:
        return False
    return price >= prev_close * (1 + limit_pct(ticker)) - 1e-6


def is_limit_down(price: float, prev_close: float, ticker: str) -> bool:
    if prev_close <= 0:
        return False
    return price <= prev_close * (1 - limit_pct(ticker)) + 1e-6


def is_st(name: str) -> bool:
    return "ST" in name.upper() or "退" in name


def can_buy(ticker: str, name: str, price: float, prev_close: float) -> tuple[bool, str]:
    """买入可行性（不含额度，仅市场规则）。"""
    if is_st(name):
        return False, "ST/* 股票禁投"
    if is_limit_up(price, prev_close, ticker):
        return False, "涨停板无法买入"
    return True, ""


def can_sell(ticker: str, name: str, price: float, prev_close: float, bought_today: bool) -> tuple[bool, str]:
    """卖出可行性：A 股 T+1，当日买入不能当日卖出。"""
    if is_st(name):
        return False, "ST/* 股票禁投"
    if is_limit_down(price, prev_close, ticker):
        return False, "跌停板无法卖出"
    if bought_today:
        return False, "T+1：当日买入不可当日卖出"
    return True, ""


def short_selling_allowed() -> bool:
    return False  # A 股禁止裸卖空
