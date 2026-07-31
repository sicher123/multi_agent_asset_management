from data.provider import BaseDataProvider, Quote, Fundamentals, MacroReport
from data.ashare_rules import board_of
from state.schemas import (Portfolio, Holding, ManagerDecision, OrderLeg, Side,
                           RiskLimits, Trend, AnalystReport, Rating)
from risk.engine import RiskEngine
from risk.rules.base import RiskContext


class FakeData(BaseDataProvider):
    def __init__(self, adv=1e9, price=100.0, limit_up=False):
        self.adv = adv
        self.price = price
        # 涨停：当前价=基准*1.1；否则基准=当前价
        self.prev = price / 1.1 if limit_up else price
        self.name = "测试"

    def get_quote(self, ticker):
        return Quote(ticker=ticker, name=self.name, last=self.price,
                     prev_close=self.prev, industry="测试")

    def get_fundamentals(self, ticker):
        return Fundamentals(ticker=ticker, market_cap=1, net_profit_ttm=1, revenue_ttm=1, dividend_yield=0.02)

    def get_macro(self):
        return MacroReport(trend=Trend.SIDEWAYS, risk_appetite="", suggested_max_position=0.7)

    def get_adv(self, ticker, days=20):
        return self.adv

    def get_news(self, ticker):
        return []


def _pf(cash=10_000_000.0):
    return Portfolio(cash=cash, holdings=[])


def _ctx(decision, pf, limits, adv=1e9, price=100.0, trend=Trend.SIDEWAYS):
    return RiskContext(decision=decision, portfolio=pf, limits=limits, trend=trend,
                       data=FakeData(adv=adv, price=price), analyst_reports=[])


def test_approved_when_within_limits():
    limits = RiskLimits()
    pf = _pf()
    leg = OrderLeg(ticker="600519", side=Side.BUY, quantity=300, expected_price=1480.0)
    dec = ManagerDecision(worth_trading=True, stop_loss_pct=0.08, take_profit_pct=0.20,
                          rebalance_orders=[leg])
    res = RiskEngine().check(_ctx(dec, pf, limits, adv=1e9, price=1480.0))
    assert res.decision.value == "APPROVED", res.feedback


def test_rejected_single_position_over_limit():
    limits = RiskLimits()
    pf = _pf()
    leg = OrderLeg(ticker="600519", side=Side.BUY, quantity=1000, expected_price=1480.0)  # 1.48M > 800k
    dec = ManagerDecision(worth_trading=True, stop_loss_pct=0.08, take_profit_pct=0.20,
                          rebalance_orders=[leg])
    res = RiskEngine().check(_ctx(dec, pf, limits, adv=1e9, price=1480.0))
    assert res.decision.value == "REJECTED"
    assert any("单票" in v for v in res.hard_violations)


def test_rejected_liquidity_over_adv():
    limits = RiskLimits()
    pf = _pf()
    leg = OrderLeg(ticker="600519", side=Side.BUY, quantity=300, expected_price=1480.0)  # 444k
    dec = ManagerDecision(worth_trading=True, stop_loss_pct=0.08, take_profit_pct=0.20,
                          rebalance_orders=[leg])
    res = RiskEngine().check(_ctx(dec, pf, limits, adv=1_000_000, price=1480.0))  # 444k/1M=44%>10%
    assert res.decision.value == "REJECTED"
    assert any("ADV" in v for v in res.hard_violations)


def test_rejected_market_rule_limit_up():
    limits = RiskLimits()
    pf = _pf()
    leg = OrderLeg(ticker="600519", side=Side.BUY, quantity=100, expected_price=100.0)
    dec = ManagerDecision(worth_trading=True, stop_loss_pct=0.08, take_profit_pct=0.20,
                          rebalance_orders=[leg])
    res = RiskEngine().check(_ctx(dec, pf, limits, adv=1e9, price=100.0, trend=Trend.SIDEWAYS))
    # 默认 price=100 prev=100 非涨停；这里改为涨停场景
    ctx = _ctx(dec, pf, limits, adv=1e9, price=100.0)
    ctx.data = FakeData(adv=1e9, price=110.0, limit_up=True)
    res2 = RiskEngine().check(ctx)
    assert res2.decision.value == "REJECTED"
    assert any("涨停" in v for v in res2.hard_violations)
