"""
交易员（含算法交易/执行优化，合并岗位）：把通过的下单策略转成可执行计划并模拟撮合。

- 算法交易/执行优化（Execution Desk）与本岗位合并：交易员既「选择执行算法」又「落地执行」。
- 执行策略选择（可插拔，策略注册表）：
    * 名义额大 或 名义/ADV 高（冲击风险大）-> VWAP（量加权，平摊冲击）；
    * 否则用默认策略（TWAP）。
- 撮合由 PaperTrader 完成（按拆单段逐笔、冲击成本∝名义/ADV），成交回报更真实。
重点仍是降低市场冲击；成交回报含实际成交价/量/冲击成本/滑点。
"""
from __future__ import annotations

from typing import Dict, Any, List

from core.context import AppContext
from strategies.registry import get_strategy
from strategies.base import StrategyContext
from state.schemas import ExecutionPlan, TradeFill, RiskDecision


def _select_strategy(notional: float, adv: float, ctx: AppContext) -> str:
    """执行台策略选择：大单/高冲击风险用 VWAP，否则用默认（TWAP）。"""
    ed = ctx.settings.execution_desk
    adv_ratio = (notional / adv) if adv > 0 else 0.0
    if notional >= ed.large_notional or adv_ratio >= ed.low_adv_ratio:
        return "VWAP"
    return ed.default_strategy


def run(state: Dict[str, Any], ctx: AppContext) -> Dict[str, Any]:
    risk = state.get("risk_result")
    if not risk or risk.decision != RiskDecision.APPROVED:
        return {"stage": "trader", "fills": [], "log": ["[trader] 风控未通过，跳过执行"]}

    md = state["manager_decision"]
    legs = md.rebalance_orders
    adv = ctx.data.get_adv(legs[0].ticker) if legs else 0.0
    notional = sum(l.quantity * l.expected_price for l in legs)
    sctx = StrategyContext(slices_count=10, adv=adv, notional=notional)

    # —— 算法交易/执行优化（合并岗位）：选择执行策略 ——
    chosen = _select_strategy(notional, adv, ctx)
    strategy = get_strategy(chosen)
    plan: ExecutionPlan = strategy.build(legs, sctx)

    adv_ratio = (notional / adv) if adv > 0 else 0.0
    memo = (f"执行台选择 {strategy.label}：名义 {notional:,.0f} 元，ADV {adv:,.0f} 元，"
            f"名义/ADV={adv_ratio:.1%}（{'高冲击风险→VWAP' if chosen=='VWAP' else '常规→TWAP'}）")

    system = ctx.prompthub.get("trader") if ctx.prompthub else "你是交易员，生成低冲击执行计划"
    _ = ctx.llm.complete(f"执行策略 {strategy.label}，名义 {notional:.0f} 元。", system=system)

    # 模拟撮合（P6：PaperTrader）
    if ctx.paper_trader:
        fills: List[TradeFill] = ctx.paper_trader.simulate(legs, plan, as_of=state.get("as_of_date", ""))
    else:
        fills = []

    return {"execution_plan": plan, "fills": fills, "execution_memo": memo, "stage": "trader",
            "log": [f"[trader] 策略={strategy.label}，拆 {len(plan.slices)} 段，成交 {len(fills)} 笔；{memo}"]}
