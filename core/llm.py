"""
LLM 抽象层：让角色与具体模型解耦，方便替换/升级。

- BaseLLM：统一接口（complete / structured）。
- MockLLM：离线可跑的确定性实现，按模型类名填默认值，使系统无需 API key 也能端到端验证。
- OpenAILLM：真实客户端（langchain-openai），用 with_structured_output 保证结构化产出。
- get_llm(settings)：工厂，按配置返回实例。
"""
from __future__ import annotations

import enum
import os
from typing import Type, TypeVar

from pydantic import BaseModel

from core.config import Settings

T = TypeVar("T", bound=BaseModel)

# 针对关键模型给 Mock 一个贴近业务的默认值，使流程能跑通并能演示分歧
_MOCK_DEFAULTS = {
    "MacroReport": {"trend": "SIDEWAYS", "suggested_max_position": 0.70,
                    "risk_appetite": "中性偏谨慎", "liquidity_view": "流动性合理"},
    "AnalystReport": {"rating": "ADD", "target_price": 100.0, "dcf_implied_L": 120.0,
                      "entry_point": 90.0, "exit_point": 130.0},
    "ManagerDecision": {"worth_trading": True, "stop_loss_pct": 0.08, "take_profit_pct": 0.20,
                        "rationale": "（模拟）宏观中性，个股评级偏积极，组合可小幅建仓"},
    "RiskCheckResult": {"decision": "APPROVED"},
}


class BaseLLM:
    def complete(self, prompt: str, system: str = "") -> str:
        raise NotImplementedError

    def structured(self, prompt: str, system: str, model_cls: Type[T]) -> T:
        raise NotImplementedError


class MockLLM(BaseLLM):
    """确定性 Mock：用于无 key 的端到端验证与测试。"""

    def complete(self, prompt: str, system: str = "") -> str:
        return f"[MOCK] {system or 'assistant'}: 已处理 {len(prompt)} 字prompt"

    def structured(self, prompt: str, system: str, model_cls: Type[T]) -> T:
        overrides = _MOCK_DEFAULTS.get(model_cls.__name__, {})
        data = {}
        for name, field_obj in model_cls.model_fields.items():
            if name in overrides:
                data[name] = overrides[name]
                continue
            ann = field_obj.annotation
            if isinstance(ann, type) and issubclass(ann, enum.Enum):
                data[name] = list(ann)[0].value
            elif ann is bool:
                data[name] = (name == "worth_trading") or False
            elif ann is float:
                data[name] = 0.0
            elif ann is int:
                data[name] = 0
            elif ann is str:
                data[name] = f"（模拟）{name}"
            elif hasattr(ann, "__origin__") and ann.__origin__ is list:
                data[name] = []
            elif ann is dict or (hasattr(ann, "__origin__") and ann.__origin__ is dict):
                data[name] = {}
            else:
                data[name] = None
        # 去掉 None 以允许模型字段有默认值
        data = {k: v for k, v in data.items() if v is not None}
        return model_cls.model_validate(data)


class OpenAILLM(BaseLLM):
    """真实客户端：结构化输出交给模型保证。"""

    def __init__(self, model: str, temperature: float, api_key: str, base_url: str = ""):
        from langchain_openai import ChatOpenAI
        kwargs = {"model": model, "temperature": temperature, "api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._chat = ChatOpenAI(**kwargs)

    def complete(self, prompt: str, system: str = "") -> str:
        msgs = []
        if system:
            msgs.append(("system", system))
        msgs.append(("human", prompt))
        return self._chat.invoke(msgs).content

    def structured(self, prompt: str, system: str, model_cls: Type[T]) -> T:
        msgs = []
        if system:
            msgs.append(("system", system))
        msgs.append(("human", prompt))
        return self._chat.with_structured_output(model_cls).invoke(msgs)


def get_llm(settings: Settings) -> BaseLLM:
    if settings.llm.provider == "openai":
        return OpenAILLM(
            model=settings.llm.model,
            temperature=settings.llm.temperature,
            api_key=settings.resolve_api_key(),
            base_url=settings.llm.base_url,
        )
    return MockLLM()
