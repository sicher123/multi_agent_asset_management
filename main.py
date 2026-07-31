"""
入口：离线（Mock）端到端跑通「研究 → 投决 → 风控 → 交易 → 复盘 → 评估」。

已接入（P4-P6）：
- 动态知识图谱（KGExtractor/GraphRAGRetriever/KGUpdater）
- Harness（DataValidator / PromptHub / 可观测性 / 成本护栏 / 注入防护 / HITL）
- PaperTrader 模拟撮合 + 回测引擎 + 系统级 Evaluator（对基准）
- 监控面板（HTML/JSON）+ 调度钩子 + KG sqlite 持久化

运行：
    cd multi_agent_research
    python main.py                      # Mock 端到端（HITL 离线自动放行）
    python main.py -q                   # 安静模式（不打印 trace）
切换到真实模型/数据：把 config/settings.yaml 的 llm.provider 改为 openai、data.provider 改为 tdx。
"""
from __future__ import annotations

import os
import sys
from datetime import date

from core.config import Settings
from core.llm import get_llm
from core.context import AppContext
from data.provider import get_provider
from risk.engine import RiskEngine
from state.schemas import Portfolio, Holding, Market, BoardType

from kg.store import KnowledgeGraph
from kg.extractor import KGExtractor
from kg.retrieval import GraphRAGRetriever
from kg.updater import KGUpdater
from harness.prompthub import PromptHub
from harness.observability import Tracer, Metrics
from harness.cost_guard import CostGuard
from harness.injection_guard import InjectionGuard
from harness.datavalidator import DataValidator
from harness.traced_llm import TracedLLM
from backtest.paper_trader import PaperTrader
from backtest.engine import BacktestEngine
from eval.evaluator import Evaluator
from monitor.monitor import Monitor
from scheduler.scheduler import Scheduler, SchedContext, ConsolePushSink, FilePushSink, MultiPushSink
from scheduler.jobs import make_default_jobs, run_research_for_ticker
from graph.builder import build_app
import strategies.registry as _  # 触发策略注册

# —— HITL 断点 resume（仅 --hitl 且装有 langgraph 时生效）——
try:
    from langgraph.errors import GraphInterrupt as _GraphInterrupt
except Exception:  # pragma: no cover
    try:
        from langgraph.types import GraphInterrupt as _GraphInterrupt
    except Exception:
        class _GraphInterrupt(Exception):
            pass
try:
    from langgraph.types import Command as _LGCommand
except Exception:  # pragma: no cover
    _LGCommand = None


def _build_initial_portfolio(settings: Settings) -> Portfolio:
    holdings = [
        Holding(ticker=h.ticker, name=h.name, industry=h.industry, quantity=h.quantity,
                cost_price=h.cost_price, last_price=h.cost_price, board=h.board)
        for h in settings.initial_holdings
    ]
    return Portfolio(cash=settings.initial_cash, holdings=holdings)


def _build_catalog(settings: Settings, data) -> dict:
    cat = {}
    for w in settings.watchlist:
        t = w["ticker"]
        q = data.get_quote(t)
        cat[t] = {"name": q.name, "industry": w.get("industry", "未知")}
    return cat


def _build_context(settings: Settings, llm, data, tracer, cost, kg) -> "AppContext":
    """构造完整 AppContext（供 main 与端到端测试复用，避免 ctx 构造逻辑腐烂）。"""
    catalog = _build_catalog(settings, data)
    return AppContext(
        settings=settings,
        llm=llm,
        data=data,
        risk_engine=RiskEngine(),
        strategy_name=settings.default_strategy,
        # —— P4 知识图谱 ——
        kg=kg,
        extractor=KGExtractor(catalog=catalog),
        retriever=GraphRAGRetriever(kg),
        kg_updater=KGUpdater(kg),
        # —— P5 Harness ——
        prompthub=PromptHub(),
        tracer=tracer,
        metrics=Metrics(),
        cost_guard=cost,
        injection=InjectionGuard(),
        validator=DataValidator(),
        # —— P6 完善 ——
        paper_trader=PaperTrader(data),
        backtest=BacktestEngine(data),
        evaluator=Evaluator(),
    )


