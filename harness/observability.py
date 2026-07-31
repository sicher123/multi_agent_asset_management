"""
可观测性（Observability）：结构化日志 + 指标埋点。

- Tracer：把关键事件以 JSON 行写入日志文件（供监控/审计），同时打印（可关）。
- Metrics：简单计数器/计时器，端到端可看 LLM 调用次数、风控循环次数、成本等。
- 纯标准库，无外部依赖，离线可跑。
"""
from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from typing import Any, Dict


class Tracer:
    def __init__(self, log_path: str = "", name: str = "research", silent: bool = False):
        self.log_path = log_path
        self.silent = silent
        self._fh = open(log_path, "a", encoding="utf-8") if log_path else None
        self._logger = logging.getLogger(f"harness.{name}")
        if not self._logger.handlers:
            self._logger.addHandler(logging.NullHandler())

    def event(self, actor: str, action: str, **fields: Any) -> None:
        rec = {"ts": time.time(), "actor": actor, "action": action, **fields}
        line = json.dumps(rec, ensure_ascii=False, default=str)
        if self._fh:
            self._fh.write(line + "\n")
            self._fh.flush()
        if not self.silent:
            print(f"[trace] {actor}/{action} " + " ".join(f"{k}={v}" for k, v in fields.items()))

    def close(self):
        if self._fh:
            self._fh.close()
            self._fh = None

    @contextmanager
    def span(self, actor: str, action: str, **fields: Any):
        start = time.time()
        self.event(actor, action + ".start", **fields)
        try:
            yield
        finally:
            self.event(actor, action + ".end", elapsed=round(time.time() - start, 3))


class Metrics:
    def __init__(self):
        self._counters: Dict[str, int] = {}
        self._gauges: Dict[str, float] = {}

    def inc(self, name: str, by: int = 1) -> None:
        self._counters[name] = self._counters.get(name, 0) + by

    def set(self, name: str, val: float) -> None:
        self._gauges[name] = val

    def snapshot(self) -> Dict[str, Any]:
        return {"counters": dict(self._counters), "gauges": dict(self._gauges)}
