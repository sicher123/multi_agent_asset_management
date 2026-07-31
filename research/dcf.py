"""
DCF 适配器：把 skill 的 dcf_implied.py（穿透叙事 DCF 隐含天花板反算）封装成
结构化、可程序化调用的估值组件。

核心语义（与 skill 工具一致）：
- implied_ceiling(cap, e1, e2, e3, r)：由「当前市值 + 前3年一致预期业绩」反算
  市场隐含的终局利润 L（亿元）。这是"穿透叙事"里市场已经 price-in 的终局预期。
- fair_value(L, e1, e2, e3, r)：由「分析师假设的终局利润 L」正算合理市值（亿元）。

每股合理价 = 现价 × (合理市值 / 当前市值)，避免引入股本字段、维度自洽。

设计要点：
- 纯函数、无副作用，离线可跑（不依赖 LLM / 网络）。
- DcfResult 全程可序列化，写入 AnalystReport 与 KG / 监控。
- 当基本面或价格缺失时优雅降级（dcf_used=False），不抛异常。
- terminal_multiple、central_r 由配置注入，便于后期替换估值假设（可插拔）。
"""
from __future__ import annotations

from typing import Dict, Any

from pydantic import BaseModel, Field

from tools.dcf_implied import implied_ceiling, fair_value, implied_growth_rate, R_GRID


class DcfAssumptions(BaseModel):
    """DCF 反算所需的输入。全部以「亿元 / 元」为单位（A 股惯例）。"""
    ticker: str
    market_cap: float = Field(..., description="当前总市值（亿元）")
    e1: float = Field(..., description="第1年一致预期净利润（亿元）")
    e2: float = Field(..., description="第2年一致预期净利润（亿元）")
    e3: float = Field(..., description="第3年一致预期净利润（亿元）")
    current_price: float = Field(..., description="当前股价（元）")
    terminal_multiple: float = Field(1.0, description="分析师终局利润假设 = terminal_multiple × E3")
    central_r: float = Field(10.0, description="中枢折现率(%)")


class DcfResult(BaseModel):
    ticker: str
    dcf_used: bool = True
    L_market_implied: Dict[str, float] = Field(default_factory=dict, description="各折现率下市场隐含终局利润 L（亿元）")
    L_analyst: float = Field(0.0, description="分析师假设终局利润 L（亿元）")
    fair_cap: Dict[str, float] = Field(default_factory=dict, description="各折现率下合理市值（亿元）")
    fair_price: Dict[str, float] = Field(default_factory=dict, description="各折现率下每股合理价（元）")
    central_fair_price: float = Field(0.0, description="中枢折现率下的每股合理价（元）")
    dcf_inputs: Dict[str, Any] = Field(default_factory=dict)
    sensitivity: Dict[str, Any] = Field(default_factory=dict)
    note: str = ""


def run_dcf(a: DcfAssumptions) -> DcfResult:
    """运行 DCF 反算 + 正算，返回结构化结果。

    输入不完整（市值/业绩/价格缺失）时返回 dcf_used=False，由调用方降级处理。
    """
    if not (a.market_cap > 0 and a.e1 > 0 and a.e2 > 0 and a.e3 > 0 and a.current_price > 0):
        return DcfResult(ticker=a.ticker, dcf_used=False,
                         note="基本面/价格缺失，无法反算 DCF（降级）")

    L_mkt: Dict[str, float] = {}
    for r in R_GRID:
        L_mkt[str(r)] = round(implied_ceiling(a.market_cap, a.e1, a.e2, a.e3, r), 2)

    L_analyst = round(a.e3 * a.terminal_multiple, 2)

    fair_cap: Dict[str, float] = {}
    fair_price: Dict[str, float] = {}
    for r in R_GRID:
        fv = fair_value(L_analyst, a.e1, a.e2, a.e3, r)
        fair_cap[str(r)] = round(fv, 2)
        # 每股合理价 = 现价 × (合理市值 / 当前市值)，维度自洽
        fair_price[str(r)] = round(a.current_price * (fv / a.market_cap), 2)

    central_key = str(int(a.central_r))
    central_fair = fair_price.get(central_key, fair_price["10"])
    central_L_mkt = L_mkt.get(central_key, 0.0)

    return DcfResult(
        ticker=a.ticker,
        dcf_used=True,
        L_market_implied=L_mkt,
        L_analyst=L_analyst,
        fair_cap=fair_cap,
        fair_price=fair_price,
        central_fair_price=central_fair,
        dcf_inputs={
            "market_cap_yi": a.market_cap,
            "e1": a.e1, "e2": a.e2, "e3": a.e3,
            "current_price": a.current_price,
            "terminal_multiple": a.terminal_multiple,
            "central_r": a.central_r,
            "L_market_implied_central": central_L_mkt,
            "implied_g_pct": round(implied_growth_rate(central_L_mkt, a.e3), 2),
        },
        sensitivity={
            "r_grid": R_GRID,
            "fair_price_by_r": fair_price,
            "fair_cap_by_r": fair_cap,
            "L_market_implied_by_r": L_mkt,
        },
        note=(f"市场隐含终局L≈{central_L_mkt}亿（中枢r={central_key}%）；"
              f"分析师终局L={L_analyst}亿；"
              f"中枢r下DCF合理价≈{central_fair}元"),
    )
