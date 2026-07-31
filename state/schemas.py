"""
全局状态与产出 Schema（Harness 第一道防线）。

设计要点：
- 所有「角色间的传递物」（报告、决策、风控结论、订单、成交回报）都用 Pydantic 严格定义。
- 图的容器状态用 TypedDict + reducer（列表字段累加），保证 LangGraph 多层流转安全。
- 仅面向 A 股（Market 固定为 A_SHARE），市场规则相关字段（板块、涨停、T+1）内建。
"""
from __future__ import annotations

from enum import Enum
from typing import TypedDict, Annotated, List, Optional, Dict, Any

from pydantic import BaseModel, Field


# 跨角色共享的基本面数据结构（DataValidator 与数据层共用）
class Fundamentals(BaseModel):
    ticker: str
    market_cap: float = Field(..., description="总市值（亿元）")
    net_profit_ttm: float = Field(..., description="归母净利润 TTM（亿元）")
    revenue_ttm: float = Field(..., description="营业收入 TTM（亿元）")
    dividend_yield: float = Field(0.0, description="股息率")
    # —— DCF 反算所需的三年一致预期净利润（亿元）；真实数据缺失时置 0 ——
    e1: float = Field(0.0, description="第1年一致预期净利润（亿元）")
    e2: float = Field(0.0, description="第2年一致预期净利润（亿元）")
    e3: float = Field(0.0, description="第3年一致预期净利润（亿元）")


# ----------------------------- 枚举 -----------------------------
class Market(str, Enum):
    A_SHARE = "A_SHARE"          # 当前只支持 A 股


class BoardType(str, Enum):
    MAIN = "MAIN"                # 主板 ±10%
    CHINEXT = "CHINEXT"          # 创业板 300xxx ±20%
    STAR = "STAR"                # 科创板 688xxx ±20%
    ST = "ST"                    # 风险警示 ±5%（且通常禁投）


class Trend(str, Enum):
    BULL = "BULL"
    BEAR = "BEAR"
    SIDEWAYS = "SIDEWAYS"


class Rating(str, Enum):
    BUY = "BUY"
    ADD = "ADD"
    HOLD = "HOLD"
    REDUCE = "REDUCE"
    SELL = "SELL"


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class StrategyType(str, Enum):
    MARKET = "MARKET"            # 市价/即时
    TWAP = "TWAP"                # 时间加权拆单
    VWAP = "VWAP"                # 量加权拆单


class RiskDecision(str, Enum):
    APPROVED = "APPROVED"        # 通过
    REJECTED = "REJECTED"        # 驳回（硬约束 violated，需投资经理优化）
    EXCEPTION = "EXCEPTION"      # 触发例外，需人工 HITL 审批


# ----------------------------- 风险限额（同时是配置对象） -----------------------------
class RiskLimits(BaseModel):
    single_position_max: float = Field(0.08, description="单票仓位上限（占组合净值）")
    industry_concentration_max: float = Field(0.25, description="单一行业集中度上限")
    total_position_max: float = Field(0.80, description="常态总仓位/净敞口上限")
    bear_market_position_max: float = Field(0.50, description="熊市（宏观判熊）后自动降到的上限")
    adv_liquidity_pct: float = Field(0.10, description="拟买入额不超过过去 N 日日均成交额 ADV 的比例")
    stop_loss_pct: float = Field(0.08, description="个股回撤≥此值强制减仓")
    portfolio_drawdown_pause: float = Field(0.15, description="组合回撤≥此值暂停新开仓")
    var_1d_95_max: float = Field(0.02, description="组合 1 日 95% VaR 上限")
    forbid_st: bool = Field(True, description="是否禁投 ST/*")
    allow_short: bool = Field(False, description="A 股禁止裸卖空，固定 False")

    def effective_total_position_max(self, trend: Trend) -> float:
        return self.bear_market_position_max if trend == Trend.BEAR else self.total_position_max


# ----------------------------- 标的 / Universe 入口 -----------------------------
class UniverseEntry(BaseModel):
    ticker: str
    name: str
    industry: str
    trigger: str = Field("watchlist", description="进入研究的原因：watchlist/event/scan")


# ----------------------------- 宏观研究员产出 -----------------------------
class MacroReport(BaseModel):
    trend: Trend
    risk_appetite: str = Field(..., description="风险偏好描述")
    suggested_max_position: float = Field(..., description="建议仓位上限 0-1")
    liquidity_view: str = Field("", description="流动性判断")
    summary: str = Field("", description="研判摘要")


