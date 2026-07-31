"""
LangGraph 编排：把多角色（研究员/基金经理/风险合规/交易员/投委会/量化/事件监控/归因）组装成状态机。

流程（冲刺项：角色扩编）：
  START -> event_monitor(舆情/事件监控，入口)
  event_monitor -> screener
  screener ->[并行] macro & industry -> quant(量化) -> manager
  manager -(值得?)-> risk(风险合规合并岗位) /  -(不值得)-> reviewer
  risk -> risk_review (风控结论人工审核 HITL，所有结论均确认)
  risk_review -(确认通过)-> 按原风控结论流转：
       APPROVED  -> confirm_trade (下单前 HITL)
       EXCEPTION -> exception_human (例外审批 HITL) -> approve/deny
       REJECTED  -> manager (带风险反馈回落，loop_count++)
  risk_review -(否决)-> reviewer(terminate) 或 manager(rejudge，loop_count++)，由 settings.risk_review.on_reject 决定
  confirm_trade -(通过)-> ic_gate (投委会/终审委员 HITL，重大投决人工终审)
  ic_gate -(通过)-> trader(含算法交易/执行优化) -> attribution(绩效归因) -> reviewer -> replayer -> END
  ic_gate -(否决)-> reviewer(terminate) 或 manager(rejudge，loop_count++)，由 settings.ic_gate.on_reject 决定
  risk -(超循环)-> reviewer            （防死锁：强制终结）

- 首选：LangGraph（支持 checkpointer / HITL / 可视化）。
- 回退：若环境无 langgraph（如离线/受限），用 LocalRunner 复刻同一流转，保证可运行可验证。
- HITL 断点由 harness.hitl.human_approve 提供；无人值守 Demo 用 enabled=False 自动放行。
"""
from __future__ import annotations

from typing import Dict, Any, Tuple

from state.schemas import ResearchState, RiskDecision, AuditEntry

from agents.screener import run as screener_run
from agents.macro import run as macro_run
from agents.industry import run as industry_run
from agents.quant import run as quant_run
from agents.manager import run as manager_run
from agents.risk_controller import run as risk_run
from agents.trader import run as trader_run
from agents.attribution import run as attribution_run
from agents.reviewer import run as reviewer_run
from agents.replayer import run as replayer_run
from agents.event_monitor import run as event_monitor_run

from harness.hitl import human_approve

try:
    from langgraph.graph import StateGraph, END, START
    _HAS_LANGGRAPH = True
except Exception:  # pragma: no cover - 离线回退
    _HAS_LANGGRAPH = False


def _bind(fn, ctx):
    def node(state: Dict[str, Any]) -> Dict[str, Any]:
        return fn(state, ctx)
    return node


# 列表型字段用 reducer 累加（与 LangGraph Annotated 语义一致）
_LIST_FIELDS = {"log", "audit_log", "analyst_reports", "fills"}
_DICT_FIELDS = {"kg_stats", "monitor"}


# ----------------------------- HITL 节点 -----------------------------
def _exception_human(state: Dict[str, Any], ctx) -> Dict[str, Any]:
    q = "风控触发软约束例外，是否人工放行？"
    approved = human_approve(q, default=True, enabled=ctx.settings.hitl_enabled)
    return {
        "human_decision": approved,
        "log": [f"[hitl] 例外审批={'通过' if approved else '拒绝'}"],
        "audit_log": [AuditEntry(stage="hitl_exception", actor="human",
                                  action="approve" if approved else "deny", detail=q)],
    }


def _confirm_trade(state: Dict[str, Any], ctx) -> Dict[str, Any]:
    q = "策略已通过风控与投委会，是否确认下单？"
    approved = human_approve(q, default=True, enabled=ctx.settings.hitl_enabled)
    return {
        "pre_trade_approved": approved,
        "log": [f"[hitl] 下单前确认={'通过' if approved else '拒绝'}"],
        "audit_log": [AuditEntry(stage="hitl_pretrade", actor="human",
                                 action="approve" if approved else "deny", detail=q)],
    }


