---
name: cognition-evolution
description: 从老师观点提炼可复用交易认知，通过实例验证与周期复盘让交易体系持续进化（Phase 1b 手动闭环版，默认输出强候选）
version: "1.1"
---

# Skill: 交易认知进化（手动闭环版）

## 使用场景

当用户说：

- 「帮我从昨天老师观点里抽认知」
- 「给 `cog_xxx` 加一条实例」
- 「把 `inst_xxx` 验证一下」
- 「生成本周认知复盘 / 生成修复右侧窗复盘」
- 「这条候选认知要不要升 active」
- 「看一下当前有哪些 candidate 认知 / 某分类下的 active 认知」
- 「列出今日可验证的 pending 实例」

时激活此 skill。

## Phase 1b 范围说明

本版本是 **Phase 1b「手动闭环」**：

- Agent 负责结构化提炼与写入辅助，**所有认知的新建 / 升级 / 合并 / 弃用必须由用户确认**
- 实例验证必须挂到事实层（`daily_market` / `market_fact_snapshots` / `teacher_notes` 等），无事实源保持 `pending`
- 周期复盘由 CLI 生成 `draft`，用户补充 `user_reflection` 后再 `confirm`

### Phase 1b 已落地

- `CognitionService` CRUD（add/list/show/refine/deprecate + add_instance/batch/validate/list + review generate/show/confirm + **list_reviews**）
- CLI 命令组 `knowledge cognition-* / instance-* / review-*`（`scripts/main.py`），含 **`review-list`**
- **只读 Web 看板 `/cognition`**（`web/src/pages/CognitionWorkbench.tsx` + `scripts/api/routes/cognition.py`，5 个 GET 端点）
- Schema 三张表 + 触发器维护 `instance_count` / `validated_count` / `invalidated_count` / `confidence`
- `UNIQUE(cognition_id, observed_date, source_type, source_note_id)` NULL 漏洞的 service 层 existence check
- `outcome_fact_source` 格式正则校验（`<table>[:<sub>]:<YYYY-MM-DD>`）
- `--input-by` 非空校验

### Phase 1b 已知降级项（service 层当前未实现）

| 能力 | 当前状态 | 目标阶段 |
|---|---|---|
| `conflict_group` 同组 active 冲突告警 | 字段已存，service 层不告警 | Phase 2 |
| `teacher_aliases` / `topic_aliases` 归一 | YAML 已备，service 层未调用 | Phase 2 |
| 基于 `teacher_notes` 自动回填 `teacher_id` / `teacher_name_snapshot` | 未实现，需调用方自行传入 | Phase 2 |
| `generate_review` 使用交易日历 | 仅做字符串区间，`trading_day_count` 未填充 | Phase 3 |
| 多老师共识度自动计算 / 高共识标记 | 未实现 | Phase 2 |
| 种子认知库（`config/seed_cognitions.json`）匹配 | 未实现 | Phase 2 |
| `instance-batch-add` 原子事务 | 逐条独立，失败项进 `failed`，成功项仍落库 | Phase 2 |
| `cognition-merge` CLI 命令 | 未实现 | Phase 2 |
| `sub_category` 与 `cognition_taxonomy.yaml` 严格校验 | 仅一级 `category` 严格校验 | Phase 2 |
| 周期复盘共识/分歧摘要自动生成 | 未实现 | Phase 3 |
| 认知写入 UI（Web 直接 add / refine / validate） | 未实现，仅 CLI 可写 | Phase 4 |
| Web `conflict_group` 自动告警 | 未实现（看板以红底高亮替代） | Phase 2 |
| 列表端点 `total` 字段语义 | 当前为「本页返回条数」（= `len(items)`），非符合条件的全量计数，分页场景下不可当作总命中数 | Phase 2 |
| 复盘日期筛选语义 | `/api/cognition/reviews` 的 `date_from/date_to` 语义为「period_start ≥ date_from AND period_end ≤ date_to」（即完全落入窗口内），不是区间相交 | Phase 2 |

若需上述能力，按 [`交易认知进化系统-v2` 方案](../../plans/交易认知进化系统-v2_10b8e3d2.plan.md) 推进 Phase 2+。

## 核心流程

完整闭环分 5 步，按顺序执行。所有写入命令必须带 `--input-by cursor | claude | web | manual`。

