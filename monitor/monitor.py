"""
监控面板（Monitor）：把一轮运行的产物汇总为 JSON + 简单 HTML 仪表盘。

- collect(state, metrics, cost, kg_stats)：汇总本轮核心指标。
- render_html(path)：输出单文件 HTML（内联样式，浏览器直接打开）。
- 真实环境可替换为 Grafana / 企微推送；此处保证离线可见、可交付。
"""
from __future__ import annotations

import html
import json
import os
from typing import Any, Dict


class Monitor:
    def __init__(self):
        self.run_id = ""

    def collect(self, state: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
        self.run_id = state.get("run_id", "na")
        summary: Dict[str, Any] = {
            "run_id": self.run_id,
            "as_of_date": state.get("as_of_date"),
            "loop_count": state.get("loop_count", 0),
            "worth_trading": bool(state.get("manager_decision") and state["manager_decision"].worth_trading),
            "risk_decision": (state.get("risk_result").decision.value if state.get("risk_result") else None),
            "fills": len(state.get("fills", [])),
            "kg_stats": state.get("kg_stats", {}),
            "portfolio": ({"total_value": state["portfolio"].total_value,
                           "cash": state["portfolio"].cash,
                           "holdings": len(state["portfolio"].holdings)}
                          if state.get("portfolio") else None),
            "eval": state.get("monitor", {}).get("eval"),
            "cost": extra.get("cost"),
            "audit_count": len(state.get("audit_log", [])),
        }
        return summary

    def render_html(self, summary: Dict[str, Any], path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        rows = "".join(
            f"<tr><td>{html.escape(str(k))}</td><td>{html.escape(str(v))}</td></tr>"
            for k, v in summary.items()
        )
        doc = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>投研系统监控 - {html.escape(self.run_id)}</title>
<style>
 body{{font-family:-apple-system,'PingFang SC',Segoe UI,sans-serif;margin:24px;color:#222;}}
 h1{{font-size:20px;}} table{{border-collapse:collapse;width:100%;max-width:760px;}}
 td{{border:1px solid #ddd;padding:8px 12px;font-size:14px;}}
 td:first-child{{background:#f6f8fa;font-weight:600;width:220px;}}
 .ok{{color:#0a7d28;}} .warn{{color:#b54708;}}
</style></head><body>
<h1>多 Agent 投研系统 · 运行监控</h1>
<table>{rows}</table>
<p style="color:#888;font-size:12px">由 Monitor 自动生成（离线可交付）。</p>
</body></html>"""
        with open(path, "w", encoding="utf-8") as f:
            f.write(doc)

    def render_json(self, summary: Dict[str, Any], path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