def _risk_review(state: Dict[str, Any], ctx) -> Dict[str, Any]:
    """风控结论人工审核（HITL）：对所有风控结论（APPROVED/REJECTED/EXCEPTION）做人工确认。

    - 确认通过：保留原风控结论，后续按 _route_risk 原逻辑流转。
    - 否决：按 settings.risk_review.on_reject 决定去向：
        * terminate -> 直接终结进审核者（reviewer）；
        * rejudge   -> 回落投资经理重研判（loop_count++，防死锁）。
    无人值守/离线 Demo（hitl_enabled=False）自动放行。
    """
    risk = state.get("risk_result")
    decision = risk.decision.value if risk else "UNKNOWN"
    hv = "; ".join(risk.hard_violations) if (risk and risk.hard_violations) else "无"
    soft = ("; ".join(f"{m.name}={m.value}" for m in risk.soft_findings)
            if (risk and risk.soft_findings) else "无")
    fb = risk.feedback if risk else ""
    on_reject = ctx.settings.risk_review.on_reject
    q = (f"【风控人工审核】风控结论={decision}。\n"
         f"  硬约束违规：{hv}\n"
         f"  软约束发现：{soft}\n"
         f"  给投决的反馈：{fb}\n"
         f"是否确认采纳该风控结论？"
         f"（通过=按结论继续流转；拒绝={'回落投资经理重研判' if on_reject=='rejudge' else '直接终结进审核者'}）")
    approved = human_approve(q, default=True, enabled=ctx.settings.hitl_enabled)
    update: Dict[str, Any] = {
        "risk_review_approved": approved,
        "stage": "risk_review",
        "log": [f"[hitl] 风控人工审核={'通过' if approved else '拒绝'}"],
        "audit_log": [AuditEntry(stage="hitl_risk_review", actor="human",
                                 action="approve" if approved else "deny", detail=q)],
    }
    if (not approved) and on_reject == "rejudge":
        update["loop_count"] = state.get("loop_count", 0) + 1
    return update


def _ic_materiality(state: Dict[str, Any], ctx) -> Tuple[bool, str]:
    """判断本次投决是否达到投委会终审门槛（重大）。"""
    md = state.get("manager_decision")
    pf = state.get("portfolio")
    mat = ctx.settings.ic_gate.materiality
    reasons: list = []
    if md:
        max_w = max((w for w in md.target_portfolio_weights.values()), default=0.0)
        if max_w > mat.single_position_weight + 1e-9:
            reasons.append(f"单票目标权重 {max_w:.1%} 超 {mat.single_position_weight:.0%}")
        tv = (pf.total_value if pf else None) or 1.0
        dev = 0.0
        if pf:
            for tk, tw in md.target_portfolio_weights.items():
                dev += abs(tw - pf.weight_of(tk))
        if dev > mat.total_deviation + 1e-9:
            reasons.append(f"调仓总偏离 {dev:.1%} 超 {mat.total_deviation:.0%}")
    risk = state.get("risk_result")
    if risk and risk.decision == RiskDecision.EXCEPTION and mat.exception_is_material:
        reasons.append("风控结论为 EXCEPTION（例外）")
    return (len(reasons) > 0, "；".join(reasons) or "常规投决，未触发重大门槛")


def _ic_gate(state: Dict[str, Any], ctx) -> Dict[str, Any]:
    """投委会/终审委员（HITL）：对重大投决做人工终审。

    - 非重大：自动过会（无需真人），留痕为 system/auto_pass。
    - 重大：human_approve 真人终审；通过→下单，否决→按 settings.ic_gate.on_reject 回落/终结。
    无人值守/离线 Demo（hitl_enabled=False）重大项也自动放行。
    """
    material, desc = _ic_materiality(state, ctx)
    on_reject = ctx.settings.ic_gate.on_reject
    if not material:
        return {
            "ic_gate_approved": True,
            "stage": "ic_gate",
            "log": [f"[hitl] 投委会：非重大（{desc}），自动过会"],
            "audit_log": [AuditEntry(stage="hitl_ic", actor="system", action="auto_pass", detail=desc)],
        }
    q = (f"【投委会终审】重大投决：{desc}\n是否批准执行？"
         f"（通过=下单；拒绝={'回落投资经理重研判' if on_reject=='rejudge' else '直接终结进审核者'}）")
    approved = human_approve(q, default=True, enabled=ctx.settings.hitl_enabled)
    update: Dict[str, Any] = {
        "ic_gate_approved": approved,
        "stage": "ic_gate",
        "log": [f"[hitl] 投委会终审={'通过' if approved else '拒绝'}（{desc}）"],
        "audit_log": [AuditEntry(stage="hitl_ic", actor="human",
                                 action="approve" if approved else "deny", detail=q)],
    }
    if (not approved) and on_reject == "rejudge":
        update["loop_count"] = state.get("loop_count", 0) + 1
    return update


# ----------------------------- 路由 -----------------------------
def _route_manager(state: Dict[str, Any]) -> str:
    md = state.get("manager_decision")
    return "risk" if (md and md.worth_trading) else "reviewer"


