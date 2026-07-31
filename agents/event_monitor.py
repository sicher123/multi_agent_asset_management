"""
舆情/事件监控员：流程入口的「市场心跳」节点。

- 对观察池（watchlist）逐标的读取新闻/公告，扫描负面与重大关键词。
- 产出 market_events：标注是否「重大事件」（material），供调度层触发重研、供投决参考。
- 与基本面/量化正交：它只负责「发生了什么」，不预测方向。
- 真实环境可接入 tdx-connector 的 wenda_news / 龙虎榜 / 公告流。
"""
from __future__ import annotations

from typing import Dict, Any, List

from core.context import AppContext
from state.schemas import MarketEvent


def run(state: Dict[str, Any], ctx: AppContext) -> Dict[str, Any]:
    if not ctx.settings.event_monitor.enabled:
        return {"market_events": [], "stage": "event_monitor",
                "log": ["[事件监控] 已禁用（event_monitor.enabled=false）"]}

    cfg = ctx.settings.event_monitor
    neg = set(cfg.negative_keywords or [])
    mat = set(cfg.material_keywords or [])
    events: List[MarketEvent] = []

    tickers = [w["ticker"] for w in ctx.settings.watchlist]
    for tk in tickers:
        for raw in ctx.data.get_news(tk):
            is_neg = any(k in raw for k in neg)
            # 重大关键词须排除「无…」类否定表述（如「无重大负面」不应触发）
            is_mat = any(k in raw for k in mat) and "无" not in raw
            material = is_neg or is_mat
            if is_neg or is_mat:
                events.append(MarketEvent(
                    ticker=tk, kind="news", headline=raw,
                    material=material,
                    detail=("负面" if is_neg else "") + ("重大" if is_mat else ""),
                ))

    n_mat = sum(1 for e in events if e.material)
    return {"market_events": events, "stage": "event_monitor",
            "log": [f"[事件监控] 扫描 {len(tickers)} 只标的，命中 {len(events)} 条事件（重大 {n_mat}）",
                    *[f"[事件监控] {e.ticker}: {e.headline}" for e in events[:5]]
            ]}
