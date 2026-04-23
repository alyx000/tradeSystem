---
name: portfolio-manager
description: 管理持仓池、关注池、黑名单、交易记录，提供标准化的增删改查接口供 AI Agent 调用
version: "1.4"
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
python3 main.py db holdings-refresh --date YYYY-MM-DD   # 现价回填（原 make holdings-refresh）
python3 main.py db holdings-import-yaml               # 遗留 YAML 一次性导入 DB
python3 main.py db watchlist-add ...
python3 main.py db watchlist-sync-from-note --note-id <teacher_notes.id>
python3 main.py db watchlist-update ...
python3 main.py db add-trade ...
python3 main.py db blacklist-add ...
```

## 核心流程

1. 先识别动作类型：持仓、关注池、交易记录、黑名单。
2. 提取结构化字段：代码、名称、股数、价格、层级、方向等。
3. 对所有写操作先展示结构化确认摘要，用户确认后再执行。
4. 执行后立刻回查列表或目标记录，确认结果落库。

## 买入原因与备注（两类文本）

`holdings` 表区分两个文本字段：

| 字段 | CLI 参数 | 语义 |
|------|----------|------|
| `entry_reason` | `--entry-reason` | **买入原因**：开仓逻辑、触发理由（一次性记录，通常在开仓时写） |
| `note` | `--note` | **备注**：持仓期间的调仓记录、观察点等（可持续补充） |

Agent 录入持仓时，若用户提到「买入原因」或「进场逻辑」，写入 `--entry-reason`；若提到「备注」或「提醒」，写入 `--note`。两者均可同时使用。

## 证券代码与简称（Agent 必查）

`db holdings-add` / `db watchlist-add` 的 CLI **同时要求** `--code` 与 `--name`。若用户**只给了股票代码**或**只给了证券简称**，Agent **不得**凭记忆编造另一半，必须先通过**已配置的数据源（Provider）**查询、核对后再写入。

**推荐做法（在 `scripts/` 目录下优先走统一 CLI `db stock-resolve`，底层会通过 `registry.call(...)` 调 Provider）：**

1. **仅有代码、缺名称**：执行 `python3 main.py db stock-resolve --code 300750 --json`，从返回的 `resolved` 里取官方简称与规范代码。
2. **仅有名称、缺代码**：执行 `python3 main.py db stock-resolve --name 天孚通信 --json`，在返回列表中按**证券简称精确命中**筛选；若进入 `ambiguous`，必须把候选列表展示给用户，**由用户选定唯一代码**后再执行 `db ...`。
3. 查询结果与用户意图一致时，把**拟写入的 code + name** 写入确认摘要，**用户明示确认**后再跑 `python3 main.py db holdings-add ...` / `watchlist-add ...`。

**禁止**：在未做接口核对（或用户未从多候选中选定）时，用「猜」的名称或代码凑满 CLI 参数。

## 禁止事项

- 不要猜测股票代码、价格、股数、`tier` 或交易方向。
- 用户只提供代码或只提供简称时，必须先按上文「证券代码与简称」用 Provider 查询补全并经确认，不得只补全一半却编造另一半。
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