# ----------------------------- 行业/个股研究员产出（复用穿透叙事 skill） -----------------------------
class AnalystReport(BaseModel):
    ticker: str
    name: str
    industry: str
    rating: Rating
    target_price: float = Field(..., description="目标价（元）")
    dcf_used: bool = Field(False, description="是否调用 dcf_implied.py 真实反算")
    dcf_implied_L: float = Field(0.0, description="DCF 反算：市场隐含终局利润 L（亿元）")
    dcf_fair_price: float = Field(0.0, description="DCF 正算：中枢折现率下每股合理价（元）")
    dcf_inputs: Dict[str, Any] = Field(default_factory=dict, description="DCF 输入与中间量")
    dcf_sensitivity: Dict[str, Any] = Field(default_factory=dict, description="DCF 敏感性（各 r 合理价/市值）")
    entry_point: float = Field(0.0, description="建议买点")
    exit_point: float = Field(0.0, description="建议卖点")
    narrative_summary: str = Field("", description="穿透叙事摘要")
    data_quality_ok: bool = Field(True, description="是否通过 DataValidator 红线")


# ----------------------------- 投资经理产出（组合层） -----------------------------
class OrderLeg(BaseModel):
    ticker: str
    side: Side
    quantity: int = Field(..., gt=0)
    expected_price: float = Field(..., gt=0)
    target_weight: float = Field(0.0, description="目标权重（用于组合层 diff）")


class ManagerDecision(BaseModel):
    worth_trading: bool = Field(..., description="是否值得下单")
    rationale: str = Field("", description="综合研判理由（宏观+行业+基本面+技术面）")
    rebalance_orders: List[OrderLeg] = Field(default_factory=list, description="调仓指令集（组合层 diff 产出）")
    stop_loss_pct: float = Field(..., description="个股止损线")
    take_profit_pct: float = Field(..., description="个股止盈线")
    target_portfolio_weights: Dict[str, float] = Field(default_factory=dict, description="目标组合权重（风险预算优化，已封顶）")
    conviction_scores: Dict[str, float] = Field(default_factory=dict, description="各标的评级映射的置信度（风险预算输入）")
    industry_sentiment: Dict[str, float] = Field(default_factory=dict, description="各行业景气度代理（评级分布聚合）")


# ----------------------------- 下单策略产出 -----------------------------
class ScheduledSlice(BaseModel):
    seq: int
    time_label: str
    quantity: int


class ExecutionPlan(BaseModel):
    strategy_type: StrategyType
    slices: List[ScheduledSlice] = Field(default_factory=list, description="拆单时间表")
    estimated_market_impact_pct: float = Field(0.0, description="预估市场冲击成本")
    notes: str = Field("", description="策略说明")


# ----------------------------- 风控产出 -----------------------------
class RiskMetric(BaseModel):
    name: str
    value: float
    limit: float
    passed: bool


class RiskCheckResult(BaseModel):
    decision: RiskDecision
    hard_violations: List[str] = Field(default_factory=list)
    soft_findings: List[RiskMetric] = Field(default_factory=list)
    feedback: str = Field("", description="给投资经理的优化反馈")
    requires_human: bool = Field(False, description="是否需人工审批例外")


# ----------------------------- 成交回报 -----------------------------
class TradeFill(BaseModel):
    ticker: str
    side: Side
    filled_qty: int
    avg_price: float
    commission: float = 0.0
    impact_cost: float = 0.0
    slippage: float = 0.0
    timestamp: str = ""


# ----------------------------- 持仓 / 组合 -----------------------------
class Holding(BaseModel):
    ticker: str
    name: str
    industry: str
    quantity: int
    cost_price: float
    last_price: float = 0.0
    board: BoardType = Field(BoardType.MAIN, description="所属板块（A 股涨跌停幅度依据）")

    @property
    def market_value(self) -> float:
        return self.quantity * (self.last_price or self.cost_price)


class Portfolio(BaseModel):
    cash: float = Field(..., gt=0)
    holdings: List[Holding] = Field(default_factory=list)

    @property
    def holdings_value(self) -> float:
        return sum(h.market_value for h in self.holdings)

    @property
    def total_value(self) -> float:
        return self.cash + self.holdings_value

    def industry_exposure(self) -> Dict[str, float]:
        tv = self.total_value or 1.0
        out: Dict[str, float] = {}
        for h in self.holdings:
            out[h.industry] = out.get(h.industry, 0.0) + h.market_value / tv
        return out

    def weight_of(self, ticker: str) -> float:
        tv = self.total_value or 1.0
        for h in self.holdings:
            if h.ticker == ticker:
                return h.market_value / tv
        return 0.0


