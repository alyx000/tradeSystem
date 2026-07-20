---
name: daily-review
description: 协助用户完成每日「八步复盘法」，自动拉取客观数据、引导填写主观判断，并将复盘写入数据库
version: "1.9"
---

# Skill: 每日复盘（八步复盘法）

本文件为**速查**；八步中每一步的**详细提问话术、占位示例与附录说明**见 [references/eight-step-prompt-templates.md](references/eight-step-prompt-templates.md)。

> **两种复盘形态**：本 SKILL 主流程是**单 Agent 表单式**复盘（review workbench 预填 + 引导填写 + 写库/钉钉）。
> 当用户要求「像之前那样出 HTML 复盘」「用多 agent 复盘 YYYY-MM-DD」时，改走 [references/multi-agent-review.md](references/multi-agent-review.md)：
> 9 路 subagent 仍完整采集 → 主会话只筛选变化、冲突、会改变总裁决或影响持仓的内容 → 精简正文 + 可折叠证据的只读 HTML 报告（`data/reports/复盘_*.html`）。每路主输出固定为 1 条裁决、最多 3 条相对上一交易日的增量事实、最多 2 条冲突/缺口、各 1 条 `confirm_if` / `invalidate_if`；完整事实和表格进入证据层。口径基线（单位换算 / 两市综指口径 `000001.SH + 399106.SZ` / ETF 拆分名单 / 北向禁用 / 红线）与 [HTML 模板](references/html-report-template/README.md) 不变。

多 Agent HTML 报告默认每节只显示「1 句裁决 + 最多 3 条证据 + 1 条证伪或缺口」，同一结论只允许在速览短引一次、在唯一归属章节完整解释一次；无新增内容只写一行，不生成空表。组装器对固定 8 chunks、16 anchors、Claim / evidence 结构、正文/附录预算和静态外部依赖做硬校验，超限必须定位责任章节并拒绝生成，不得自动删字。

HTML 默认展示顺序为：`速览 → 三位一体重点因子 → ⓪前日判分 → ①–⑦ → 老师观点 → 行业信息 → 认知对照 → 次日推演 → ⑧次日计划 → 数据缺口`。重点因子仍必须在 9 路完整采集、八步复盘与认知对照综合完成后生成，只是在 HTML 中前置展示，不得因展示顺序提前而跳过后续事实输入。该章节必须用 `data-factor-mode` 区分正式 `factor-score`、`rule_only`、人工影子分析或明确无数据；未运行正式评分时必须使用 `shadow` 并写明“影子口径、不写库”。次日推演消费因子裁决后再衔接次日计划。

