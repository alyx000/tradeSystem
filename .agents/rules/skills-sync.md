---
description: 当修改 CLI 或 API 文件时，提醒同步更新 skills 文档
globs:
  - scripts/db/cli.py
  - scripts/db/migrate.py
  - scripts/db/schema.py
  - scripts/main.py
  - scripts/cli/wechat_teacher_feed.py
  - scripts/cli/review_factors.py
  - scripts/cli/tail_scan.py
  - scripts/cli/daily_leaders.py
  - scripts/services/trinity_factor/*.py
  - scripts/services/tail_scan/*.py
  - scripts/services/daily_leaders/*.py
  - scripts/providers/base.py
  - scripts/providers/tushare_provider.py
  - scripts/api/routes/*.py
  - .agents/skills/**/*.md
  - .cursor/skills/**/*.md
---

# Skills 同步检查规则

## 触发条件

当你修改以下任何文件时，此规则自动触发：

- `scripts/db/cli.py` — CLI 子命令定义
- `scripts/db/migrate.py` / `scripts/db/schema.py` — schema 版本、显式迁移门禁与唯一索引契约
- `scripts/main.py` — 顶层命令注册（pre/post/schedule 等）
- `scripts/cli/wechat_teacher_feed.py` — 微信公众号白名单 phase、归档、候选过滤与失败语义
- `scripts/cli/review_factors.py` / `scripts/services/trinity_factor/*.py` — 三位一体评分、人工确认、T+1 与影子指标语义
- `scripts/cli/tail_scan.py` / `scripts/services/tail_scan/*.py` — 尾盘实时筛选、逐票产业逻辑、证据边界与报告/推送语义
- `scripts/cli/daily_leaders.py` / `scripts/services/daily_leaders/*.py` — 每日最票候选的板块口径、属性、收敛上限、LLM 复核与确定性降级语义
- `scripts/services/tail_scan/concept_context.py`，或 `scripts/providers/base.py` / `scripts/providers/tushare_provider.py` 中 `get_stock_concept_memberships` capability — 尾盘扫描当前归属与 T-1 热概念分层语义
- `scripts/api/routes/*.py` — API 路由定义
- `.agents/skills/**/*.md` — skill 文档本身（真源；`.cursor/skills/` 与 `.claude/skills/` 是 symlink 壳）

## 必须执行的检查清单

### 0. 先判断是否存在统一入口

更新 `.agents/skills/**/*.md` 时，优先检查仓库根目录 `Makefile` 是否已经提供等价入口：

- 检查优先写 `make check` / `make check-web` / `make check-scripts`
- 开发启动优先写 `make dev` / `make dev-api` / `make dev-web`
- 日常任务优先写 `make today-*`
- 若 `Makefile` 已提供别名，SKILL.md 示例里应优先展示 `make` 入口，再补充底层 `python3 main.py ...`

`INDEX.md` 里的依赖表仍保留真实底层 CLI/API，不要改成 `make` 目标名。

### 1. 检查 INDEX.md 是否需要更新

打开 `.agents/skills/INDEX.md`，逐行核对：

- [ ] 所有新增 CLI 子命令都已添加到依赖表
- [ ] 所有重命名的命令已在表中更新
- [ ] 所有删除的命令已从表中移除
- [ ] 新增 API 端点已添加到 API 依赖表
- [ ] `main.py` 新增的 `ingest/plan/knowledge` 命令已同步到相关 skill
- [ ] `review factor-*` 的双层评分、人工确认、严格 T+1、20 日影子指标及“不进入 TradeDraft”边界已同步到 `daily-review` / `sector-projection-analysis`
- [ ] 若命令已从“骨架”变为真实可执行，移除 `SKILL.md` / `INDEX.md` 中的“规划中/骨架”描述

### 2. 运行 Smoke 测试

```bash
make check-scripts
```

若仅需快速验证 CLI 签名，也可单独运行：

```bash
python3 -m pytest scripts/tests/test_cli_smoke.py -v
```

- 所有测试必须通过才算完成
- 若有失败，说明 skill 引用的命令签名已过期，必须同时修复：
  - `cli.py` 中的命令定义，或
  - `test_cli_smoke.py` 中的 `ALL_SKILL_COMMANDS`，以及
  - 对应 `SKILL.md` 中的使用示例

### 2.1 新增顶层 subparser 时必加 ARCHITECTURE_COMMANDS

**`scripts/main.py` 新增任何顶层子命令组**（如 `review` / `recommend` / `new-high` / `ingest` / `plan` / `knowledge` / `executions` 等），不仅要在 `INDEX.md` 加依赖行，**还必须**：

