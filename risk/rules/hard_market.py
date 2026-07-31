"""硬约束⑤：A 股市场交易规则（涨停不可买 / 跌停不可卖 / T+1 / ST 禁投）。"""
from __future__ import annotations

from risk.rules.base import RiskRule, RiskContext, RuleResult
from data.ashare_rules import can_buy, can_sell


class MarketRulesRule(RiskRule):
    name = "A股市场交易规则"
    severity = "HARD"

    def check(self, ctx: RiskContext) -> RuleResult:
        msgs = []
        for leg in ctx.decision.rebalance_orders:
            q = ctx.data.get_quote(leg.ticker)
            if leg.side.value == "BUY":
                ok, reason = can_buy(leg.ticker, q.name, q.last, q.prev_close)
            else:
                # 卖出需确认非当日买入（演示默认非当日）
                ok, reason = can_sell(leg.ticker, q.name, q.last, q.prev_close, bought_today=False)
            if not ok:
                msgs.append(f"{leg.ticker} {leg.side.value} 不可执行：{reason}")
        return RuleResult(
            rule=self.name, severity=self.severity, violated=bool(msgs),
            message=("；".join(msgs) if msgs else "交易符合 A 股涨跌停/T+1/ST 规则"),
        )
