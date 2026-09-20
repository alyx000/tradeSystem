---
name: repo-maintenance-workflows
description: 排查与维护 tradeSystem：代码审查、跨入口语义对齐、只读巡检、授权修复及文档索引同步。
---

# 仓库维护

## 范围与边界

- 诊断、Review、巡检与对齐检查默认只读；用户明确要求修复或开发时，在已授权范围内实施，不重复索要授权。
- 只改任务相关内容，保护既有未提交改动。业务写入走标准 CLI，显式带 `--input-by`；不直接写 SQLite、YAML 或手工拼 JSON，不绕过确认写正式 `TradePlan`。

## 执行

1. 读取 `AGENTS.md`；操作文件/命令时补读 `.cursor/agent-context/30-runtime-and-ops.md`，涉及 CLI/API/DB/业务写入时补读 `10-agent-collaboration.md`（同目录）。用 `git status --short --branch` 与 `git diff --name-only` 记录工作树基线。
2. 按任务读取[维护检查清单](references/maintenance-checklist.md)对应章节，从真实入口追踪 service、schema、状态流转、写入目标与测试，取得根因或不一致证据。涉及 `teacher_notes` v40 备份/迁移时先读[受控迁移流程](references/teacher-notes-v40-migration.md)。
3. 修改前确定验证命令与完成标准，再做最小修复。按 [skills-sync.md](../../rules/skills-sync.md) 同步 `INDEX.md`、受影响的 Skill/reference 与命令索引，并核对 `agents/openai.yaml`。
4. 按下表验证；实质性代码改动再按 [post-dev-review.md](../../rules/post-dev-review.md) 完成对应审查门或说明豁免。结束时运行 `git diff --check`，对比工作树基线，检查运行时业务数据有无意外修改。

## 验证

按影响范围执行；CLI smoke 不能替代业务逻辑测试。详细要求见 [dev-workflow.md](../../rules/dev-workflow.md) 与 [test-design.md](../../rules/test-design.md)。

| 改动范围 | 最小验证 |
|---|---|
| 只读检查 | 复现或针对性只读验证 |
| Skill / 文档 | Skill 结构、索引、UI 元数据、symlink 与 CLI smoke |
| CLI / API / 后端逻辑 | 对应 pytest + `make check-scripts` |
| Web | 对应前端测试 + `make check-web` |
| 跨层或全仓改动 | `make check`，并追加目标场景验证 |
| 命令索引 | 先 `make commands-check`；需要更新且在授权范围内才运行 `make commands-doc`，随后复查 |

## 专项入口

- 计划流转 → [plan-workbench](../plan-workbench/SKILL.md)；资料分流/老师观点草稿 → [knowledge-to-plan](../knowledge-to-plan/SKILL.md)。
- 采集失败、接口状态或补跑 → [ingest-inspector](../ingest-inspector/SKILL.md)；确认是代码缺陷后返回本技能修复。

## 汇报

区分 `[事实]`、`[判断]` 与未验证风险。只读检查报告问题、修复候选及覆盖限制；Review 按“严重/中等/轻微”排序并附文件与行号。修复报告根因、实际改动、测试与审查结果及剩余风险。
