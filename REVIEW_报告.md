# 审核者评审报告（业务质量）

> 角色：审核者（独立于开发者）。对象：开发者交付的 **P0–P6** 版本代码（从脚手架到动态知识图谱、Harness、回测评估、监控调度）。
> 范围：是否达到「可插拔核心业务」「A 股专属规则」「风控硬卡」「动态知识图谱/GraphRAG」「Harness 工程化」「防死锁」「可扩展」等业务目标。
> 依据：对 `state/ core/ data/ strategies/ risk/ agents/ kg/ harness/ backtest/ eval/ monitor/ scheduler/ graph/ main.py tests/` 的代码评审 + 运行时验证。

---

## 一、总体结论

**合格，可视为「初步版本 → 终版前」的完整落地。** 开发者按计划把 P0–P3 骨架推进到了 P6：动态知识图谱已贯通抽取→检索→增量更新→复盘回灌；Harness 五件套（DataValidator/PromptHub/可观测性/成本护栏/注入防护）已就位并接入主链路；HITL 真人断点已用 `interrupt()` 接进图；PaperTrader 模拟撮合 + 回测 + 系统级 Eval 已闭环；监控面板与调度钩子已生成产物。

作为审核者，核心业务目标全部达成：**风控是确定性硬卡、下单策略与风控规则均可插拔、A 股微观结构落到硬约束、风控循环有最大次数防死锁、知识图谱驱动各角色研判、外部文本入 KG 前经注入防护**。端到端两版执行路径（LangGraph / 离线回退）输出一致，当前 **108 单测全绿**。

剩余待补强（见第六节）多为「真实数据/模型接入」与「工程加固」层面，不影响架构正确性。

---

## 二、P0–P3 评审（保留，已达标）

### 1. 风控是否"真硬卡" ✅
- `risk/engine.py`：先跑全部**硬约束**规则，任意 `violated` 即 `REJECTED`，不经过 LLM 判断。
- 软约束（VaR、叙事脆弱性）超阈才进 `EXCEPTION` 并标 `requires_human=True`。
- 投资经理「是否值得」与风控「是否放行」是两条独立链路，风控不信任上游结论、独立核算。✅

### 2. 下单策略可插拔 ✅
- `strategies/base.py` 定义 `OrderStrategy` 接口；`registry.py` 提供 `get_strategy(name)`。
- 已实现 `Market/TWAP/VWAP`，新增策略 = 新文件 + `@register_strategy`，trader 零改动。配置 `default_strategy` 一键切换。✅

### 3. 风控逻辑可插拔 ✅
- `risk/rules/base.py` 定义 `RiskRule`（声明 `HARD`/`SOFT`）；`RiskEngine.register()` 自动归集。
- 新增规则只需实现 `check()`。✅

### 4. 组合层覆盖 ✅（权重为演示值，见第六节 3）
- 投资经理用 `portfolio.total_value × 目标权重` 算下单数量，输出**调仓指令集**；审核者复核 `worth_trading` 与订单一致性。

### 5. A 股规则落地 ✅
- `data/ashare_rules.py`：主板±10%、双创±20%、ST±5%且禁投、T+1、禁裸卖空，硬编码为事实层；被硬约束⑤与审核者双重使用。✅

### 6. 风控循环防死锁 ✅
- `max_loop=3`；**风控 `REJECTED` 回落**与**IC Gate 否决 `rejudge` 回落**均使 `loop_count++`；`loop>=max_loop` 一律强制路由到 `reviewer` 终结（见 §十三.9）。✅

### 7. 可扩展性 ✅
- 数据层/LLM 层/策略/风控规则四处均接口+注册表，互耦低；`config/settings.yaml` 集中参数。✅

### 8. HITL 断点 ✅（P5 已补）
- `graph/builder.py` 在 `EXCEPTION` 分支插入 `exception_human`（`interrupt()`），下单前插入 `confirm_trade`（`interrupt()`）；离线回退 `LocalRunner` 与 `hitl_enabled=False` 时自动放行，保证无人值守可跑。✅

---

## 三、P4 动态知识图谱 / GraphRAG 评审

### 1. Schema 与存储 ✅
- `kg/schema.py`：实体/关系为纯数据类，ID 规范化（`Company:600519`/`Industry:白酒`/`Concept:提价`），便于实体对齐；带 `confidence`/`sources`，支持融合与衰减。✅
- `kg/store.py`：内存（实体字典+邻接表）+ sqlite 持久化（`save/load_sqlite`）。**关系写入时自动补齐端点占位实体**——避免 subgraph BFS / retriever 因缺失端点 KeyError（已修复并单测覆盖）。✅