1. 在 `scripts/tests/test_cli_smoke.py` 的 `ARCHITECTURE_COMMANDS` 数组里**加参数化用例**，覆盖：
   - 子命令的所有 mode（如 `recommend daily` / `recommend weekly`）
   - 常用选项组合（`--dry-run` / `--lookback-days` 等）
   - 至少 1 条最简形式 + 1 条全选项形式
2. 这些用例由现有 `test_architecture_command_parseable` 跑参数化校验，仅做 `parser.parse_args()`，**不真实执行命令**
3. 验证：`pytest scripts/tests/test_cli_smoke.py -k <new-cmd> -v` 必须全绿

**为什么**：INDEX.md 写了新命令、SKILL.md 给了示例，但如果 argparse 实际签名不对（参数名错、required 漏标、subparser 没注册），Agent 调用就会失败。`ALL_SKILL_COMMANDS` 校验 `db` 子命令，`ARCHITECTURE_COMMANDS` 校验 `main.py` 顶层子命令，两者分工不能漏。

行业推荐项目（2026-05-16）G3 R12 实战：先在 `ARCHITECTURE_COMMANDS` 加 5 条 `recommend` 参数化 → 跑出 `invalid choice: 'recommend'` RED → 再去 `main.py` 注册 subparser → 验证 GREEN。严格按这个顺序走的项目内 CLI 永远不会出现"agent 调用失败但代码全绿"的悬空状态。

### 3. 检查受影响的 SKILL.md

根据改动内容，检查对应 skill 文档：

