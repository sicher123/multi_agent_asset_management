"""知识图谱跨模块集成测试（冲刺项⑥）。

覆盖图谱子系统的端到端集成链路：
抽取 -> 存储(upsert/置信度融合/实体对齐) -> 检索(GraphRAG) -> 增量更新 -> 衰减 ->
持久化往返 -> 复盘闭环 -> 重大事件演化。

注意：本文件聚焦 KG 子系统自身跨模块协作（不依赖完整 agent 流程），
全系统接线（完整研究闭环填充 KG）见 tests/test_kg_e2e.py。
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone, timedelta

from kg.store import KnowledgeGraph
from kg.extractor import KGExtractor
from kg.retrieval import GraphRAGRetriever
from kg.updater import KGUpdater
from kg.schema import E, R, Entity, Relation, eid, _now
from state.schemas import AnalystReport, Rating


CATALOG = {
    "600519": {"name": "贵州茅台", "industry": "白酒"},
    "000858": {"name": "五粮液", "industry": "白酒"},
    "000001": {"name": "平安银行", "industry": "银行"},
    "300750": {"name": "宁德时代", "industry": "新能源"},
}


def _report(ticker, name, industry, rating=Rating.ADD, target=1500.0, L=1700.0,
            narrative="提价 扩产 景气"):
    return AnalystReport(ticker=ticker, name=name, industry=industry, rating=rating,
                         target_price=target, dcf_implied_L=L, narrative_summary=narrative)


def _seed_multi(kg: KnowledgeGraph, up: KGUpdater) -> None:
    """把多只标的的报告抽取并 upsert 进同一张 KG（模拟行业研究员跨标的沉淀）。"""
    ex = KGExtractor(catalog=CATALOG)
    for t, meta in CATALOG.items():
        r = _report(t, meta["name"], meta["industry"])
        ents, rels = ex.extract_from_report(r)
        up.apply(ents, rels, source="analyst_report")


# ---------------------------------------------------------------- 全链路检索
def test_multiticker_retrieval_sees_peers_and_concepts():
    kg = KnowledgeGraph()
    up = KGUpdater(kg)
    _seed_multi(kg, up)

    ret = GraphRAGRetriever(kg)
    items = ret.retrieve("600519")
    whys = {it.why for it in items}
    assert "所属行业" in whys
    assert "同行" in whys           # 五粮液（同为白酒）

    peer_names = [it.entity.name for it in items if it.why == "同行"]
    assert "五粮液" in peer_names
    assert "平安银行" not in peer_names   # 不同行业不应成为同行

    ctx = ret.to_context("600519")
    assert "贵州茅台" in ctx and "白酒" in ctx


# ----------------------------------------------------------- 置信度融合语义
def test_confidence_fusion_merges_sources_and_takes_max():
    kg = KnowledgeGraph()
    up = KGUpdater(kg)
    co = eid(E.COMPANY, "600519")
    ind = eid(E.INDUSTRY, "白酒")

    # 第一次：低置信、单来源
    up.apply([], [Relation(src=co, dst=ind, type=R.BELONGS_TO, confidence=0.3, sources=["a"])])
    rels = kg.neighbors(co, R.BELONGS_TO)
    assert len(rels) == 1 and rels[0].confidence == 0.3 and rels[0].sources == ["a"]

    # 第二次：更高置信、另一来源 => 取 max 且来源合并
    up.apply([], [Relation(src=co, dst=ind, type=R.BELONGS_TO, confidence=0.9, sources=["b"])])
    rels = kg.neighbors(co, R.BELONGS_TO)
    assert len(rels) == 1
    assert rels[0].confidence == 0.9
    assert set(rels[0].sources) == {"a", "b"}


# ------------------------------------------------------- 子图多跳 + 深度限制
def test_subgraph_multihop_respects_depth():
    kg = KnowledgeGraph()
    up = KGUpdater(kg)
    _seed_multi(kg, up)
    # 行业 -> 宏观驱动
    kg.add_relation(Relation(src=eid(E.INDUSTRY, "白酒"), dst=eid(E.MACRO, "BULL"),
                             type=R.DRIVEN_BY, confidence=0.8))

    # depth=1：只到直接邻居（行业），不应到达宏观
    ents1, _ = kg.subgraph([eid(E.COMPANY, "600519")], max_depth=1)
    ids1 = {e.id for e in ents1}
    assert eid(E.COMPANY, "600519") in ids1
    assert eid(E.INDUSTRY, "白酒") in ids1
    assert eid(E.MACRO, "BULL") not in ids1

    # depth=2：应经行业到达宏观
    ents2, _ = kg.subgraph([eid(E.COMPANY, "600519")], max_depth=2)
    assert eid(E.MACRO, "BULL") in {e.id for e in ents2}


# ---------------------------------------------- 非结构化文本抽取：启发式
def test_heuristic_text_extraction_catalog_and_concepts():
    ex = KGExtractor(catalog=CATALOG)
    text = "贵州茅台宣布提价，带动白酒行业景气；贵州茅台扩产以满足需求。"
    ents, rels = ex.extract_from_text(text, source="news")

    types = {e.type for e in ents}
    assert E.EVENT in types
    assert E.CONCEPT in types

    rel_types = {r.type for r in rels}
    assert R.EVENT_AFFECTS in rel_types
    assert R.RELATED_CONCEPT in rel_types

    con_names = {e.name for e in ents if e.type == E.CONCEPT}
    assert "提价" in con_names and "扩产" in con_names


# ------------------------------------------------- 非结构化文本抽取：LLM
def test_llm_text_extraction_aligns_canonical_ids():
    class _FakeTriple:
        def __init__(self, subject, subject_type, predicate, obj, obj_type,
                     confidence=0.8, value=None, text=""):
            self.subject = subject
            self.subject_type = subject_type
            self.predicate = predicate
            self.obj = obj
            self.obj_type = obj_type
            self.confidence = confidence
            self.value = value
            self.text = text

    class _FakeOut:
        def __init__(self, triples):
            self.triples = triples

    class FakeLLM:
        def structured(self, text, system=None, model_cls=None):
            return _FakeOut(triples=[
                _FakeTriple("贵州茅台", "Company", "RELATED_CONCEPT", "提价", "Concept",
                            confidence=0.9, text="茅台关联提价"),
            ])

    ex = KGExtractor(catalog=CATALOG)
    ents, rels = ex.extract_from_text("茅台提价", source="llm_news", llm=FakeLLM())

    ids = {e.id for e in ents}
    assert eid(E.COMPANY, "贵州茅台") in ids
    assert eid(E.CONCEPT, "提价") in ids

    rel = next(r for r in rels if r.type == "RELATED_CONCEPT")
    assert rel.src == eid(E.COMPANY, "贵州茅台")
    assert rel.dst == eid(E.CONCEPT, "提价")


# ----------------------------------------------------- 持久化往返 + 可读
def test_sqlite_roundtrip_preserves_and_retrievable():
    kg = KnowledgeGraph()
    up = KGUpdater(kg)
    _seed_multi(kg, up)
    before = kg.stats()

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "kg.sqlite")
        kg.save_sqlite(path)
        kg2 = KnowledgeGraph.load_sqlite(path)

    assert kg2.stats()["entities"] == before["entities"]
    assert kg2.stats()["relations"] == before["relations"]

    # 往返后仍可被检索，且实体属性保留
    ret = GraphRAGRetriever(kg2)
    assert ret.retrieve("600519")
    co = kg2.get_entity(eid(E.COMPANY, "600519"))
    assert co.attrs.get("industry") == "白酒"


# ----------------------------------------------------------- 衰减数学正确
def test_decay_respects_half_life_and_leaves_fresh_untouched():
    kg = KnowledgeGraph()
    up = KGUpdater(kg)
    co = eid(E.COMPANY, "600519")
    old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    fresh_ts = _now()

    kg.add_relation(Relation(src=co, dst=eid(E.INDUSTRY, "白酒"),
                             type=R.BELONGS_TO, confidence=1.0, ts=old_ts))
    kg.add_relation(Relation(src=co, dst=eid(E.PRICE, "600519"),
                             type=R.HAS_TARGET, confidence=1.0, ts=fresh_ts, value=1500))

    up.decay(half_life_days=30)

    old_rel = kg.neighbors(co, R.BELONGS_TO)[0]
    fresh_rel = kg.neighbors(co, R.HAS_TARGET)[0]
    assert abs(old_rel.confidence - 0.5) < 1e-6    # 30 天 / 半衰期 30 => ×0.5
    assert abs(fresh_rel.confidence - 1.0) < 1e-6  # 新鲜关系不变


# ------------------------------------------------------- 复盘回报闭环
def test_outcome_feedback_up_and_down_and_bounded():
    kg = KnowledgeGraph()
    up = KGUpdater(kg)
    up.apply(*KGExtractor(catalog=CATALOG).extract_from_report(
        _report("600519", "贵州茅台", "白酒")))

    # 命中：实际价≈预测价 => 置信度上调
    c_hit = up.update_outcome("600519", predicted_target=1500.0, actual_price=1500.0)
    assert c_hit is not None and c_hit > 0.5

    # 偏离大：实际价腰斩 => 置信度下降
    c_miss = up.update_outcome("600519", predicted_target=1500.0, actual_price=750.0)
    assert c_miss is not None and c_miss < c_hit

    # 多次回灌收敛且有界
    c = c_hit
    for _ in range(10):
        c = up.update_outcome("600519", predicted_target=1500.0, actual_price=1500.0)
    assert 0.0 <= c <= 1.0


# ------------------------------------------------------- 实体对齐去重
def test_entity_resolution_no_duplication():
    kg = KnowledgeGraph()
    up = KGUpdater(kg)
    ex = KGExtractor(catalog=CATALOG)

    e1, r1 = ex.extract_from_report(_report("600519", "贵州茅台", "白酒"))
    n1 = up.apply(e1, r1)
    e_first = kg.stats()["entities"]
    assert n1 > 0                       # 首次确有写入

    # 完全相同的报告再次抽取：实体/关系应被融合，不增加
    e2, r2 = ex.extract_from_report(_report("600519", "贵州茅台", "白酒"))
    n2 = up.apply(e2, r2)
    assert kg.stats()["entities"] == e_first
    assert n2 == 0


# ------------------------------------------- 重大事件注入 -> 检索可见
def test_material_event_injected_visible_in_retrieval():
    kg = KnowledgeGraph()
    up = KGUpdater(kg)
    up.apply(*KGExtractor(catalog=CATALOG).extract_from_report(
        _report("600519", "贵州茅台", "白酒")))

    evt = Entity(id=eid(E.EVENT, "abc"), type=E.EVENT, name="茅台半年报超预期",
                 confidence=0.6, sources=["news"])
    up.apply([evt], [Relation(src=evt.id, dst=eid(E.COMPANY, "600519"),
                              type=R.EVENT_AFFECTS, confidence=0.6,
                              text="茅台半年报超预期", sources=["news"])])

    ret = GraphRAGRetriever(kg)
    items = ret.retrieve("600519")
    assert "事件" in {it.why for it in items}

    ctx = ret.to_context("600519")
    assert "茅台半年报超预期" in ctx
