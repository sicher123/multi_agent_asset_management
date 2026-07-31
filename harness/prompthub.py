"""
PromptHub：角色 prompt 的版本化管理（Harness 工程化的一部分）。

- 角色 system prompt / 模板集中存于此，支持多版本与回滚。
- 调用方用 render(name, **kw) 取最新（或指定版本）模板，不再把 prompt 硬编码在 agent 里。
- 真实环境可把模板外置为文件/DB；此处用内存 + 可选 JSON 文件持久化。
"""
from __future__ import annotations

import json
import os
from typing import Dict, List


_DEFAULTS: Dict[str, Dict[str, str]] = {
    "macro": {
        "v1": "你是宏观研究员，研判 A 股大盘走势（BULL/BEAR/SIDEWAYS），给出建议仓位上限与流动性判断。",
    },
    "industry": {
        "v1": "你是行业/个股研究员，使用穿透叙事六步法分析 {name}({ticker}, {industry})。",
    },
    "manager": {
        "v1": "你是投资经理，综合宏观与个股报告做组合层决策，给出止损/止盈与调仓指令。",
        "v2": (
            "你是投资经理，负责组合层决策。请基于「风险预算优化」给出目标组合权重 target_portfolio_weights：\n"
            "1) 宏观仓位上限（MacroReport.suggested_max_position）作为总敞口上限；\n"
            "2) 个股评级映射为置信度（BUY=1.0/ADD=0.7/HOLD=0.4/REDUCE=0.15/SELL=0）；\n"
            "3) 行业景气由同行业各标的评级分布聚合；\n"
            "4) 目标权重 = 置信度 × (0.5 + 0.5×行业景气)，单票不超过 single_position_max，合计不超过宏观上限；\n"
            "5) 与当前持仓 diff 产出调仓指令（买入补足 / 卖出降仓）。\n"
            "同时给出个股止损/止盈线。"
        ),
    },
    "risk": {
        "v1": "你是风控，对策略做硬约束+软约束审核，硬违规一票否决，软约束超阈触发例外。",
    },
    "trader": {
        "v1": "你是交易员，把通过的下单策略转为低冲击执行计划并模拟撮合。",
    },
    "reviewer": {
        "v1": "你是审核者，从业务正确性视角审计本轮产出（风控硬卡/组合层/规则自洽）。",
    },
    "replayer": {
        "v1": "你是复盘，把成交落地到组合并更新知识图谱置信度。",
    },
}


class PromptHub:
    def __init__(self, persistence_path: str = ""):
        self._store: Dict[str, Dict[str, str]] = {k: dict(v) for k, v in _DEFAULTS.items()}
        self._latest: Dict[str, str] = {k: max(v.keys()) for k, v in self._store.items()}
        self._path = persistence_path
        if persistence_path and os.path.exists(persistence_path):
            self._load()

    def register(self, name: str, template: str, version: str) -> None:
        self._store.setdefault(name, {})[version] = template
        # 若版本号更大则更新 latest
        cur = self._latest.get(name, "v0")
        if version > cur:
            self._latest[name] = version
        self._persist()

    def get(self, name: str, version: str = "") -> str:
        vers = version or self._latest.get(name)
        return self._store.get(name, {}).get(vers, "")

    def versions(self, name: str) -> List[str]:
        return list(self._store.get(name, {}).keys())

    def render(self, name: str, version: str = "", **kw) -> str:
        return self.get(name, version).format(**kw)

    def _persist(self):
        if self._path:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump({"store": self._store, "latest": self._latest}, f, ensure_ascii=False, indent=2)

    def _load(self):
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                d = json.load(f)
            self._store.update(d.get("store", {}))
            self._latest.update(d.get("latest", {}))
        except Exception:
            pass
