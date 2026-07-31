"""
投资经理（组合层）：聚合宏观 + 行业 + 基本面 + 技术面 + 知识图谱上下文，判断是否值得下单。

P4 增强：对每个候选标的，用 GraphRAGRetriever 拉取同行/概念/宏观驱动/事件上下文，注入 LLM prompt，
使投决能利用「机构记忆」（而不仅仅是本轮报告）。

冲刺项②：目标权重 **不再固定 5%/只**，而是由风险预算优化得到：
    目标权重 = f(宏观仓位上限, 行业景气, 个股评级) ，且与「当前持仓权重」做 diff 产出调仓指令（含卖出降仓）。
下单数量由 Python 按组合层规则计算（确定性、可审计），LLM 只给研判与止损止盈；真实模式下 LLM 也可直接
返回 target_portfolio_weights，经理侧统一用 `clamp_targets` 兜底封顶，保证不破硬约束。
"""
from __future__ import annotations

from typing import Dict, Any, List

from core.context import AppContext
from state.schemas import ManagerDecision, OrderLeg, Side, Rating, Portfolio
from portfolio.optimizer import (
    risk_budget_allocation,
    industry_sentiment,
    conviction_of,
    clamp_targets,
    build_rebalance_orders,
)


def run(state: Dict[str, Any], ctx: AppContext) -> Dict[str, Any]:
    macro = state.get("macro_report")
    pf: Portfolio = state["portfolio"]
    analyst_reports = state.get("analyst_reports", [])
    quant_signals = state.get("quant_signals", {})

    kg_ctx_parts = []
    for r in analyst_reports:
        if ctx.retriever:
            kg_ctx_parts.append(ctx.retriever.to_context(r.ticker))
    kg_ctx = "\n".join(kg_ctx_parts)

    system = ctx.prompthub.get("manager") if ctx.prompthub else "你是投资经理，负责组合层决策"
    prompt = (f"综合宏观({macro.trend.value if macro else 'NA'})与 {len(analyst_reports)} 份个股报告做投决。\n"
              f"请基于「宏观仓位上限 × 行业景气 × 个股评级」做风险预算优化，给出 target_portfolio_weights，"
              f"并与当前持仓做 diff 产出调仓指令。\n"
              f"知识图谱上下文：\n{kg_ctx}")
    if quant_signals:
        qsum = " ".join(f"{t}:{s.quant_view}({s.factor_score:+.2f})" for t, s in quant_signals.items())
        prompt += f"\n量化/因子研究员正交信号（参考，不替代基本面）：{qsum}"
    md: ManagerDecision = ctx.llm.structured(prompt, system=system, model_cls=ManagerDecision)

    # —— 风险预算优化：宏观仓位上限 × 行业景气 × 个股评级 ——
    macro_cap = (macro.suggested_max_position if macro
                 else ctx.settings.risk_limits.total_position_max)
    sentiment = industry_sentiment(analyst_reports)
    current_weights = {r.ticker: pf.weight_of(r.ticker) for r in analyst_reports}
    opt_target = risk_budget_allocation(
        analyst_reports, macro_cap, ctx.settings.risk_limits.single_position_max,
        current_weights=current_weights, sentiment=sentiment,
    )

    # LLM（真实模式）可能直接给出 target_portfolio_weights；否则用优化器结果。
    # 无论来源，统一封顶，保证不破单票/总仓位硬约束。
    llm_target = md.target_portfolio_weights or {}
    raw: Dict[str, float] = {}
    for r in analyst_reports:
        tk = r.ticker
        raw[tk] = llm_target.get(tk, opt_target.get(tk, 0.0))
    target = clamp_targets(raw, macro_cap, ctx.settings.risk_limits.single_position_max)

    # 与当前持仓 diff -> 调仓指令集（含买入补足 / 卖出降仓）
    orders: List[OrderLeg] = build_rebalance_orders(target, pf, ctx.data.get_quote, analyst_reports)

    md.rebalance_orders = orders
    md.target_portfolio_weights = {t: round(w, 4) for t, w in target.items() if w > 0}
    md.conviction_scores = {r.ticker: round(conviction_of(r.rating), 3) for r in analyst_reports}
    md.industry_sentiment = {k: round(v, 3) for k, v in sentiment.items()}
    # 量化信号作为正交参考（不改变权重，仅注入研判与日志）
    if quant_signals:
        qsum = " ".join(f"{t}:{s.quant_view}" for t, s in quant_signals.items())
        md.rationale = (md.rationale + f"\n[量化正交参考] {qsum}").strip()
    md.worth_trading = len(orders) > 0

    n_buy = sum(1 for o in orders if o.side == Side.BUY)
    n_sell = sum(1 for o in orders if o.side == Side.SELL)
    return {"manager_decision": md, "stage": "manager", "kg_stats": {"kg_context_used": len(kg_ctx_parts)},
            "log": [f"[manager] 风险预算优化：目标权重={md.target_portfolio_weights}；"
                    f"调仓 {len(orders)} 条（买 {n_buy}/卖 {n_sell}，已注入 KG 上下文）"]}
