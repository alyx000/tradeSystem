---
name: repo-maintenance-workflows
description: 调试 tradeSystem 仓库问题、review 代码改动、对齐 CLI/API/Web/service 语义、执行每日固定巡检，并同步 skills/commands 文档索引
version: "0.2"
---

# Skill: 仓库维护工作流

## 使用场景

当用户说：

- 「排查这个问题 / 回归」
- 「review 当前改动」
- 「对齐 CLI 和 API」
- 「检查 Web / API / CLI 是否共用同一 service」
- 「做一次每日固定巡检」
- 「看 docs/commands 或 skills 索引有没有漏同步」

时激活此 skill。

详细检查清单见 [references/maintenance-checklist.md](references/maintenance-checklist.md)。

## 优先入口

常用仓库级检查：

```bash
make check-scripts
make commands-check
make commands-doc
python3 -m pytest scripts/tests/test_cli_smoke.py -v
```

## 核心流程

1. 先读 `AGENTS.md`、真实入口、service、schema、route、cli。
2. 按任务类型执行：
   - Debug：先定位根因再改
   - Review：先报 findings 再总结
   - 对齐 / 巡检：先列真实入口，再区分确定问题与可疑风险
3. 修改 `scripts/main.py`、`scripts/api/routes/*.py`、`.agents/skills/**/*.md` 后，强制检查同步项。
4. 修改完成后至少跑 smoke 校验，再决定是否补更大范围检查。

## 禁止事项

- 不要只看表层 CLI 或页面就下结论。
- 不要直接写 SQLite、YAML 或手拼 JSON。
- 不要绕过人工确认直接写正式计划。
- 不要在未核对同步项时结束修改。

## 最小验证

- 至少运行 `python3 -m pytest scripts/tests/test_cli_smoke.py -v`。
- 若改动涉及命令索引，追加 `make commands-doc` 与 `make commands-check`。
- 若改动涉及具体模块，再补对应最小测试或 smoke。

## 切换条件

- 若问题已经明确属于计划工作流，切到 [`plan-workbench/SKILL.md`](../plan-workbench/SKILL.md)。
- 若问题属于资料分流或老师观点草稿，切到 [`knowledge-to-plan/SKILL.md`](../knowledge-to-plan/SKILL.md)。
- 若问题属于采集失败和接口补跑，切到 [`ingest-inspector/SKILL.md`](../ingest-inspector/SKILL.md)。

## 结果汇报格式

1. 根因 / findings
2. 实际改动
3. 验证结果
4. 剩余风险
