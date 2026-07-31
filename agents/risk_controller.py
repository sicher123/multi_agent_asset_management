"""
风险合规（合并岗位）：调用 RiskEngine 做硬约束 + 软约束审核，并调用 ComplianceEngine 做合规审核。

- 风控与合规同属一个岗位、同一节点产出，但合规结论单独留痕（compliance_result）便于审计。
- 合规硬违规（禁投清单/ST、静默期）与风控硬约束同权：命中即升级为 REJECTED，回落投资经理。
- 合规软提示（举牌披露、公平交易）仅留痕，不阻断流程。
- 风控是「确定性硬卡」，不靠 LLM 拍板；合规同样是确定性规则。
"""
from __future__ import annotations

from typing import Dict, Any

from core.context import AppContext
from risk.rules.base import RiskContext
from risk.compliance import ComplianceEngine, ComplianceContext
from state.schemas import RiskDecision


def run(state: Dict[str, Any], ctx: AppContext) -> Dict[str, Any]:
    decision = state.get("manager_decision")
    macro = state.get("macro_report")
    rc = RiskContext(
        decision=decision,
        portfolio=state["portfolio"],
        limits=state["risk_limits"],
        trend=macro.trend if macro else None,
        data=ctx.data,
        analyst_reports=state.get("analyst_reports", []),
        as_of=state.get("as_of_date", ""),
    )
    result = ctx.risk_engine.check(rc)

    # —— 合规审核（与风控同岗位）——
    comp_engine = ComplianceEngine()
    cc = ComplianceContext(
        decision=decision,
        portfolio=state["portfolio"],
        limits=state["risk_limits"],
        compliance_cfg=ctx.settings.compliance,
        data=ctx.data,
        analyst_reports=state.get("analyst_reports", []),
        as_of=state.get("as_of_date", ""),
    )
    compliance_result = comp_engine.check(cc)

    log = [f"[风险合规] 风控结论={result.decision.value}；合规={'通过' if compliance_result.passed else '硬违规'}"]

    # 合规硬违规回灌风控结论（与硬约束同权）
    if not compliance_result.passed:
        result.hard_violations = list(result.hard_violations) + compliance_result.restricted_hits
        result.decision = RiskDecision.REJECTED
        result.requires_human = False
        result.feedback = (result.feedback + "；" if result.feedback else "") + compliance_result.detail
        log.append("[风险合规] 合规硬违规，升级为 REJECTED（回落投资经理优化）")

    update: Dict[str, Any] = {
        "risk_result": result,
        "compliance_result": compliance_result,
        "stage": "risk",
        "log": log,
    }
    if result.decision == RiskDecision.REJECTED:
        update["loop_count"] = state.get("loop_count", 0) + 1
    return update