# ----------------------------- 审计（审核者/合规用） -----------------------------
class AuditEntry(BaseModel):
    stage: str
    actor: str
    action: str
    detail: str = ""
    timestamp: str = ""


# ----------------------------- 合规产出（风控与合规合并为单一岗位） -----------------------------
class ComplianceResult(BaseModel):
    """合规检查汇总：与风控同节点产出，但单独留痕便于审计/展示。

    - restricted_hits / insider_hits：硬违规（禁投清单/ST、内幕/静默期），命中即触发 REJECTED。
    - disclosure_hits：举牌 5% 等披露义务（警示，不阻断）。
    - fairtrade_hits：公平交易（单账户模拟下默认通过）。
    """
    passed: bool = True
    restricted_hits: List[str] = Field(default_factory=list)
    insider_hits: List[str] = Field(default_factory=list)
    disclosure_hits: List[str] = Field(default_factory=list)
    fairtrade_hits: List[str] = Field(default_factory=list)
    detail: str = ""


# ----------------------------- 量化/因子研究员产出 -----------------------------
class QuantSignal(BaseModel):
    ticker: str
    momentum: float = Field(0.0, description="动量（近 window 涨跌，正=强势）")
    volatility: float = Field(0.0, description="波动率代理（0-1）")
    liquidity_score: float = Field(0.0, description="流动性评分（ADV/名义）")
    factor_score: float = Field(0.0, description="综合因子分（-1~1）")
    quant_view: str = Field("NEUTRAL", description="BULLISH / NEUTRAL / BEARISH")
    note: str = ""


# ----------------------------- 舆情/事件监控产出 -----------------------------
class MarketEvent(BaseModel):
    ticker: str
    kind: str = Field("news", description="news/announcement/dragon_tiger/rating")
    headline: str = ""
    material: bool = Field(False, description="是否重大事件（触发重研）")
    detail: str = ""


# ----------------------------- 绩效归因产出 -----------------------------
class AttributionReport(BaseModel):
    selection_pnl: float = Field(0.0, description="选股贡献（相对等权基准）")
    timing_pnl: float = Field(0.0, description="择时/执行贡献（含滑点）")
    slippage_cost: float = Field(0.0, description="总滑点成本")
    per_researcher: Dict[str, float] = Field(default_factory=dict, description="各研究员（标的）贡献")
    summary: str = ""


# ----------------------------- LangGraph 容器状态 -----------------------------
def _extend(a, b):
    return (a or []) + (b or [])


def _merge_dict(a, b):
    c = dict(a or {})
    c.update(b or {})
    return c


class ResearchState(TypedDict, total=False):
    run_id: str
    as_of_date: str
    market: Market
    universe: List[UniverseEntry]
    macro_report: Optional[MacroReport]
    analyst_reports: Annotated[List[AnalystReport], _extend]
    manager_decision: Optional[ManagerDecision]
    execution_plan: Optional[ExecutionPlan]
    risk_result: Optional[RiskCheckResult]
    fills: Annotated[List[TradeFill], _extend]
    portfolio: Optional[Portfolio]
    risk_limits: RiskLimits
    loop_count: int
    human_decision: bool = False    # HITL 审批结果（风控例外放行）
    pre_trade_approved: bool = False  # HITL 审批结果（下单前确认）
    risk_review_approved: bool = False  # HITL 审批结果（风控结论人工审核）
    # —— 新增角色产出（冲刺项：角色扩编）——
    compliance_result: Optional[ComplianceResult]   # 风险合规合并岗位：合规留痕
    quant_signals: Dict[str, QuantSignal] = Field(default_factory=dict, description="量化/因子研究员信号")
    market_events: List[MarketEvent] = Field(default_factory=list, description="舆情/事件监控员产出")
    attribution_report: Optional[AttributionReport]  # 绩效归因师产出
    ic_gate_approved: bool = False  # HITL 审批结果（投委会/终审委员）
    execution_memo: str = Field("", description="算法交易/执行优化合并进交易员后的执行备忘录")
    stage: str
    log: Annotated[List[str], _extend]
    audit_log: Annotated[List[AuditEntry], _extend]
    kg_stats: Annotated[Dict[str, Any], _merge_dict] = Field(default_factory=dict, description="知识图谱本轮统计")
    monitor: Dict[str, Any] = Field(default_factory=dict, description="监控/评估输出（回测、绩效）")
    error: Optional[str]
