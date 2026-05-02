# Codex Quick Reference

> 面向 `tradeSystem` 的短版速查表。需要完整模板时看 [`docs/codex-prompts.md`](/Users/alyx/tradeSystem/docs/codex-prompts.md)，需要使用说明时看 [`docs/codex-workflows.md`](/Users/alyx/tradeSystem/docs/codex-workflows.md)，需要高频实战案例时看 [`docs/codex-task-recipes.md`](/Users/alyx/tradeSystem/docs/codex-task-recipes.md)，需要直接贴到 Obsidian / Notion 时看 [`docs/codex-prompts-obsidian.md`](/Users/alyx/tradeSystem/docs/codex-prompts-obsidian.md) 和 [`docs/codex-prompts-notion.md`](/Users/alyx/tradeSystem/docs/codex-prompts-notion.md)。

## Debug

```text
先读 AGENTS.md 和相关代码，排查这个问题：<现象>。先定位真实入口、service 和状态流转，找到根因后直接修复。只改必要文件，不要顺手重构。改完跑最小验证，最后告诉我根因、改动、验证结果、剩余风险。
```

## Review

```text
review 当前改动，重点找这些问题：绕过人工确认直接写 confirmed TradePlan、把 judgement 伪装成 fact、CLI/API/Web 语义不一致、双写或回填链路的数据不一致风险、缺少必要测试。按严重程度列 findings，给出文件和行号。先说问题，再说总结。
```

## CLI / API 对齐

```text
先读 AGENTS.md。检查并对齐 <功能名> 的 CLI 和 API 语义，只改 <允许修改的目录>。要求默认值一致、校验一致、错误语义一致、状态流转一致，并确认是否共用同一 service。不要动前端，不要做顺手重构。改完跑相关测试，并告诉我根因、改动、验证结果、剩余风险。
```

## 巡检

```text
按 AGENTS.md 对仓库做一次巡检，主题是 <plan / knowledge / ingest / commands / skills-sync>。先列真实入口和关键文件，再列出确定问题与可疑风险。如果机械问题范围可控，直接修掉并验证。
```

## 文档同步

```text
检查这次改动是否需要同步 docs/commands.md、docs/commands.json、.agents/skills/INDEX.md 和相关规则文档。有缺漏就补齐，并跑对应校验。最后告诉我哪些是代码改动，哪些是文档同步。
```

## 每日固定巡检

```text
按 AGENTS.md 对仓库做一次每日固定巡检，重点覆盖：CLI/API/Web 是否仍共用同一 service，`plan`、`knowledge`、`ingest` 是否有语义漂移，标准写入命令是否显式带 `--input-by`，新增命令或入口后文档索引是否同步，缺失测试、缺失状态校验、缺失 missing_data 降级的点。输出 1. 确定问题 2. 可疑风险 3. 可顺手修的机械问题。如果机械问题范围可控，直接修掉并验证。
```
