"""
冲刺项②：组合层目标权重（风险预算优化）单测 + 经理 diff 集成测试。

覆盖：
- 评级->置信度映射、行业景气聚合
- 风险预算封顶（单票 ≤ single_position_max，合计 ≤ 宏观仓位上限）
- 建仓闸门（HOLD/REDUCE/SELL 不从 0 发起新仓）
- 与当前持仓 diff 产出买入补足 / 卖出降仓
- 经理端到端：目标权重不再固定 5%，且卖出降仓生效
"""
from __future__ import annotations

from pathlib import Path

from state.schemas import (
    AnalystReport, Rating, MacroReport, Trend, Portfolio, Holding, BoardType, Side, OrderLeg,
)
from portfolio.optimizer import (
    conviction_of, industry_sentiment, risk_budget_allocation,
    build_rebalance_orders, clamp_targets,
)
from core.llm import MockLLM
from data.provider import MockAshareProvider
from harness.prompthub import PromptHub
from risk.engine import RiskEngine
from core.context import AppContext
from core.config import Settings
from agents.manager import run as manager_run

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = Settings.load(str(ROOT / "config" / "settings.yaml"))


def _report(ticker, name, industry, rating):
    return AnalystReport(ticker=ticker, name=name, industry=industry,
                         rating=rating, target_price=100.0)


# ---------------- 单元：映射与景气 ----------------
def test_conviction_map():
    assert conviction_of(Rating.BUY) == 1.0
    assert conviction_of(Rating.ADD) == 0.7
    assert conviction_of(Rating.HOLD) == 0.4
    assert conviction_of(Rating.REDUCE) == 0.15
    assert conviction_of(Rating.SELL) == 0.0


def test_industry_sentiment_mean():
    reps = [
        _report("600519", "茅台", "白酒", Rating.BUY),
        _report("000858", "五粮液", "白酒", Rating.ADD),
        _report("300750", "宁德", "新能源", Rating.HOLD),
    ]
    sent = industry_sentiment(reps)
    # 白酒 = mean(1.0, 0.7) = 0.85；新能源 = 0.4
    assert abs(sent["白酒"] - 0.85) < 1e-9
    assert abs(sent["新能源"] - 0.4) < 1e-9


# ---------------- 单元：风险预算封顶 ----------------
def test_allocation_caps_single_position_and_total():
    reps = [
        _report("600519", "茅台", "白酒", Rating.BUY),
        _report("000001", "平安", "银行", Rating.BUY),
        _report("300750", "宁德", "新能源", Rating.BUY),
    ]
    macro_cap = 0.70
    spm = 0.08
    target = risk_budget_allocation(reps, macro_cap, spm)
    # 单票封顶
    for w in target.values():
        assert w <= spm + 1e-9
    # 合计不超过宏观上限
    assert sum(target.values()) <= macro_cap + 1e-9
    # 风险预算驱动的权重 != 旧固定 5%
    assert all(abs(w - 0.05) > 1e-6 for w in target.values())


def test_allocation_no_initiate_for_hold_sell_when_empty():
    reps = [
        _report("600519", "茅台", "白酒", Rating.HOLD),
        _report("300750", "宁德", "新能源", Rating.SELL),
        _report("000001", "平安", "银行", Rating.BUY),
    ]
    target = risk_budget_allocation(reps, 0.70, 0.08)
    assert target.get("600519", 0.0) == 0.0   # HOLD 空仓不发起
    assert target.get("300750", 0.0) == 0.0   # SELL 目标 0
    assert target.get("000001", 0.0) > 0.0     # BUY 发起建仓


def test_clamp_targets_normalizes_llm_output():
    # LLM 给出远超硬约束的权重，应被封顶
    raw = {"600519": 0.5, "000001": 0.5, "300750": 0.5}
    out = clamp_targets(raw, macro_cap=0.70, single_position_max=0.08)
    for w in out.values():
        assert w <= 0.08 + 1e-9
    assert sum(out.values()) <= 0.70 + 1e-9