### 步骤 1：老师观点结构化提炼

1. 读取目标 `teacher_notes`（通过 `python3 main.py db query-notes ...` 或 API `/api/teacher-notes`）
2. 按方案 §5.1 要求，为每条观点输出结构化项清单并交用户确认：

| 项 | 必填 | 说明 |
|---|---|---|
| `canonical_topic` | 是 | 归一后的主题（如「修复后等回踩」「算力租赁」） |
| `time_horizon` | 是 | `intraday` / `swing` / `mid_term` / `long_term` / `structural` |
| `action_bias` | 是 | `low_absorb` / `reduce_on_strength` / `do_t` / `hold_base_position` / `value_hold` / `track_earnings` / `defense_counter` / `embrace_new_high` 之一 |
| `position_cap` | 否 | 老师明确给出仓位上限时填（如 `0.6`） |
| `avoid_action` | 否 | 如 `avoid_chasing_strength` / `avoid_full_position` / `avoid_late_entry` |
| `market_regime` | 否 | `incremental` / `stock` / `decremental` |
| `cross_market_anchor` | 否 | 跨市场信号锚点，如 `["offshore_rmb","crude_oil"]` |
| `invalidation_conditions_json` | 否 | 失效条件 JSON |
| `consensus_key` | 是 | 跨老师聚合键，如 `market:repair_then_pullback:low_absorb` |

3. **Phase 1b 手工归一**：老师别名归一（`沈纯` / `沈淳` 合并到 canonical teacher）与板块同义词归一（`算力` / `AI算力` / `国产算力` / `算力租赁`）**由 Agent 在提炼阶段完成**后再进入下一步——service 层 Phase 1b 未调用 `config/cognition_taxonomy.yaml` 的 `teacher_aliases` / `topic_aliases`，也不会自动从 `teacher_notes` 回填 `teacher_id` / `teacher_name_snapshot`，Agent 传入的值会被原样保存

### 步骤 1.5：候选认知质量协议（默认强候选，不做占位版）

用户说「能提取什么交易认知」「这篇能沉淀什么认知」「提炼认知候选」时，**默认先停留在候选预览态，不直接写库**。候选质量规则见 [references/cognition-candidate-rules.md](references/cognition-candidate-rules.md)，最小执行标准如下：

1. **默认输出 1-4 条最值得长期保留的候选**，不要为了“覆盖全面”堆很多弱条目
2. 每条候选在写库前必须至少回答清楚 6 件事：

| 项 | 作用 | 最低要求 |
|---|---|---|
| `title` | 认知标题 | 不是当期观点口号，半年后仍能看懂 |
| `description` | 为什么成立 | 说明驱动逻辑，不得只写一句标签式结论 |
| `pattern` | 如何复用 | 能抽象到下一次类似盘面 / 行业 / 周期中使用 |
| `applicability` | 适用边界 | 说明适用于什么盘面、周期、结构或数据条件 |
| `invalidation` | 失效边界 | 说明什么时候不能再套用这条认知 |
| `why_keep` | 值得保留的理由 | 解释它为什么比“当期观点”更值得入库 |

3. **若不值得落库，要明确说“不建议落库”以及原因**；不要把一次性观点、估值口号或标题党硬抬成认知
4. 若用户追问「太简单」「再精炼」：**默认先给 refine 候选文案，再等确认后执行 `cognition-refine`**
5. 仅当用户明确说「确认落」「确认 refine」「写入」等，才进入 CLI 写入阶段

> 一个常见误区是：为了方便确认，先给“能落库”的短描述。这里默认反过来处理：**先给能长期复用的强候选，再让用户决定是否写库**。

### 步骤 2：匹配已有认知 or 新建候选

1. 列出可匹配的现有认知：

```bash
python3 main.py knowledge cognition-list --category signal --status active --json
python3 main.py knowledge cognition-show --id cog_a1b2c3d4 --json
```

2. 若能匹配现有 `active` 认知 → 直接跳到步骤 3 写实例
3. 若无匹配 → 先按“强候选卡片”向用户展示，再新建 `candidate` 级认知

推荐展示格式：

