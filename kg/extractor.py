"""
KG 抽取流水线：把「结构化报告」与「非结构化文本（新闻/公告/研报）」转成实体-关系三元组。

- 结构化（AnalystReport）：确定性映射，无需 LLM，离线稳定。
- 非结构化文本：优先用 LLM 结构化抽取（真实环境）；离线/Mock 走启发式兜底（扫描已知标的与概念关键词）。
- 抽取前由调用方先做提示注入/数据投毒清洗（见 harness.injection_guard），本模块只消费清洗后的文本。
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from kg.schema import (
    E, R, Entity, Relation, Triple, eid, norm_event_id, _now,
)
from state.schemas import AnalystReport

# 概念关键词（启发式兜底用；真实环境由 LLM 抽取任意概念）
_CONCEPT_KEYWORDS = [
    "提价", "降价", "扩产", "减产", "去产能", "政策", "监管", "景气", "需求", "库存",
    "补贴", "制裁", "出口", "进口替代", "涨价", "跌价", "毛利", "份额", "格局", "周期",
]

_RATING_INDEX = {"BUY": 1.0, "ADD": 0.75, "HOLD": 0.5, "REDUCE": 0.25, "SELL": 0.0}


class KGExtractor:
    def __init__(self, catalog: Optional[Dict[str, Dict[str, str]]] = None):
        # catalog: {ticker: {"name":.., "industry":..}}  用于文本抽取时识别标的
        self.catalog = catalog or {}

    # ----------------------- 结构化：个股报告 -----------------------
    def extract_from_report(self, r: AnalystReport) -> Tuple[List[Entity], List[Relation]]:
        entities: List[Entity] = []
        relations: List[Relation] = []

        co_id = eid(E.COMPANY, r.ticker)
        entities.append(Entity(
            id=co_id, type=E.COMPANY, name=r.name,
            attrs={"ticker": r.ticker, "industry": r.industry,
                   "rating": r.rating.value, "dcf_implied_L": r.dcf_implied_L,
                   "data_quality_ok": r.data_quality_ok},
            confidence=1.0 if r.data_quality_ok else 0.5,
            sources=["analyst_report"],
        ))
        ind_id = eid(E.INDUSTRY, r.industry)
        entities.append(Entity(id=ind_id, type=E.INDUSTRY, name=r.industry,
                               attrs={}, confidence=1.0, sources=["analyst_report"]))
        relations.append(Relation(src=co_id, dst=ind_id, type=R.BELONGS_TO,
                                  confidence=1.0, text=f"{r.name}属于{r.industry}",
                                  sources=["analyst_report"]))

        price_id = eid(E.PRICE, r.ticker)
        entities.append(Entity(id=price_id, type=E.PRICE, name=f"{r.name}目标价",
                               attrs={"value": r.target_price}, confidence=0.9,
                               sources=["analyst_report"]))
        relations.append(Relation(src=co_id, dst=price_id, type=R.HAS_TARGET,
                                  confidence=0.9, value=r.target_price,
                                  text=f"目标价{r.target_price}", sources=["analyst_report"]))

        relations.append(Relation(
            src=eid("Analyst", "system"), dst=co_id, type=R.RATED,
            confidence=1.0 if r.data_quality_ok else 0.5,
            value=_RATING_INDEX.get(r.rating.value, 0.5),
            text=f"评级{r.rating.value}", sources=["analyst_report"]))

        # 叙事摘要里的概念抽取（启发式）
        if r.narrative_summary:
            for kw in _CONCEPT_KEYWORDS:
                if kw in r.narrative_summary:
                    con_id = eid(E.CONCEPT, kw)
                    entities.append(Entity(id=con_id, type=E.CONCEPT, name=kw,
                                           attrs={}, confidence=0.7, sources=["narrative"]))
                    relations.append(Relation(src=co_id, dst=con_id, type=R.RELATED_CONCEPT,
                                              confidence=0.7, text=f"关联概念:{kw}",
                                              sources=["narrative"]))
        return entities, relations

    # ----------------------- 非结构化：文本 -----------------------
    def extract_from_text(self, text: str, source: str = "news",
                          llm=None) -> Tuple[List[Entity], List[Relation]]:
        """返回 (entities, relations)。llm 为可选真实抽取器。"""
        if llm is not None:
            return self._extract_with_llm(text, source, llm)
        return self._extract_heuristic(text, source)

    def _extract_with_llm(self, text, source, llm) -> Tuple[List[Entity], List[Relation]]:
        # 真实环境：让 LLM 输出 List[Triple]，再对齐到规范实体 ID。
        # 离线/Mock 不进入此分支（由上层判断 llm 是否可用）。
        from pydantic import BaseModel, Field
        from typing import List as TList

        class _TripleM(BaseModel):
            subject: str
            subject_type: str
            predicate: str
            obj: str
            obj_type: str
            confidence: float = 0.8
            value: Optional[float] = None
            text: str = ""

        class _Out(BaseModel):
            triples: TList[_TripleM] = Field(default_factory=list)

        out: _Out = llm.structured(text, system="抽取实体关系三元组", model_cls=_Out)
        entities, relations = [], []
        for t in out.triples:
            sub_id = eid(t.subject_type, t.subject)
            obj_id = eid(t.obj_type, t.obj)
            entities.append(Entity(id=sub_id, type=t.subject_type, name=t.subject,
                                   confidence=t.confidence, sources=[source]))
            entities.append(Entity(id=obj_id, type=t.obj_type, name=t.obj,
                                   confidence=t.confidence, sources=[source]))
            relations.append(Relation(src=sub_id, dst=obj_id, type=t.predicate,
                                      confidence=t.confidence, value=t.value,
                                      text=t.text, sources=[source]))
        return entities, relations

    def _extract_heuristic(self, text, source) -> Tuple[List[Entity], List[Relation]]:
        entities: List[Entity] = []
        relations: List[Relation] = []
        sentences = re.split(r"[。！？\n;；]", text)
        for s in sentences:
            s = s.strip()
            if not s:
                continue
            for ticker, meta in self.catalog.items():
                if ticker in s or meta.get("name") in s:
                    evt_id = norm_event_id(s)
                    entities.append(Entity(id=evt_id, type=E.EVENT, name=s[:24],
                                           attrs={"sentence": s}, confidence=0.6,
                                           sources=[source]))
                    relations.append(Relation(
                        src=evt_id, dst=eid(E.COMPANY, ticker), type=R.EVENT_AFFECTS,
                        confidence=0.6, text=s, sources=[source]))
                    for kw in _CONCEPT_KEYWORDS:
                        if kw in s:
                            con_id = eid(E.CONCEPT, kw)
                            entities.append(Entity(id=con_id, type=E.CONCEPT, name=kw,
                                                   confidence=0.6, sources=[source]))
                            relations.append(Relation(
                                src=eid(E.COMPANY, ticker), dst=con_id,
                                type=R.RELATED_CONCEPT, confidence=0.6,
                                text=f"关联概念:{kw}", sources=[source]))
        return entities, relations