def _build_benchmark_portfolio(settings: Settings, data, tickers, total_cash: float):
    """等权买入持有基准（用同样初始现金均分建仓）。"""
    if not tickers:
        return Portfolio(cash=total_cash, holdings=[])
    per_cash = total_cash / len(tickers)
    holdings = []
    for t in tickers:
        q = data.get_quote(t)
        price = q.last or 10.0
        qty = int(per_cash / price)
        if qty > 0:
            holdings.append(Holding(ticker=t, name=q.name, industry=q.industry,
                                    quantity=qty, cost_price=price, last_price=price,
                                    board=BoardType.MAIN))
    return Portfolio(cash=total_cash - sum(h.quantity * h.cost_price for h in holdings),
                     holdings=holdings)


def _prompt_human() -> dict:
    """读取真人审批输入，返回 human_approve 期望的 resume 结构。"""
    raw = input("  是否通过？(y/n): ").strip().lower()
    return {"approve": raw in ("y", "yes", "1", "t", "true")}


def _run_app(app, init_state: dict, cfg: dict, hitl_enabled: bool) -> dict:
    """invoke，并在 --hitl 模式下对真人断点逐个询问并 resume。

    - 无 --hitl（默认）：human_approve 自动放行，单次 invoke 直达 END。
    - 有 --hitl 且装了 langgraph：风控/下单等节点的 interrupt 会暂停，逐个询问真人后 resume，
      直到流程走到 END。LocalRunner（无 langgraph）永不在节点内中断。
    """
    try:
        return app.invoke(init_state, config=cfg)
    except _GraphInterrupt as e:
        if not hitl_enabled:
            raise
        for it in (getattr(e, "interrupts", None) or []):
            q = it.get("question") if isinstance(it, dict) else str(it)
            print(f"\n[HITL 真人断点] {q}")
        while True:
            try:
                return app.invoke(_LGCommand(resume=_prompt_human()), config=cfg)
            except _GraphInterrupt as e2:
                for it in (getattr(e2, "interrupts", None) or []):
                    q = it.get("question") if isinstance(it, dict) else str(it)
                    print(f"\n[HITL 真人断点] {q}")
                continue


def _post_process(final: dict, ctx: AppContext, sched: Scheduler) -> dict:
    """回测 + 评估 + 监控 + KG 持久化 + 调度钩子（真实写库/推送）。"""
    fills = final.get("fills", [])
    traded = []
    for f in fills:
        if f.ticker not in traded:
            traded.append(f.ticker)

    eval_result = None
    if traded:
        price_paths = ctx.backtest.gen_price_paths(traded, days=20)
        strategy_equity = ctx.backtest.replay(final["portfolio"], fills, price_paths)
        bench = _build_benchmark_portfolio(ctx.settings, ctx.data, traded, ctx.settings.initial_cash)
        bench_equity = ctx.backtest.replay(bench, [], price_paths)
        eval_result = ctx.evaluator.evaluate(strategy_equity, bench_equity, label="投研策略")
        final.setdefault("monitor", {})["eval"] = eval_result
        final["monitor"]["strategy_equity"] = strategy_equity
        final["monitor"]["benchmark_equity"] = bench_equity

    # KG 持久化
    os.makedirs("run_artifacts", exist_ok=True)
    ctx.kg.save_sqlite("run_artifacts/kg.sqlite")

    # 监控面板
    monitor = Monitor()
    summary = monitor.collect(final, extra={"cost": ctx.cost_guard.status()})
    monitor.render_html(summary, "run_artifacts/monitor.html")
    monitor.render_json(summary, "run_artifacts/monitor.json")

    # 调度：收盘后钩子（真实写库 + 推送 + 运行台账）
    sched.trigger("post_market", final)
    return final