```md
候选 X
标题：高位分支退潮后，资金更容易回流同一大主题下的低位承接环节
分类：structure.rotation
description：当大主题核心驱动未被证伪时，某个高位补涨分支的退潮往往不代表整条主线结束，资金更常见的路径是在同一主题内部迁徙，寻找共享底层逻辑、但位置更低且预期差更大的承接环节。
pattern：当{高位补涨分支退潮}且{主线核心驱动未破坏}且{存在共享底层逻辑的低位分支}时，{资金更可能在主题内部迁徙而非整体撤退}→{优先跟踪同逻辑低位承接，而不是直接判断主线结束}
适用边界：主题主线仍在、核心中军未破坏、资金仍围绕同一产业叙事
失效边界：主题核心中军同步走弱；主线成交量明显衰减；资金切向完全不同的大主题
为什么值得保留：它是复用性很强的轮动框架，不绑定单一板块名，可直接用于后续复盘和盘前判断
```

确认后再执行：

```bash
python3 main.py knowledge cognition-add \
  --category structure \
  --sub-category rotation \
  --title "高位分支退潮后，资金更容易回流同一大主题下的低位承接环节" \
  --description "当一个产业大主题已经形成共识时，高位补涨分支的退潮往往不代表整条主线结束。只要主题核心驱动未被证伪，资金更常见的路径是在同一主题内部重新寻找逻辑相通、位置更低、预期差更大的承接环节。" \
  --pattern "当{高位补涨分支退潮}且{主线核心驱动未破坏}且{存在共享底层逻辑的低位分支}时，{资金更可能在主题内部迁徙而非整体撤退}→{优先跟踪同逻辑低位承接，而不是直接判断主线结束}" \
  --evidence-level hypothesis \
  --time-horizon swing \
  --action-template hold_base_position \
  --conditions-json '{"theme":"同一产业大主题","core_driver":"未证伪","rotation_type":"高位退潮后的内部迁徙"}' \
  --invalidation-conditions-json '["主题核心中军同步走弱","主线成交量明显衰减","资金切向完全不同的大主题"]' \
  --tags '["轮动","高位分支","低位承接"]' \
  --first-source-note-id 42 \
  --input-by cursor
```

4. `conflict_group` 同组 `active` 认知的自动告警 **在 Phase 1b 未实现**（字段已落库，Phase 2 由 service 层补告警）；当前若需发现同组冲突，由用户手工 `cognition-list --conflict-group <label> --status active` 检查，再决定是否 `cognition-deprecate` 旧认知或保留并存

### 步骤 3：写入实例

单条实例：

```bash
python3 main.py knowledge instance-add \
  --cognition-id cog_a1b2c3d4 \
  --observed-date 2026-04-14 \
  --source-type teacher_note \
  --source-note-id 42 \
  --teacher-id 3 \
  --time-horizon swing \
  --action-bias low_absorb \
  --position-cap 0.6 \
  --consensus-key market:repair_then_pullback:low_absorb \
  --regime-tags-json '{"emotion_phase":"分歧"}' \
  --teacher-original-text "原文片段..." \
  --input-by cursor
```

同一次提炼产生多条时批量写入：

```bash
python3 main.py knowledge instance-batch-add \
  --file instances_batch.json \
  --input-by cursor
```

> `--file` 接受 JSON 数组，每条字段与 `instance-add` 一致。**Phase 1b 行为（非原子）**：service 层逐条独立调用 `add_instance`，失败项进入响应的 `failed` 数组（含 `reason`），成功项已经落库不会回滚；同一数组内即使某条重复或字段校验失败，其他成功条目仍会保留。若需原子批次，见方案 Phase 2 Backlog（`--atomic` 开关或单事务改造）。
>
> 响应结构：`{"created": ["inst_xxx", ...], "failed": [{"item": {...}, "reason": "instance_exists: inst_yyy ..."}, ...], "total": N}`。

### 步骤 4：盘后验证

1. 列出今日可验证实例：

```bash
python3 main.py knowledge instance-pending --check-ready --date 2026-04-15 --json
```

2. 对每条 `pending` 实例执行验证：

```bash
python3 main.py knowledge validate \
  --instance-id inst_e5f6g7h8 \
  --outcome validated \
  --outcome-fact-source "daily_market:2026-04-15" \
  --outcome-detail "4/15 高开 +0.8%，盘中最高 4010，符合惯性冲高判断" \
  --input-by cursor
```

