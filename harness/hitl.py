"""
HITL（人工断点）：在关键决策处插入真人审批。

- 优先用 LangGraph 的 interrupt() 实现「可恢复断点」（需 checkpointer 支持）。
- 离线（LocalRunner / 无 langgraph）时自动放行（default=True），保证端到端可跑。
- 调用方在节点内直接 human_approve(question)，返回值即人工决策。
"""
from __future__ import annotations

try:
    from langgraph.types import interrupt
    _HAS_INTERRUPT = True
except Exception:  # pragma: no cover - 离线回退
    _HAS_INTERRUPT = False


def human_approve(question: str, default: bool = True, enabled: bool = True) -> bool:
    """在关键决策处插入真人审批。

    - enabled=False 时直接返回 default（无人值守/离线 Demo 自动放行，不调用 interrupt）。
    - 否则：有 langgraph 则 interrupt() 挂起等待真人；无则回退 default。
    """
    if not enabled:
        return default
    if _HAS_INTERRUPT:
        ans = interrupt({"type": "approval", "question": question})
        if isinstance(ans, dict):
            return bool(ans.get("approve", default))
        return bool(ans)
    return default