多 Agent HTML 中的“容量中军”必须从全市场成交额排名**独立筛选**，不得把 `trend_leader_pool`、`leader_tracking` 或最票身份直接当成容量资格：`core` = 当日全市场成交额排名 ≤30 且归属方向成交额排名 ≤2；`candidate` = 当日全市场成交额排名 31～50 且归属方向成交额排名 ≤2。最近 5 个开放交易日进入 Top50 的次数只展示容量连续性，不能覆盖当日成交额门槛。未达门槛者只能进入“趋势池历史代表”或“辨识度票”分表，禁止使用“旧池中军”标签；⑤必须输出结构化容量表、完整来源下的无合格项声明，或来源不足声明。完整筛选、健康度和 HTML 元数据契约见 [多 Agent 流程](references/multi-agent-review.md#容量中军独立筛选硬契约) 与 [HTML 模板](references/html-report-template/README.md#容量中军元数据硬门)。

容量排名不能由 Agent 自报：主会话选定 1～3 个申万二级方向后，必须运行模板内 `build_capacity_manifest.py`，从只读镜像生成 `capacity_<REPORT_DATE>.json`；官方 `assemble_report.py` 落盘时默认读取该 sidecar，并逐 `ts_code` 对账全部资格行。禁止手写、编辑或复制旧 sidecar；helper 返回失败 sidecar 时，⑤只能使用结构化 `missing-data` 并在数据缺口保持可见，不能绕过 sidecar 发布。

②板块的集中度和“主升方向 × 辨识度个股”不得被精简掉：默认正文固定保留 1 句集中度裁决，折叠证据分别保留唯一集中度表、唯一主升辨识度矩阵及与之成对的主跌辨识度矩阵；无合格项与来源缺失必须使用不同的结构化状态，禁止静默省略。⑥节点同样固定保留 1 句未来事件窗裁决，以及报告日后 7 个自然日的结构化证据或无数据/缺失状态。具体硬门见 [HTML 模板](references/html-report-template/README.md#板块集中度与主升主跌辨识度硬门) 和 [事件窗硬门](references/html-report-template/README.md#未来七日事件窗硬门)。

⑤龙头还必须保留“历史新高结构”：默认正文 1 句前复权滚动 60/120/250 日新高裁决与最多 3 条增量证据，完整双日计数、行业 Top3/CR3、名单延续和代表票进入折叠层。该口径由 `build_new_high_structure_manifest.py` 对最近 251 个开放日的 `daily.high × adj_factor` 只读重算，不能拿 `daily_new_high_stats` 的全历史高水位结果代替；无法完整重算时必须输出结构化 `missing-data` 和可见缺口，不得静默删除。详见 [历史新高结构硬门](references/html-report-template/README.md#历史新高结构硬门)。

## 使用场景

当用户说：

- 「开始今天的复盘」
- 「帮我复盘」
- 「打开复盘工作台」
- 「看一下今天的复盘预填充」
- 「做三位一体因子评分 / 看 20 日影子指标」

时激活此 skill。

## 优先入口

优先使用仓库根目录：

```bash
make review-open DATE=YYYY-MM-DD
make review-prefill DATE=YYYY-MM-DD
make notes-search KEYWORD=主线 FROM=YYYY-MM-DD TO=YYYY-MM-DD
make db-search KEYWORD=情绪 FROM=YYYY-MM-DD TO=YYYY-MM-DD
python3 main.py review factor-score --date YYYY-MM-DD --input-by USER [--json]
python3 main.py review factor-metrics [--days 20] [--json]
```

需要调试时再使用 API：

- `GET /api/review/{date}/prefill`
- `GET /api/review/{date}`
- `PUT /api/review/{date}`
- `POST /api/review/{date}/to-draft`
- `POST /api/review-factors/{date}/score`
- `GET /api/review-factors/{date}/evaluation`
- `PUT /api/review-factors/{date}/evaluation`
- `GET /api/review-factors/metrics?days=20`

## 核心流程

1. 先确认复盘日期。
2. 拉取预填充数据，先展示客观事实，再引导用户填写主观判断。（逐步提问时可打开 [附录话术模板](references/eight-step-prompt-templates.md)。）
   其中 `projection_candidates.facts` 会同时返回 `emotion_leader` / `capacity_leader`，兼容字段 `lead_stock` 默认跟随 `capacity_leader`；资金流源字段里的股票只作“资金流字段股”参考，不要直接当成板块龙头结论。老师笔记 `sectors` 若是自由文本且匹配不到已知板块，不应直接升格成候选板块。
   预填充同时会返回 `cognitions_by_step`（按八步聚合的 `status=active` 底层认知，每步最多 5 条），用法见下文「认知联动」章节。
3. 输出第 0 节「前日对照判分」（见下文「前日对照判分（T-1 验证）」章节）：前日观察打分 vs 当日实际、前日老师方向性观点判分。若 T-1 无打分文件或无可判分观点，对应子块写「本日不适用」，不要静默省略。
4. 按八步复盘法汇总：
   - 大盘分析
   - 板块梳理（机构定价视角可引用 `python3 main.py research-digest trend` 的申万一级研报覆盖占比 [事实] 计数；「哪些板块覆盖热度上行/下行」属趋势解读，提及时标 [判断]。**定位=机构议程背景，禁止当短线方向先验引用**——2026-07 实测覆盖 Δpp 与后 1~2 日板块涨跌 Spearman≈0[单期样本]，它反映卖方研究日历而非资金轮动；数据不足时跳过，不强行引用）
   - 情绪周期
   - 风格化赚钱效应
   - 龙头 / 最票识别（**Agent 主动推荐**：进入此步时，Agent 先读取预填候选 `step5_leaders` + 历史最票 `leader_tracking` + 前 4 步上下文 + [最票方法论](../sector-projection-analysis/references/leader-identification.md)，综合分析后输出结构化最票推荐，标注 `[判断]`，等用户确认/修改后再录入）
   - 节点判断
   - 持仓检视
   - 次日计划
   - 每日 22:30 会由 `python3 main.py daily-leaders propose --push` 生成第 5 步「龙头 / 最票」候选确认稿并推送钉钉 Markdown；候选优先按当前申万二级分组，概念仅作来源证据，未映射票统一标「未分类」；以趋势中军/连板核心/前排活跃/弹性前排为语义属性，程序强制同板块同属性仅 1 只、股票全局唯一、最终最多 15 只。LLM 必须完整覆盖受控候选池且不得夹带池外股票，否则按同约束确定性兜底。v1 只作为确认草稿，用户在 Codex 中确认后再由 Agent 执行 `python3 main.py daily-leaders confirm --date YYYY-MM-DD --input-by codex` 写入复盘第 5 步并同步 `leader_tracking`；确认入口复用提案层的 Unicode 空白压缩板块键，并在事务前重新校验上述三项硬约束，旧稿不合规则拒绝而不静默裁剪。合法股票代码规范为裸 6 位身份；同股同板块属性的旧名称型 tracking 行仅在全库及同批名称代码映射无歧义时于该事务内迁移或合并，避免历史误归与分叉。不要描述为钉钉按钮回调已可写回。
5. 保存前先给用户一版结构化复盘摘要，用户确认后再写入。
6. 若处于三位一体 20 日影子期，保存后按下文「三位一体因子影子评分」执行评分与专属人工确认；系统建议不能代替用户确认。
7. 若用户要把复盘直接衔接到次日计划，保存后调用 `POST /api/review/{date}/to-draft`，只生成 observation / draft，不确认正式计划；当前转换实现只读取 `step1_market` 与 `step2_sectors`，忽略 `step8_plan` 及其 `key_factor / secondary_factors` 兼容镜像，**不得把 factor 评分、确认或回验结果手工补进 `TradeDraft`**。

## 三位一体因子影子评分（20 个有效交易日）

这是复盘内的**影子观察层**，不是交易计划生成器：

1. `factor-score` 从当日复盘第 1～6 步与 `prefill` 生成四张固定因子证据卡：`market_node / sector_rhythm / style_regime / leader_signal`。`style_regime` 的独立客观来源族是指数相对强弱、10/20/30cm 板型混合、已实现溢价；`leader_signal` 的独立客观来源族是剔除 ST 的连板梯队、晋级实现与前高标回验。`promotion_realization` 当前只保证 `promotion.trade_date` 与评分日一致；`prior_core_feedback` 的来源日必须等于 `trade_calendar` 严格上一开放日，不得以 `prefill.prev_market` 的最近行情日替代。日期血缘优先读取显式 `popularity_provenance`，该键存在但类型非法或日期错位时直接拒绝且不得 fallback，只有历史数据完全缺少该键时，才允许用同一 `style_factors.promotion.prev_date / trade_date` 作兼容 fallback。来源日期缺失或错位不得抬高证据质量。规则门、证据质量、封顶与总分由程序控制，不喂给 LLM；主观 context 可供解释，但不能抬高客观门槛。
2. 第 1 层 LLM 只给四因子相对重要度，程序重算后选主因子；只有主因子成立且存在候选时，才把该主因子的受控证据卡传入第 2 层，对确定性排序中的最多 6 个 `candidate_tier=core` 板块评分。`watch/context` 不得升格；Step 5 人工最票与自动 leader 名称只能作为标注 `[判断]` 的 context，不能伪装成客观来源或提升证据质量。每行必须引用至少一条正向证据和一个 T+1 check；所有分数只表示相对重要度，**不是概率、胜率或买卖建议，也不得进入 `TradeDraft` / `TradePlan`**。
3. 因子层失败时不展示数字 LLM 分，只允许唯一规则降级或“不确定”；板块层失败时保留已成立的主因子，并回退到确定性 `core` 排序。`sector_failed` 是完整可展示的部分降级结果，可命中缓存；需要重跑时显式传 `--retry-of-run-id`，新建 append-only 子 run，不覆盖旧 run。
4. 展示系统建议后，必须让用户三选一：`accepted`（接受）、`overridden`（改选，必须写 `override_reason`）或 `undetermined`（看不懂/不确认）。CLI 入口：

```bash
python3 main.py review factor-score --date YYYY-MM-DD --input-by USER [--steps-file steps.json] [--no-llm] [--retry-of-run-id RUN_ID] [--json]
python3 main.py review factor-confirm --date YYYY-MM-DD --run-id RUN_ID --decision-file decision.json --input-by USER [--json]
```

不带 `--no-llm` 时必须显式配置非空 `LLM_MODEL`；系统不会偷偷选默认模型。`--no-llm` 只跑规则门并产出 `rule_only` 影子建议。

`decision.json` 示例：

```json
{"status":"accepted"}
{"status":"overridden","primary_factor":"style_regime","supporting_factors":["market_node"],"override_reason":"人工看到不同连接"}
{"status":"undetermined","override_reason":"看不懂，继续观察"}
```

`factor-score` 只追加审计 score run，但仍必须带 `--input-by`：每次有效请求都追加 `daily_review_factor_score_requests`，缓存命中也记录当前请求者与 resolved run；请求者不参与 cache key，旧 run 的 `diagnostics.request.input_by` 保留首次请求信息。`POST /api/review-factors/{date}/score` 的 `input_by` 同样是运行时必填，缺失、空值或仅空白均返回 422；Web 显式传 `web`，且 `input_by` 不进入 cache key。评分、人工确认与回验仅接受完整交易日历中的开放日，写入必须显式标注 `--input-by` / `input_by`。确认时服务端必须在同一写事务内用当前预填与第 1～6 步重建证据摘要；摘要与 score run 不一致时拒绝确认并要求重跑。CLI `factor-confirm` 读取数据库已保存步骤，因此通过 `--steps-file` 评分的内容必须先保存到同日复盘，再基于当前内容重跑评分。确认只写 `daily_reviews.step8_plan.factor_decision`，并镜像兼容字段 `key_factor / secondary_factors`；其他第 8 步内容必须保留。确认后，Web/API 或 `daily-leaders confirm` 实际改写第 1～6 步时必须同步清除旧决定及兼容镜像，防止回验消费陈旧结论。未确认前不得写入 `factor_decision`。`to-draft` 只读取第 1、2 步，不读取第 8 步或上述兼容镜像；CLI/API/Web 都不得因此创建或更新 `TradeDraft` / `TradePlan`。

### 严格 T+1 与 20 日指标

- 来源日与回验日都必须是完整交易日历中的开放日，且回验日必须是来源复盘日的**严格下一交易日**。程序按主因子比较来源 run 证据快照与次日客观事实：大盘比较方向，风格比较可比维度，板块比较 core 连续性，龙头比较客观梯队事实；不可比才返回 `missing_data`。人工再确认 `hit / partial / miss / missing_data / not_applicable`。
- CLI `factor-evaluate` 会合并“生成建议 + 人工确认”两步；`--run-id` 不传时优先取来源日已确认 run，否则取最新可用 run：

```bash
python3 main.py review factor-evaluate --date T_PLUS_1 --source-date SOURCE_DATE [--run-id RUN_ID] --outcome hit|partial|miss|missing_data|not_applicable --input-by USER [--note TEXT] [--json]
python3 main.py review factor-metrics [--days 20] [--json]
```

- “20 日影子模式”只统计开放交易日；同日存在 retry 时优先采用人工决定引用的 canonical run，无确认时再取最新可缓存有效 run，避免失败 retry 覆盖已确认父 run。用 `factor-metrics --days 20` 比较成功率、降级率、覆盖率、接受/改选与分组表现；它不是自动定时器或放行开关。
- API 对应为 `POST /api/review-factors/{date}/score`、`GET/PUT /api/review-factors/{date}/evaluation`、`GET /api/review-factors/metrics?days=20`。评分 body 的 `input_by` 运行时必填，缺失、空值或仅空白返回 422；Web 必须显式传 `web`，且 `input_by` 不进入 cache key。因子人工确认没有单独的 `review-factors` endpoint，Web/API 走 `PUT /api/review/{date}` 的 `step8_plan.factor_decision`，同样必须带 `score_run_id / status / input_by`。

## 保存字段契约

复盘工作台是结构化表单。Agent 写入时优先使用页面字段：

- `step1_market.notes`（开头放第 0 节「前日验证」段，见「前日对照判分」章节）
- `step2_sectors.selection_summary / projections / next_day_focus / notes`
- `step3_emotion.phase / transition.reason / notes`
- `step4_style.preference / effects / notes`
- `step5_leaders.top_leaders / transition / notes`
- `step6_nodes.market_node / sector_node / style_node / overall`
- `step7_positions.positions[].action_plan`
  - 涉及"建仓周期"复盘时,可调 `db thesis-review --id N --executed-as-planned {0,1,2} --input-by U [--lessons --discipline-score --exit-trigger]` 写到 `thesis_review` 表(plan precious-crunching-ocean v24)
- `step8_plan.key_factor / watch_directions / risks / discipline / summary`
- `step8_plan.factor_decision`（三位一体人工确认：`score_run_id / status / primary_factor / supporting_factors / override_reason / confirmed_at / input_by`；同时镜像 `key_factor / secondary_factors`，但不进入 `TradeDraft`）

`PUT /api/review/{date}` 会兼容摘要式 `facts / judgement / plan / holdings` 写入并轻量映射到页面可见字段，但这只是兜底；正式复盘稿仍应按上面的表单字段组织，避免“API 保存成功但页面看不到重点内容”。若 `step5_leaders` 的股票展示包含非法代码后缀，或展示内代码与显式 `stock_code` 冲突，接口返回 422，并在同一事务内回滚复盘与 `leader_tracking` 写入。

## 前日对照判分（T-1 验证）

从 v1.5 起，每次复盘在进入八步**之前**，先产出第 0 节「前日对照判分」；保存时并入 `step1_market.notes` 开头的「前日验证」段。两个子块都要出现（不适用时显式写「本日不适用」）：

### 0a. 前日观察打分 vs 当日实际

- 若存在 `data/reports/观察股打分_<T-1>.md`（观察股打分的标准落盘位置与命名），或 T-1 复盘含观察优先级排序，则用当日真实行情（tushare 只读）对照：
  - 高 / 中 / 低分组的当日平均涨跌；
  - 各维度分（题材 / 位置 / 承接 / 情绪）与当日收益的方向一致性，重点标记**反向失效的维度**（如兑现日「前日承接强」次日反而领跌）。
- 结论全部标 `[判断]`。单日样本噪音大，**单日反向不构成框架否定**；同一维度连续多日（参考阈值 ≥3 个交易日）同向失效，才升级为打分框架修订议题。

### 0b. 老师观点判分（多空对照记分）

- 从 T-1 `teacher_notes` 提取**次日可验证的方向性观点**：有明确验证点或失效条件的短线观点才可判分；swing / 中长线观点标 `pending`，不判死。
- 用当日事实（指数、申万行业涨跌、关键个股）逐条判 `✓ 命中 / ✗ 落空 / ◐ 部分 / pending`，同时记录当日市况标签（如「兑现日」「修复日」「风格切换日」）——跨日累计后形成「谁在什么市况下更准」的先验。
- 判分表进复盘报告第 0 节；老师观点原文引用一律标「老师观点」，判分结论标 `[判断]`。

### 边界与红线

- 判分对象是「观点 vs 事实」的对照，不评价老师个人，**不得**由判分结果推导出「跟随 / 回避某老师」类操作建议。
- 判分中发现的可复用模式（如「兑现日盘中反包次日多为反向指标」）→ **切到 [`cognition-evolution`](../cognition-evolution/SKILL.md)** 出强候选卡片，用户确认后才落库；本 skill 只判分、只记录，不写认知库（与「认知联动 · 边界」同一约束）。
- 第 0 节所有内容不构成买卖建议。

## 认知联动（cognitions_by_step）

从 v1.4 起，`GET /api/review/{date}/prefill` 额外返回 `cognitions_by_step`：按八步聚合的 `status=active` 底层认知快照（来自 [`cognition-evolution`](../cognition-evolution/SKILL.md) 沉淀的 `trading_cognitions`）。对应 Web 复盘工作台每步顶部的「相关底层认知」只读面板。

### 每一步都要做的一件事

进入每个步骤 **之前**，先扫 `cognitions_by_step.<step_key>`：

- 若数组非空，**用 1–2 句自然语言**把相关认知「挂到」当前步骤的主观引导中（例如：「这里有一条 active 认知『连板高度决定板块生命周期』，置信度 80%，你判断板块节奏时是否适用？」）
- 若与当前事实冲突，**必须显式指出冲突**并询问用户（不要替用户选择哪条正确）
- 若数组为空，**不要强行编造**或绕行去 `/api/cognition` 查全集——本步骤没有沉淀的认知，按正常话术推进

### 步骤 → category 映射（只读，与后端 `_STEP_CATEGORY_MAP` 对齐）

| 步骤 | step_key | 覆盖 category |
|------|----------|---------------|
| 大盘分析 | `step1_market` | `structure`, `macro`, `cycle` |
| 板块梳理 | `step2_sectors` | `structure`, `signal` |
| 情绪周期 | `step3_emotion` | `sentiment` |
| 风格化赚钱效应 | `step4_style` | `structure`, `signal` |
| 龙头 / 最票 | `step5_leaders` | `execution` |
| 节点判断 | `step6_nodes` | `cycle`, `position` |
| 持仓检视 | `step7_positions` | `sizing`, `position`, `execution`, `fundamental` |
| 次日计划 | `step8_plan` | `execution`, `synthesis`, `valuation` |

排序规则：`confidence DESC, instance_count DESC, updated_at DESC, cognition_id ASC`，每步最多 5 条；字段白名单包含 `cognition_id / title / category / sub_category / evidence_level / confidence / instance_count / validated_count / invalidated_count / pattern / conflict_group / tags`。

### 边界：引用 ≠ 验证

- **本 skill 只「引用」不「验证」**：复盘对话中**禁止**调用 `python3 main.py knowledge instance-add / validate / cognition-refine` 等写操作
- 若在引用过程中用户发现「这条认知今天又被一次验证 / 推翻」，**切到** [`cognition-evolution`](../cognition-evolution/SKILL.md) 处理实例录入与验证
- 若用户认为某条认知需要 refine 或 deprecate，**同样切到 `cognition-evolution`**，不要在复盘对话里就地修认知

## 禁止事项

- 不要替用户臆造主观判断。
- 不要把 `[判断]` 写成 `[事实]`。
- 不要在用户未确认前直接提交 `PUT /api/review/{date}`。
- 不要把复盘工作台任务误当成 `TradePlan` 确认流程。
- 不要把 factor 系统建议自动视为用户决定；必须人工 `accepted / overridden / undetermined` 后才写 `factor_decision`。
- 不要把 factor score run、`factor_decision`、T+1 回验或 20 日指标写入/转换为 `TradeDraft`、`TradePlan` 或交易池。
- 不要在用户否定最票推荐后反复质疑——用户拍板即以用户结论为准。
- **不要在复盘对话里调用 `instance-add` / `validate` / `cognition-refine` 等认知写操作**（见上文「认知联动 · 边界」），需求切到 `cognition-evolution`。
- 不要把 `cognitions_by_step` 的认知标题或 pattern **当作客观事实**引用——它们是过往沉淀的**判断体系**，需和当日事实对照后再决定是否采纳。

## 最小验证

- `make review-prefill DATE=YYYY-MM-DD` 或 `GET /api/review/{date}/prefill` 能返回数据。
- 保存后重新读取 `GET /api/review/{date}`，确认内容存在。
- 保存后打开 `/review/{date}` 做页面级验证，至少检查 1–2 个关键文本可见；若页面提示“当前存在本地草稿，可能覆盖服务端版本”，先按用户意图选择本地草稿或服务端版本。
- 若当天数据缺失，明确提示先切到 `market-tasks` 补跑采集。

## 切换条件

- 若缺少当日行情或盘后数据，切到 [`market-tasks/SKILL.md`](../market-tasks/SKILL.md)。
- 若用户要生成或确认次日计划，切到 [`plan-workbench/SKILL.md`](../plan-workbench/SKILL.md)。
- 若用户要先录入老师观点，再作为复盘依据，切到 [`record-notes/SKILL.md`](../record-notes/SKILL.md)。
- 若用户想在本次复盘基础上新增 / 验证 / refine / deprecate 认知，切到 [`cognition-evolution/SKILL.md`](../cognition-evolution/SKILL.md)。
- 若发现 API / Web / CLI 语义漂移或保存异常，切到 [`repo-maintenance-workflows/SKILL.md`](../repo-maintenance-workflows/SKILL.md)。

## 结果汇报格式

1. 已读取 / 已保存的对象与日期
2. 客观事实摘要与用户主观结论
3. 验证结果
4. 剩余风险或待补充项
