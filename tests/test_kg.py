"""知识图谱：抽取 / 检索 / 增量更新（P4）。"""
from __future__ import annotations

from kg.store import KnowledgeGraph
from kg.extractor import KGExtractor
from kg.retrieval import GraphRAGRetriever
from kg.updater import KGUpdater
from kg.schema import E, R, eid
from state.schemas import AnalystReport, Rating


def _report(ticker="600519", name="贵州茅台", industry="白酒", target=1500.0, L=1700.0):
    return AnalystReport(ticker=ticker, name=name, industry=industry, rating=Rating.ADD,
                         target_price=target, dcf_implied_L=L, narrative_summary="提价 扩产 景气")


def test_store_neighbors_and_subgraph():
    kg = KnowledgeGraph()
    kg.add_entity(__import__("kg.schema", fromlist=["Entity"]).Entity(
        id=eid(E.COMPANY, "600519"), type=E.COMPANY, name="茅台", confidence=1.0))
    kg.add_relation(__import__("kg.schema", fromlist=["Relation"]).Relation(
        src=eid(E.COMPANY, "600519"), dst=eid(E.INDUSTRY, "白酒"),
        type=R.BELONGS_TO, confidence=1.0))
    assert len(kg.neighbors(eid(E.COMPANY, "600519"), R.BELONGS_TO)) == 1
    ents, rels = kg.subgraph([eid(E.COMPANY, "600519")], max_depth=2)
    assert len(ents) == 2 and len(rels) == 1
    assert kg.stats()["entities"] == 2


def test_extractor_report_and_updater():
    kg = KnowledgeGraph()
    ex = KGExtractor(catalog={"600519": {"name": "贵州茅台", "industry": "白酒"}})
    up = KGUpdater(kg)
    ents, rels = ex.extract_from_report(_report())
    before = kg.stats()["relations"]
    n = up.apply(ents, rels, source="analyst_report")
    assert n >= 1
    assert kg.stats()["relations"] > before
    # 行业实体应被写入
    assert kg.get_entity(eid(E.INDUSTRY, "白酒")) is not None


def test_retriever_pulls_peers_and_macro():
    kg = KnowledgeGraph()
    ex = KGExtractor(catalog={"600519": {"name": "贵州茅台", "industry": "白酒"}})
    up = KGUpdater(kg)
    up.apply(*ex.extract_from_report(_report()))
    # 行业 -> 宏观 驱动关系
    kg.add_relation(__import__("kg.schema", fromlist=["Relation"]).Relation(
        src=eid(E.INDUSTRY, "白酒"), dst=eid(E.MACRO, "BULL"),
        type=R.DRIVEN_BY, confidence=0.8))
    ret = GraphRAGRetriever(kg)
    items = ret.retrieve("600519", topk=10)
    kinds = {it.why for it in items}
    assert "所属行业" in kinds
    assert "宏观驱动" in kinds
    ctx = ret.to_context("600519")
    assert "贵州茅台" in ctx


def test_updater_decay_and_outcome():
    kg = KnowledgeGraph()
    ex = KGExtractor(catalog={})
    up = KGUpdater(kg)
    up.apply(*ex.extract_from_report(_report()))
    # 衰减不应抛错
    up.decay(half_life_days=30)
    # 复盘回灌：实际价接近预测 -> 置信度应 > 0
    c = up.update_outcome("600519", predicted_target=1500.0, actual_price=1520.0)
    assert c is not None and c > 0.0
