"""
提示注入 / 数据投毒防护（Injection Guard）。

系统会摄入大量外部文本（新闻/公告/研报/网页）。恶意文本可能试图：注入指令劫持 Agent、
污染知识图谱、伪造关系。本模块在「文本进入 LLM / 进入 KG 抽取」前做检测与中性化。

- scan(text)：返回 (清洗后文本, 命中的风险标记列表)。
- 仅检测+标记+轻量中性化（用引号包裹疑似指令段），不静默删除内容（保留可审计）。
- 置信度层面：被标记的文本在 KG 抽取时降权（由调用方处理）。
"""
from __future__ import annotations

import re
from typing import List, Tuple

# 注入模式（中英文，覆盖常见越权指令）
_PATTERNS = [
    (r"忽略(之前|上文|上述|以上|先前)的?(所有)?(指令|提示|规则|要求)", "ignore_prior_instructions"),
    (r"ignore (the )?(previous|above|prior|all) (instructions|prompts|rules)", "ignore_prior_instructions"),
    (r"你(现在)?(必须|应该|要|得)?(扮演|假装|模拟)成?", "role_play_injection"),
    (r"pretend (to be|you are)", "role_play_injection"),
    (r"</?system>", "system_tag_injection"),
    (r"system\s*:", "system_label_injection"),
    (r"助手\s*:", "assistant_label_injection"),
    (r"(作为|成为|假装|扮演|模拟)成?(一个|一名)?(开发者|管理员|root|超级用户)", "privilege_escalation"),
    (r"(disregard|forget|bypass|override) (the |your )?(safety|risk|constraint|limit)", "constraint_bypass"),
    (r"把这些(关系|实体|数据)标记为(高置信|真实|权威)", "kg_poisoning_mark"),
    (r"(强制|务必|一定)?(把|将)\s*.{0,12}\s*(加入|写入|设为)\s*(知识图谱|图谱|kg)", "kg_poisoning_write"),
]


class InjectionGuard:
    def __init__(self):
        self._compiled = [(re.compile(p, re.IGNORECASE), tag) for p, tag in _PATTERNS]

    def scan(self, text: str) -> Tuple[str, List[str]]:
        flags: List[str] = []
        clean = text
        for rx, tag in self._compiled:
            for m in rx.finditer(text):
                flags.append(tag)
                # 轻量中性化：把命中的指令段用方括号标注，降低其对 LLM 的「指令性」
                seg = m.group(0)
                clean = clean.replace(seg, f"「疑似注入:{seg}」")
        return clean, list(dict.fromkeys(flags))  # 去重保序

    def is_suspicious(self, text: str) -> bool:
        return any(rx.search(text) for rx, _ in self._compiled)
