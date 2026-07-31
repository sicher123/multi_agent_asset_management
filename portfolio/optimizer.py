"""
组合层：风险预算优化（投资经理「目标权重」的来源）。

设计（冲刺项②）：
- 目标权重 **不再固定为 5%/只**，而是由「宏观仓位上限 × 行业景气 × 个股评级」的风险预算优化得到。
- 输入全部来自模型/数据：
    * 宏观仓位上限  -> MacroReport.suggested_max_position（LLM 产出）
    * 个股评级      -> AnalystReport.rating（LLM 产出，映射为 conviction 分数）
    * 行业景气      -> 由「同一行业内各标的评级」聚合得到的景气度代理（LLM 评级的分布）
- 确定性数学部分（风险预算归一化、封顶）放这里，可审计、可单测；真实模式下 LLM 也可直接给出
  target_portfolio_weights，经理侧再用同一 `clamp_targets` 兜底封顶，保证不破硬约束。
- 经理据此与「当前持仓权重」做 diff，产出调仓指令集（含买入补足与卖出降仓）。

仅 A 股。
"""
from __future__ import annotations

from typing import Dict, List

from state.schemas import AnalystReport, Rating, Side, OrderLeg, Portfolio


# 评级 -> 风险预算置信度（conviction）
CONVICTION_BY_RATING: Dict[Rating, float] = {
    Rating.BUY: 1.0,
    Rating.ADD: 0.7,
    Rating.HOLD: 0.4,
    Rating.REDUCE: 0.15,
    Rating.SELL: 0.0,
}


def conviction_of(rating: Rating) -> float:
    return CONVICTION_BY_RATING.get(rating, 0.4)


def industry_sentiment(reports: List[AnalystReport]) -> Dict[str, float]:
    """行业景气度代理：行业内各标的 conviction 的均值（跨标的评级分布）。

    单标的行业（universe 内仅 1 只）退回该标的自身 conviction，避免默认值失真。
    """
    acc: Dict[str, List[float]] = {}
    for r in reports:
        acc.setdefault(r.industry, []).append(conviction_of(r.rating))
    return {ind: sum(v) / len(v) for ind, v in acc.items()}


def _raw_budget(reports: List[AnalystReport], sentiment: Dict[str, float]) -> Dict[str, float]:
    """未封顶的风险预算：conviction × (0.5 + 0.5 × 行业景气)。"""
    out: Dict[str, float] = {}
    for r in reports:
        c = conviction_of(r.rating)
        s = sentiment.get(r.industry, 0.5)
        out[r.ticker] = c * (0.5 + 0.5 * s)
    return out


def _gate(reports: List[AnalystReport], budgets: Dict[str, float],
          current_weights: Dict[str, float]) -> Dict[str, float]:
    """建仓闸门：避免对 HOLD/REDUCE/SELL 凭空发起新仓；仅在已有持仓时做降仓。

    - BUY/ADD：直接采用预算（可从 0 发起建仓）
    - HOLD：有持仓才维持到预算权重，无持仓不发起
    - REDUCE：若已有持仓，目标不超过当前权重的 50%（降仓）；无持仓不动
    - SELL：目标权重 0（清仓/不持）
    """
    gated: Dict[str, float] = {}
    for r in reports:
        cur = current_weights.get(r.ticker, 0.0)
        b = budgets.get(r.ticker, 0.0)
        if r.rating in (Rating.BUY, Rating.ADD):
            gated[r.ticker] = b
        elif r.rating == Rating.HOLD:
            gated[r.ticker] = b if cur > 0 else 0.0
        elif r.rating == Rating.REDUCE:
            gated[r.ticker] = min(b, cur * 0.5) if cur > 0 else 0.0
        else:  # SELL
            gated[r.ticker] = 0.0
    return gated


def clamp_targets(raw: Dict[str, float], macro_cap: float, single_position_max: float) -> Dict[str, float]:
    """把任意来源（LLM 或优化器）的目标权重归一化并封顶：

    - 单票 ≤ single_position_max（硬约束）
    - 合计 ≤ macro_cap（宏观敞口上限；总额不足时不强行拉满，避免超配）
    """
    total = sum(raw.values())
    if total <= 0:
        return {k: 0.0 for k in raw}
    scale = min(1.0, macro_cap / total)
    return {k: min(v * scale, single_position_max) for k, v in raw.items()}


def risk_budget_allocation(reports: List[AnalystReport], macro_cap: float,
                           single_position_max: float,
                           current_weights: Dict[str, float] | None = None,
                           sentiment: Dict[str, float] | None = None) -> Dict[str, float]:
    """对外主入口：返回 {ticker: 目标权重}（已封顶、已建仓闸门过滤）。"""
    current_weights = current_weights or {}
    sentiment = sentiment or industry_sentiment(reports)
    budgets = _raw_budget(reports, sentiment)
    gated = _gate(reports, budgets, current_weights)
    return clamp_targets(gated, macro_cap, single_position_max)


def build_rebalance_orders(target: Dict[str, float], pf: Portfolio,
                           price_of, reports: List[AnalystReport],
                           min_trade_weight: float = 0.005) -> List[OrderLeg]:
    """目标权重 vs 当前持仓权重 做 diff，产出调仓指令集。

    - delta>阈值：买入补足到目标（仅 universe 内标的）
    - delta<-阈值：卖出降仓到目标（含 SELL 评级清仓；尊重当前可卖数量）
    不在 universe 内的既有持仓不参与本次调仓（不强行清仓整组合）。
    """
    tv = pf.total_value or 1.0
    by_ticker = {r.ticker: r for r in reports}
    cur_qty = {h.ticker: h.quantity for h in pf.holdings}
    orders: List[OrderLeg] = []

    for ticker, tw in target.items():
        if ticker not in by_ticker:
            continue  # 只调仓本轮研究的标的
        cur_w = pf.weight_of(ticker)
        delta = tw - cur_w
        # 清仓（目标权重=0，如 SELL 评级）不受最小交易阈值限制，确保主动撤出；
        # 其余买卖受阈值约束，避免无谓摩擦。
        is_full_exit = abs(tw) < 1e-9
        if not is_full_exit and abs(delta) < min_trade_weight:
            continue
        quote = price_of(ticker)
        price = quote.last if quote else 0.0
        if price <= 0:
            continue
        if delta > 0:
            qty = int(tv * delta / price)
            if qty > 0:
                orders.append(OrderLeg(ticker=ticker, side=Side.BUY, quantity=qty,
                                       expected_price=round(price, 2), target_weight=round(tw, 4)))
        else:
            target_qty = int(tv * tw / price)
            held = cur_qty.get(ticker, 0)
            if held <= 0:
                continue
            sell_qty = max(0, min(held - target_qty, held))
            if sell_qty > 0:
                orders.append(OrderLeg(ticker=ticker, side=Side.SELL, quantity=sell_qty,
                                       expected_price=round(price, 2), target_weight=round(tw, 4)))
    return orders