3. `--outcome-fact-source` **必填且必须是 `<table>[:<sub>]:<YYYY-MM-DD>` 格式**（如 `daily_market:2026-04-15` 或 `market_fact_snapshots:index:2026-04-15`）。Phase 1b service 层已实现：① 正则格式校验；② 白名单校验（`daily_market` / `market_fact_snapshots` / `fact_entities`）；③ **查表校验事实记录是否真实存在**。三项任一失败均拒绝 outcome 变更（保持 `pending`）
4. 若依赖多事实源（成交量 + 汇率 + 板块快照），必须补 `--outcome-fact-refs-json`：

```bash
--outcome-fact-refs-json '["daily_market:2026-04-15","market_fact_snapshots:index:2026-04-15","market_fact_snapshots:fx:2026-04-15"]'
```

5. 方法论类认知（`not_applicable` 策略）直接写实例时标记，不走逐实例验证

### 步骤 5：周期复盘

1. 生成复盘草稿（支持三类窗口）：

```bash
python3 main.py knowledge review-generate \
  --period-type weekly \
  --scope calendar_period \
  --from 2026-04-13 \
  --to 2026-04-17 \
  --input-by cursor

python3 main.py knowledge review-generate \
  --period-type weekly \
  --scope event_window \
  --regime-label "清明假期风险窗" \
  --from 2026-04-03 \
  --to 2026-04-08 \
  --input-by cursor

python3 main.py knowledge review-generate \
  --period-type monthly \
  --scope regime_window \
  --regime-label "修复右侧窗" \
  --from 2026-04-01 \
  --to 2026-04-30 \
  --input-by cursor
```

2. 查看生成的草稿：

```bash
python3 main.py knowledge review-show --id rev_xxx --json
```

3. 用户补充 `user_reflection` / `action_items_json` 后确认：

```bash
python3 main.py knowledge review-confirm \
  --id rev_xxx \
  --user-reflection "本周..." \
  --input-by manual
```

> **Phase 1b 降级**：`review-generate` 当前把 `--from` / `--to` 直接当字符串区间落入 `period_start` / `period_end`，**未接入 `scripts/utils/trade_date.py` 交易日历**，因此 `trading_day_count` 暂不填充、周期边界非交易日时需使用方自行对齐。Phase 3 复盘引擎会补齐交易日序列聚合。

## 关键约束

- **`--input-by` 必填**：所有写入命令必须带 `--input-by cursor | claude | web | manual`，空值或缺省由 service 层拒绝（与现有 `knowledge` 规范一致）
- **Agent 不得写 `confirmed` 级 `TradePlan`**：本 skill 范围仅到认知 / 实例 / 复盘层；映射计划走 [`plan-workbench`](../plan-workbench/SKILL.md)
- **新建认知默认 `status=candidate`**：升 `active` 必须由用户显式确认（例如用户说「这条先留为 candidate 观察 3 个实例再升」）
- **默认输出强候选，不做占位版短描述**：若候选还无法清楚说明“为什么成立 / 适用于何处 / 何时失效”，先继续打磨或明确“不建议落库”，不要为了尽快写库输出一条过短 description
- **认知状态流转**：`cognition-add` 仅允许 `candidate` / `active` 初始状态；`deprecated` 必须走 `cognition-deprecate`；`merged` **仅允许通过 merge 流程达成（Phase 1b `cognition-merge` CLI 未实现，当前不可用）**
- **`outcome_fact_source` 校验**：`<table>[:<sub>]:<YYYY-MM-DD>` 格式；表名必须 ∈ 白名单 `{daily_market, market_fact_snapshots, fact_entities}`；service 层会真实查表（按对应日期字段）确认记录存在。三项任一失败均抛 `ValueError` 并保持 `outcome=pending`
- **多事实源必填 `outcome_fact_refs_json`**：验证同时依赖成交量、汇率、融资数据、板块快照等多个锚点时，必须写完整事实引用数组；`outcome_fact_source` 仅保留主引用
- **`conflict_group` 冲突告警 (Phase 2)**：`cognition-add` / `cognition-refine` 命中同组 `active` 认知时的 service 层 warning **Phase 1b 未实现**；字段已落库，需手工 `cognition-list --conflict-group <label>` 排查
- **老师别名归一 (Phase 2)**：`teacher_aliases` / `topic_aliases` 在 `config/cognition_taxonomy.yaml` 已备，但 **Phase 1b service 层未调用归一**；`source_type=teacher_note` 时 `teacher_id` / `teacher_name_snapshot` 由调用方显式传入，**service 层不会自动从 `teacher_notes` 回填**
- **实例唯一性 NULL 漏洞 + 重复写入行为**：`UNIQUE(cognition_id, observed_date, source_type, source_note_id)` 在 `source_note_id=NULL` 时 SQLite 约束失效，service 层 `add_instance` 写入前做 existence check。**命中重复组合时抛 `ValueError`**，CLI 返回 `status=validation_error` + `message` 含 `instance_exists: <已有 instance_id>` 提示；调用方需自行决定是否使用已有 `instance_id` 还是放弃写入（**不是静默去重**）
- **批量写入非原子**：`instance-batch-add` 逐条独立调 `add_instance`，失败项进入响应 `failed`，成功项保留落库不回滚；同一批次内的重复/字段错误不会影响其他条目。若需原子批次，见 Phase 2 Backlog
- **触发器统计维护**：`instance_count` / `validated_count` / `invalidated_count` / `confidence` 全部由 DB 触发器维护；禁止在 service 或 CLI 层直接写这些列
- **交易日历 (Phase 3)**：`review-generate` 当前只把 `--from` / `--to` 作为字符串区间写入 `period_start` / `period_end`；**`scripts/utils/trade_date.py` 交易日历尚未接入**，`trading_day_count` 暂不填充

