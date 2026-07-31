"""DCF 适配器（冲刺项①）：真实调用 skill 的 dcf_implied.py 反算/正算。"""
from __future__ import annotations

from research.dcf import DcfAssumptions, run_dcf


def test_real_dcf_runs_and_is_self_consistent():
    # 茅台近似：市值18500亿，前3年业绩约1776/1918/2072亿，现价1480
    a = DcfAssumptions(ticker="600519", market_cap=18500.0, e1=1918.0, e2=2072.0,
                       e3=2238.0, current_price=1480.0, terminal_multiple=1.0, central_r=10.0)
    res = run_dcf(a)
    assert res.dcf_used is True
    assert res.central_fair_price > 0
    # 中枢 r=10 合理价应接近现价（terminal_multiple=1.0，无额外增长假设）
    ratio = res.central_fair_price / a.current_price
    assert 1.0 < ratio < 1.5
    # 市场隐含终局 L 应 <= 分析师终局 L（terminal_multiple=1.0 时 L_analyst=e3）
    assert res.L_analyst == 2238.0
    assert res.L_market_implied["10"] <= res.L_analyst + 1e-6
    # 敏感性覆盖三档折现率
    assert set(res.fair_price.keys()) == {"8", "10", "12"}


def test_dcf_degrades_gracefully_without_inputs():
    # 缺少业绩 -> 降级
    a = DcfAssumptions(ticker="X", market_cap=100.0, e1=0.0, e2=0.0, e3=0.0, current_price=10.0)
    res = run_dcf(a)
    assert res.dcf_used is False
    assert res.central_fair_price == 0.0


def test_dcf_fair_price_dimension_consistent():
    # fair_price = current * (fair_cap / market_cap)
    a = DcfAssumptions(ticker="600519", market_cap=18500.0, e1=1918.0, e2=2072.0,
                       e3=2238.0, current_price=1480.0, terminal_multiple=1.2, central_r=10.0)
    res = run_dcf(a)
    expected = a.current_price * (res.fair_cap["10"] / a.market_cap)
    assert abs(res.central_fair_price - round(expected, 2)) < 1e-6
