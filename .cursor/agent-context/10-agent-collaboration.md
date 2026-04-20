# AI 协作规范

## AI 协作规则（`.cursor/rules/`）

以下规则文件均为 `alwaysApply: true`，Cursor 环境每次对话自动注入；非 Cursor 环境的 Agent 应主动阅读。

| 规则文件 | 作用 |
|---------|------|
| `language.mdc` | 所有 AI 输出使用简体中文，代码标识符保持英文 |
| `karpathy-behavior.mdc` | 行为基线：先校验假设、简洁优先、精准修改、目标驱动验证，减少 Agent 常见失误 |
| `dev-workflow.mdc` | 开发三阶段流程：设计验证方案 → 实现（含单测）→ 执行验证并报告；验证命令参考表 |
| `implementation-plan.mdc` | 实施计划必须含测试验证方案 + 复杂任务多 Agent 并行分组 |
| `solution-format.mdc` | 技术方案 / 执行计划 / 业务逻辑解析默认使用结构化章节、表格与纯 Mermaid 图表输出 |
| `test-design.mdc` | 分层测试设计：金字塔原则、隔离原则、自底向上执行 |
| `subagent-code-review.mdc` | 每轮实质性代码改动后启动 subagent 审查，按高/中/低分类处理 |
| `skills-sync.mdc` | CLI / API / Skills 变更后同步 `INDEX.md`、跑 `test_cli_smoke`、检查受影响 SKILL.md |

## Cursor Skills（项目级工作流）

面向 AI 的可执行工作流（何时调用哪条 CLI/API）位于 **`.cursor/skills/`**：各子目录下的 **`SKILL.md`** 描述具体流程；**`.cursor/skills/INDEX.md`** 汇总各 Skill 依赖的 `db` 子命令与 API。修改 **`scripts/db/cli.py`** 或 **`scripts/api/routes/`** 后，需按 **`.cursor/rules/skills-sync.mdc`** 更新索引并跑 `test_cli_smoke`。

技术方案、执行计划、API 契约的标准模板位于 **`docs/templates/`**，默认包括：

- [`technical-design.md`](/Users/alyx/tradeSystem/docs/templates/technical-design.md)
- [`execution-plan.md`](/Users/alyx/tradeSystem/docs/templates/execution-plan.md)
- [`api-contract.md`](/Users/alyx/tradeSystem/docs/templates/api-contract.md)

涉及架构图、模块依赖、交互流程、状态流转时，统一按 **`.cursor/rules/solution-format.mdc`** 使用 Mermaid 输出，禁止使用 PlantUML。

## Agent 入口与协作语义

系统区分 **人工入口** 与 **Agent 标准入口**：

- **人工入口**：Web / API / CLI 都可用
- **Agent 标准入口**：统一通过 CLI 写入
- **统一语义层**：CLI / API / Web 必须共享同一 service、同一默认值、同一校验与状态流转

当前及后续标准命令组：

- `python3 main.py db ...`
- `python3 main.py ingest ...`
- `python3 main.py plan ...`
- `python3 main.py knowledge ...`

当前已落地的最小闭环：

- `ingest run / run-interface / inspect / retry` 已接入真实 service，并会写 `ingest_runs`、`raw_interface_payloads`、`market_fact_snapshots`
- `plan draft / show-draft / confirm / diagnose / review` 已接入真实 service，并会写 `MarketObservation / TradeDraft / TradePlan / PlanReview`
- `knowledge add-note / list / draft-from-asset` 已接入真实 service：面向 `knowledge_assets`（**禁止**新建 `teacher_note` / `course_note`；API 422 / CLI validation_error），`draft-from-asset` 会生成 `knowledge_asset` observation 与 `TradeDraft`
- `knowledge draft-from-teacher-note` 与 `db add-note` 共用 `teacher_notes` 作为老师观点唯一事实源，生成 `MarketObservation(source_type=teacher_note)` 与 `TradeDraft`，不向 `knowledge_assets` 双写

约束：

- Agent 不允许直接写 SQLite、YAML 或手工拼接 JSON 文件，除非通过标准命令
- 所有 agent 写入命令必须显式带 `--input-by`
- Agent 可触发 `MarketObservation` / `TradeDraft`，但不得绕过确认直接写 `confirmed` 的 `TradePlan`
- **老师观点 `db add-note`（Agent）**：写入前必须先输出**结构化总结**（含 `title`、`core-view`、至少 2 条 `key-points`、**每次**提炼「跟踪个股」映射 `--stocks` 等，长文用 `--raw-content-file`/stdin 保留原文），经**用户明示确认**后再执行 CLI；**关注池**：默认只记笔记与候选输出，**仅当**用户对「是否入池」单独确认后，再使用 `--sync-watchlist-from-stocks` 或 `db watchlist-sync-from-note --note-id`；细则见 [`.cursor/skills/record-notes/SKILL.md`](/Users/alyx/tradeSystem/.cursor/skills/record-notes/SKILL.md) 与 [`references/ingestion-rules.md`](/Users/alyx/tradeSystem/.cursor/skills/record-notes/references/ingestion-rules.md)
- **`db holdings-add` / `db watchlist-add`（Agent）**：CLI 同时需要 `--code` 与 `--name`。若用户**只提供代码**或**只提供证券简称**，须先用 Provider（如 `get_stock_basic_batch` / `get_stock_basic_list`）查询补全并经用户确认，**禁止**编造；多候选时由用户选定唯一代码。细则见 [`.cursor/skills/portfolio-manager/SKILL.md`](/Users/alyx/tradeSystem/.cursor/skills/portfolio-manager/SKILL.md) 中「证券代码与简称」。
- 修改 `scripts/main.py`、`scripts/api/routes/*.py`、`.cursor/skills/**/*.md` 后，必须同步更新 `.cursor/skills/INDEX.md` 与 `.cursor/rules/skills-sync.mdc`