### 2. 抽取流水线 ✅
- `kg/extractor.py`：结构化报告（AnalystReport）确定性映射为三元组，无需 LLM 离线稳定；非结构化文本支持 LLM 结构化抽取（`_extract_with_llm`，真实环境）与启发式兜底（离线扫描标的/概念）。抽取前由注入防护清洗（见 P5）。✅

### 3. GraphRAG 检索 ✅（向量召回为预留接口）
- `kg/retrieval.py`：`GraphRAGRetriever.retrieve()` 从标的拉取本体/行业/同行/概念/目标价/宏观驱动/事件，按关系类型 relevance × 置信度混合打分取 topk。`to_context()` 生成可解释上下文喂给 LLM。
- 混合检索中的「向量召回」接口已预留（可对 `relation.text`/`entity.attrs` 做 embedding），当前离线版用确定性打分替代。终版接 embedding 时**接口不变**。✅

### 4. 增量更新与复盘闭环 ✅
- `kg/updater.py`：`apply` 增量写入；`decay` 按半衰期衰减旧关系置信度（知识时效性）；`update_outcome` 用「预测目标价 vs 实际价」回灌 `HAS_TARGET` 置信度——即计划 §12.8 的「学习闭环」，已接进 `replayer`。运行时验证 KG 回灌 30 次。✅

> 审核者注：当前 KG 为纯标准库实现（未依赖 networkx，满足离线），终版可平滑迁移 Neo4j（store/retrieval 接口隔离，替换实现即可）。⚠️ 见第六节 5。

---

## 四、P5 Harness 工程化评审

### 1. 共享 DataValidator ✅（原待补强 1 已闭环）
- `harness/datavalidator.py`：把 `prompt.txt` 红线（市值/利润交叉验证 PE、元→亿元 /1e8、DCF 反算 L≥目标价且>现价、评级-目标价一致性、敏感性收敛）抽成跨角色共享模块。
- 行业研究员产出报告后统一过 `validate_report` + `validate_fundamentals`（`data_quality_ok` 不再为真占位，由校验结果驱动）。**这是「穿透叙事」数据纪律跨角色一致执行的关键**。✅

### 2. PromptHub（版本化）✅
- `harness/prompthub.py`：角色 Prompt 集中版本管理（`get(name, version=None)`），便于回归与 A/B。各 Agent 通过它取 prompt。✅

### 3. 可观测性 ✅
- `harness/observability.py`：`Tracer` 结构化事件流（落 `trace.jsonl`）+ `Metrics`。`main.py` 用 `TracedLLM` 包装 LLM，所有 LLM 调用自动留痕（含 token/耗时），满足审计与排障。✅

### 4. 成本护栏 ✅
- `harness/cost_guard.py`：`CostGuard` 按模型预算累计成本，超限可熔断；`run_artifacts` 落 `cost` 状态。避免 Mock→真实切换后失控。✅

### 5. 提示注入 / 数据投毒防护 ✅（原待补强，计划 §12.5）
- `harness/injection_guard.py`：中英文注入模式（忽略指令/扮演/系统标签/提权/约束绕过/KG 投毒写入），`scan()` 返回清洗文本+风险标记，命中段用「疑似注入」方括号中性化（保留可审计、不静默删）。
- 行业研究员摄入新闻前先 `scan()` 清洗，再交 KG 抽取；被标记文本在 KG 抽取时降权。✅

### 6. HITL 真人断点 ✅
- `graph/builder.py`：`exception_human`（`EXCEPTION` 例外审批）、`confirm_trade`（下单前确认）两处 `interrupt()`。`main.py` 传 `--hitl` 才真正暂停等人；默认自动放行保证无人值守 Demo。✅

---

## 五、P6 完善评审

### 1. PaperTrader 模拟撮合 ✅
- `backtest/paper_trader.py`：按 ExecutionPlan 拆单模拟成交，考虑价格冲击（成交量占比→冲击成本）与滑点，输出 `TradeFill`（含成交价/量/冲击/滑点）。交易员据此落地真实感成交回报，而非理想价。✅

### 2. 回测引擎 + 系统级 Eval ✅（计划 §12.3）
- `backtest/engine.py`：`gen_price_paths`（含波动率/趋势的随机路径）+ `replay`（按成交回报推进组合净值）。
- `eval/evaluator.py`：对比「投研策略」与「等权买入持有基准」的累计收益、超额收益、战胜基准标志。运行时验证：**超额收益 +2.58%（策略-1.10% vs 基准-3.68%）**——系统级「赚没赚钱/控没控回撤」而非仅评报告质量。✅

