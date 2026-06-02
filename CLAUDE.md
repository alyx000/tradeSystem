# 交易系统 - Claude 协作入口

本文件仅保留总则与索引，具体规则请按任务加载对应主题文档。
权威入口与最新拆分结构以 [AGENTS.md](/Users/alyx/tradeSystem/AGENTS.md) 和 `.cursor/agent-context/` 为准；本文件作为 Claude / 兼容 Agent 的等价入口。

> **目录中性化声明**：`.agents/skills/` 与 `.agents/rules/` 是团队共享 agent context 真源，与具体 IDE 无关。`.cursor/skills`、`.cursor/rules/*.mdc` 是 Cursor IDE 的 symlink 壳；`.claude/skills`、`.claude/rules/*.md` 是 Claude Code 的 symlink 壳。**所有写入只动 `.agents/`**。本期 `.agents/` 仅承载 skills + rules，`agent-context` 暂留 `.cursor/agent-context/`，待 v2 一并迁出。

## 先读结论

1. 这是一个 A股/港股短线交易分析系统，AI 负责复盘、分析、整理与执行辅助，**不替代交易决策**。
2. Agent 写入统一走 CLI 标准入口，**禁止直接写 SQLite、YAML 或手工拼 JSON**。
3. 所有写入命令必须显式带 `--input-by`；Agent **不得绕过确认直接写 `confirmed` 的 `TradePlan`**。
4. 所有 AI 输出使用简体中文；涉及技术方案、执行计划、业务逻辑解析时，默认遵循 `.agents/rules/solution-format.md`。
5. 修改 `scripts/main.py`、`scripts/api/routes/*.py`、`.agents/skills/**/*.md` 后，必须同步更新 `.agents/skills/INDEX.md` 与 `.agents/rules/skills-sync.md`。

## 渐进式加载顺序


| 任务类型                             | 必读文件                                                                                                       | 按需补读                                                                                                     |
| -------------------------------- | ---------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| 任意任务                             | `CLAUDE.md` 或 `AGENTS.md`                                                                                  | 无                                                                                                        |
| 盘前/盘后/复盘/主线/情绪判断                 | [00-core-trading-framework.md](/Users/alyx/tradeSystem/.cursor/agent-context/00-core-trading-framework.md) | [20-architecture-and-data.md](/Users/alyx/tradeSystem/.cursor/agent-context/20-architecture-and-data.md) |
| CLI / API / DB / 计划流转 / Agent 写入 | [10-agent-collaboration.md](/Users/alyx/tradeSystem/.cursor/agent-context/10-agent-collaboration.md)       | [20-architecture-and-data.md](/Users/alyx/tradeSystem/.cursor/agent-context/20-architecture-and-data.md) |
| 架构、数据模型、事实层 / 草稿 / 计划状态流         | [20-architecture-and-data.md](/Users/alyx/tradeSystem/.cursor/agent-context/20-architecture-and-data.md)   | [10-agent-collaboration.md](/Users/alyx/tradeSystem/.cursor/agent-context/10-agent-collaboration.md)     |
| 命令执行、环境、推送、目录结构、文件修改规范           | [30-runtime-and-ops.md](/Users/alyx/tradeSystem/.cursor/agent-context/30-runtime-and-ops.md)               | [10-agent-collaboration.md](/Users/alyx/tradeSystem/.cursor/agent-context/10-agent-collaboration.md)     |
| 需要拆分对照或回滚老版本                     | [99-full-reference.md](/Users/alyx/tradeSystem/.cursor/agent-context/99-full-reference.md)                 | `00` 到 `30` 号主题文档                                                                                        |


## 红线

- 不做具体买卖建议
- 不预测具体价格目标
- 不在没有数据支撑时做主观判断
- 不将 `[判断]` 伪装成 `[事实]`
- 不替代用户的“看得懂”判断

## 标准写入语义

系统区分 **人工入口** 与 **Agent 标准入口**：

- **人工入口**：Web / API / CLI 都可用
- **Agent 标准入口**：统一通过 CLI 写入
- **统一语义层**：CLI / API / Web 必须共享同一 service、同一默认值、同一校验与状态流转

当前及后续标准命令组：

- `python3 main.py db ...`（含 `db thesis-{open,close,fill,list,suggest,review,reopen}` 交易思路中间层 v24，半自动联动 broker_executions 与复盘）
- `python3 main.py ingest ...`
- `python3 main.py plan ...`
- `python3 main.py knowledge ...`
- `python3 main.py executions ...`（券商成交流水事实层：`import` / `list` / `audit-export`；`import` 默认严格 thesis 模式 + auto-close 联动）
- `python3 main.py volume-watch ...`（成交额 Top20 板块集中度：`daily` 采集+落库+渲染+钉钉推送 / `trend` 只读趋势；申万二级口径联动 `get_sector_rankings`，落 `daily_volume_concentration`）
- `python3 main.py sector-correlation ...`（板块相关性：`daily` 采集+落库+渲染+钉钉 / `matrix` 完整矩阵只读 / `trend` 漂移趋势；Tushare 主源多日活跃选板块[行业成交额 / 概念换手率]+4 指数，多窗 5/20/60 原始相关+剔大盘超额相关+β，落 `sector_correlation_daily`）
- `python3 main.py research-digest daily ...`（每日研报速读：A股研报评级[巨潮 cninfo `get_research_report_list`] + 美股机构评级[yfinance `upgrades_downgrades`，仅方向变动 init/up/down/reinit] → 鞠磊框架「首次覆盖」加权 Top3 → MD 落盘 + 钉钉；`--dry-run` 仅打印、`--no-llm` 关美股叙事；红线只约束 LLM 叙事不约束取数；工作日 06:42 launchd 单源调度，不进 `schedule`/APScheduler）
- `python3 main.py cognition-digest recent3d|weekly|monthly ...`（交易认知沉淀只读汇总：只读认知三表[`trading_cognitions`/`cognition_instances`]按窗口算热度+共识+新增 Top-N + gemini 体系/方向建议[复用 gemini runner + `REDLINE_KEYWORDS` 红线护栏] → 钉钉；`--dry-run` 仅打印、`--no-llm` 模板兜底；3 个 per-task launchd[recent3d 日 18:30 / weekly 周日 20:00 / monthly 每月 1 号 09:00]，不写库不改 schema 不进 `schedule`/APScheduler）