| 改动文件 | 需检查的 SKILL.md |
|---------|-----------------|
| `cli.py` 的 `add-note/add-industry/add-macro` | `record-notes/SKILL.md` |
| `cli.py` 的 `stock-resolve` | `record-notes/SKILL.md`、`portfolio-manager/SKILL.md` |
| `cli.py` 的 `holdings-*`（含 `--thesis-id` 关联语义）/ `watchlist-*` / `add-trade` / `blacklist-*` | `portfolio-manager/SKILL.md` |
| `cli.py` 的 `query-notes/db-search` | `daily-review/SKILL.md` |
| `cli.py` 的 `db backup/db migrate` 或 `db/migrate.py` / `db/schema.py` 的 teacher_notes provenance | `repo-maintenance-workflows/references/teacher-notes-v40-migration.md`、`record-notes/SKILL.md`、`INDEX.md`；保留停写→0600 完整备份→源快照 SHA 绑定→显式原子迁移边界，普通入口不得隐式跨 v39→v40 |
| `main.py` 的 `pre/post/schedule` | `market-tasks/SKILL.md` |
| `main.py` / `scripts/cli/wechat_teacher_feed.py` / `scripts/services/wechat_teacher_feed/` | `market-tasks/SKILL.md`、`record-notes/SKILL.md`、`record-notes/references/ingestion-rules.md`、`INDEX.md` 与 `AGENTS.md`；保持“采集只落 manifest、按 digest 确认后才 add-note、默认不入池”边界 |
| `scripts/main.py` 的 `cmd_post` new-high 接线、`scripts/cli/new_high.py`、`scripts/services/new_high/` 或 `scripts/utils/trade_date.py` 的 new-high 日历语义 | `market-tasks/SKILL.md` + `INDEX.md` 中 `new-high` 行；须核对复用 `today-post` 工作日 20:00 单一调度、无独立 launchd/APScheduler、只写两表及目标日报告且不自动推送、schema/基线相等/自然日日历完整、行业源非空、有效行情绝对地板、重复/有效 join/复权宇宙/申万覆盖/相邻日市场数门禁、按开放日动态升序补缺、canonical 只追加、单日同事务、`BEGIN IMMEDIATE` 二次查重 + 尾日 CAS、成功前缀与失败续跑、目标日报告原子替换及 `already_complete` 损坏自愈、手工 `daily` 连续协调/`backfill` 强刷年度日历且拒绝跳过尾日后开放日/历史更正须重建后缀、失败隔离且不阻断 margin 的完整契约 |
| `scripts/cli/trend_leader.py` 或 `scripts/services/trend_leader/` | `market-tasks/SKILL.md` + `INDEX.md` 中 `trend-leader` 行 + `AGENTS.md` / `CLAUDE.md`；必须核对自动申万主线最近最多 3 个有效集中度快照（空/全部 UNCLASSIFIED 不计）、2～3 条至少命中 2 次/仅 1 条命中 1 次、`--sectors` 绕过稳定门、默认 `hybrid` LLM 失败关闭概念并标 `fallback_l2`、显式 `hybrid --no-llm` / `l2+concept` 机械分支、报告快照数/门槛/来源/LLM 状态及不回溯清理历史池边界 |
| `scripts/cli/tail_scan.py` 或 `scripts/services/tail_scan/` | `market-tasks/SKILL.md` + `INDEX.md` 中 `tail-scan` 行 + `AGENTS.md` / `CLAUDE.md` |
| `scripts/cli/daily_leaders.py` 或 `scripts/services/daily_leaders/` | `market-tasks/SKILL.md` + `INDEX.md` 中 `daily-leaders` 行 + `AGENTS.md` / `CLAUDE.md`；核对申万二级板块口径、语义属性与板型分离、同板块同属性唯一、最终最多 15、LLM 失败仍按相同硬约束兜底，并保持展示内代码解析、非法后缀及显式代码冲突 fail-closed |
| `scripts/services/tail_scan/concept_context.py`，或 provider 的 `get_stock_concept_memberships` capability | `market-tasks/SKILL.md` + `INDEX.md` 中 `tail-scan` capability/消费者/字段用途 + `AGENTS.md` / `CLAUDE.md`；必须核对当前 `type=N` 快照（非历史 as-of）与 T-1 热概念分层、共享容器过滤、报告 5 个/2 个上限、完整归属不进粗分/PK、兼容热字段语义，以及 `source_failed` / `coverage_failed` / `member_failed` / `missing` 状态不混淆 |
| `scripts/utils/llm_cli.py` 或 LLM CLI/env 语义调整 | `market-tasks/SKILL.md` + `INDEX.md` 中 recommend/research-digest/cognition-digest 行 |
| `scripts/workflows/research-digest-workflow.mjs` / `scripts/workflows/huibo_helper.py` / 慧博 Antigravity 诊断语义调整 / `HUIBO_REPORT_PDF_DIR` 下载归档目录约定调整 | `market-tasks/SKILL.md` + `INDEX.md` 中 research-digest workflow 行 |
| `main.py` 的 `ingest *` | `ingest-inspector/SKILL.md` |
| `main.py` 的 `plan *` | `plan-workbench/SKILL.md` |
| `main.py` 的 `knowledge add-note/list/draft-*` | `knowledge-to-plan/SKILL.md` |
| `main.py` 的 `knowledge cognition-* / instance-* / review-*` | `cognition-evolution/SKILL.md`（若涉及观点结构化字段、`feedback_action` 或 `evolving_views_json` 聚合，也同步 `INDEX.md` 中 cognition-evolution / `/api/cognition/instances` 说明） |
| `main.py` 的 `executions import / list / audit-export` | `portfolio-manager/SKILL.md`（券商成交流水事实层，与 `db add-trade` 复盘维度分离） |
| `main.py` / `scripts/cli/review_factors.py` 的 `review factor-*`，或 `scripts/services/trinity_factor/` | `daily-review/SKILL.md`、`sector-projection-analysis/SKILL.md`、`INDEX.md` 与 `AGENTS.md`；必须同步独立客观来源族，并精确区分 `promotion_realization` 仅校验 outcome 评分日、`prior_core_feedback` 显式 `popularity_provenance` 优先且非法 / 错位即拒绝、仅 provenance 键缺失的历史数据允许用同一 `promotion` 日期元数据 fallback；Step 5 人工最票 / 自动 leader 只能作 context 且不得抬高证据质量；同时保留评分/确认/回验仅接受开放交易日、每次 score（含 cache hit）追加 request audit 且 `input_by` 不进 cache key、主因子受控证据卡进入第 2 层、确认原子重建证据摘要并拒绝陈旧 run、后续评分输入写入自动清除旧决定、来源感知严格 T+1、canonical run 指标、append-only retry 与“不进入 TradeDraft/TradePlan”边界 |
| `scripts/cli/executions.py` 任意改动 | `portfolio-manager/SKILL.md` + `INDEX.md` 中 `executions ...` 行 |
| `scripts/services/broker_executions/` 任意改动 | `portfolio-manager/SKILL.md`（若行为契约变更）；任何 schema 字段/UNIQUE 调整还需同步 `INDEX.md` |
| `scripts/services/trade_thesis/` 或 `scripts/db/schema.py` 中 `trade_mode` 语义/枚举调整 | `portfolio-manager/SKILL.md` + `INDEX.md` 中 `thesis-*` 行 |
| 仓库维护工作流、CLI/API 对齐、巡检、文档/索引同步 | `repo-maintenance-workflows/SKILL.md` + `references/maintenance-checklist.md`；诊断、Review 与巡检默认只读，修改须按范围验证 |
| `api/routes/review.py` | `daily-review/SKILL.md`、`sector-projection-analysis/SKILL.md`（含 `POST /api/review/{date}/to-draft` 时也检查 `plan-workbench/SKILL.md`；若预填字段语义调整，如 `lead_stock` / `emotion_leader` / `capacity_leader`，或保存字段标准化语义调整，同步 Skill 文案；`step5_leaders` 身份校验失败必须返回 422，并原子回滚复盘与 tracking 写入） |
| `api/routes/review_factors.py` | `daily-review/SKILL.md`、`sector-projection-analysis/SKILL.md` 与 `INDEX.md`；评分/回验/metrics 路径必须与 `review factor-*` CLI 共用 service 语义 |
| `api/routes/planning.py` 中 `/api/plans/*` | `plan-workbench/SKILL.md` |
| `api/routes/planning.py` 中 `/api/knowledge/*` | `knowledge-to-plan/SKILL.md` |