def _route_risk(state: Dict[str, Any], max_loop: int) -> str:
    risk = state.get("risk_result")
    loop = state.get("loop_count", 0)
    if risk and risk.decision == RiskDecision.APPROVED:
        return "confirm_trade"
    if risk and risk.decision == RiskDecision.EXCEPTION:
        return "exception_human"
    if loop >= max_loop:
        return "reviewer"          # 防死锁：超循环上限强制终结
    return "manager"               # 驳回回落，带风险反馈


def _route_exception(state: Dict[str, Any]) -> str:
    return "confirm_trade" if state.get("human_decision") else "reviewer"


def _route_confirm(state: Dict[str, Any]) -> str:
    return "ic_gate" if state.get("pre_trade_approved") else "reviewer"


def _route_risk_review(state: Dict[str, Any], ctx) -> str:
    """风控人工审核后的路由。"""
    if state.get("risk_review_approved"):
        return _route_risk(state, ctx.settings.max_loop)
    if ctx.settings.risk_review.on_reject == "terminate":
        return "reviewer"
    if state.get("loop_count", 0) >= ctx.settings.max_loop:
        return "reviewer"          # 防死锁：否决重研超上限强制终结
    return "manager"               # 否决后回落投资经理重研判


def _route_ic(state: Dict[str, Any], ctx) -> str:
    """投委会终审后的路由。"""
    if state.get("ic_gate_approved"):
        return "trader"
    if ctx.settings.ic_gate.on_reject == "terminate":
        return "reviewer"
    if state.get("loop_count", 0) >= ctx.settings.max_loop:
        return "reviewer"          # 防死锁：否决重研超上限强制终结
    return "manager"               # 否决后回落投资经理重研判


def build_graph(ctx):
    """LangGraph 版。"""
    g = StateGraph(ResearchState)
    g.add_node("event_monitor", _bind(event_monitor_run, ctx))
    g.add_node("screener", _bind(screener_run, ctx))
    g.add_node("macro", _bind(macro_run, ctx))
    g.add_node("industry", _bind(industry_run, ctx))
    g.add_node("quant", _bind(quant_run, ctx))
    g.add_node("manager", _bind(manager_run, ctx))
    g.add_node("risk", _bind(risk_run, ctx))
    g.add_node("risk_review", _bind(_risk_review, ctx))
    g.add_node("exception_human", _bind(_exception_human, ctx))
    g.add_node("confirm_trade", _bind(_confirm_trade, ctx))
    g.add_node("ic_gate", _bind(_ic_gate, ctx))
    g.add_node("trader", _bind(trader_run, ctx))
    g.add_node("attribution", _bind(attribution_run, ctx))
    g.add_node("reviewer", _bind(reviewer_run, ctx))
    g.add_node("replayer", _bind(replayer_run, ctx))

    g.add_edge(START, "event_monitor")
    g.add_edge("event_monitor", "screener")
    g.add_edge("screener", "macro")
    g.add_edge("screener", "industry")
    g.add_edge("industry", "quant")
    g.add_edge("quant", "manager")
    g.add_edge("macro", "manager")

    g.add_conditional_edges("manager", _route_manager,
                            {"risk": "risk", "reviewer": "reviewer"})
    # 风控结论先经人工审核（所有结论），再按审核结果路由
    g.add_edge("risk", "risk_review")
    g.add_conditional_edges("risk_review", lambda s: _route_risk_review(s, ctx),
                            {"confirm_trade": "confirm_trade", "exception_human": "exception_human",
                             "manager": "manager", "reviewer": "reviewer"})
    g.add_conditional_edges("exception_human", _route_exception,
                            {"confirm_trade": "confirm_trade", "reviewer": "reviewer"})
    g.add_conditional_edges("confirm_trade", _route_confirm,
                            {"ic_gate": "ic_gate", "reviewer": "reviewer"})
    g.add_conditional_edges("ic_gate", lambda s: _route_ic(s, ctx),
                            {"trader": "trader", "manager": "manager", "reviewer": "reviewer"})
    g.add_edge("trader", "attribution")
    g.add_edge("attribution", "reviewer")
    g.add_edge("reviewer", "replayer")
    g.add_edge("replayer", END)
    return g