## 禁止事项

- **禁止**绕过 CLI 直接 SQL 写 `trading_cognitions` / `cognition_instances` / `periodic_reviews` 三张表
- **禁止**未经用户确认直接把 `candidate` 升 `active`（包括通过 `cognition-refine` 隐式升级）
- **禁止**用 `cognition-refine --status merged` 把认知改为 `merged` 状态：`merged` 只能通过 merge 流程达成；Phase 1b `cognition-merge` CLI 未实现前，**不应出现 `merged` 状态的新增行**
- **禁止**在无 `outcome_fact_source` 或事实源记录不存在时，把 `outcome` 改为 `validated` / `invalidated`（只能保持 `pending`）
- **禁止**用自由文本老师名填 `teacher_name_snapshot` 而不经确认；Phase 1b service 层不做别名归一，调用方需自行保证 `teacher_id` 与 `teacher_name_snapshot` 来源真实
- **禁止**对同一 `(cognition_id, observed_date, source_type, source_note_id)` 重复写实例凑数量；重复写会被 service 层拒绝并抛 `instance_exists`，**不是静默去重**
- **禁止**在 `review-generate` 时绕过将来接入的交易日历手工拼凑日期（Phase 1b 虽只校验字符串区间，仍应使用实际交易日边界）
- **禁止**把主观判断伪装成 `outcome_detail` 里的「已验证事实」（原文证据必须来自 `teacher_original_text` / `outcome_fact_refs_json`）

## 最小验证

每轮闭环结束后逐项自检：

- `python3 main.py knowledge cognition-list --status candidate --json` 能拿到候选清单（若本轮有新建）
- `python3 main.py knowledge instance-add ...` 返回 `instance_id`；重复写入同一 `(cognition_id, observed_date, source_type, source_note_id)` 组合时 **返回 `status=validation_error` + `message` 含 `instance_exists`**，且已有实例的 `instance_id` 会附带在 message 中
- 写入后 `python3 main.py knowledge cognition-show --id <id> --json` 的 `instance_count` 应 +1（触发器已维护）
- `python3 main.py knowledge validate ...` 成功后，对应认知的 `validated_count` / `invalidated_count` / `confidence` 应相应刷新
- 周期复盘生成后，`python3 main.py knowledge review-show --id <id> --json` 能读到 `status=draft` 且 `active_cognitions_json` / `validation_stats_json` 等快照字段已填
- 若本轮用户先要求“提取候选”再确认写入：候选展示里应能清楚看到 `title / description / pattern / 适用边界 / 失效边界 / why_keep` 六项，不应只剩标题和一句话描述

## Web 查看入口（只读）

Phase 1b + 增量交付了只读 Web 看板，路径：`http://localhost:5173/cognition`（`web/src/pages/CognitionWorkbench.tsx`）。看板用三个 tab 展示：

