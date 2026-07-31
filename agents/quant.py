"""
量化/因子研究员：与基本面（行业研究员）正交的系统化信号源。

- 不依赖 LLM 拍板，纯系统性计算（动量 / 波动 / 流动性 / 综合因子分），确定性、可审计、可单测。
- 复用数据层 get_quote / get_adv；真实环境可接入因子库 / 回测信号。
- 产出 quant_signals[ticker] = QuantSignal，供投资经理在投决时交叉验证 / 加权融合。
- 与基本面结论独立，避免单一方法论盲区。
"""
from __future__ import annotations

from typing import Dict, Any

from core.context import AppContext
from state.schemas import QuantSignal, AnalystReport


def _stable_vol(ticker: str) -> float:
    """确定性「波动率代理」：由标的代码派生，避免随机性破坏可复现。"""
    s = sum(ord(c) for c in ticker)
    return round(0.015 + (s % 5) * 0.008, 4)


def run(state: Dict[str, Any], ctx: AppContext) -> Dict[str, Any]:
    reports: list = state.get("analyst_reports", [])
    if not ctx.settings.quant.enabled:
        return {"quant_signals": {}, "stage": "quant",
                "log": ["[quant] 已禁用（quant.enabled=false）"]}

    band = ctx.settings.quant.neutral_band
    signals: Dict[str, QuantSignal] = {}
    for r in reports:
        tk = r.ticker
        q = ctx.data.get_quote(tk)
        adv = ctx.data.get_adv(tk)
        momentum = ((q.last - q.prev_close) / q.prev_close) if q.prev_close > 0 else 0.0
        # 流动性评分：ADV 相对名义的代理（这里用 ADV 归一化）
        liquidity_score = round(min(1.0, adv / 1.0e8), 3) if adv > 0 else 0.0
        # 综合因子分：动量为主，波动做轻微惩罚（确定性、无随机）
        factor_score = round(max(-1.0, min(1.0, momentum * 4 - (_stable_vol(tk) - 0.015) * 2)), 3)
        if factor_score > band:
            view = "BULLISH"
        elif factor_score < -band:
            view = "BEARISH"
        else:
            view = "NEUTRAL"
        note = (f"动量{momentum:+.1%}，波动{_stable_vol(tk):.1%}，"
                f"流动性{liquidity_score:.2f} -> {view}")
        signals[tk] = QuantSignal(
            ticker=tk, momentum=round(momentum, 4), volatility=_stable_vol(tk),
            liquidity_score=liquidity_score, factor_score=factor_score,
            quant_view=view, note=note,
        )

    n_bull = sum(1 for s in signals.values() if s.quant_view == "BULLISH")
    n_bear = sum(1 for s in signals.values() if s.quant_view == "BEARISH")
    return {"quant_signals": signals, "stage": "quant",
            "log": [f"[quant] 生成 {len(signals)} 个因子信号（看多 {n_bull}/看空 {n_bear}）",
                    *[f"[quant] {tk}: {s.note}" for tk, s in signals.items()]]
            }
