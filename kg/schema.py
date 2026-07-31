"""
知识图谱 Schema（动态 KG + GraphRAG 的实体/关系定义）。

设计要点：
- 实体/关系均为纯数据类，可序列化到 sqlite，也可被 LLM 抽取流水线产出。
- 实体 ID 规范化（company:600519 / industry:白酒 / concept:提价 ...），便于实体对齐（entity resolution）。
- 仅 A 股场景：实体覆盖 公司/行业/概念/宏观因子/事件/人物。
- 关系带 confidence（0-1）与 sources，支持增量更新时的置信度融合与衰减。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def eid(etype: str, key: str) -> str:
    return f"{etype}:{key}"


def norm_event_id(text: str) -> str:
    h = hashlib.md5(text.encode("utf-8")).hexdigest()[:8]
    return eid("EVENT", h)


# 实体类型（字符串常量，便于扩展）
class E:
    COMPANY = "Company"
    INDUSTRY = "Industry"
    CONCEPT = "Concept"
    MACRO = "Macro"
    EVENT = "Event"
    PERSON = "Person"
    PRICE = "Price"


# 关系类型
class R:
    BELONGS_TO = "BELONGS_TO"          # 公司 -> 行业
    PEER_OF = "PEER_OF"                # 公司 <-> 公司（同行业）
    RATED = "RATED"                    # 分析师/系统 -> 公司（含评级）
    HAS_TARGET = "HAS_TARGET"          # 公司 -> 目标价实体（value）
    RELATED_CONCEPT = "RELATED_CONCEPT"  # 公司/行业 -> 概念
    DRIVEN_BY = "DRIVEN_BY"            # 行业 -> 宏观因子
    EVENT_AFFECTS = "EVENT_AFFECTS"    # 事件 -> 公司/行业
    SUPPLIES = "SUPPLIES"              # 公司 -> 公司（供应链）


@dataclass
class Entity:
    id: str
    type: str
    name: str
    attrs: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    sources: List[str] = field(default_factory=list)
    ts: str = field(default_factory=_now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "type": self.type, "name": self.name,
            "attrs": self.attrs, "confidence": self.confidence,
            "sources": self.sources, "ts": self.ts,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Entity":
        return cls(id=d["id"], type=d["type"], name=d["name"],
                   attrs=d.get("attrs", {}), confidence=d.get("confidence", 1.0),
                   sources=d.get("sources", []), ts=d.get("ts", _now()))


@dataclass
class Relation:
    src: str
    dst: str
    type: str
    confidence: float = 1.0
    value: Optional[float] = None       # 关系数值（如目标价、评级序号）
    text: str = ""                       # 关系自然语言描述（检索/可解释用）
    sources: List[str] = field(default_factory=list)
    ts: str = field(default_factory=_now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "src": self.src, "dst": self.dst, "type": self.type,
            "confidence": self.confidence, "value": self.value,
            "text": self.text, "sources": self.sources, "ts": self.ts,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Relation":
        return cls(src=d["src"], dst=d["dst"], type=d["type"],
                   confidence=d.get("confidence", 1.0), value=d.get("value"),
                   text=d.get("text", ""), sources=d.get("sources", []),
                   ts=d.get("ts", _now()))


# LLM 抽取流水线的结构化产出（真实 LLM 用）
@dataclass
class Triple:
    subject: str          # 实体名（抽取阶段用名，后续对齐到规范 ID）
    subject_type: str
    predicate: str        # 关系类型（R.*）
    obj: str
    obj_type: str
    confidence: float = 0.8
    value: Optional[float] = None
    text: str = ""
