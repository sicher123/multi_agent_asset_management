"""
成本护栏（Cost Guard）：为 LLM 调用估算 token / 费用并守预算。

- 离线 Mock 成本≈0，但逻辑齐备，切真实模型即生效。
- 价格表按模型名（美元/ token）；token 估算用字符数近似（中文约 1.5 字/token，英文 ~4 字符/token）。
- 超过预算时 record() 抛 BudgetExceeded（由调用方决定阻断或告警）。
"""
from __future__ import annotations

from typing import Dict

# 美元 / token（示意价，真实可热更新）
_PRICE_TABLE: Dict[str, Dict[str, float]] = {
    "gpt-4o-mini": {"in": 0.150 / 1_000_000, "out": 0.600 / 1_000_000},
    "gpt-4o": {"in": 2.5 / 1_000_000, "out": 10.0 / 1_000_000},
    "mock": {"in": 0.0, "out": 0.0},
}


class BudgetExceeded(Exception):
    pass


def _estimate_tokens(text: str) -> int:
    # 粗略：中文按字，英文按 4 字符；取两者较大近似
    cjk = sum(1 for ch in text if ord(ch) > 0x4E00)
    other = max(len(text) - cjk, 0)
    return int(cjk * 1.5 + other / 4) + 1


class CostGuard:
    def __init__(self, model: str = "mock", budget: float = 1.0):
        self.model = model
        self.budget = budget
        self.used = 0.0
        self.calls = 0

    def record(self, name: str, prompt: str, completion: str = "") -> float:
        price = _PRICE_TABLE.get(self.model, _PRICE_TABLE["mock"])
        p_tokens = _estimate_tokens(prompt)
        c_tokens = _estimate_tokens(completion)
        cost = p_tokens * price["in"] + c_tokens * price["out"]
        self.used += cost
        self.calls += 1
        if self.used > self.budget:
            raise BudgetExceeded(f"LLM 成本 ${self.used:.4f} 超过预算 ${self.budget:.4f}（调用 {name}）")
        return cost

    def status(self) -> Dict[str, float]:
        return {"model": self.model, "used": round(self.used, 6),
                "budget": self.budget, "calls": self.calls,
                "remaining": round(self.budget - self.used, 6)}
