"""
风控规则基类 + 上下文。

- RiskRule：单条规则（硬/软）。新增规则只需实现 check() 并 register 到 RiskEngine。
- RiskContext：规则执行所需的全部上下文（决策/组合/限额/数据/分析师报告）。
硬约束 violated => 一票否决；软约束 violated => 触发例外（需人工 HITL）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional

from pydantic import BaseModel

from state.schemas import ManagerDecision, Portfolio, RiskLimits, RiskMetric, Trend, AnalystReport
from data.provider import BaseDataProvider


@dataclass
class RiskContext:
    decision: ManagerDecision
    portfolio: Portfolio
    limits: RiskLimits
    trend: Trend
    data: BaseDataProvider
    analyst_reports: List[AnalystReport] = field(default_factory=list)
    as_of: str = ""


class RuleResult(BaseModel):
    rule: str
    severity: str                       # "HARD" | "SOFT"
    violated: bool
    message: str
    metric: Optional[RiskMetric] = None


class RiskRule(ABC):
    name: str
    severity: str                       # 子类声明 HARD / SOFT

    @abstractmethod
    def check(self, ctx: RiskContext) -> RuleResult:
        ...
