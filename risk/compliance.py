"""
合规检查（与风控合并为「风险合规」单一岗位）。

- 合规检查可插拔：register(check) 即可加入，风险节点不感知具体检查。
- 检查分两类：
    * 硬违规（restricted / insider）：命中即触发风控 REJECTED（与硬约束同权）。
    * 披露/公平交易（disclosure / fairtrade）：警示留痕，不阻断流程。
- 产出 ComplianceResult 单独留痕（审计/展示），同时硬违规回灌风控结论。

仅 A 股：ST 禁投、举牌 5% 披露、静默期/内幕隔离、公平交易。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional

from pydantic import BaseModel

from state.schemas import (
    ManagerDecision, Portfolio, RiskLimits, BoardType, AnalystReport, ComplianceResult,
)
from data.provider import BaseDataProvider
from core.config import ComplianceConfig


@dataclass
class ComplianceContext:
    decision: Optional[ManagerDecision]
    portfolio: Portfolio
    limits: RiskLimits
    compliance_cfg: ComplianceConfig
    data: BaseDataProvider
    analyst_reports: List[AnalystReport] = field(default_factory=list)
    as_of: str = ""


class ComplianceFinding(BaseModel):
    rule: str
    severity: str                       # "HARD" | "SOFT"
    violated: bool
    message: str


class ComplianceCheck(ABC):
    name: str
    severity: str                       # 子类声明 HARD / SOFT

    @abstractmethod
    def check(self, ctx: ComplianceContext) -> ComplianceFinding:
        ...


class RestrictedListCheck(ComplianceCheck):
    """禁投清单 + ST 禁投（硬违规）。"""
    name = "禁投清单/ST"
    severity = "HARD"

    def check(self, ctx: ComplianceContext) -> ComplianceFinding:
        hits: List[str] = []
        restricted = set(ctx.compliance_cfg.restricted_list or [])
        for leg in (ctx.decision.rebalance_orders if ctx.decision else []):
            if leg.ticker in restricted:
                hits.append(f"{leg.ticker} 在禁投清单内")
            # ST 禁投：组合已有持仓或计划买入的标的若属 ST 且 forbid_st=True
        if ctx.limits.forbid_st:
            for h in ctx.portfolio.holdings:
                if h.board == BoardType.ST:
                    hits.append(f"{h.ticker} 为 ST/* 标的（forbid_st=True 禁投）")
        return ComplianceFinding(rule=self.name, severity=self.severity,
                                 violated=bool(hits), message="；".join(hits) or "无禁投标的")


class InsiderSilentPeriodCheck(ComplianceCheck):
    """静默期 / 内幕信息隔离（硬违规）。"""
    name = "静默期/内幕隔离"
    severity = "HARD"

    def check(self, ctx: ComplianceContext) -> ComplianceFinding:
        silent = set(ctx.compliance_cfg.silent_period or [])
        hits = [f"{t} 处于静默期/内幕信息隔离" for t in silent
                if ctx.decision and any(l.ticker == t for l in ctx.decision.rebalance_orders)]
        return ComplianceFinding(rule=self.name, severity=self.severity,
                                 violated=bool(hits), message="；".join(hits) or "无静默期标的")


class Disclosure5pctCheck(ComplianceCheck):
    """举牌线 5% 披露义务（警示，不阻断）。"""
    name = "举牌5%披露"
    severity = "SOFT"

    def check(self, ctx: ComplianceContext) -> ComplianceFinding:
        pf = ctx.portfolio
        tv = pf.total_value or 1.0
        hits: List[str] = []
        legs = ctx.decision.rebalance_orders if ctx.decision else []
        for leg in legs:
            if leg.side.value != "BUY":
                continue
            cur = sum(h.market_value for h in pf.holdings if h.ticker == leg.ticker)
            add = leg.quantity * leg.expected_price
            new_w = (cur + add) / tv if tv else 0
            if new_w > 0.05 + 1e-9:
                hits.append(f"{leg.ticker} 计划后权重 {new_w:.1%} 超 5%，须履行举牌披露义务")
        return ComplianceFinding(rule=self.name, severity=self.severity,
                                 violated=bool(hits), message="；".join(hits) or "无举牌披露义务")


class FairTradeCheck(ComplianceCheck):
    """公平交易（单账户模拟下默认通过）。"""
    name = "公平交易"
    severity = "SOFT"

    def check(self, ctx: ComplianceContext) -> ComplianceFinding:
        return ComplianceFinding(rule=self.name, severity=self.severity,
                                 violated=False, message="单账户模拟，公平交易通过")


class ComplianceEngine:
    def __init__(self):
        self.hard: List[ComplianceCheck] = []
        self.soft: List[ComplianceCheck] = []
        self._register_defaults()

    def register(self, c: ComplianceCheck) -> None:
        (self.hard if c.severity == "HARD" else self.soft).append(c)

    def _register_defaults(self):
        for c in (RestrictedListCheck(), InsiderSilentPeriodCheck(),
                  Disclosure5pctCheck(), FairTradeCheck()):
            self.register(c)

    def check(self, ctx: ComplianceContext) -> ComplianceResult:
        hard = [c.check(ctx) for c in self.hard]
        soft = [c.check(ctx) for c in self.soft]
        restricted_hits = [h.message for h in hard if h.violated and h.severity == "HARD"]
        # 硬违规统一进 restricted_hits（禁投/静默期均属硬性合规阻断）
        insider_hits = [h.message for h in hard if h.violated and h.rule == "静默期/内幕隔离"]
        disclosure_hits = [s.message for s in soft if s.violated]
        fairtrade_hits = [s.message for s in soft if s.violated and s.rule == "公平交易"]
        passed = not any(h.violated for h in hard)
        detail = ("合规通过" if passed else "合规硬违规：" + "；".join(restricted_hits))
        return ComplianceResult(
            passed=passed,
            restricted_hits=restricted_hits,
            insider_hits=insider_hits,
            disclosure_hits=disclosure_hits,
            fairtrade_hits=fairtrade_hits,
            detail=detail,
        )
