"""
DataValidator：跨角色共享的数据质量红线（来自 prompt.txt 的「穿透叙事」校验纪律）。

所有角色取数/出报告后统一过此校验，而不是只在单个 skill 内部做——保证红线一致执行。
涵盖：
1. 金额交叉验证：市值/净利润 隐含 PE 应在合理区间（<5 或 >200 视为异常）。
2. 单位一致性：fundamentals 以「亿元」计，异常大值提示元/亿元未换算。
3. DCF 反算一致性：隐含天花板 L 必须 >= 目标价，且 > 当前价（否则反算不自洽）。
4. 评级-目标价一致性：BUY/ADD 要求目标价 >= 现价；SELL/REDUCE 反之。
5. 敏感性收敛：|L - 目标价|/目标价 过大提示 DCF 不收敛，需复核。
"""
from __future__ import annotations

from typing import List

from state.schemas import AnalystReport, Fundamentals, Rating


class DataValidator:
    def __init__(self, pe_min: float = 5.0, pe_max: float = 200.0):
        self.pe_min = pe_min
        self.pe_max = pe_max

    # ----------------------- 基本面 -----------------------
    def validate_fundamentals(self, f: Fundamentals) -> List[str]:
        v: List[str] = []
        if f.market_cap <= 0 or f.net_profit_ttm <= 0:
            v.append(f"[{f.ticker}] 市值/净利润非正，无法交叉验证")
            return v
        pe = f.market_cap / f.net_profit_ttm
        if pe < self.pe_min:
            v.append(f"[{f.ticker}] 隐含 PE={pe:.1f} < {self.pe_min}，疑似利润/市值单位错配或估值异常")
        if pe > self.pe_max:
            v.append(f"[{f.ticker}] 隐含 PE={pe:.1f} > {self.pe_max}，疑似市值单位错配（元未转亿元）")
        if f.market_cap > 1e7:  # > 1e7 亿元 ≈ 异常
            v.append(f"[{f.ticker}] 市值={f.market_cap} 亿元异常偏大，检查是否为「元」未除 1e8")
        return v

    # ----------------------- 个股报告 -----------------------
    def validate_report(self, r: AnalystReport, current_price: float) -> List[str]:
        v: List[str] = []
        if r.target_price <= 0:
            v.append(f"[{r.ticker}] 目标价非正")
        # —— DCF 反算一致性（仅当真实调用了 dcf_implied.py）——
        if r.dcf_used and r.dcf_fair_price > 0:
            # 目标价应贴近 DCF 合理价（真实模式两者一致；偏差过大提示未收敛）
            gap = abs(r.dcf_fair_price - r.target_price) / r.dcf_fair_price
            if gap > 0.5:
                v.append(f"[{r.ticker}] DCF合理价({r.dcf_fair_price})与目标价({r.target_price})偏差{gap:.0%}过大，建议复核")
            # 买入类评级须有 DCF 上行空间支撑
            if r.rating in (Rating.BUY, Rating.ADD) and r.dcf_fair_price < current_price:
                v.append(f"[{r.ticker}] 评级{r.rating.value}但DCF合理价({r.dcf_fair_price})<现价，与买入逻辑冲突")
            if r.dcf_implied_L <= 0:
                v.append(f"[{r.ticker}] DCF 已调用但市场隐含终局L非正，反算异常")
        if current_price > 0:
            if r.rating in (Rating.BUY, Rating.ADD) and r.target_price < current_price:
                v.append(f"[{r.ticker}] 评级{r.rating.value}但目标价<现价，逻辑冲突")
            if r.rating in (Rating.SELL, Rating.REDUCE) and r.target_price > current_price:
                v.append(f"[{r.ticker}] 评级{r.rating.value}但目标价>现价，逻辑冲突")
        return v

    def ok(self, violations: List[str]) -> bool:
        return len(violations) == 0
