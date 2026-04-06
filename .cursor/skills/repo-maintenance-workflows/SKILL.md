---
name: repo-maintenance-workflows
description: 调试 tradeSystem 仓库问题、review 代码改动、对齐 CLI/API/Web/service 语义、执行每日固定巡检，并同步 skills/commands 文档索引
version: "0.1"
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

若问题已明显属于业务工作台操作本身，再结合对应 skill：

- `plan-workbench`
- `knowledge-to-plan`
- `ingest-inspector`

## 默认工作流

### 1. 先建立真实调用链

开始前先读：

- `AGENTS.md`
- 相关入口文件
- 相关 service / schema / route / cli

不要只看表层命令或页面。要确认：

- 真实入口是什么
- 是否复用同一 service
- 默认值是否一致
- 校验是否一致
- 状态流转是否一致
- 最终写到了哪些表 / 状态

### 2. 按任务类型执行

#### Debug

- 先定位根因，再修改
- 只改必要文件，避免顺手重构
- 优先确认入口、状态流转、默认值漂移、写入语义

#### Review

- 先报 findings，再给总结
- 优先找行为风险、状态流转风险、回归风险、缺失测试
- 重点盯：
  - 是否绕过人工确认直接写正式 `TradePlan`
  - 是否把 `judgement_checks` 伪装成 `fact_checks`
  - 是否把缺快照场景伪装成通过 / 失败，而不是 `missing_data`
  - 是否把 `teacher_note` 错写到 `knowledge_assets`

#### 对齐 / 巡检

- 先列真实入口和关键文件
- 区分“确定问题”和“可疑风险”
- 机械问题范围可控时，直接修掉并验证

## 默认约束

- Agent 标准写入只能走 `python3 main.py ...`
- 不得直接写 SQLite、YAML 或手拼 JSON 文件
- 不得绕过人工确认直接写 `confirmed` 的 `TradePlan`
- 不得把 `[判断]` 伪装成 `[事实]`
- 高频操作优先用 `make` 入口；需要细粒度参数时再退回底层命令

常用入口：

```bash
make check-scripts
make commands-check
make commands-doc
python3 -m pytest scripts/tests/test_cli_smoke.py -v
```

## 修改后必须检查

若你修改了这些文件：

- `scripts/main.py`
- `scripts/api/routes/*.py`
- `.cursor/skills/**/*.md`

则必须同步检查：

- `.cursor/skills/INDEX.md`
- `.cursor/rules/skills-sync.mdc`

并至少运行：

```bash
python3 -m pytest scripts/tests/test_cli_smoke.py -v
```

若改动涉及命令索引或 `Makefile` 入口，再检查：

```bash
make commands-doc
make commands-check
```

## 结果汇报格式

默认按这四项汇报：

1. 根因 / findings
2. 实际改动
3. 验证结果
4. 剩余风险
