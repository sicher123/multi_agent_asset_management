"""共享 pytest fixture：复用 main._build_context 构造真实 AppContext（多角色测试统一入口）。"""
from __future__ import annotations

import os
from datetime import date

import pytest

from core.llm import get_llm
from core.context import AppContext
from data.provider import get_provider, MockAshareProvider
from harness.observability import Tracer
from harness.cost_guard import CostGuard
from harness.traced_llm import TracedLLM
from kg.store import KnowledgeGraph
from main import _build_context, _build_initial_portfolio
from core.config import Settings

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def settings():
    return Settings.load(os.path.join(_ROOT, "config", "settings.yaml"))


@pytest.fixture
def ctx(settings):
    tracer = Tracer(silent=True)
    cost = CostGuard(model="mock", budget=5.0)
    llm = TracedLLM(get_llm(settings), tracer, cost, actor="system")
    data = get_provider(settings)
    kg = KnowledgeGraph()
    return _build_context(settings, llm, data, tracer, cost, kg)


@pytest.fixture
def init_state(settings):
    return {
        "run_id": "ut", "as_of_date": date.today().isoformat(), "market": "A_SHARE",
        "portfolio": _build_initial_portfolio(settings),
        "risk_limits": settings.risk_limits, "loop_count": 0,
    }


@pytest.fixture
def mock_data():
    return MockAshareProvider()
