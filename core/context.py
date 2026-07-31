"""应用上下文：在节点间共享的「依赖容器」。节点通过它访问 LLM/数据/风控/策略/KG/Harness，不直接 new 依赖。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from core.config import Settings
from core.llm import BaseLLM
from data.provider import BaseDataProvider
from risk.engine import RiskEngine
from kg.store import KnowledgeGraph
from kg.extractor import KGExtractor
from kg.retrieval import GraphRAGRetriever
from kg.updater import KGUpdater
from harness.prompthub import PromptHub
from harness.observability import Tracer, Metrics
from harness.cost_guard import CostGuard
from harness.injection_guard import InjectionGuard
from harness.datavalidator import DataValidator
from backtest.paper_trader import PaperTrader
from backtest.engine import BacktestEngine
from eval.evaluator import Evaluator
from harness.traced_llm import TracedLLM


@dataclass
class AppContext:
    settings: Settings
    llm: BaseLLM                       # 可能是 TracedLLM 包装
    data: BaseDataProvider
    risk_engine: RiskEngine
    strategy_name: str
    # —— P4 知识图谱 ——
    kg: KnowledgeGraph = field(default_factory=KnowledgeGraph)
    extractor: Optional[KGExtractor] = None
    retriever: Optional[GraphRAGRetriever] = None
    kg_updater: Optional[KGUpdater] = None
    # —— P5 Harness ——
    prompthub: Optional[PromptHub] = None
    tracer: Optional[Tracer] = None
    metrics: Metrics = field(default_factory=Metrics)
    cost_guard: Optional[CostGuard] = None
    injection: Optional[InjectionGuard] = None
    validator: Optional[DataValidator] = None
    # —— P6 完善 ——
    paper_trader: Optional[PaperTrader] = None
    backtest: Optional[BacktestEngine] = None
    evaluator: Optional[Evaluator] = None
