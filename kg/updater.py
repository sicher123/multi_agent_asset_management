"""
KG 增量更新器（事件驱动）：把抽取结果写回 KG，并维护「动态」特性。

- apply：增量写入实体/关系（置信度在 store 层融合）。
- decay：随时间衰减旧关系置信度（半衰期可配），体现知识时效性。
- update_outcome：复盘闭环——用「预测目标价 vs 实际价」回灌，上调/下调 HAS_TARGET 置信度。
- reconcile：按规范 ID + 名称做简单实体对齐，避免重复（扩展可做模糊匹配）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, List, Optional, Tuple

from kg.schema import E, R, Entity, Relation, eid
from kg.store import KnowledgeGraph


class KGUpdater:
    def __init__(self, kg: KnowledgeGraph):
        self.kg = kg

    def apply(self, entities: Iterable[Entity], relations: Iterable[Relation],
              source: str = "pipeline") -> int:
        before = self.kg.stats()["relations"]
        self.kg.upsert(entities, relations)
        after = self.kg.stats()["relations"]
        return after - before

    def decay(self, half_life_days: float = 30.0) -> None:
        now = datetime.now(timezone.utc)
        for rels in self.kg.adj.values():
            for r in rels:
                try:
                    ts = datetime.fromisoformat(r.ts)
                    age_days = (now - ts).total_seconds() / 86400.0
                except Exception:
                    age_days = 0.0
                factor = 0.5 ** (age_days / max(half_life_days, 1e-6))
                r.confidence = round(r.confidence * factor, 4)

    def update_outcome(self, ticker: str, predicted_target: float,
                       actual_price: float) -> Optional[float]:
        """复盘回灌：命中率越高，HAS_TARGET 置信度越上调。返回更新后的置信度。"""
        if predicted_target <= 0:
            return None
        co_id = eid(E.COMPANY, ticker)
        err = abs(actual_price - predicted_target) / predicted_target
        hit = max(0.2, 1.0 - err)
        for r in self.kg.neighbors(co_id, R.HAS_TARGET):
            r.confidence = round(r.confidence * 0.5 + hit * 0.5, 4)
            return r.confidence
        return None
