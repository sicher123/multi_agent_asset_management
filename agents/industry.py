"""
行业/个股研究员：复用「穿透叙事」分析法，并把产出沉淀到动态知识图谱。

P4/P5 增强：
- 用 DataValidator 校验基本面与报告（prompt.txt 红线），结论写入 data_quality_ok。
- 新闻/公告先做提示注入清洗（InjectionGuard），再抽取三元组写回 KG。
- 个股报告结构化抽取为实体-关系，增量写回 KG（供 GraphRAG 检索）。
- system prompt 走 PromptHub（版本化）。

冲刺项① 真实 DCF：
- 当能取到市值与前3年一致预期业绩时，调用 research.dcf.run_dcf
  （底层为 skill 的 dcf_implied.py）真实反算：
    · dcf_implied_L  = 市场隐含终局利润（亿元）
    · dcf_fair_price = 中枢折现率下每股合理价（元），作为目标价的依据
    · rating 由 DCF 上行空间推导，保证报告数字自洽
- 真实数据缺失（如 TdxProvider 未实现）时优雅降级为锚定目标价，dcf_used=False。
"""
from __future__ import annotations

from typing import Dict, Any, List

from core.context import AppContext
from state.schemas import AnalystReport, UniverseEntry, Rating
from research.dcf import DcfAssumptions, run_dcf


def _rating_from_upside(up: float) -> Rating:
    """由 DCF 上行空间推导评级，保证目标价与评级自洽。"""
    if up >= 0.15:
        return Rating.BUY
    if up >= 0.02:
        return Rating.ADD
    if up > -0.10:
        return Rating.HOLD
    return Rating.REDUCE


def run(state: Dict[str, Any], ctx: AppContext) -> Dict[str, Any]:
    reports: List[AnalystReport] = []
    kg_writes = 0
    violations_all: List[str] = []

    for u in state.get("universe", []):
        q = ctx.data.get_quote(u.ticker)
        system = ctx.prompthub.get("industry") if ctx.prompthub else "你是行业研究员，使用穿透叙事六步法"
        prompt = (f"对 {u.name}({u.ticker}, {u.industry}) 做穿透叙事分析："
                  f"当前价 {q.last}，行业 {u.industry}。")
        r: AnalystReport = ctx.llm.structured(
            prompt, system=system, model_cls=AnalystReport)
        r.ticker = u.ticker
        r.name = u.name
        r.industry = u.industry
        r.entry_point = round(q.last * 0.95, 2)
        r.exit_point = round(r.target_price * 1.1, 2)

        # —— 冲刺项①：真实 DCF 反算目标价 ——
        f = ctx.data.get_fundamentals(u.ticker)
        dcf = None
        if f.market_cap > 0 and f.e1 > 0 and f.e2 > 0 and f.e3 > 0:
            dcf = run_dcf(DcfAssumptions(
                ticker=u.ticker, market_cap=f.market_cap,
                e1=f.e1, e2=f.e2, e3=f.e3, current_price=q.last,
                terminal_multiple=ctx.settings.dcf_terminal_multiple,
                central_r=ctx.settings.dcf_central_r,
            ))

        if dcf and dcf.dcf_used:
            r.dcf_used = True
            r.dcf_implied_L = dcf.L_market_implied.get(str(int(ctx.settings.dcf_central_r)), 0.0)
            r.dcf_fair_price = dcf.central_fair_price
            r.dcf_inputs = dcf.dcf_inputs
            r.dcf_sensitivity = dcf.sensitivity
            # 目标价由真实 DCF 合理价驱动（覆盖 LLM 的随机价）
            r.target_price = round(dcf.central_fair_price, 2)
            r.exit_point = round(r.target_price * 1.1, 2)
            up = (r.target_price / q.last - 1.0) if q.last > 0 else 0.0
            r.rating = _rating_from_upside(up)
            r.narrative_summary = (r.narrative_summary or "") + f"\n[DCF] {dcf.note}"
        else:
            # 降级：锚定目标价（真实数据缺失时）
            r.target_price = round(q.last * 1.18, 2)
            r.dcf_used = False
            r.dcf_implied_L = 0.0
            r.dcf_fair_price = 0.0
            r.exit_point = round(r.target_price * 1.1, 2)

        # —— DataValidator 红线校验 ——
        if ctx.validator:
            v = ctx.validator.validate_fundamentals(f) + ctx.validator.validate_report(r, q.last)
            violations_all.extend(v)
            r.data_quality_ok = ctx.validator.ok(v)
        else:
            r.data_quality_ok = True

        reports.append(r)

        # —— 新闻：注入清洗 -> KG 抽取 ——
        if ctx.extractor and ctx.kg_updater and ctx.injection:
            for raw in ctx.data.get_news(u.ticker):
                clean, flags = ctx.injection.scan(raw)
                ents, rels = ctx.extractor.extract_from_text(clean, source=f"news:{u.ticker}")
                if flags:
                    for e in ents:
                        e.confidence *= 0.5   # 被标记文本在 KG 降权
                kg_writes += ctx.kg_updater.apply(ents, rels, source=f"news:{u.ticker}")

        # —— 报告：结构化抽取 -> KG ——
        if ctx.extractor and ctx.kg_updater:
            ents, rels = ctx.extractor.extract_from_report(r)
            kg_writes += ctx.kg_updater.apply(ents, rels, source="analyst_report")

    kg_stats = {"industry_kg_writes": kg_writes,
                "validation_violations": len(violations_all),
                "violations_sample": violations_all[:5]}
    return {"analyst_reports": reports, "kg_stats": kg_stats,
            "log": [f"[industry] 完成 {len(reports)} 份报告，KG 写入 {kg_writes} 条关系",
                    *([f"[industry][DCF] {u.ticker} dcf_used={any(r.dcf_used for r in reports)}"]),
                    *([f"[industry][红线] {v}" for v in violations_all[:5]])]}
