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
python3 main.py db holdings-add ... [--thesis-id <id>]
python3 main.py db holdings-remove ...
python3 main.py db holdings-refresh --date YYYY-MM-DD   # 现价回填（原 make holdings-refresh）
python3 main.py db holdings-import-yaml               # 遗留 YAML 一次性导入 DB
python3 main.py db watchlist-add ... --input-by cursor
python3 main.py db watchlist-sync-from-note --note-id <teacher_notes.id> --input-by cursor
python3 main.py db watchlist-update ... --input-by cursor
python3 main.py db add-trade ...
python3 main.py db blacklist-add ...
```

券商成交流水（**事实层**，独立顶层组 `executions`）：

```bash
python3 main.py executions import --file <path> --input-by broker_export [--account default] [--dry-run]
python3 main.py executions list [--from YYYY-MM-DD --to YYYY-MM-DD --account ... --limit 50 --json]
python3 main.py executions audit-export --from YYYY-MM-DD --to YYYY-MM-DD [--account ... --out tmp/audit-reports/<name>.md]
```

## 核心流程

1. 先识别动作类型：持仓、关注池、交易记录、黑名单、**券商流水导入**。
2. 提取结构化字段：代码、名称、股数、价格、层级、方向等。
3. 对所有写操作先展示结构化确认摘要，用户确认后再执行。
4. 执行后立刻回查列表或目标记录，确认结果落库。

## 券商成交流水导入（事实层）

`db add-trade` 是单条**复盘维度**写入（写 `trades` 表 / 含 sector / role / lesson 等主观字段）；
`executions import` 是批量**事实层**写入（写 `broker_executions` 表 / 含完整费用 / 合同号 / 原始 payload）。两条链路独立、不相互派生。

### 何时用
- 用户说「把这次的成交记录导进来」「我从券商导出了一份 xls」「定期把交易记录同步」→ 用 `executions import`
- 用户说「记一笔我刚买/卖的」「这笔的复盘体会」→ 用 `db add-trade`

### 导入流程
1. 收到文件路径（券商导出 .xls / .tsv / .xlsx，目前仅 GBK TSV 已实装；其它格式 stub）。
2. 默认先跑 `--dry-run` 预演，让用户看到 `inserted / skipped / conflicts / degraded / errors` 真实数字（基于 DB 现状）。
3. 用户确认后去掉 `--dry-run` 真写库；事务 COMMIT 后自动拷贝源文件到 `tmp/imports/<ts>_<basename>` 并把路径回写 `source_archive_path`。
4. 控制台前 10 条 skipped 摘要 + 全量 markdown 写 `tmp/import-reports/<ts>.md`，重复导入自动幂等（同行命中 UNIQUE 跳过；非键字段差异 → `conflicts` 单列、老值不覆盖）。
5. 同账户多文件导入用 `--account <name>` 区分（多账户字段已预留，未传则 `'default'`）。

### 审计
- 按需审计（如周/月/季回顾、报税前对账、出问题时追溯）：`executions audit-export --from --to [--account] [--out]` 生成跨批次 markdown（总览 / 各股明细 / 导入批次列表）。无强制周期，由用户决定何时跑。
- 单次回溯：`executions list --from --to [--account --json]`。

### 禁止
- 不要把 `executions import` 输出的"事实"再手工 `db add-trade` 到 `trades` 表（重复维度，且复盘字段为空）。
- 真写库前必须 `--dry-run` 一次让用户确认 conflicts/degraded 数字。
- 不要去 `tmp/imports/` 手工删归档副本，那是审计追溯的依据。

## 交易思路（thesis 中间层 / schema v24）

`trade_thesis` 是事实层（`broker_executions`）与复盘（`thesis_review` / `trades`）之间的中间层，按**建仓周期（round-trip）**划分：同一票 `holdings` 从 0 涨到正再回到 0 = 一个 thesis。

### 创建（严格模式 / 用户必经入口）

```bash
python3 main.py db thesis-open \
  --code 600519 --name 贵州茅台 --account A001 --opened-at 2026-05-14 \
  --entry-reason "板块共振+反包" --trade-mode break \
  --failure-condition "尾盘破板" --planned-position-pct 0.15 \
  --sector 白酒 --market-region a-share --input-by alyx \
  # 可选: --target-price 1700 --stop-loss 1500 --mode-note "二连反包" --plan-id plan_1