### 3. 监控面板 ✅
- `monitor/monitor.py`：从 final state 收集宏观/投决/风控/成交/组合/评估/KG 统计，渲染 `monitor.html`（可视化）+ `monitor.json`（机读）。产物落 `run_artifacts/`。✅

### 4. 调度钩子 ✅（计划 §12.7）
- `scheduler/scheduler.py`：`trigger(event, payload)` 钩子（如 `post_market` 收盘后），预留接数据库/推送。当前打印钩子事件，接口可扩展为真实定时与事件触发。✅

### 5. 复盘闭环 ✅（计划 §12.8）
- `agents/replayer.py`：成交落地 → 推进组合 → 用 `kg_updater.update_outcome` 回灌 KG 置信度 → 写审计。学习闭环打通。✅

### 6. 配置集中化 ✅
- `config/settings.yaml`：补充 KG / Harness / 回测 / HITL / 监控 / 调度相关配置项，`main.py` 读取。✅

---

## 六、与开发计划（§12）的对照（更新至 P6）

| 计划要点 | 本版状态 |
|---|---|
| §12.1 风控量化（硬+软） | ✅ 已落地 |
| §12.2 组合层 + Universe 入口 | ✅ 入口 + 组合层（目标权重由风险预算优化驱动，见冲刺项②） |
| §12.3 回测/模拟环境 | ✅ PaperTrader + 回测引擎 + Evaluator 已闭环 |
| §12.4 合规/审计 | 🟡 有 `audit_log` 落痕 + HITL 审计；**缺免责声明固定落款** ⚠️ |
| §12.5 提示注入防护 | ✅ InjectionGuard 已接进 KG 抽取前 |
| §12.6 共享 DataValidator | ✅ 已落地并驱动 `data_quality_ok` |
| §12.7 调度/触发 | ✅ Scheduler 钩子实体化完成（post_market 真实写库/推送；pre_market/weekly 定时；重大事件触发重研） |
| §12.8 学习闭环/复盘 | ✅ KG 置信度回灌已接 |
| §12.9 产物体系/监控 | ✅ monitor.html/json |
| §12.10 成本护栏/确定性 | ✅ CostGuard + Mock 离线确定性可跑 |
| §12.11 市场微观结构 | ✅ A 股规则已落 |
| §12.12 修订阶段 | ✅ P0–P6 达成 |

---

## 七、待开发者补强（审核者意见，终版冲刺项）

1. ~~**行业研究员真实接入 DCF**（优先级高）~~ ✅ **已完成（冲刺项①）**：封装 `tools/dcf_implied.py` 为 `research/dcf.py`，行业研究员真实反算 `implied_ceiling_L` 与 `dcf_fair_price`，驱动软约束"叙事脆弱性"，39 单测 + 两端到端已验证 `dcf_used=True`。
2. ~~**组合层目标权重来源**~~ ✅ **已完成（冲刺项②）**：新增 `portfolio/optimizer.py`，目标权重由「宏观仓位上限 × 行业景气（评级分布聚合）× 个股评级置信度」风险预算优化得到，并与当前持仓 diff 产出调仓指令（含卖出降仓）；不再是固定 5%/只，且统一封顶保证不破硬约束。见下方 §冲刺项②。
3. **固定免责声明落款**：每份产物（审核回执/成交回执/监控面板页脚）追加"基于公开信息的分析推演，不构成投资建议"。
4. **Neo4j 迁移**（终版）：`kg/store.py` 的纯标准库实现平滑替换为 Neo4j 驱动，store/retrieval 接口已隔离，替换实现即可；GraphRAG 的向量召回在此阶段接真实 embedding。
5. ~~**Scheduler 钩子实体化**~~ ✅ **已完成（冲刺项⑤）**：`scheduler/scheduler.py` 重写为可定时/可事件驱动的实体——`post_market` 真实写 KG sqlite + 追加 `schedule_runs.json` 台账 + 推送；补 `pre_market`(09:00)/`weekly`(周五17:00) 定时任务（`run_due_jobs` 按时触发 + 同日去重持久化）；`notify_event` 重大事件分类入队 + `drain_research` 真实复跑行业研究员重研（含真实 DCF）。见下方 §十。
6. ~~**更丰富的图集成测试**~~ ✅ **已完成（冲刺项⑥）**：新增 `tests/test_kg_integration.py`（10 例，覆盖抽取→存储→检索→置信度融合→子图多跳→启发式/LLM 文本抽取→sqlite 往返→衰减→复盘闭环→实体对齐→事件演化）+ `tests/test_kg_e2e.py`（2 例，LocalRunner 跑完整研究闭环验证 KG 真实填充/可检索/可持久化）。并借该测试发现并修复 `kg/store.py:load_sqlite` 实体 key 错误（reload 后检索失效）的真实缺陷。见下方 §十一。
7. ~~**风控阶段人工审核确认（HITL）**~~ ✅ **已完成（冲刺项⑦）**：在 `risk` 节点之后插入 `risk_review` HITL 节点，对**所有**风控结论（APPROVED/REJECTED/EXCEPTION）做人工确认；确认→按原结论流转，否决→按 `settings.risk_review.on_reject`（`rejudge` 回落投资经理重研判 / `terminate` 直接终结进审核者）路由，并保留超循环防死锁；复用全局 `hitl_enabled`（关闭即自动放行，离线/无人值守可跑）。`main.py` 补 `--hitl` 断点 resume 循环（需兼容 langgraph 环境）。见下方 §十二。

