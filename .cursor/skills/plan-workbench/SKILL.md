---
name: plan-workbench
description: 生成、查看、确认、诊断和回写 TradeDraft / TradePlan / PlanReview 的工作台流程
version: "0.1"
---

# Skill: 交易计划工作台

## 使用场景

当用户说：

- 「生成明天的交易计划草稿」
- 「看一下今天的计划草稿」
- 「确认这份计划」
- 「诊断一下这份计划」
- 「回写今天计划执行结果」

时激活此 skill。

## 当前标准 CLI

```bash
make plan-open DATE=YYYY-MM-DD
make plan-draft DATE=YYYY-MM-DD
make plan-show-draft DATE=YYYY-MM-DD
make plan-confirm DRAFT_ID=draft_xxx DATE=YYYY-MM-DD
make plan-diagnose PLAN_ID=plan_xxx
make plan-review PLAN_ID=plan_xxx DATE=YYYY-MM-DD
python3 main.py plan draft --date YYYY-MM-DD
python3 main.py plan show-draft --date YYYY-MM-DD
python3 main.py plan confirm --date YYYY-MM-DD
python3 main.py plan diagnose --date YYYY-MM-DD
python3 main.py plan review --date YYYY-MM-DD
```

若需要结构化输出，附加：

```bash
--json
```

说明：

- 若用户是要进入 Web 工作台本身，优先使用 `make plan-open`
- 高频查看和诊断优先使用 `make plan-*`
- `confirm` / `review` 现已支持参数化 `make` 别名
- 需要补充更细粒度参数时，再使用底层 `python3 main.py plan ...`

## 协作规则

- Agent 只能生成草稿、候选检查项、诊断结果
- Agent 不得绕过人工确认直接写正式 `confirmed` 计划
- `fact_checks` 最终由人确认
- 主观交易语义应进入 `judgement_checks`

## 当前能力

这些命令已经接入真实 service，并会写入：

- `MarketObservation`
- `TradeDraft`
- `TradePlan`
- `PlanReview`
- `PlanDiagnostics`（按需计算）

当前限制：

- `plan draft` 默认生成最小 `manual` observation，再创建 draft
- `fact_checks` 仍以人工确认后的项为准
- Web 端已支持结构化编辑 `observation`、`draft`、`plan`，并可直接编辑 `watch_items` 中的 `fact_checks`、`judgement_checks`、`trigger_conditions`、`invalidations`
- `TradePlan.watch_items` 现已支持显式排序和优先级维护：可上移/下移，并可设置 `priority`
- `watch_items.fact_checks` 现已支持显式排序和优先级维护：可上移/下移，并可设置检查项 `priority`
- JSON 编辑仍保留，但已折叠为“高级 JSON 编辑”；默认工作流应优先使用结构化表单，而不是直接手改 JSON
- CLI 诊断会优先读取 `market_fact_snapshots`；当前已支持市场级快照检查 `northbound_net_positive`、`margin_balance_change_positive`
- 对 `price_above_ma*`、`ret_1d_gte`、`ret_5d_gte`、`announcement_exists`，CLI 可在缺快照时降级查询 provider
- 对 `market_amount_gte_prev_day`、`sector_change_positive`、`sector_limit_up_count_gte`，CLI 可复用 `daily_market` 与盘后信封里的板块扩展字段做诊断
- API 的 `/api/plans/{plan_id}/diagnostics` 现已对齐同一套诊断逻辑；拿不到 provider 时会自动退回快照/DB 模式
- 仍有部分 `fact_checks` 在事实快照不足时会返回 `missing_data`