```

**11 必填**（缺一即 argparse reject）：`--code --name --account --opened-at --entry-reason --trade-mode --failure-condition --planned-position-pct --sector --market-region --input-by`。`--trade-mode` 枚举：`break/dip/trend/scalp/swing/arbitrage/gap_jump/sentiment_relay/other`；`--market-region`：`a-share/hk/us`。

### 与 `executions import` 的联动（严格模式）

- `executions import` 默认 `enforce_strict_thesis=True`：每笔 buy 必须能匹配同账户同票当前 open thesis，否则**整批 reject** 并在错误中给出可执行 `db thesis-open` 命令模板。
- 降级：`executions import ... --allow-orphan-buy` → 允许 `thesis_id=NULL` 写入（仅用于历史回补，事后用 `db thesis-list --filter historical-orphan` 巡检）。
- 同批 `sell` 让某 thesis 累计 holdings 归零 → 自动 `status=closed` + notes 追加 `[auto-close YYYY-MM-DD] holdings 归零自动关闭`。`--no-auto-close` 反转。

### 状态变更与复盘

| 命令 | 何时用 |
|------|--------|
| `db thesis-close --id N --closed-at DATE --input-by U` | 手动关闭（auto-close 已覆盖大多数场景，手动主要用于异常）|
| `db thesis-fill --id N --notes "..."` | closed 后补备注（主字段冻结；要改主字段先 `thesis-reopen`）|
| `db thesis-reopen --id N --reason "..." --reopened-at DATE --input-by U` | 重开 closed thesis；`reopen_count++` + notes 追加 `[reopen DATE] reason`；`>3` 在 list 自动标黄（plan R6） |
| `db thesis-review --id N --executed-as-planned {0,1,2} --input-by U [--lessons --discipline-score --exit-trigger]` | upsert thesis_review（允许多次增量更新，未传字段保留原值） |
| `db thesis-list [--status open/closed --account --code --filter placeholder --without-review --reopened --json]` | 列表查询；`--without-review` 看 closed 但无复盘的，`--reopened` 看异常重开的 |
| `db thesis-suggest [--account]` | 三类待补:待 open（broker_executions.thesis_id IS NULL）/ 待 close（open thesis 当前 holdings=0）/ 待 review（closed 无 thesis_review） |

### Agent 调用边界

- **新流程不强制写 `trades` 表**：thesis_review 是周期总账，trades 是单笔卖出细颗粒；当前双轨运行 1-2 月后评估弃用 trades（plan Q4）。
- **跨账户独立**：同票在不同 `account_id`（如 `my-htf` + `my-ipo`）下可同时有独立 open thesis；半自动检测的判定范围是 `(account_id, stock_code)`。
- **`market_region` 不是隔离维度**：仅 thesis 自身属性，不影响 unique 判定（同账户跨市场场景本期不支持）。
- **关闭后主字段冻结**：`fill` closed thesis 会拒绝改 `entry_reason/failure_condition/target_price/stop_loss/trade_mode/mode_note/planned_position_pct/sector/market_region` 并提示 `Use db thesis-reopen to modify`。

## 买入原因与备注（两类文本）

`holdings` 表区分两个文本字段：

| 字段 | CLI 参数 | 语义 |
|------|----------|------|
| `entry_reason` | `--entry-reason` | **买入原因**：开仓逻辑、触发理由（一次性记录，通常在开仓时写） |
| `note` | `--note` | **备注**：持仓期间的调仓记录、观察点等（可持续补充） |

Agent 录入持仓时，若用户提到「买入原因」或「进场逻辑」，写入 `--entry-reason`；若提到「备注」或「提醒」，写入 `--note`。两者均可同时使用。

若持仓由已确认的 `trade_thesis` 或 `executions import` 补账产生，写入 `--thesis-id <id>` 保持持仓与建仓周期可追溯。

## 证券代码与简称（Agent 必查）

`db holdings-add` / `db watchlist-add` 的 CLI **同时要求** `--code` 与 `--name`。若用户**只给了股票代码**或**只给了证券简称**，Agent **不得**凭记忆编造另一半，必须先通过**已配置的数据源（Provider）**查询、核对后再写入。

**推荐做法（在 `scripts/` 目录下优先走统一 CLI `db stock-resolve`，底层会通过 `registry.call(...)` 调 Provider）：**

1. **仅有代码、缺名称**：执行 `python3 main.py db stock-resolve --code 300750 --json`，从返回的 `resolved` 里取官方简称与规范代码。
2. **仅有名称、缺代码**：执行 `python3 main.py db stock-resolve --name 天孚通信 --json`，在返回列表中按**证券简称精确命中**筛选；若进入 `ambiguous`，必须把候选列表展示给用户，**由用户选定唯一代码**后再执行 `db ...`。
3. 查询结果与用户意图一致时，把**拟写入的 code + name** 写入确认摘要，**用户明示确认**后再跑 `python3 main.py db holdings-add ...` / `watchlist-add ... --input-by cursor`。

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
