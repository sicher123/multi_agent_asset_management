"""
TracedLLM：包装任意 BaseLLM，自动做成本计量 + 链路追踪（Harness 可观测性落地）。

- 不改变 LLM 语义，仅在外层织入 cost_guard.record 与 tracer.event。
- 真实环境可进一步在此加重试/降级/限流（Harness 工程化）。
"""
from __future__ import annotations

from typing import Type, TypeVar

from pydantic import BaseModel

from core.llm import BaseLLM
from harness.cost_guard import CostGuard
from harness.observability import Tracer

T = TypeVar("T", bound=BaseModel)


class TracedLLM(BaseLLM):
    def __init__(self, wrapped: BaseLLM, tracer: Tracer, cost: CostGuard, actor: str = "llm"):
        self._w = wrapped
        self._tracer = tracer
        self._cost = cost
        self._actor = actor

    def complete(self, prompt: str, system: str = "") -> str:
        out = self._w.complete(prompt, system)
        try:
            self._cost.record(f"{self._actor}.complete", prompt, out)
        except Exception:
            pass
        self._tracer.event(self._actor, "complete", plen=len(prompt), clen=len(out))
        return out

    def structured(self, prompt: str, system: str, model_cls: Type[T]) -> T:
        out = self._w.structured(prompt, system, model_cls)
        try:
            self._cost.record(f"{self._actor}.structured", prompt, str(out)[:500])
        except Exception:
            pass
        self._tracer.event(self._actor, "structured", model=model_cls.__name__)
        return out