# ---------------- 单元：diff 买入/卖出 ----------------
def _pf(cash, holdings):
    return Portfolio(cash=cash, holdings=holdings)


def test_diff_buys_underweight_and_sells_overweight():
    reps = [
        _report("600519", "茅台", "白酒", Rating.BUY),
        _report("000001", "平安", "银行", Rating.SELL),
    ]
    # 当前：茅台 100 股@1480（约 1.45%），平安银行 200 股@11.5（约 0.023%）
    pf = _pf(10_000_000, [
        Holding(ticker="600519", name="茅台", industry="白酒", quantity=100,
                cost_price=1480.0, last_price=1480.0),
        Holding(ticker="000001", name="平安", industry="银行", quantity=200,
                cost_price=11.5, last_price=11.5),
    ])
    target = risk_budget_allocation(reps, macro_cap=0.70, single_position_max=0.08,
                                    current_weights={"600519": pf.weight_of("600519"),
                                                     "000001": pf.weight_of("000001")})
    orders = build_rebalance_orders(target, pf, MockAshareProvider().get_quote, reps)
    sides = {o.ticker: o.side for o in orders}
    # 茅台 BUY 补足到目标；平安 SELL 清仓
    assert sides.get("600519") == Side.BUY
    assert sides.get("000001") == Side.SELL
    sell = next(o for o in orders if o.ticker == "000001")
    assert sell.quantity == 200  # 清仓


# ---------------- 集成：经理端到端 ----------------
def _ctx():
    return AppContext(
        settings=SETTINGS,
        llm=MockLLM(),
        data=MockAshareProvider(),
        risk_engine=RiskEngine(),
        strategy_name=SETTINGS.default_strategy,
        prompthub=PromptHub(),
    )


def test_manager_risk_budget_end_to_end():
    ctx = _ctx()
    state = {
        "macro_report": MacroReport(trend=Trend.SIDEWAYS, risk_appetite="中性",
                                    suggested_max_position=0.70, summary="模拟"),
        "portfolio": _pf(10_000_000, []),
        "analyst_reports": [
            _report("600519", "茅台", "白酒", Rating.ADD),
            _report("000001", "平安", "银行", Rating.ADD),
            _report("300750", "宁德", "新能源", Rating.ADD),
        ],
    }
    out = manager_run(state, ctx)
    md = out["manager_decision"]
    assert md.worth_trading
    assert md.target_portfolio_weights  # 非空
    # 目标权重由风险预算驱动，非固定 5%
    assert all(abs(w - 0.05) > 1e-6 for w in md.target_portfolio_weights.values())
    # 每个目标权重均在单票上限内
    for w in md.target_portfolio_weights.values():
        assert w <= SETTINGS.risk_limits.single_position_max + 1e-9
    # 全部为买入（空仓 + ADD）
    assert all(o.side == Side.BUY for o in md.rebalance_orders)
    # 审计字段已填
    assert md.conviction_scores and md.industry_sentiment


def test_manager_sell_on_downgrade_with_holding():
    ctx = _ctx()
    # 已有茅台持仓，本轮评级 SELL -> 应卖出降仓
    state = {
        "macro_report": MacroReport(trend=Trend.SIDEWAYS, risk_appetite="中性",
                                    suggested_max_position=0.70, summary="模拟"),
        "portfolio": _pf(10_000_000, [
            Holding(ticker="600519", name="茅台", industry="白酒", quantity=100,
                    cost_price=1480.0, last_price=1480.0),
        ]),
        "analyst_reports": [
            _report("600519", "茅台", "白酒", Rating.SELL),
            _report("000001", "平安", "银行", Rating.BUY),
        ],
    }
    out = manager_run(state, ctx)
    md = out["manager_decision"]
    sides = {o.ticker: o.side for o in md.rebalance_orders}
    assert sides.get("600519") == Side.SELL
    assert sides.get("000001") == Side.BUY
    sell = next(o for o in md.rebalance_orders if o.ticker == "600519")
    assert sell.quantity == 100
