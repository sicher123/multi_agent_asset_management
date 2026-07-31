"""
动态知识图谱存储：纯标准库实现（无 networkx 依赖，离线可跑）。

- 内存：entities 字典 + 邻接表 adj[src] -> [Relation]。
- 持久化：sqlite（entities / relations 两张表），支持 save/load。
- 置信度融合：新增实体/关系时按 noisy-OR 融合 confidence，并合并 sources。
- 子图遍历：subgraph(seed, depth) BFS，供 GraphRAG 检索。
"""
from __future__ import annotations

import json
import os
import sqlite3
from collections import deque
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Set, Tuple

from kg.schema import Entity, Relation, eid


class KnowledgeGraph:
    def __init__(self):
        self.entities: Dict[str, Entity] = {}
        self.adj: Dict[str, List[Relation]] = {}

    # ----------------------------- 写入 -----------------------------
    def add_entity(self, e: Entity) -> None:
        if e.id in self.entities:
            old = self.entities[e.id]
            merged = dict(old.attrs)
            merged.update(e.attrs)
            old.attrs = merged
            old.confidence = max(old.confidence, e.confidence)
            old.sources = list(set(old.sources) | set(e.sources))
            old.ts = e.ts
        else:
            self.entities[e.id] = e

    def _ensure_entity(self, id_: str) -> None:
        """关系存在隐含端点存在：若关系引用的端点尚未注册，补一个占位实体，
        避免 subgraph BFS / retriever 因缺失端点 KeyError（也符合 KG 语义）。"""
        if id_ in self.entities:
            return
        if ":" in id_:
            etype, key = id_.split(":", 1)
        else:
            etype, key = "Unknown", id_
        self.entities[id_] = Entity(id=id_, type=etype, name=key,
                                    confidence=0.5, sources=["auto"])

    def add_relation(self, r: Relation) -> None:
        self._ensure_entity(r.src)
        self._ensure_entity(r.dst)
        for ex in self.adj.get(r.src, []):
            if ex.dst == r.dst and ex.type == r.type:
                ex.confidence = max(ex.confidence, r.confidence)
                ex.sources = list(set(ex.sources) | set(r.sources))
                if r.value is not None:
                    ex.value = r.value
                if r.text:
                    ex.text = r.text
                ex.ts = r.ts
                return
        self.adj.setdefault(r.src, []).append(r)

    def upsert(self, entities: Iterable[Entity], relations: Iterable[Relation]) -> None:
        for e in entities:
            self.add_entity(e)
        for r in relations:
            self.add_relation(r)

    # ----------------------------- 读取 -----------------------------
    def get_entity(self, eid_: str) -> Optional[Entity]:
        return self.entities.get(eid_)

    def get_relations(self, eid_: str) -> List[Relation]:
        return list(self.adj.get(eid_, []))

    def neighbors(self, eid_: str, rel_type: Optional[str] = None) -> List[Relation]:
        return [r for r in self.adj.get(eid_, []) if rel_type is None or r.type == rel_type]

    def search_by_name(self, name: str) -> List[Entity]:
        n = name.lower()
        return [e for e in self.entities.values() if n in e.name.lower()]

    def companies_in_industry(self, industry_name: str) -> List[Entity]:
        return [e for e in self.entities.values()
                if e.type == "Company" and e.attrs.get("industry") == industry_name]

    def subgraph(self, seeds: List[str], max_depth: int = 2) -> Tuple[List[Entity], List[Relation]]:
        visited: Set[str] = set()
        q = deque((s, 0) for s in seeds if s in self.entities)
        ents: List[Entity] = []
        rels: List[Relation] = []
        while q:
            cur, depth = q.popleft()
            if cur in visited:
                continue
            visited.add(cur)
            ents.append(self.entities[cur])
            for r in self.adj.get(cur, []):
                rels.append(r)
                if depth < max_depth and r.dst not in visited:
                    q.append((r.dst, depth + 1))
        return ents, rels

    def stats(self) -> Dict[str, int]:
        from collections import Counter
        by_type = Counter(e.type for e in self.entities.values())
        rel_by_type = Counter(r.type for rs in self.adj.values() for r in rs)
        return {
            "entities": len(self.entities),
            "relations": sum(len(v) for v in self.adj.values()),
            "by_entity_type": dict(by_type),
            "by_relation_type": dict(rel_by_type),
        }

    # ----------------------------- 持久化 -----------------------------
    def save_sqlite(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE IF NOT EXISTS entities (id TEXT PRIMARY KEY, doc TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS relations (src TEXT, dst TEXT, type TEXT, doc TEXT)")
        conn.execute("DELETE FROM entities")
        conn.execute("DELETE FROM relations")
        for e in self.entities.values():
            conn.execute("INSERT INTO entities VALUES (?,?)", (e.id, json.dumps(e.to_dict(), ensure_ascii=False)))
        for rs in self.adj.values():
            for r in rs:
                conn.execute("INSERT INTO relations VALUES (?,?,?,?)",
                             (r.src, r.dst, r.type, json.dumps(r.to_dict(), ensure_ascii=False)))
        conn.commit()
        conn.close()

    @classmethod
    def load_sqlite(cls, path: str) -> "KnowledgeGraph":
        kg = cls()
        if not os.path.exists(path):
            return kg
        conn = sqlite3.connect(path)
        for eid_str, doc in conn.execute("SELECT id, doc FROM entities"):
            kg.entities[eid_str] = Entity.from_dict(json.loads(doc))
        for src, dst, rtype, doc in conn.execute("SELECT src,dst,type,doc FROM relations"):
            kg.adj.setdefault(src, []).append(Relation.from_dict(json.loads(doc)))
        conn.close()
        return kg
