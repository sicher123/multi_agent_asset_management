"""
宏观研究员：输出大盘走势研判与仓位上限建议，并把宏观因子写回知识图谱。

P4 增强：把宏观趋势实体化，并通过 DRIVEN_BY 关系连到关注的行业（供 GraphRAG 检索行业时回带宏观上下文）。
"""
from __future__ import annotations

from typing import Dict, Any

from core.context import AppContext
from state.schemas import MacroReport
from kg.schema import E, R, Entity, Relation, eid


def run(state: Dict[str, Any], ctx: AppContext) -> Dict[str, Any]:
    macro = ctx.data.get_macro()
    system = ctx.prompthub.get("macro") if ctx.prompthub else "你是宏观研究员"
    prompt = f"日期 {state.get('as_of_date')}。基于宏观数据给出研判。"
    llm_out: MacroReport = ctx.llm.structured(prompt, system=system, model_cls=MacroReport)
    report = MacroReport(
        trend=macro.trend, risk_appetite=llm_out.risk_appetite or macro.risk_appetite,
        suggested_max_position=macro.suggested_max_position,
        liquidity_view=macro.liquidity_view, summary=llm_out.summary or macro.summary,
    )

    kg_writes = 0
    if ctx.kg_updater:
        macro_id = eid(E.MACRO, report.trend.value)
        ents = [Entity(id=macro_id, type=E.MACRO, name=f"宏观:{report.trend.value}",
                       attrs={"risk_appetite": report.risk_appetite,
                              "suggested_max_position": report.suggested_max_position},
                       confidence=1.0, sources=["macro"])]
        rels: list = []
        for w in ctx.settings.watchlist:
            ind_id = eid(E.INDUSTRY, w.get("industry", "未知"))
            rels.append(Relation(src=ind_id, dst=macro_id, type=R.DRIVEN_BY,
                                 confidence=0.8, text=f"{w.get('industry')}受宏观{report.trend.value}驱动",
                                 sources=["macro"]))
        kg_writes = ctx.kg_updater.apply(ents, rels, source="macro")

    return {"macro_report": report,
            "kg_stats": {"macro_kg_writes": kg_writes},
            "log": [f"[macro] 趋势={report.trend.value}，建议仓位上限={report.suggested_max_position:.0%}，KG 写入 {kg_writes}"]}