### 3.1 检查 `agents/openai.yaml` 是否仍匹配

若受影响的 skill 目录中存在 `agents/openai.yaml`：

- [ ] `display_name` 仍与 skill 目标一致
- [ ] `short_description` 仍能准确概括当前 SKILL.md
- [ ] `default_prompt` 仍显式引用 `$skill-name`
- [ ] 若 SKILL.md 已明显改义，重新生成或更新 `agents/openai.yaml`

### 4. 验证报告（每次修改后输出）

```
Skills 同步检查结果：
- [✅/❌] INDEX.md 已更新
- [✅/❌] test_cli_smoke.py 全部通过
- [✅/❌] 受影响的 SKILL.md 已检查并更新（如需）
- [✅/❌] 受影响的 agents/openai.yaml 已检查并更新（如需）
```

## Rules 文件真源 + IDE symlink 壳同步

`.agents/rules/<rule>.md` 是真源，`.claude/rules/<rule>.md` 与 `.cursor/rules/<rule>.mdc` 是 symlink 壳（让两个 IDE 都能加载到真源）。

### 新增 / 重命名 / 删除 `.agents/rules/` 文件时必须的连带操作

| 操作 | `.agents/rules/` | `.claude/rules/` | `.cursor/rules/` |
|---|---|---|---|
| 新增 `<rule>.md` | `git add .agents/rules/<rule>.md` | `ln -s ../../.agents/rules/<rule>.md .claude/rules/<rule>.md && git add` | `ln -s ../../.agents/rules/<rule>.md .cursor/rules/<rule>.mdc && git add` |
| 重命名 `A.md` → `B.md` | `git mv .agents/rules/A.md .agents/rules/B.md` | 删旧 symlink + 建新（路径变了） | 删旧 + 建新（文件名 + 扩展名都变） |
| 删除 `<rule>.md` | `git rm .agents/rules/<rule>.md` | `git rm .claude/rules/<rule>.md` | `git rm .cursor/rules/<rule>.mdc` |

### 验证

```bash
python3 -m pytest scripts/tests/test_agent_symlinks.py -v
```

测试覆盖：
- `.agents/` 真源目录存在
- `.cursor/skills` 与 `.claude/skills` 是符号链接指向 `.agents/skills/`
- `.agents/rules/*.md` 都有对应的 `.claude/rules/<>.md` 与 `.cursor/rules/<>.mdc` symlink

`pre-push` hook 也跑全套 pytest，会兜底 catch；但**别依赖 hook，新增 rule 时主动建好三处**，否则会被 pre-push 拒推 + 需要补一个 amend/follow-up commit。

### 同时必须做的两件事（容易遗漏）

1. **在 `CLAUDE.md` + `AGENTS.md` 的"AI 协作规则"表格里加索引行**：写明该规则的"作用"，让 agent 启动时能看到规则存在。
2. **如果新规则涉及代码触发条件**（如"修改 X 文件时触发 Y 检查"），考虑加进现有规则的 globs / 触发条件章节，或建明确触发链。

参见 [[karpathy-behavior]]（精准修改、清理影响面），[[implementation-plan]]（计划阶段的范围声明）。

## 背景说明

AI Agent（Claude Code / Codex / Cursor）通过 `.agents/skills/` 中的文档了解如何调用 CLI 和 API（`.cursor/skills/` 与 `.claude/skills/` 是 symlink 壳）。
如果 CLI 签名或 API 接口变更而 skill 文档未更新，Agent 将生成错误的命令，导致数据写入失败。
尤其是 `scripts/main.py` 中新增的 `review`、`ingest`、`plan`、`knowledge` 命令组，以及 `api/routes/review_factors.py` / `planning.py` 中的复盘评分、计划和资料接口，会直接影响影子评分 / observation / draft / plan / 采集诊断的协作流。
此规则确保每次底层变更时，skill 文档始终与实际接口保持同步。