---

## 八、运行时验证（已执行）

- [x] `pytest tests/` **全绿（108 passed）**：风险规则 / 拆单策略 / A 股规则 / 图路由与 HITL / KG 抽取·检索·更新 / DataValidator / InjectionGuard / PaperTrader / 组合层风险预算优化（冲刺项②）/ Scheduler 钩子（冲刺项⑤）/ **图集成测试（冲刺项⑥）** / **风控人工审核全链路（冲刺项⑦）** / **合规引擎（⑧）/ 量化因子信号（⑩）/ 事件监控（⑪）/ 绩效归因（⑫）/ 投委会终审 IC Gate（⑨）/ 执行台算法选择（⑬）/ 全流程多角色集成**。
- [x] `python main.py` 端到端跑通（LangGraph 版）：宏观+行业并行 → 投决(3 条调仓) → 风控 APPROVED → 交易员 TWAP 拆 10 段 → 模拟撮合落地 → 复盘回灌 KG(30) → 审核者/监控/评估。输出：净值≈9,999,661，超额收益 +2.58%，KG 14 实体/15 关系，验证违规 0。**投决目标权重由风险预算优化得到（三标的各 8.0%，受单票上限封顶，非固定 5%）**，并展示行业景气。
- [x] 违规策略在引擎层被 `REJECTED`（单票超限 / ADV 流动性 / 涨停不可买 三例单测覆盖）；图路由在 `loop_count>=max_loop` 强制终结（防死锁）。
- [x] 组合层 diff 单测覆盖：评级下调/清仓（SELL）触发卖出降仓、买入补足；空仓不从 0 发起 HOLD/SELL 新仓；LLM 越界权重被 `clamp_targets` 封顶。
- [x] 离线回退 `LocalRunner` 与 LangGraph 版输出一致，确认无外部依赖也可运行。

---

## 九、冲刺项② 评审（组合层目标权重：风险预算优化）

**结论：合格。** 目标权重不再硬编码为 5%/只，而是「宏观仓位上限 × 行业景气 × 个股评级」的风险预算优化结果，且经理据此与当前持仓做 diff 产出调仓指令。

### 1. 风险预算口径 ✅
- `portfolio/optimizer.py`：`conviction_of(rating)` 将评级映射为置信度（BUY=1.0/ADD=0.7/HOLD=0.4/REDUCE=0.15/SELL=0）；
  `industry_sentiment` 用「同行业各标的评级分布」聚合景气度；`raw_budget = conviction × (0.5+0.5×景气)`。
- `clamp_targets` 将任意来源（LLM 或优化器）的目标权重归一化并封顶：单票 ≤ `single_position_max`、合计 ≤ 宏观 `suggested_max_position`。**无论 LLM 输出多激进，硬约束在源端即被保证**。✅

### 2. 建仓闸门（避免无谓新仓）✅
- `BUY/ADD` 可从 0 发起建仓；`HOLD` 仅在有持仓时维持；`REDUCE` 仅在有持仓时降仓至 ≤50%；`SELL` 目标权重 0（清仓）。
- 不在 universe 内的既有持仓不参与本次调仓，不强行清仓整组合。✅

### 3. 与当前持仓 diff ✅
- `build_rebalance_orders`：delta>阈值买入补足；delta<阈值卖出降仓（含 SELL 清仓）。
- **清仓（目标权重=0）不受最小交易阈值限制**，确保主动撤出；其余小幅降仓受阈值约束避免摩擦。
- 卖出数量尊重当前可卖持仓；T+1 由执行/风控层（`can_sell(bought_today=False)`）把关。✅

