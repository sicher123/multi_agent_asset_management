"""
持久化 / 断点：LangGraph checkpointer 封装。

- memory：内存（默认，演示用）。
- sqlite：落盘，支持进程重启后续跑与审计；若环境缺少 sqlite 额外依赖则回退 memory。
HITL 的 interrupt() 依赖 checkpointer 才能恢复，故单独成模块。
"""
from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver


def build_checkpointer(kind: str = "memory", path: str = "checkpoints.db"):
    if kind == "sqlite":
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver
            return SqliteSaver.from_conn_string(path)
        except Exception:
            return MemorySaver()
    return MemorySaver()