def _demo_scheduler(sched: Scheduler, sctx: SchedContext, final: dict) -> None:
    """演示调度器的定时与事件能力（离线可跑，复现真实部署行为）。

    - run_due_jobs：用合成时刻证明 pre_market(09:00)/weekly(周五17:00) 按时触发；
      定时演示使用独立临时状态目录，避免污染主调度的持久化（主流程 post_market 真实触发需去重）。
    - notify_event + drain_research：模拟「重大事件」入队并真实复跑行业研究员重研。
    """
    import shutil
    import tempfile
    from datetime import datetime

    print("==================== 调度器能力演示 ====================")
    # 1) 定时触发演示：独立临时 scheduler，每次运行都能稳定展示触发且不影响主状态
    tmp = tempfile.mkdtemp(prefix="sched_demo_")
    try:
        sctx_demo = SchedContext(ctx=sctx.ctx, run_dir=tmp, push=sctx.push)
        sdemo = Scheduler(sctx_demo)
        for job in make_default_jobs(sctx_demo):
            sdemo.register_job(job)
        due = sdemo.run_due_jobs(datetime(2027, 1, 4, 9, 0), payload=final)
        print(f"[调度] 盘前定时触发 -> {[d['job_id'] for d in due] or '无'}")
        due = sdemo.run_due_jobs(datetime(2027, 1, 8, 17, 0), payload=final)
        print(f"[调度] 周度定时触发 -> {[d['job_id'] for d in due] or '无'}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    # 2) 重大事件触发重研（沿用主 sched）
    # 先把事件监控员产出的重大事件注入调度队列（若命中），再演示一个显式事件
    for e in final.get("market_events", []):
        if e.material:
            sched.notify_event(e.ticker, e.kind, e.headline)
    ev = sched.notify_event("600519", "earnings", "贵州茅台发布半年报，营收超预期")
    print(f"[事件] {ev.ticker} {ev.kind} material={ev.material} 已入队重研")
    results = sched.drain_reresearch(lambda ticker: run_research_for_ticker(sctx, ticker))
    for r in results:
        if "error" in r:
            print(f"   重研 {r['ticker']} 失败: {r['error']}")
        else:
            s = r["result"]
            print(f"   重研 {r['ticker']} -> 评级={s['first_rating']} 目标价={s['first_target']} "
                  f"DCF={s['dcf_used']} KG写入={s['kg_writes']}")
    print("--------------------------------------------------------")
def _print_summary(s: dict, sched: "Scheduler | None" = None) -> None:
    print("\n==================== 投研闭环结果 ====================")
    if s.get("macro_report"):
        m = s["macro_report"]
        print(f"[宏观] 趋势={m.trend.value} 建议仓位上限={m.suggested_max_position:.0%}")
    for r in s.get("analyst_reports", []):
        print(f"[个股] {r.name}({r.ticker}) 评级={r.rating.value} 目标价={r.target_price} "
              f"DCF合理价={r.dcf_fair_price} 隐含L={r.dcf_implied_L} dcf_used={r.dcf_used} 数据质量={r.data_quality_ok}")
    md = s.get("manager_decision")
    if md:
        n_buy = sum(1 for o in md.rebalance_orders if o.side.value == "BUY")
        n_sell = sum(1 for o in md.rebalance_orders if o.side.value == "SELL")
        print(f"[投决] worth_trading={md.worth_trading} 指令数={len(md.rebalance_orders)}（买{n_buy}/卖{n_sell}）"
              f" 止损={md.stop_loss_pct:.0%} 止盈={md.take_profit_pct:.0%}")
        if md.target_portfolio_weights:
            tw = " ".join(f"{t}:{w:.1%}" for t, w in md.target_portfolio_weights.items())
            print(f"      目标权重(风险预算)={tw}")
        if md.industry_sentiment:
            sn = " ".join(f"{k}:{v:.2f}" for k, v in md.industry_sentiment.items())
            print(f"      行业景气={sn}")
    risk = s.get("risk_result")
    if risk:
        print(f"[风控] 结论={risk.decision.value} 循环次数={s.get('loop_count',0)} "
              f"人工审核={s.get('risk_review_approved')} 例外放行={s.get('human_decision')} 下单确认={s.get('pre_trade_approved')}")
        if risk.hard_violations:
            print("   硬约束违规:", "; ".join(risk.hard_violations))
    comp = s.get("compliance_result")
    if comp:
        hits = comp.restricted_hits + comp.insider_hits
        print(f"[合规] 通过={comp.passed} 硬违规={hits or '无'} 举牌披露={comp.disclosure_hits or '无'}")
    qs = s.get("quant_signals")
    if qs:
        print("[量化] " + " ".join(f"{t}:{sig.quant_view}" for t, sig in qs.items()))
    evs = s.get("market_events")
    if evs:
        print(f"[事件] 命中 {len(evs)} 条（重大 {sum(1 for e in evs if e.material)}）")
    if s.get("execution_plan"):
        ep = s["execution_plan"]
        print(f"[交易] 策略={ep.strategy_type.value} 拆单段数={len(ep.slices)} 预估冲击={ep.estimated_market_impact_pct:.2%}")
        if s.get("execution_memo"):
            print(f"[执行台] {s['execution_memo']}")
    print(f"[投委会] 终审通过={s.get('ic_gate_approved')}")
    for f in s.get("fills", []):
        print(f"   成交 {f.ticker} {f.side.value} {f.filled_qty}@{f.avg_price} 滑点={f.slippage} 冲击={f.impact_cost}")
    pf = s.get("portfolio")
    if pf:
        print(f"[组合] 净值={pf.total_value:,.0f} 现金={pf.cash:,.0f} 持仓数={len(pf.holdings)}")
    attr = s.get("attribution_report")
    if attr:
        print(f"[归因] {attr.summary}")
    ev = s.get("monitor", {}).get("eval")
    if ev:
        print(f"[评估] 超额收益={ev['excess']['total_return']:+.2%} 战胜基准={ev['beat_benchmark']} "
              f"(策略{ev['strategy']['total_return']:+.2%} vs 基准{ev['benchmark']['total_return']:+.2%})")
    print("[知识图谱]", s.get("kg_stats", {}))
    print("[审计]")
    for a in s.get("audit_log", []):
        print(f"   - {a.actor}/{a.stage}: {a.detail}")
    if sched:
        fired = sched.fired_jobs
        ok = sum(1 for r in fired if r.get("ok"))
        print(f"[调度] 触发钩子 {len(fired)} 次（成功 {ok}）："
              + ", ".join(f"{r['job_id']}" for r in fired) if fired else "[调度] 本轮无触发")
        q = sched.reresearch_queue
        if q:
            print(f"   重研队列残留: {[e.ticker for e in q]}")
    print("====================================================\n")


def main():
    settings = Settings.load("config/settings.yaml")
    silent = "-q" in sys.argv
    # 无人值守 Demo 默认关闭 HITL 中断（自动放行）；传 --hitl 才进入真人断点
    settings.hitl_enabled = "--hitl" in sys.argv
    os.makedirs("run_artifacts", exist_ok=True)

    raw_llm = get_llm(settings)
    tracer = Tracer(log_path="run_artifacts/trace.jsonl", silent=silent)
    cost = CostGuard(model="mock" if settings.llm.provider == "mock" else settings.llm.model, budget=5.0)
    llm = TracedLLM(raw_llm, tracer, cost, actor="system")
    data = get_provider(settings)
    kg = KnowledgeGraph()
    ctx = _build_context(settings, llm, data, tracer, cost, kg)

    # —— 调度器（冲刺项⑤）：推送通道(控制台+文件) + 上下文 + 默认真实钩子 ——
    push = MultiPushSink([
        ConsolePushSink(),
        FilePushSink(os.path.join("run_artifacts", "push_log.jsonl")),
    ])
    sctx = SchedContext(ctx=ctx, run_dir="run_artifacts", push=push)
    sched = Scheduler(sctx)
    for job in make_default_jobs(sctx):
        sched.register_job(job)

    app = build_app(ctx)
    init_state = {
        "run_id": "run-001",
        "as_of_date": date.today().isoformat(),
        "market": Market.A_SHARE,
        "portfolio": _build_initial_portfolio(settings),
        "risk_limits": settings.risk_limits,
        "loop_count": 0,
    }
    cfg = {"configurable": {"thread_id": init_state["run_id"]}}
    final = _run_app(app, init_state, cfg, settings.hitl_enabled)

    final = _post_process(final, ctx, sched)
    _demo_scheduler(sched, sctx, final)
    _print_summary(final, sched)

    tracer.event("system", "run.end", kg_entities=kg.stats()["entities"],
                 kg_relations=kg.stats()["relations"])
    tracer.close()
    return final


if __name__ == "__main__":
    main()