## 规则与模板入口

### AI 协作规则（真源 `.agents/rules/`）


| 规则文件                      | 作用                                                                    |
| ------------------------- | --------------------------------------------------------------------- |
| `language.md`             | 所有 AI 输出使用简体中文，代码标识符保持英文                                              |
| `karpathy-behavior.md`    | 行为基线：先校验假设、简洁优先、精准修改、目标驱动验证，减少 Agent 常见失误                            |
| `dev-workflow.md`         | 开发三阶段流程：设计验证方案 → 实现（含单测）→ 执行验证并报告                                     |
| `implementation-plan.md`  | 实施计划必须含测试验证方案 + 复杂任务多 Agent 并行分组                                      |
| `solution-format.md`      | 技术方案 / 执行计划 / 业务逻辑解析默认使用结构化章节、表格与纯 Mermaid 图表输出                       |
| `test-design.md`          | 分层测试设计：金字塔原则、隔离原则、自底向上执行                                              |
| `subagent-code-review.md` | 每轮实质性代码改动后启动 subagent 审查（轻量本地 Explore）                                  |
| `post-dev-codex-review.md` | 实质性代码改动后必须跑 codex:codex-rescue 审查；6 条二值结束条件 + 3 轮上限防无限循环               |
| `skills-sync.md`          | CLI / API / Skills 变更后同步 `INDEX.md`、跑 `test_cli_smoke`、检查受影响 SKILL.md；新增顶层 subparser 必加 `ARCHITECTURE_COMMANDS` 参数化 |
| `launchd-deploy.md`       | macOS launchd 定时任务部署规范：包装脚本必须 set PATH + source env；安装后必须 launchctl start 真触发验证；LLM 任务超时建议 180s+ |
| `tdd-commit-strategy.md`  | TDD 实施完成后按功能层次切 commit（不每个 R/G 一个、不全 squash）；commit message 标 What/Why/TDD 轮数；`git add` 用具体路径不用 `-A` |

> **Rules 激活差**：Cursor 通过 `alwaysApply` / `globs` 自动注入；Claude Code 不解析这两个字段，全量加载 `.claude/rules/*.md`。`skills-sync.md` 在 Cursor 仅命中 `globs` 时触发，在 Claude Code 视为常驻提示。

### Skills 入口（Codex / Claude Code / Cursor 共用，真源在 `.agents/skills/`）

| Skill | 路径 | 何时加载 |
| --- | --- | --- |
| cognition-evolution | .agents/skills/cognition-evolution/SKILL.md | 提炼认知 / 验证 / 复盘 |
| daily-review | .agents/skills/daily-review/SKILL.md | 八步盘后复盘 |
| ingest-inspector | .agents/skills/ingest-inspector/SKILL.md | 采集诊断与重试 |
| knowledge-to-plan | .agents/skills/knowledge-to-plan/SKILL.md | 资料转草稿（新闻 / 课程 / 笔记） |
| market-tasks | .agents/skills/market-tasks/SKILL.md | 盘前 / 盘后采集任务 |
| plan-workbench | .agents/skills/plan-workbench/SKILL.md | 草稿 / 确认 / 诊断 / 回写 |
| portfolio-manager | .agents/skills/portfolio-manager/SKILL.md | 持仓 / 关注池 / 黑名单 |
| record-notes | .agents/skills/record-notes/SKILL.md | 录入老师观点 / 行业 / 宏观 |
| repo-maintenance-workflows | .agents/skills/repo-maintenance-workflows/SKILL.md | CLI / API 对齐与索引同步 |
| sector-projection-analysis | .agents/skills/sector-projection-analysis/SKILL.md | 板块推演 |

CLI / API 依赖对照见 `.agents/skills/INDEX.md`（唯一真源）。Codex CLI / Claude Code 在执行任务前按关键词命中读对应 SKILL.md，再调用 CLI。

### 模板入口

- [technical-design.md](/Users/alyx/tradeSystem/docs/templates/technical-design.md)
- [execution-plan.md](/Users/alyx/tradeSystem/docs/templates/execution-plan.md)
- [api-contract.md](/Users/alyx/tradeSystem/docs/templates/api-contract.md)

## 主题索引

1. [AGENTS.md](/Users/alyx/tradeSystem/AGENTS.md)
2. [00-core-trading-framework.md](/Users/alyx/tradeSystem/.cursor/agent-context/00-core-trading-framework.md)
3. [10-agent-collaboration.md](/Users/alyx/tradeSystem/.cursor/agent-context/10-agent-collaboration.md)
4. [20-architecture-and-data.md](/Users/alyx/tradeSystem/.cursor/agent-context/20-architecture-and-data.md)
5. [30-runtime-and-ops.md](/Users/alyx/tradeSystem/.cursor/agent-context/30-runtime-and-ops.md)
6. [99-full-reference.md](/Users/alyx/tradeSystem/.cursor/agent-context/99-full-reference.md)
