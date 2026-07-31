"""配置加载：yaml -> Pydantic Settings。环境隔离（dev/test/prod），参数集中管理。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List

import yaml
from pydantic import BaseModel, Field

from state.schemas import RiskLimits, Market, BoardType


class LLMConfig(BaseModel):
    provider: str = "mock"          # mock | openai
    model: str = "gpt-4o-mini"
    temperature: float = 0.0
    api_key_env: str = "OPENAI_API_KEY"
    base_url: str = ""


class DataConfig(BaseModel):
    provider: str = "mock"          # mock | tdx


class RiskReviewConfig(BaseModel):
    """风控阶段人工审核确认（HITL）参数。"""
    on_reject: str = "rejudge"      # rejudge=否决后回落投资经理重研判；terminate=否决即终结进审核者


class ICGateMateriality(BaseModel):
    """投委会/终审委员触发门槛（达到任一即视为重大，需人工终审）。"""
    single_position_weight: float = 0.05   # 单票目标权重超过此值视为重大
    total_deviation: float = 0.10          # 调仓总偏离（Σ|目标-当前|）超过此值视为重大
    exception_is_material: bool = True     # 风控结论为 EXCEPTION（例外）时一律视为重大


class ICGateConfig(BaseModel):
    """投委会/终审委员（IC Gate）HITL 参数。"""
    on_reject: str = "rejudge"      # rejudge=否决后回落投资经理重研判；terminate=否决即终结进审核者
    materiality: ICGateMateriality = ICGateMateriality()


class ComplianceConfig(BaseModel):
    """合规配置（风险合规合并岗位使用）。"""
    restricted_list: List[str] = Field(default_factory=list, description="禁投标的（含 ST/* 由 forbid_st 控制）")
    silent_period: List[str] = Field(default_factory=list, description="处于静默期/内幕信息隔离的标的")


class QuantConfig(BaseModel):
    """量化/因子研究员配置。"""
    enabled: bool = True
    momentum_window: int = 20
    neutral_band: float = 0.02     # 动量落在 ±band 内视为中性


class EventMonitorConfig(BaseModel):
    """舆情/事件监控员配置。"""
    enabled: bool = True
    negative_keywords: List[str] = Field(default_factory=lambda: ["立案", "调查", "处罚", "退市", "商誉减值", "暴雷"])
    material_keywords: List[str] = Field(default_factory=lambda: ["超预期", "重大", "并购", "重组", "定增", "中标", "举牌"])


class AttributionConfig(BaseModel):
    """绩效归因师配置。"""
    enabled: bool = True


class ExecutionDeskConfig(BaseModel):
    """算法交易/执行优化（合并进交易员）配置。"""
    default_strategy: str = "TWAP"
    large_notional: float = 5_000_000.0   # 名义额超过此值倾向 VWAP
    low_adv_ratio: float = 0.3            # 名义/ADV 超过此比例倾向 VWAP 以降低冲击


class InitialHolding(BaseModel):
    ticker: str
    name: str
    industry: str
    quantity: int
    cost_price: float
    board: BoardType = BoardType.MAIN


class Settings(BaseModel):
    market: Market = Market.A_SHARE
    llm: LLMConfig = LLMConfig()
    data: DataConfig = DataConfig()
    max_loop: int = 3
    default_strategy: str = "TWAP"
    hitl_enabled: bool = True
    risk_review: RiskReviewConfig = RiskReviewConfig()  # 风控阶段人工审核（HITL）参数
    # —— 角色扩编配置（冲刺项：合规/量化/事件/归因/投委会/执行台）——
    ic_gate: ICGateConfig = ICGateConfig()          # 投委会/终审委员（HITL）
    compliance: ComplianceConfig = ComplianceConfig()  # 风险合规合并岗位
    quant: QuantConfig = QuantConfig()              # 量化/因子研究员
    event_monitor: EventMonitorConfig = EventMonitorConfig()  # 舆情/事件监控员
    attribution: AttributionConfig = AttributionConfig()      # 绩效归因师
    execution_desk: ExecutionDeskConfig = ExecutionDeskConfig()  # 算法交易/执行优化（合并进交易员）
    initial_cash: float = 10_000_000.0
    initial_holdings: List[InitialHolding] = Field(default_factory=list)
    risk_limits: RiskLimits = RiskLimits()
    watchlist: List[Dict[str, str]] = Field(default_factory=list)
    # —— DCF 估值假设（可插拔：后期可替换为更复杂的终局利润模型）——
    dcf_terminal_multiple: float = 1.0   # 分析师终局利润 L = terminal_multiple × E3
    dcf_central_r: float = 10.0          # 中枢折现率(%)
    # —— KG 时效性：周度衰减半衰期（天）——
    kg_decay_half_life: float = 30.0

    @classmethod
    def load(cls, path: str) -> "Settings":
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        return cls.model_validate(raw)

    def resolve_api_key(self) -> str:
        return os.environ.get(self.llm.api_key_env, "")