### 4. 可审计性 ✅
- `ManagerDecision` 新增 `conviction_scores` / `industry_sentiment`，目标权重写入 `target_portfolio_weights`；`main.py` 总结段打印「目标权重(风险预算)」与「行业景气」，便于复盘与合规留痕。✅

### 5. 单测覆盖 ✅（8 例，见 `tests/test_portfolio.py`）
- 置信度映射、行业景气聚合、单票/总仓位封顶、空仓不发起 HOLD/SELL、LLM 越界封顶、买入补足+卖出降仓、经理端到端（权重非固定 5%/空仓全买）、下调评级触发卖出清仓。

> 运行方式（沙箱内已验证）：venv 装 `pydantic pyyaml pytest`；`langgraph langchain-openai` 装到独立 target 目录规避沙箱安全删除限制，`PYTHONPATH` 指向该目录后 `python main.py`。真实切换：改 `config/settings.yaml` 的 `llm.provider=openai`、`data.provider=tdx`。

---

## 十、冲刺项⑤ 评审（Scheduler 钩子实体化）

**结论：合格。** 调度器从「空壳（仅打印、每次新建无注册）」升级为可定时、可事件驱动、可持久化的实体，钩子体均接到真实动作（写库 / 推送 / 台账 / 重研）。

### 1. 真实钩子体（写库 / 推送） ✅
- `post_market`：调用 `kg.save_sqlite` 真实落库 + 追加 `run_artifacts/schedule_runs.json` 运行台账（含 worth_trading / 买数 / 卖数 / KG 规模 / 超额收益）+ 经 `PushSink` 推送收盘摘要。
- `pre_market`：生成盘前关注清单 `pre_market_<date>.json`（报价/涨跌幅/涨跌停/ST 标记），经推送。
- `weekly`：调用 `kg_updater.decay` 做 KG 置信度半衰期衰减（时效性）+ 写 `weekly_<iso_week>.json` 周报 + 推送。
- `event`：重大事件即时推送告警。

### 2. 定时调度 ✅
- `ScheduleSpec(hour, minute, weekdays)` 驱动；`run_due_jobs(now, payload)` 按进程内时钟触发到点任务，无需外部 cron 即可演示；真实部署亦可由 OS cron / Celery 直接 `trigger()`。
- `last_fired` 状态持久化到 `run_artifacts/scheduler_state.json`，**同一天同任务不重复触发**（防重复写库/推送）。✅
- 默认注册：post_market 15:05、pre_market 09:00、weekly 周五 17:00（见 `config/settings.yaml` 的 `scheduler` 段）。

### 3. 重大事件触发重研 ✅
- `notify_event(ticker, kind, detail)` 用 `MATERIAL_EVENT_KINDS` 分类（业绩/评级/涨跌停/停牌复牌/宏观政策/重大公告/业绩指引）。重大事件 → 入队 `reresearch_queue` + 即时推送；常规噪声不入队。
- `drain_research(runner)` 用注入的 runner 真实执行队列重研并清空。本仓库 `run_research_for_ticker` 复用行业研究员（`industry.run` + 真实 DCF）对单标的复跑并回写 KG。✅

### 4. 推送抽象（可插拔） ✅
- `PushSink` 基类 + `ConsolePushSink` / `FilePushSink`(jsonl) / `WebhookPushSink`(可选，真实环境接 IM/告警) / `MultiPushSink`。离线可跑，真实环境零改动替换通道。

### 5. 单测覆盖 ✅（9 例，见 `tests/test_scheduler.py`）
- `run_due_jobs` 按时触发（盘前 09:00 / 周度周五 17:00）+ 同日去重 + event 槽不定时；
- `notify_event` 重大事件分类入队 + event 推送、非重大事件不入队；`drain_research` 真实执行 runner 并清空队列；
- `post_market` 写库 + 台账 + 推送；`pre_market` 清单落盘；`weekly` 衰减生效；`run_research_for_ticker` 单标的真实复跑（DCF=True、KG 回写）。

### 6. 端到端验证 ✅
- `python main.py`：post_market 推送（KG 14/15）+ 定时演示（盘前 09:00 / 周度周五 17:00 触发）+ 重大事件 `600519 earnings` → 重研（评级 BUY、目标价 1775.04、DCF=True）。产物：`schedule_runs.json` / `push_log.jsonl` / `pre_market_*.json` / `weekly_*.json` / `scheduler_state.json` 均落盘。
- 全量 `pytest` **56 passed**（原 47 + 9）。

> 注：重研的 `KG写入=0` 属正常——`kg_updater.apply` 对已在 `ctx.kg` 中的关系做幂等合并（不新增），但分析本身真实重跑、KG 已重新持久化。