class LocalRunner:
    """离线回退执行器：复刻 build_graph 的流转（无 langgraph 依赖）。"""

    def __init__(self, ctx):
        self.ctx = ctx
        self._nodes = {
            "event_monitor": _bind(event_monitor_run, ctx),
            "screener": _bind(screener_run, ctx),
            "macro": _bind(macro_run, ctx),
            "industry": _bind(industry_run, ctx),
            "quant": _bind(quant_run, ctx),
            "manager": _bind(manager_run, ctx),
            "risk": _bind(risk_run, ctx),
            "risk_review": _bind(_risk_review, ctx),
            "exception_human": _bind(_exception_human, ctx),
            "confirm_trade": _bind(_confirm_trade, ctx),
            "ic_gate": _bind(_ic_gate, ctx),
            "trader": _bind(trader_run, ctx),
            "attribution": _bind(attribution_run, ctx),
            "reviewer": _bind(reviewer_run, ctx),
            "replayer": _bind(replayer_run, ctx),
        }

    @staticmethod
    def _merge(state: Dict[str, Any], upd: Dict[str, Any]) -> None:
        for k, v in upd.items():
            if k in _LIST_FIELDS and isinstance(v, list):
                state.setdefault(k, []).extend(v)
            elif k in _DICT_FIELDS and isinstance(v, dict):
                d = state.setdefault(k, {})
                d.update(v)
            else:
                state[k] = v

    def invoke(self, init_state: Dict[str, Any], config=None) -> Dict[str, Any]:
        state: Dict[str, Any] = dict(init_state)
        for f in _LIST_FIELDS:
            state.setdefault(f, [])
        for f in _DICT_FIELDS:
            state.setdefault(f, {})
        call = lambda n: self._merge(state, self._nodes[n](state))

        # —— 入口：舆情/事件监控 -> 筛选 -> 宏观/行业 -> 量化 -> 投决 ——
        call("event_monitor")
        call("screener")
        call("macro")
        call("industry")
        call("quant")
        call("manager")

        guard = 0
        max_loop = self.ctx.settings.max_loop
        while guard < max_loop + 5:
            md = state.get("manager_decision")
            if not (md and md.worth_trading):
                break
            call("risk")
            risk = state.get("risk_result")
            if risk is None:
                break
            # —— 风控人工审核（HITL）：所有风控结论先经人工确认 ——
            call("risk_review")
            if not state.get("risk_review_approved"):
                rr = self.ctx.settings.risk_review
                if rr.on_reject == "terminate":
                    break  # 否决即直接终结进审核者
                # rejudge：回落投资经理重研判（loop_count 已在 risk_review 节点自增）
                if state.get("loop_count", 0) >= max_loop:
                    break  # 防死锁
                call("manager")
                guard += 1
                continue
            # 人工确认通过：按原风控结论流转
            if risk.decision == RiskDecision.APPROVED:
                call("confirm_trade")
                if not state.get("pre_trade_approved"):
                    break
                # 投委会/终审委员（HITL 重大投决人工终审）
                call("ic_gate")
                if not state.get("ic_gate_approved"):
                    if not _ic_deny_continue(state, self.ctx):
                        break
                    call("manager")
                    guard += 1
                    continue
                call("trader")
                call("attribution")
                break
            if risk.decision == RiskDecision.EXCEPTION:
                call("exception_human")
                if not state.get("human_decision"):
                    break
                call("confirm_trade")
                if not state.get("pre_trade_approved"):
                    break
                call("ic_gate")
                if not state.get("ic_gate_approved"):
                    if not _ic_deny_continue(state, self.ctx):
                        break
                    call("manager")
                    guard += 1
                    continue
                call("trader")
                call("attribution")
                break
            # REJECTED：回落投资经理重研判（带风险反馈，loop_count 已在 risk 节点自增）
            if state.get("loop_count", 0) >= max_loop:
                break  # 防死锁
            call("manager")
            guard += 1

        call("reviewer")
        call("replayer")
        return state


def _ic_deny_continue(state: Dict[str, Any], ctx) -> bool:
    """投委会否决后的去向判定：返回 True 表示回落经理重研（继续循环），False 表示终结。

    loop_count 已在 _ic_gate 节点（rejudge 时）自增，这里据此判断是否超循环。
    """
    ic = ctx.settings.ic_gate
    if ic.on_reject == "terminate":
        return False
    return state.get("loop_count", 0) < ctx.settings.max_loop


def build_app(ctx):
    """返回可 .invoke() 的应用：优先 LangGraph，否则 LocalRunner。"""
    if _HAS_LANGGRAPH:
        from core.checkpointer import build_checkpointer
        return build_graph(ctx).compile(checkpointer=build_checkpointer("memory"))
    return LocalRunner(ctx)
