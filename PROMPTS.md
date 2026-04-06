# PROMPTS

> 最短入口版。打开仓库后如果只想快速复制一条，就从这里拿。完整模板见 [`docs/codex-prompts.md`](/Users/alyx/tradeSystem/docs/codex-prompts.md)，高频实战见 [`docs/codex-task-recipes.md`](/Users/alyx/tradeSystem/docs/codex-task-recipes.md)，使用说明见 [`docs/codex-workflows.md`](/Users/alyx/tradeSystem/docs/codex-workflows.md)。

## Debug

```text
先读 AGENTS.md 和相关代码，排查这个问题：<现象>。先定位真实入口、service 和状态流转，找到根因后直接修复。只改必要文件，不要顺手重构。改完跑最小验证，最后告诉我根因、改动、验证结果、剩余风险。
```

## Review

```text
review 当前改动，重点找这些问题：绕过人工确认直接写 confirmed TradePlan、把 judgement 伪装成 fact、CLI/API/Web 语义不一致、双写或回填链路的数据不一致风险、缺少必要测试。按严重程度列 findings，给出文件和行号。先说问题，再说总结；如果没有 findings，明确说明剩余风险和测试空白。
```

## 每日巡检

```text
按 AGENTS.md 对仓库做一次每日固定巡检，重点覆盖：CLI/API/Web 是否仍共用同一 service，`plan`、`knowledge`、`ingest` 是否有语义漂移，标准写入命令是否显式带 `--input-by`，`plan diagnose` 缺快照时是否正确降级到 `missing_data`，新增命令、Skill 或入口后文档索引是否同步，是否存在缺失测试、缺失状态校验、错误双写。输出 1. 确定问题 2. 可疑风险 3. 可顺手修的机械问题。如果机械问题范围可控，直接修掉并验证。
```
