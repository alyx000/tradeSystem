---
name: portfolio-manager
description: 管理持仓池、关注池、黑名单、交易记录，提供标准化的增删改查接口供 AI Agent 调用
version: "1.2"
---

# Skill: 投资组合管理

## 使用场景

当用户说：

- 「我买了 / 卖了」
- 「看一下我的持仓」
- 「加到关注池 / 从关注池移除」
- 「记录一笔交易」
- 「加入黑名单」

时激活此 skill。

## 优先入口

查询类优先使用仓库根目录：

```bash
make holdings-open
make watchlist-open
make holdings
make watchlist
```

写操作默认在 `scripts/` 目录使用底层命令：

```bash
python3 main.py db holdings-add ...
python3 main.py db holdings-remove ...
python3 main.py db watchlist-add ...
python3 main.py db watchlist-update ...
python3 main.py db add-trade ...
python3 main.py db blacklist-add ...
```

## 核心流程

1. 先识别动作类型：持仓、关注池、交易记录、黑名单。
2. 提取结构化字段：代码、名称、股数、价格、层级、方向等。
3. 对所有写操作先展示结构化确认摘要，用户确认后再执行。
4. 执行后立刻回查列表或目标记录，确认结果落库。

## 禁止事项

- 不要猜测股票代码、价格、股数、`tier` 或交易方向。
- 不要把多动作一句话直接当成一次写库，先拆分再确认。
- 不要直接写 DB，必须通过 `python3 main.py db ...`。
- 不要把关注池或持仓变更误写成计划确认流程。

## 最小验证

- 持仓 / 关注池写入后，用 `make holdings` / `make watchlist` 或对应 CLI 回查。
- 交易记录 / 黑名单写入后，至少确认命令输出成功且参数符合预期。
- 若由老师观点触发关注池动作，确认 `source-note-id` 或来源说明可回溯。

## 切换条件

- 若输入本质上是老师观点或资料记录，切到 [`record-notes/SKILL.md`](../record-notes/SKILL.md)。
- 若用户在管理持仓时开始谈次日计划，切到 [`plan-workbench/SKILL.md`](../plan-workbench/SKILL.md)。
- 若命令签名或行为异常，切到 [`repo-maintenance-workflows/SKILL.md`](../repo-maintenance-workflows/SKILL.md)。

## 结果汇报格式

1. 已执行的写操作或查询
2. 关键持仓 / 关注池 / 交易摘要
3. 验证结果
4. 剩余风险或待确认字段