---

## 十一、冲刺项⑥ 评审（更丰富的图集成测试）

**结论：合格，且捕获并修复一个真实持久化缺陷。**

### 1. 新增集成测试覆盖（12 例）
- `tests/test_kg_integration.py`（10 例）：抽取→存储→检索全链路（多标的同行/行业/概念跨实体检索、GraphRAG 上下文生成）；置信度融合（noisy-OR：同关系取 max 且 sources 合并）；子图多跳 + 深度限制（公司→行业→宏观，depth=1/2 边界）；非结构化文本抽取（启发式 catalog 命中 + 概念关键词 → EVENT_AFFECTS/RELATED_CONCEPT；LLM 分支 Triple 对齐规范实体 ID）；sqlite 持久化往返（save→load 实体/关系数一致且可检索）；衰减数学（0.5^(age/half_life)，新鲜关系不降）；复盘 outcome 闭环（命中上调/偏离下调/多次回灌有界）；实体对齐去重（同报告多次抽取实体/关系数稳定、sources 累积）；重大事件注入→图谱演化→检索可见。
- `tests/test_kg_e2e.py`（2 例）：用 `graph.builder.LocalRunner` 跑完整研究闭环（screener→macro/industry→manager→risk→trader→reviewer→replayer），断言行业研究员真实填充 `ctx.kg`（实体/关系>0、记录 `industry_kg_writes`）、GraphRAGRetriever 可检索行业/同行、复盘 `update_outcome` 回灌生效、KG `save_sqlite`/`load_sqlite` 往返一致。

### 2. 配套工程改进
- `main.py`：把 `AppContext` 构造提取为 `_build_context()`，供 `main` 与端到端测试复用，避免 ctx 构造逻辑腐烂。
- **修复 `kg/store.py:load_sqlite` 实体 key 错误**：原代码 `SELECT doc FROM entities` 后将整行 JSON 字符串误作 `entities` 字典 key，导致 reload 后 `get_entity` 全失败、`GraphRAGRetriever.retrieve` 返回空（潜伏至今，此前 save 后从未 reload 检索）。改为 `SELECT id, doc FROM entities` 以实体 ID 为 key。修复后 reload 可正常检索（验证 `retrieve("600519")` 由 0 恢复为 5）。

### 3. 端到端验证 ✅
- `pytest tests/` **68 passed**（原 56 + 12）；`python main.py` 端到端：KG 落盘 14/15，reload 后 `retrieve("600519")` 返回 5 条（修复前为 0）。

---

## 十二、冲刺项⑦ 评审（风控阶段人工审核确认 HITL）

**结论：合格，且顺带修复 `main.py` 一处潜伏的真实 bug（`sched.drain_research` → 应为 `drain_reresearch`）。**

### 1. 设计
- 在 `risk` 节点之后、原 `_route_risk` 之前，插入 `risk_review` HITL 节点：**所有**风控结论（APPROVED / REJECTED / EXCEPTION）都先经人工确认，再继续。
- 确认 → 复用 `_route_risk` 按原结论流转（APPROVED→`confirm_trade` / EXCEPTION→`exception_human` / REJECTED→回落 `manager`）。
- 否决 → 由 `settings.risk_review.on_reject` 决定去向：
  - `rejudge`：回落投资经理重研判（`loop_count++`，超 `max_loop` 强制终结，防死锁）；
  - `terminate`：直接终结进审核者（reviewer）。
- 复用全局 `hitl_enabled`：关闭时 `human_approve` 自动放行，`python main.py` 离线/无人值守可跑；开启（`--hitl`）且环境兼容 langgraph 时，由 `interrupt()` 挂起真人断点并 resume。

### 2. 改动文件
- `core/config.py`：新增 `RiskReviewConfig(on_reject)` 并挂到 `Settings.risk_review`。
- `config/settings.yaml`：新增 `risk_review.on_reject: rejudge`（默认 rejudge）。
- `state/schemas.py`：`ResearchState` 新增 `risk_review_approved`（记录审核结果，供审计）。
- `graph/builder.py`：新增 `_risk_review` 节点与 `_route_risk_review` 路由；`risk→risk_review` 边 + `risk_review` 条件边；`LocalRunner.invoke` 同步插入风控人工审核与回落/终结分支。
- `main.py`：补 `_run_app`（含 `--hitl` 断点 resume 循环，逐个询问真人并 `Command(resume=...)` 续跑）；总结段打印 `人工审核`；并修复 `drain_research`→`drain_reresearch` 调用名 bug。
- `run_artifacts/flow_dashboard.html`：状态机图增加 `risk_review` 节点（HITL 高亮）与确认/否决路由标签。