- **认知库**：按 `category` / `status` / `conflict_group` / `keyword` 过滤，点击行展开详情（`pattern` / `conditions_json` / `invalidation_conditions_json` / `tags`）；**同 `conflict_group` 多条会高亮红底**（`conflict_group` Phase 2 自动告警的临时替代）
- **实例**：按 `cognition_id` / `outcome` / `date_from/to` 过滤；`outcome` 用颜色 badge（pending 黄 / validated 绿 / invalidated 红）；点击行展开 `context_summary` / `teacher_original_text` / `outcome_detail` / `lesson`
- **复盘**：按 `period_type` / `status` / 日期区间过滤；点击行展开 `validation_stats_json` / `key_lessons_json` / `user_reflection` / `action_items_json`

### 对应 API（只读）

| 端点 | 主要过滤参数 |
|------|-------------|
| `GET /api/cognition/cognitions` | `category, sub_category, status, evidence_level, conflict_group, keyword, limit, offset` |
| `GET /api/cognition/cognitions/{id}` | — |
| `GET /api/cognition/instances` | `cognition_id, outcome, teacher_id, source_type, date_from, date_to, limit, offset` |
| `GET /api/cognition/reviews` | `period_type, review_scope, status, date_from, date_to, limit, offset` |
| `GET /api/cognition/reviews/{id}` | — |

### 范围与限制

- **仅读**：所有写操作（`cognition-add / refine / deprecate / instance-add / validate / review-generate / confirm`）仍走 CLI，Web 不提供任何写入
- **默认 limit**：API 与 Web 默认 `limit=100`（与 CLI `--limit 20` 差异有意为之，看板场景合理放大）
- **JSON 字段**：API 层统一 `json.loads` 返回对象/数组，前端不再 parse
- **未实现**（Phase 4 完整版再做）：认证中间件、instance-validate UI、conflict_group 自动告警、teacher_aliases 归一显示

### 启动方式

```bash
# 启动后端 + 前端
make dev-api   # uvicorn scripts.api.main:app
make dev-web   # vite dev
# 浏览器打开 http://localhost:5173/cognition
```

## 切换条件

- 若用户要把旧观点批量回填到 `teacher_notes` → 先走 [`record-notes/SKILL.md`](../record-notes/SKILL.md) 录入，再回到本 skill 做认知提炼
- 若用户要把认知映射为次日交易计划 → 切到 [`plan-workbench/SKILL.md`](../plan-workbench/SKILL.md)
- 若用户要从课程笔记 / 新闻资料生成 observation → 切到 [`knowledge-to-plan/SKILL.md`](../knowledge-to-plan/SKILL.md)
- 若用户要在八步复盘中**引用**（而非写入/验证）现有认知 → 切到 [`daily-review/SKILL.md`](../daily-review/SKILL.md)：`GET /api/review/{date}/prefill.cognitions_by_step` 已按八步聚合 active 认知；复盘对话中**禁止** `knowledge instance-add / knowledge validate / knowledge cognition-refine`，相关写操作发生后再回到本 skill 处理
- 若 CLI 命令签名漂移（pytest 不过、命令重命名、`INDEX.md` 与 `test_cli_smoke.py` 不一致）→ 切到 [`repo-maintenance-workflows/SKILL.md`](../repo-maintenance-workflows/SKILL.md)
- 若想启用 Phase 2 自动提取 / 种子库匹配 / 共识度计算 → 升级到 `cognition-evolution` 完整版（Phase 2 交付后替换本文件）

## 结果汇报格式

每次闭环结束，按以下模板汇报：

```
认知提炼闭环 <日期 / 窗口>：
- 匹配已有认知：N 条 → 新增实例 M 条
- 新建 candidate 认知：X 条（待用户确认是否升 active）
- 批量写入：created K 条 / failed F 条（含 instance_exists 去重提示）
- 验证结果：validated Y / invalidated Z / partial P / pending Q（缺事实源）
- 本轮复盘：<review_id>（若触发了 review-generate）
- conflict_group 手工排查：<列表>（若有；Phase 1b 自动告警未实现）
- 待确认项：<升 active / 弃用的候选清单；merge 流程 Phase 2 交付>
```
