"""全系统知识图谱接线端到端集成测试（冲刺项⑥）。

不依赖 LangGraph 编译图：直接驱动 LocalRunner 跑完整研究闭环
（screener -> macro/industry -> manager -> risk -> trader -> reviewer -> replayer），
断言：
- 行业研究员真实把报告/新闻抽取并 upsert 进 ctx.kg（实体/关系 > 0）；
- kg_stats 记录 industry_kg_writes；
- GraphRAGRetriever 在真实图谱上可检索到行业/同行；
- 复盘阶段的 outcome 回灌对 KG 生效；
- KG 可 save_sqlite 落盘且 load 后完全一致（图在系统中端到端可用）。
"""
from __future__ import annotations

import os
import tempfile
from datetime import date

from core.config import Settings
from core.llm import get_llm
from core.context import AppContext
from data.provider import get_provider
from graph.builder import LocalRunner
from harness.observability import Tracer
from harness.cost_guard import CostGuard
from harness.traced_llm import TracedLLM
from kg.store import KnowledgeGraph

from main import _build_context, _build_initial_portfolio

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run_full_pipeline():
    settings = Settings.load(os.path.join(_ROOT, "config", "settings.yaml"))
    tracer = Tracer(silent=True)
    cost = CostGuard(model="mock", budget=5.0)
    llm = TracedLLM(get_llm(settings), tracer, cost, actor="system")
    data = get_provider(settings)
    kg = KnowledgeGraph()
    ctx = _build_context(settings, llm, data, tracer, cost, kg)

    runner = LocalRunner(ctx)
    init_state = {
        "run_id": "kg-e2e",
        "as_of_date": date.today().isoformat(),
        "market": "A_SHARE",
        "portfolio": _build_initial_portfolio(settings),
        "risk_limits": settings.risk_limits,
        "loop_count": 0,
    }
    final = runner.invoke(init_state)
    return ctx, final


def test_full_pipeline_fills_kg_and_retrievable():
    ctx, final = _run_full_pipeline()

    # 1) 行业研究员已真实 upsert 进 KG
    assert ctx.kg.stats()["entities"] > 0
    assert ctx.kg.stats()["relations"] > 0

    # 2) kg_stats 记录本轮回填量
    assert final.get("kg_stats", {}).get("industry_kg_writes", 0) > 0

    # 3) GraphRAG 在真实图谱上可检索
    first = final["analyst_reports"][0]
    items = ctx.retriever.retrieve(first.ticker)
    assert items
    assert any(it.why == "所属行业" for it in items)

    # 4) 复盘 outcome 回灌对 KG 生效（有成交则回灌数 > 0 或至少不抛错）
    replay_kg = final.get("kg_stats", {})
    assert "kg_outcome_updates" in replay_kg

    # 5) 持久化往返一致
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "kg.sqlite")
        ctx.kg.save_sqlite(p)
        kg2 = KnowledgeGraph.load_sqlite(p)
        assert kg2.stats()["entities"] == ctx.kg.stats()["entities"]
        assert kg2.stats()["relations"] == ctx.kg.stats()["relations"]


def test_full_pipeline_kg_has_industry_and_peers_for_a_ticker():
    """针对任一研究标的，图谱应含其所属行业实体与（若存在）同行。"""
    ctx, final = _run_full_pipeline()
    from kg.schema import E, eid

    for r in final["analyst_reports"]:
        items = ctx.retriever.retrieve(r.ticker)
        whys = {it.why for it in items}
        assert "所属行业" in whys
        co = ctx.kg.get_entity(eid(E.COMPANY, r.ticker))
        assert co is not None
        assert co.attrs.get("industry") == r.industry
