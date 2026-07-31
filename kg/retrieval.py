"""
GraphRAG 检索：子图遍历（结构） + 相关性/置信度打分（轻量向量替代，离线无 embedding）。

- 给定标的 ticker，从 KG 拉取：公司本体、所属行业、同行（同行业 PEER_OF）、关联概念、
  驱动该行业的宏观因子、影响该公司的事件。
- 混合排序：结构相关性权重 × 关系置信度，取 topk 作为 LLM 上下文（to_context）。
- 真实环境可在此叠加向量检索（对 relation.text / entity.attrs 做 embedding 召回），接口不变。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from kg.schema import E, R, Entity, Relation, eid
from kg.store import KnowledgeGraph


@dataclass
class RetrievalItem:
    entity: Optional[Entity]
    relation: Optional[Relation]
    score: float
    why: str


class GraphRAGRetriever:
    def __init__(self, kg: KnowledgeGraph):
        self.kg = kg

    def _relevance(self, rel_type: Optional[str], is_seed: bool) -> float:
        if is_seed:
            return 1.0
        return {
            R.BELONGS_TO: 0.8,
            R.PEER_OF: 0.7,
            R.RELATED_CONCEPT: 0.6,
            R.DRIVEN_BY: 0.6,
            R.EVENT_AFFECTS: 0.5,
            R.HAS_TARGET: 0.7,
            R.RATED: 0.6,
            R.SUPPLIES: 0.6,
        }.get(rel_type, 0.4)

    def retrieve(self, ticker: str, topk: int = 10) -> List[RetrievalItem]:
        co_id = eid(E.COMPANY, ticker)
        kg = self.kg
        items: List[RetrievalItem] = []
        seed = kg.get_entity(co_id)
        if seed is None:
            return items

        items.append(RetrievalItem(seed, None, 1.0, "目标公司"))

        # 行业
        for rel in kg.neighbors(co_id, R.BELONGS_TO):
            ind = kg.get_entity(rel.dst)
            if ind:
                items.append(RetrievalItem(ind, rel, rel.confidence * 0.8, "所属行业"))

        # 同行（同一行业）
        industry_name = seed.attrs.get("industry")
        if industry_name:
            for peer in kg.companies_in_industry(industry_name):
                if peer.id != co_id:
                    items.append(RetrievalItem(peer, None, 0.7, "同行"))

        # 关联概念
        for rel in kg.neighbors(co_id, R.RELATED_CONCEPT):
            con = kg.get_entity(rel.dst)
            if con:
                items.append(RetrievalItem(con, rel, rel.confidence * 0.6, "关联概念"))

        # 目标价 / 评级
        for rel in kg.neighbors(co_id, R.HAS_TARGET):
            items.append(RetrievalItem(kg.get_entity(rel.dst), rel, rel.confidence * 0.7, "目标价"))
        for rel in kg.neighbors(co_id, R.RATED):
            items.append(RetrievalItem(None, rel, rel.confidence * 0.6, "评级"))

        # 驱动行业的宏观因子（行业 DRIVEN_BY 宏观）
        for rel in kg.neighbors(co_id, R.BELONGS_TO):
            ind = kg.get_entity(rel.dst)
            if ind:
                for m in kg.neighbors(ind.id, R.DRIVEN_BY):
                    macro = kg.get_entity(m.dst)
                    if macro:
                        items.append(RetrievalItem(macro, m, m.confidence * 0.6, "宏观驱动"))

        # 影响该公司的事件
        for src, rels in kg.adj.items():
            for rel in rels:
                if rel.type == R.EVENT_AFFECTS and rel.dst == co_id:
                    evt = kg.get_entity(src)
                    if evt:
                        items.append(RetrievalItem(evt, rel, rel.confidence * 0.5, "事件"))

        # 去重（同实体取最高分），排序取 topk
        best: dict = {}
        for it in items:
            key = it.entity.id if it.entity else f"rel:{it.relation.src}:{it.relation.dst}:{it.relation.type}"
            if key not in best or it.score > best[key].score:
                best[key] = it
        ranked = sorted(best.values(), key=lambda x: x.score, reverse=True)[:topk]
        return ranked

    def to_context(self, ticker: str, topk: int = 10) -> str:
        items = self.retrieve(ticker, topk=topk)
        if not items:
            return "（知识图谱暂无该标的上下文）"
        lines = [f"【知识图谱上下文：{ticker}】"]
        for it in items:
            if it.entity and it.relation is None:
                a = it.entity.attrs
                extra = " ".join(f"{k}={v}" for k, v in a.items() if k not in ("ticker",))[:80]
                lines.append(f"- {it.entity.type} {it.entity.name}（置信{it.entity.confidence:.2f}）{extra}")
            elif it.relation:
                rel = it.relation
                val = f"={rel.value}" if rel.value is not None else ""
                txt = rel.text or ""
                lines.append(f"- {rel.type}{val}（置信{rel.confidence:.2f}）{txt}")
        return "\n".join(lines)
