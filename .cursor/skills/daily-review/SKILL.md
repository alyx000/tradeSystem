---
name: daily-review
description: 协助用户完成每日「八步复盘法」，自动拉取客观数据、引导填写主观判断，并将复盘写入数据库
version: "1.4"
---

# Skill: 每日复盘（八步复盘法）

本文件为**速查**；八步中每一步的**详细提问话术、占位示例与附录说明**见 [references/eight-step-prompt-templates.md](references/eight-step-prompt-templates.md)。

## 使用场景

当用户说：

- 「开始今天的复盘」
- 「帮我复盘」
- 「打开复盘工作台」
- 「看一下今天的复盘预填充」

时激活此 skill。

## 优先入口

优先使用仓库根目录：

```bash
make review-open DATE=YYYY-MM-DD
make review-prefill DATE=YYYY-MM-DD
make notes-search KEYWORD=主线 FROM=YYYY-MM-DD TO=YYYY-MM-DD
make db-search KEYWORD=情绪 FROM=YYYY-MM-DD TO=YYYY-MM-DD
```

需要调试时再使用 API：

- `GET /api/review/{date}/prefill`
- `GET /api/review/{date}`
- `PUT /api/review/{date}`
- `POST /api/review/{date}/to-draft`

## 核心流程

1. 先确认复盘日期。
2. 拉取预填充数据，先展示客观事实，再引导用户填写主观判断。（逐步提问时可打开 [附录话术模板](references/eight-step-prompt-templates.md)。）
   其中 `projection_candidates.facts` 会同时返回 `emotion_leader` / `capacity_leader`，兼容字段 `lead_stock` 默认跟随 `capacity_leader`；资金流源字段里的股票只作“资金流字段股”参考，不要直接当成板块龙头结论。老师笔记 `sectors` 若是自由文本且匹配不到已知板块，不应直接升格成候选板块。
   预填充同时会返回 `cognitions_by_step`（按八步聚合的 `status=active` 底层认知，每步最多 5 条），用法见下文「认知联动」章节。
3. 按八步复盘法汇总：
   - 大盘分析
   - 板块梳理
   - 情绪周期
   - 风格化赚钱效应
   - 龙头 / 最票识别（**Agent 主动推荐**：进入此步时，Agent 先读取预填候选 `step5_leaders` + 历史最票 `leader_tracking` + 前 4 步上下文 + [最票方法论](../sector-projection-analysis/references/leader-identification.md)，综合分析后输出结构化最票推荐，标注 `[判断]`，等用户确认/修改后再录入）
   - 节点判断
   - 持仓检视
   - 次日计划
4. 保存前先给用户一版结构化复盘摘要，用户确认后再写入。
5. 若用户要把复盘直接衔接到次日计划，保存后调用 `POST /api/review/{date}/to-draft`，只生成 observation / draft，不确认正式计划。

## 保存字段契约

复盘工作台是结构化表单。Agent 写入时优先使用页面字段：

- `step1_market.notes`
- `step2_sectors.selection_summary / projections / next_day_focus / notes`
- `step3_emotion.phase / transition.reason / notes`
- `step4_style.preference / effects / notes`
- `step5_leaders.top_leaders / transition / notes`
- `step6_nodes.market_node / sector_node / style_node / overall`
- `step7_positions.positions[].action_plan`
- `step8_plan.key_factor / watch_directions / risks / discipline / summary`

`PUT /api/review/{date}` 会兼容摘要式 `facts / judgement / plan / holdings` 写入并轻量映射到页面可见字段，但这只是兜底；正式复盘稿仍应按上面的表单字段组织，避免“API 保存成功但页面看不到重点内容”。

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
