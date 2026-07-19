---
name: repo-maintenance-workflows
description: 调试与维护 tradeSystem 仓库；用于排查回归、review 代码改动、对齐 CLI/API/Web/service 语义、执行每日只读巡检、修复已获授权的问题，以及同步 skills/commands 文档索引。诊断、Review 和巡检默认只读，修改后按影响范围执行测试与审查门。
---

# Skill: 仓库维护工作流

## 开始前

1. 读取 `AGENTS.md`；涉及文件修改、命令执行或目录结构时读取 `.cursor/agent-context/30-runtime-and-ops.md`，涉及 CLI / API / DB / Agent 写入时读取 `.cursor/agent-context/10-agent-collaboration.md`。
2. 运行 `git status --short --branch` 和 `git diff --name-only`，记录当前分支与既有改动；只修改本任务范围，避免覆盖他人未提交内容。
3. 明确任务模式与授权：
   - Diagnose / Review / 巡检：默认只读，只报告证据、findings 与修复候选。
   - Fix / Build：仅在用户明确要求修改后实施；先给出根因证据，再做最小改动。
   - 对齐：先列出 CLI / API / Web / service / schema 的真实入口；用户只要求检查时不得自动修复。
4. 按任务读取 [仓库维护检查清单](references/maintenance-checklist.md)；涉及 `teacher_notes` v40 备份或迁移时，额外读取 [v40 受控迁移](references/teacher-notes-v40-migration.md)。

## 核心流程

1. 从真实入口追到 service、schema、状态流转、写入目标与测试，不根据页面文案或 CLI 外观直接下结论。
2. 区分 `[事实]`、`[判断]` 与尚未验证的风险；Review findings 使用“严重 / 中等 / 轻微”并附文件与行号。
3. 获得修改授权后，先定义验证命令与完成标准，再同步实现和测试。
4. 修改 CLI、API、service、workflow、launchd 或 skill 行为契约后，按 [`skills-sync.md`](../../rules/skills-sync.md) 检查 `INDEX.md`、受影响的 SKILL/reference 与命令索引；Skill 元数据变化时再核对 `agents/openai.yaml`。
5. 实质性代码改动测试通过后，按 [`code-review-gate.md`](../../rules/code-review-gate.md) 和 [`post-dev-codex-review.md`](../../rules/post-dev-codex-review.md) 完成串行审查门；纯文档或单文件轻微改动遵循其豁免条件。

## 验证选择

按影响范围选择验证，不能用 CLI smoke 代替业务逻辑测试：

| 改动范围 | 最小验证 |
|---|---|
| 只读诊断 / Review / 巡检 | 复现或针对性只读检查；报告已验证范围与未验证项 |
| Skill / 文档 | Skill 结构校验；按 `skills-sync.md` 检查 `INDEX.md`、`agents/openai.yaml`、symlink 与 CLI smoke |
| CLI / API / 后端逻辑 | 对应 pytest + `make check-scripts` |
| Web | 对应前端测试 + `make check-web` |
| 跨层或全仓改动 | `make check`，并追加目标场景验证 |
| 命令索引 | 先运行只读的 `make commands-check`；确认需要更新且已获修改授权后，才运行会写入文档的 `make commands-doc`，随后重跑 `make commands-check` |

详细测试要求以 [`dev-workflow.md`](../../rules/dev-workflow.md) 和 [`test-design.md`](../../rules/test-design.md) 为准。结束前运行 `git diff --check`，对比工作树基线，并确认未意外修改运行时业务数据。

## 禁止事项

- 不直接写 SQLite、YAML 或手工拼 JSON；Agent 写入统一走标准 CLI，并显式传 `--input-by`。
- 不把诊断、Review 或巡检请求视为修复授权。
- 不绕过人工确认直接写正式 `TradePlan`。
- 不修改或清理与当前任务无关的工作树内容。
- 不在未核对同步项、测试结果和剩余风险时结束修改。

## 切换条件

- 计划工作流：切换到 [`plan-workbench/SKILL.md`](../plan-workbench/SKILL.md)。
- 资料分流或老师观点草稿：切换到 [`knowledge-to-plan/SKILL.md`](../knowledge-to-plan/SKILL.md)。
- 采集失败、接口状态或补跑：先切换到 [`ingest-inspector/SKILL.md`](../ingest-inspector/SKILL.md)；若确认是 provider、service 或状态流转缺陷，再返回本 Skill 修复。

## 结果汇报

- Diagnose / Review / 巡检：确定问题（含严重度与证据）→ 可疑风险 → 修复候选（未授权不实施）→ 覆盖范围与限制。
- Fix / Build：根因 → 实际改动 → 测试与审查门结果 → 剩余风险。