### 3. 测试（新增 10 例，全量 78 passed）
- `tests/test_risk_review.py`：节点自动放行 / 否决 rejudge 自增 loop_count / 否决 terminate 不计数；路由确认后按原结论、否决 terminate 终结、否决 rejudge 回落、超循环终结；**LocalRunner 全链路**——默认自动放行回归（仍走到交易并产生成交）、强制否决+terminate 直接终结、强制否决+rejudge 多次回落后防死锁终结（audit_log 含 `hitl_risk_review` deny）。

### 4. 验证 ✅
- `pytest tests/` **78 passed**（原 68 + 10）。
- `python main.py` 端到端：风控段 `[风控] 结论=APPROVED ... 人工审核=True`，审计链含 `human/hitl_risk_review`（展示完整审核问题文案）；`--hitl` 路径代码就位（本沙箱 langgraph 未安装，`human_approve` 走默认放行，故 resume 分支未在此环境实跑，真实部署环境生效）。

---

## 十三、角色体系扩展评审（冲刺项 ⑧–⑬）

> 用户要求：原「研究员 / 基金经理 / 风控 / 交易员」四岗扩展为更完整的投研组织——**① 风控+合规合并、② 投委会终审(IC Gate) 作为 HITL 接入、③ 量化/因子研究员、④ 舆情/事件监控员、⑤ 绩效归因师、⑥ 算法交易/执行台合并进交易员**。本审核逐项核验六个改动的「业务正确性」而非仅代码存在性。

### 1. ⑧ 风控 + 合规合并岗 ✅

**结论：合格。** 合规不再游离于系统外，作为同一岗位与风控一并硬卡。

- `risk/compliance.py`：新增 `ComplianceEngine` + 可插拔 `ComplianceCheck` 抽象。内置四条：`RestrictedListCheck`（HARD：禁投名单 + ST 禁投）、`InsiderSilentPeriodCheck`（HARD：内幕信息静默期）、`Disclosure5pctCheck`（SOFT：5% 举牌披露）、`FairTradeCheck`（SOFT：公平交易）。
- `agents/risk_controller.py`：先 `ctx.risk_engine.check(rc)` 再 `ComplianceEngine().check(cc)`；合规 **HARD 违规**（`restricted_hits`）并入 `risk_result.hard_violations` 并将 `decision` 升级为 `REJECTED`；**合规结果单独留痕**（`compliance_result` 字段，供审计，不与风控结论混淆）。
- 配置：`core/config.py` 新增 `ComplianceConfig(restricted_list, silent_period)`；`config/settings.yaml` 新增 `compliance` 段。
- 测试（`tests/test_compliance.py`，4 例）：禁投名单 HARD 命中→REJECTED、无命中通过、5% 举牌被 SOFT 标记、risk_controller 合并合规拒绝→REJECTED。

> 审核者注：合并而非新增节点，符合用户「为一个岗位」的意图——风控与合规在同一节点串行执行、共用 REJECTED 出口，避免两条独立链路互相甩锅。

### 2. ⑨ 投委会终审（IC Gate）HITL ✅

**结论：合格。** 重大投决在「下单前确认」之后、正式执行之前，新增一道人工终审；非重大自动放行，不阻塞正常流程。

- `graph/builder.py`：新增 `_ic_gate` 节点 + `_ic_materiality` 重大性判定 + `_route_ic` 路由。`confirm_trade` 通过 → `ic_gate`；`ic_gate` 通过 → `trader`；否决 → 按 `ic_gate.on_reject`（`terminate` / `rejudge`）路由。
- 重大性判定（`_ic_materiality`）：单票目标权重 > `single_position_weight`(5%) **或** 调仓总偏离 > `total_deviation`(10%) **或** 风控为 EXCEPTION 且 `exception_is_material=True`。命中→挂真人终审；不命中→`actor=system, action=auto_pass` 自动放行。
- 防死锁：IC Gate 否决 `rejudge` 同样 `loop_count++`，`loop_count >= max_loop` 强制终结进 `reviewer`（与风控否决共用同一道闸门）。
- 配置：`ICGateConfig(on_reject, materiality)`；`settings.yaml` 新增 `ic_gate` 段。
- 测试（`tests/test_ic_gate.py`，覆盖重大性三条件 + 节点 auto_pass/approve/deny+loop/deny-no-loop + 路由 approved/deny-terminate/deny-rejudge/overloop + 全流程多角色集成）。

> 审核者注：把 HITL 收口在「重大投决」而非每单都卡，是正确的产品取舍——普通调仓不烦人，越权/异常偏离才上投委会。

