# 交易系统 - AI 协作入口

本文件仅保留总则与索引，具体规则请按任务加载对应主题文档。
原有内容**没有删除**，已按主题拆到 `.cursor/agent-context/`。

## 先读结论

1. 这是一个 A股/港股短线交易分析系统，AI 负责复盘、分析、整理与执行辅助，**不替代交易决策**。
2. Agent 写入统一走 CLI 标准入口，**禁止直接写 SQLite、YAML 或手工拼 JSON**。
3. 所有写入命令必须显式带 `--input-by`；Agent **不得绕过确认直接写 `confirmed` 的 `TradePlan`**。
4. 所有 AI 输出使用简体中文；涉及技术方案、执行计划、业务逻辑解析时，默认遵循 `.cursor/rules/solution-format.mdc`。
5. 修改 `scripts/main.py`、`scripts/api/routes/*.py`、`.cursor/skills/**/*.md` 后，必须同步更新 `.cursor/skills/INDEX.md` 与 `.cursor/rules/skills-sync.mdc`。

## 渐进式加载顺序


| 任务类型                             | 必读文件                                                                                                       | 按需补读                                                                                                     |
| -------------------------------- | ---------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| 任意任务                             | `AGENTS.md`                                                                                                | 无                                                                                                        |
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

- `python3 main.py db ...`
- `python3 main.py ingest ...`
- `python3 main.py plan ...`
- `python3 main.py knowledge ...`

## 规则与模板入口

### AI 协作规则（`.cursor/rules/`）


| 规则文件                       | 作用                                                                    |
| -------------------------- | --------------------------------------------------------------------- |
| `language.mdc`             | 所有 AI 输出使用简体中文，代码标识符保持英文                                              |
| `karpathy-behavior.mdc`    | 行为基线：先校验假设、简洁优先、精准修改、目标驱动验证，减少 Agent 常见失误                            |
| `dev-workflow.mdc`         | 开发三阶段流程：设计验证方案 → 实现（含单测）→ 执行验证并报告                                     |
| `implementation-plan.mdc`  | 实施计划必须含测试验证方案 + 复杂任务多 Agent 并行分组                                      |
| `solution-format.mdc`      | 技术方案 / 执行计划 / 业务逻辑解析默认使用结构化章节、表格与纯 Mermaid 图表输出                       |
| `test-design.mdc`          | 分层测试设计：金字塔原则、隔离原则、自底向上执行                                              |
| `subagent-code-review.mdc` | 每轮实质性代码改动后启动 subagent 审查                                              |
| `skills-sync.mdc`          | CLI / API / Skills 变更后同步 `INDEX.md`、跑 `test_cli_smoke`、检查受影响 SKILL.md |


### 模板入口

- [technical-design.md](/Users/alyx/tradeSystem/docs/templates/technical-design.md)
- [execution-plan.md](/Users/alyx/tradeSystem/docs/templates/execution-plan.md)
- [api-contract.md](/Users/alyx/tradeSystem/docs/templates/api-contract.md)

## 主题索引

1. [00-core-trading-framework.md](/Users/alyx/tradeSystem/.cursor/agent-context/00-core-trading-framework.md)
2. [10-agent-collaboration.md](/Users/alyx/tradeSystem/.cursor/agent-context/10-agent-collaboration.md)
3. [20-architecture-and-data.md](/Users/alyx/tradeSystem/.cursor/agent-context/20-architecture-and-data.md)
4. [30-runtime-and-ops.md](/Users/alyx/tradeSystem/.cursor/agent-context/30-runtime-and-ops.md)
5. [99-full-reference.md](/Users/alyx/tradeSystem/.cursor/agent-context/99-full-reference.md)
