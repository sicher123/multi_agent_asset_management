"""标的筛选 / 触发（Universe 入口）。演示用 watchlist；真实可接事件/扫描。"""
from __future__ import annotations

from typing import Dict, Any

from core.context import AppContext
from state.schemas import UniverseEntry


def run(state: Dict[str, Any], ctx: AppContext) -> Dict[str, Any]:
    universe = [
        UniverseEntry(ticker=w["ticker"], name=w.get("name", w["ticker"]),
                      industry=w.get("industry", "未知"), trigger="watchlist")
        for w in ctx.settings.watchlist
    ]
    return {
        "universe": universe,
        "stage": "screener",
        "log": [f"[screener] 生成 Universe，共 {len(universe)} 只标的"],
    }