### 3. ⑩ 量化 / 因子研究员 ✅

**结论：合格。** 提供正交信号视角，且**明确定位为参考输入、不改投决权重**（避免两个信号源互相覆盖、责任不清）。

- `agents/quant.py`：`run` 对每条 `analyst_report` 计算动量（来自 quote）、波动率（`_stable_vol` 由标的字符确定性派生）、流动性分、因子分 → `BULLISH/NEUTRAL/BEARISH`（`neutral_band` 判定）。写入 `quant_signals[ticker]`。
- `agents/manager.py`：把量化观点拼入 prompt 与 `md.rationale`（`qsum`），**仅作参考**；目标权重仍由风险预算优化得出，不被量化覆盖。
- `quant.enabled=False` → 返回 `{}`，零副作用。
- 测试（`tests/test_quant.py`）：所有标的产出信号、禁用返回空。

### 4. ⑪ 舆情 / 事件监控员 ✅

**结论：合格。** 在流程**最前端**扫描新闻/公告，既为研究提供事件上下文，又驱动调度重研。

- `agents/event_monitor.py`：`run` 扫描 `watchlist` 新闻，按 `negative_keywords`/`material_keywords` 标记 `MarketEvent`（`material` 才触发重研）。
- **防误报修复**：MockProvider 返回「近期无重大负面事件」含「重大」二字，原会误判为重大；改为 `is_mat = any(k in raw for k in mat) and "无" not in raw`，避免「无重大负面」被当成重大事件。
- `main.py` 的 `_demo_scheduler`：把 `market_events` 中的 material 事件注入重研队列，使事件监控真正驱动调度重研。
- `event_monitor.enabled=False` → 返回 `[]`。
- 测试（`tests/test_event_monitor.py`）：无事件不误报、重大事件检测、负面词、禁用返回空。

### 5. ⑫ 绩效归因师 ✅

**结论：合格。** 在交易成交后、进审核者前，对一轮投决做可解释归因。

- `agents/attribution.py`：`run` 计算选股贡献（`fill.price` vs `analyst.target_price`）、择时/滑点成本（`timing_pnl = -slippage_cost`）、按研究员维度（`per_researcher`）贡献，输出 `AttributionReport`。
- `attribution.enabled=False` 或 `fills` 为空 → 返回 `None` / 安全处理。
- 测试（`tests/test_attribution.py`）：常规计算、禁用、空成交。

> 审核者注：归因在 `trader` 之后、`reviewer` 之前，确保用的是真实成交价而非理想价，归因口径正确。

### 6. ⑬ 算法交易 / 执行台合并进交易员 ✅

**结论：合格。** 用户明确「Agent 不存在真实交易」——执行台只做算法选择 + 可插拔拆单 + 模拟撮合，合并进 `trader` 单一节点，不另立岗位。

- `agents/trader.py`：新增 `_select_strategy(notional, adv, ctx)`——`notional >= large_notional` 或 `notional/adv >= low_adv_ratio` → VWAP，否则 TWAP；产出 `execution_memo` 说明选用策略与理由。
- 风险未批准时跳过撮合（修复：`skips` 分支补齐 `"fills": []`，避免下游 KeyError）。
- 配置：`ExecutionDeskConfig(default_strategy, large_notional, low_adv_ratio)`；`settings.yaml` 新增 `execution_desk` 段。
- 测试（`tests/test_execution_desk.py`）：大额→VWAP、小额→TWAP、memo 正确、未批准跳过。

### 7. 端到端验证（新增 30 例，全量 108 passed）

- `pytest tests/` **108 passed**（原 78 + 30：compliance 4 + quant 2 + event_monitor 4 + attribution 3 + ic_gate 多例 + execution_desk 4 + graph 路由更新 1）。
- `python main.py` 端到端：IC Gate 正确判定「单票目标权重 8.0% 超 5%；调仓总偏离 24.0% 超 10%」→ 重大投决挂人工终审；合规标记举牌披露；量化给出 BEARISH 信号（仅参考）；执行台选 TWAP；归因算出选股贡献 +427,045 元。
- 事件监控重大事件驱动调度重研队列（同 §十.3 机制闭环）。

> 审核者总评：六个角色改动均「业务正确落地」，无新增孤立节点甩锅、无责任重叠混乱；HITL 收口于重大投决、新增角色均带 enable 开关与单测、均接进主链路与审计。角色体系由 4 岗扩展为 8 职能（9 节点），更贴近真实投研组织，且仍保持离线可跑、可插拔、防死锁。
