---
description: 当修改 CLI 或 API 文件时，提醒同步更新 skills 文档
globs:
  - scripts/db/cli.py
  - scripts/db/migrate.py
  - scripts/db/schema.py
  - scripts/main.py
  - scripts/cli/emotion_leader.py
  - scripts/cli/wechat_teacher_feed.py
  - scripts/cli/review_factors.py
  - scripts/cli/tail_scan.py
  - scripts/cli/daily_leaders.py
  - scripts/cli/value_watch.py
  - scripts/cli/sector_crowding.py
  - scripts/cli/monthly_pattern.py
  - scripts/services/trinity_factor/*.py
  - scripts/services/tail_scan/*.py
  - scripts/services/daily_leaders/*.py
  - scripts/services/value_watch/*.py
  - scripts/services/sector_crowding/*.py
  - scripts/services/monthly_pattern/*.py
  - scripts/services/emotion_leader/*.py
  - scripts/providers/base.py
  - scripts/providers/tushare_provider.py
  - scripts/api/routes/*.py
  - .agents/skills/**/*.md
  - .cursor/skills/**/*.md
---

# Skills 同步检查规则

## 触发条件

当你修改以下任何文件时，此规则自动触发：

- `scripts/db/cli.py` — CLI 子命令定义
- `scripts/db/migrate.py` / `scripts/db/schema.py` — schema 版本、显式迁移门禁与唯一索引契约
- `scripts/main.py` — 顶层命令注册（pre/post/schedule 等）
- `scripts/cli/emotion_leader.py` / `scripts/services/emotion_leader/*.py` — 连板启动、情绪核心晋级、前复权生命周期统计、波段判断、归档、打开高度节点联动与三档运行语义
- `scripts/collectors/market.py` 的 `prefetch_calendar*` / `scripts/services/calendar_sync.py` — 宏观日历 YAML 原子更新、SQLite 幂等同步、覆盖收据与 06:30 launchd 语义
- `scripts/cli/wechat_teacher_feed.py` — 微信公众号白名单 phase、归档、候选过滤与失败语义
- `scripts/cli/review_factors.py` / `scripts/services/trinity_factor/*.py` — 三位一体评分、人工确认、T+1 与影子指标语义
- `scripts/cli/tail_scan.py` / `scripts/services/tail_scan/*.py` — 尾盘实时筛选、逐票产业逻辑、证据边界与报告/推送语义
- `scripts/cli/daily_leaders.py` / `scripts/services/daily_leaders/*.py` — 每日最票候选的板块口径、属性、收敛上限、LLM 复核与确定性降级语义
- `scripts/cli/value_watch.py` / `scripts/services/value_watch/*.py` — 价值投资条件监控三层口径、事件账本去重、日历闸门与三档运行语义
- `scripts/cli/sector_crowding.py` / `scripts/services/sector_crowding/*.py` — 板块拥挤度、申万二级半年线/年线位置与近期价量共振标签语义
- `scripts/services/tail_scan/concept_context.py`，或 `scripts/providers/base.py` / `scripts/providers/tushare_provider.py` 中 `get_stock_concept_memberships` capability — 尾盘扫描当前归属与 T-1 热概念分层语义
- `scripts/utils/pattern.py` — 形态篇多头排列 / MACD 零轴状态 / 5·13 均量线阳放阴缩节奏纯函数（消费方：`pattern-scan`）
- `scripts/cli/pattern_scan.py` / `scripts/services/pattern_scan/*.py` — 形态篇四条件共振口径、主线复用、前复权与三档运行语义
- `scripts/api/routes/*.py` — API 路由定义
- `.agents/skills/**/*.md` — skill 文档本身（真源；`.cursor/skills/` 与 `.claude/skills/` 是 symlink 壳）

## 必须执行的检查清单

### 0. 先判断是否存在统一入口

更新 `.agents/skills/**/*.md` 时，优先检查仓库根目录 `Makefile` 是否已经提供等价入口：

- 检查优先写 `make check` / `make check-web` / `make check-scripts`
- 开发启动优先写 `make dev` / `make dev-api` / `make dev-web`
- 日常任务优先写 `make today-*`
- 若 `Makefile` 已提供别名，SKILL.md 示例里应优先展示 `make` 入口，再补充底层 `python3 main.py ...`

`INDEX.md` 里的依赖表仍保留真实底层 CLI/API，不要改成 `make` 目标名。

### 1. 检查 INDEX.md 是否需要更新

打开 `.agents/skills/INDEX.md`，逐行核对：

- [ ] 所有新增 CLI 子命令都已添加到依赖表
- [ ] 所有重命名的命令已在表中更新
- [ ] 所有删除的命令已从表中移除
- [ ] 新增 API 端点已添加到 API 依赖表
- [ ] `main.py` 新增的 `ingest/plan/knowledge` 命令已同步到相关 skill
- [ ] `review factor-*` 的双层评分、人工确认、严格 T+1、20 日影子指标及“不进入 TradeDraft”边界已同步到 `daily-review` / `sector-projection-analysis`
- [ ] 若命令已从“骨架”变为真实可执行，移除 `SKILL.md` / `INDEX.md` 中的“规划中/骨架”描述

### 2. 运行 Smoke 测试

```bash
make check-scripts
```

若仅需快速验证 CLI 签名，也可单独运行：

```bash
python3 -m pytest scripts/tests/test_cli_smoke.py -v
```

- 所有测试必须通过才算完成
- 若有失败，说明 skill 引用的命令签名已过期，必须同时修复：
  - `cli.py` 中的命令定义，或
  - `test_cli_smoke.py` 中的 `ALL_SKILL_COMMANDS`，以及
  - 对应 `SKILL.md` 中的使用示例

### 2.1 新增顶层 subparser 时必加 ARCHITECTURE_COMMANDS

**`scripts/main.py` 新增任何顶层子命令组**（如 `review` / `recommend` / `new-high` / `ingest` / `plan` / `knowledge` / `executions` 等），不仅要在 `INDEX.md` 加依赖行，**还必须**：

1. 在 `scripts/tests/test_cli_smoke.py` 的 `ARCHITECTURE_COMMANDS` 数组里**加参数化用例**，覆盖：
   - 子命令的所有 mode（如 `recommend daily` / `recommend weekly`）
   - 常用选项组合（`--dry-run` / `--lookback-days` 等）
   - 至少 1 条最简形式 + 1 条全选项形式
2. 这些用例由现有 `test_architecture_command_parseable` 跑参数化校验，仅做 `parser.parse_args()`，**不真实执行命令**
3. 验证：`pytest scripts/tests/test_cli_smoke.py -k <new-cmd> -v` 必须全绿

**为什么**：INDEX.md 写了新命令、SKILL.md 给了示例，但如果 argparse 实际签名不对（参数名错、required 漏标、subparser 没注册），Agent 调用就会失败。`ALL_SKILL_COMMANDS` 校验 `db` 子命令，`ARCHITECTURE_COMMANDS` 校验 `main.py` 顶层子命令，两者分工不能漏。

行业推荐项目（2026-05-16）G3 R12 实战：先在 `ARCHITECTURE_COMMANDS` 加 5 条 `recommend` 参数化 → 跑出 `invalid choice: 'recommend'` RED → 再去 `main.py` 注册 subparser → 验证 GREEN。严格按这个顺序走的项目内 CLI 永远不会出现"agent 调用失败但代码全绿"的悬空状态。

### 3. 检查受影响的 SKILL.md

根据改动内容，检查对应 skill 文档：

| 改动文件 | 需检查的 SKILL.md |
|---------|-----------------|
| `cli.py` 的 `add-note/add-industry/add-macro` | `record-notes/SKILL.md`；若改动老师观点成功/duplicate 收据或回查语义，同时检查 `cognition-evolution/SKILL.md`、`INDEX.md` 与两个 skill 的 `agents/openai.yaml`，保留“新建回查后自动提取候选、认知层写入再次确认”边界 |
| `cli.py` 的 `stock-resolve` | `record-notes/SKILL.md`、`portfolio-manager/SKILL.md` |
| `cli.py` 的 `holdings-*`（含 `--thesis-id` 关联语义）/ `watchlist-*` / `add-trade` / `blacklist-*` | `portfolio-manager/SKILL.md` |
| `cli.py` 的 `query-notes/db-search` | `daily-review/SKILL.md` |
| `.agents/skills/daily-review/references/html-report-template/assemble_report.py` 或多 Agent HTML chunks / anchors / `sector-labels` / 断板反馈 / ETF 净申赎契约 | `daily-review/SKILL.md`、`daily-review/references/multi-agent-review.md`、`daily-review/references/html-report-template/README.md`、`INDEX.md` 与 `scripts/tests/test_daily_review_html_report.py`；正式布局核对 7 chunks / 15 anchors；断板反馈核对 `s3 data-board-break-feedback` 逐股时间轴与 `s4 data-style-board-break-feedback` 默认可见聚合逐项同源，`partial/source_failed` 不得伪装成 0；`sector-labels` 核对唯一可见事实/判断汇总、折叠命中并集、MA144/MA233 与 10/20 共振固定口径、命中/不足计数逐行对账、`complete/partial/missing-data` 三态；ETF 核对净申赎估算额、核心宽基互斥归属与拆分标准化对账 |
| `scripts/collectors/market.py` 的盘后跨资产采集，或多 Agent HTML `s1` 的大势 / 大类资产 / 外汇掉期契约 | `market-tasks/SKILL.md`、`daily-review/SKILL.md`、`daily-review/references/multi-agent-review.md`、`daily-review/references/html-report-template/README.md`、`INDEX.md` 与对应 provider / collector / HTML 测试；核对 `main.py post` 工作日 20:00 同批写入 `post-market.yaml.raw_data` 的全球权益/中国风险映射/商品/波动率/利率五类与人民币即期/C-Swap，复盘优先盘后且旧归档才允许盘前回退并登记缺口；A50/黄金/原油/铜须优先取不晚于目标日的日期化日线，避免晚间下一期货交易日快照混入，A50 东财期指有限重试后仅允许用明确标注 `index_proxy`/`proxy_for` 的 XIN9 日期化代理、商品东财失败后须走新浪日期化备用，日期化源均失败才允许降级 fetch-only；唯一默认可见 `data-big-picture=verdict`、折叠层唯一 `data-cross-asset-context` 与 `data-rmb-fx-observation`、最近事实日 `data-as-of` 与报告日 `data-reviewed-through` 分离；大类资产每行须有可见主数值，fetch-only 不冒充来源交易日且会强制整表 partial；USD/CNY 买卖中值和 1Y C-Swap 须锁中国货币网域名、严格观察/抓取时间、定盘/报价语义与数值不变量；外汇单腿缺失时 partial 必须保留另一腿并显式标缺，partial/failed 均须在 `ops` 可见，禁止静默省略 |
| 多 Agent HTML `data-rmb-fx-chart`、`data-emotion-leader`、`data-emotion-height-chart` 或 `data-emotion-node` 自动模块 | `daily-review/SKILL.md`、`daily-review/references/multi-agent-review.md`、`daily-review/references/html-report-template/README.md`、`INDEX.md` 与 `scripts/tests/test_daily_review_html_report.py`；外汇图只消费已验证的中国货币网即期/1Y C-Swap 事实，按来源日去重并绘制最近 8～15 个同日工作日点，历史不足不补 0、不插值；情绪生命周期核对同日 JSON 的 status/coverage/refresh/计数/晋级/二板候选/前 12 只/source_errors，波段标 `[判断]`；连板高度图按 `trade_calendar.date` 最近最多 20 个开放日对齐日报，至少 2 个有效日才绘图，确认空池才可记 0，缺日报/损坏/不可判必须断线并标 partial，不得补 0 或插值；情绪节点固定比较目标日与此前 20 个开放日非 ST 最高连板，高度对比标 `[事实]`、核心启动日节点候选标 `[判断]`，缺口必须显式 `missing-data`；四模块均由组装器注入，chunk 不得手写重复模块 |
| `cli.py` 的 `db backup/db migrate` 或 `db/migrate.py` / `db/schema.py` 的 teacher_notes provenance | `repo-maintenance-workflows/references/teacher-notes-v40-migration.md`、`record-notes/SKILL.md`、`INDEX.md`；保留停写→0600 完整备份→源快照 SHA 绑定→显式原子迁移边界，普通入口不得隐式跨 v39→v40 |
| `main.py` 的 `pre/post/schedule` | `market-tasks/SKILL.md` |
| `scripts/main.py` 的 `regulatory` / `cmd_post` 监管接线、`scripts/services/regulatory_overview.py`、`scripts/api/routes/regulatory_monitor.py`，或 provider/registry 的 `stk_alert` / `stk_shock` / `stk_high_shock` 行为 | `market-tasks/SKILL.md` + `INDEX.md` 中 `regulatory` CLI/API 行；必须核对三个 Tushare range 接口统一进入 `post_extended`、盘后派生 `regulatory_anomaly_overview`、原始空结果 `preserve_nonempty_on_empty`、总览状态 `complete/partial/failed`、来源状态 `success/empty/partial/failed/stale/late`、`[事实]` / `[计算]` 分层、沪市主板区间收益率差与深市主板/创业板/科创板逐日偏离累加的分板块口径、上市后前 5 个无涨跌幅限制日排除、确认严重异动后的下一开放日重置与缺日 `partial`、`today/next_day` 理论触发涨幅/价格/涨跌停可达性、旧 `/api/regulatory-monitor` 兼容与新 `/api/regulatory-monitor/overview?date=`、辅助失败不阻断 `cmd_post`，以及手工写入必须显式 `--input-by`；不得仅凭 API 契约宣称前端已完成改造 |
| `main.py` / `scripts/cli/wechat_teacher_feed.py` / `scripts/services/wechat_teacher_feed/` | `market-tasks/SKILL.md`、`record-notes/SKILL.md`、`record-notes/references/ingestion-rules.md`、`INDEX.md` 与 `AGENTS.md`；保持“采集只落 manifest、按 digest 确认后才 add-note、默认不入池”边界 |
| `scripts/main.py` 的 `prefetch-calendar`、`scripts/collectors/market.py` 的 `prefetch_calendar*`、`scripts/services/calendar_sync.py` 或 `scripts/providers/akshare_provider.py` 的 `get_macro_calendar*` | `market-tasks/SKILL.md` + `INDEX.md` 中 `prefetch-calendar` 行 + `AGENTS.md` / `CLAUDE.md` + `deploy/launchd/README.md`；核对单日来源异常不伪装成健康空、区间任一日失败则 fail-closed、YAML 同目录原子替换、SQLite `(date,event)` 幂等、人工来源不覆盖、显式 `--input-by`、未来 7 日覆盖不足 `partial` 非零退出，以及 06:30 早于 07:00 盘前且模板未经确认不自动安装 |
| `scripts/main.py` 的 `cmd_post` new-high 接线、`scripts/cli/new_high.py`、`scripts/services/new_high/` 或 `scripts/utils/trade_date.py` 的 new-high 日历语义 | `market-tasks/SKILL.md` + `INDEX.md` 中 `new-high` 行；须核对复用 `today-post` 工作日 20:00 单一调度、无独立 launchd/APScheduler、只写两表及目标日报告且不自动推送、schema/基线相等/自然日日历完整、行业源非空、有效行情绝对地板、重复/有效 join/复权宇宙/申万覆盖/相邻日市场数门禁、按开放日动态升序补缺、canonical 只追加、单日同事务、`BEGIN IMMEDIATE` 二次查重 + 尾日 CAS、成功前缀与失败续跑、目标日报告原子替换及 `already_complete` 损坏自愈、手工 `daily` 连续协调/`backfill` 强刷年度日历且拒绝跳过尾日后开放日/历史更正须重建后缀、失败隔离且不阻断 margin 的完整契约 |
| `scripts/cli/trend_leader.py` 或 `scripts/services/trend_leader/` | `market-tasks/SKILL.md` + `INDEX.md` 中 `trend-leader` 行 + `AGENTS.md` / `CLAUDE.md`；必须核对自动申万主线最近最多 3 个有效集中度快照（空/全部 UNCLASSIFIED 不计）、2～3 条至少命中 2 次/仅 1 条命中 1 次、`--sectors` 绕过稳定门、默认 `hybrid` LLM 失败关闭概念并标 `fallback_l2`、显式 `hybrid --no-llm` / `l2+concept` 机械分支、报告快照数/门槛/来源/LLM 状态、本地 MD 先原子落盘再推送、dry-run 零报告写入及不回溯清理历史池边界 |
| `scripts/cli/monthly_pattern.py`、`scripts/services/monthly_pattern/`、`monthly_pattern_*` schema/migration，或 provider 的 `get_market_monthly_quotes` / `get_financial_snapshots` capability | `market-tasks/SKILL.md` + `INDEX.md` 中 `monthly-pattern` 行 + `AGENTS.md` / `CLAUDE.md` + `deploy/launchd/README.md`；必须核对只用完成月、月线与 `adj_factor` 前复权、股票基础资料 `L/D/P` 全状态按 `list_date/delist_date` 还原历史 as-of 外部宇宙并用 `monthly_pattern_bar_manifests` certified 收据认证覆盖、供应商新旧影子代码重复仅在同交易所+完整有效月线九字段/复权一致+唯一 canonical 对应+窗口内无角色反转/链式映射时抑制并留版本化 manifest 收据，缓存复用校验九字段摘要并将持久化六字段+复权逐项对照 canonical bar，schema/摘要/计数/事实任一损坏即 cache miss（否则 fail-closed，不拼接分段行情或迁移旧 episode）、公告日 as-of 财务（同公告日修订按内容哈希追加且无法证明公开日时从首次观测日起可见；年报 verified / 中报仅 pre_screen）、三策略及历史行业无 as-of 时题材 fail-closed、基本面 verified 下一严格完成月确认、active/risk 严格重入资格、连续两完成月跌破 MA5 才进入 exited 终态并保留 episode 历史、申万二级成交额稳定前排仅标 `[判断]`、五表、所有 daily/backfill 写入口显式 `--input-by`、裸/`--no-push`/`--dry-run` 三档、backfill/pool 边界、每月 2 日 23:10 per-task launchd 单次调度且休眠错过可接受，以及不写 TradeDraft/TradePlan/关注池、不提供买卖建议 |
> 月度模式日报评分变更还必须核对：全量技术候选只计数、财务 verified 才可入榜；同股多策略去重后的固定 100 分权重与确定性排序；Top10 按申万二级聚合且板块内降序、Top3 排除 risk/行业冲突/低完整度；`daily_basic` 必须目标日前最近成功且不超过 7 天、覆盖≥90%，金融 PB 与非金融 PE(TTM) 优先及同行业同指标最少 5 个样本；合同负债/研发投入按行业适用，缺失不冒充失败。该层只能改变日报与推送展示，不得改变 pool 状态机或计划层。
| `scripts/services/monthly_pattern/indicator_watch*.py` / `monitor_daily.py` 或 `monthly-pattern monitor*` CLI | `market-tasks/SKILL.md` + `INDEX.md` 中 `monthly-pattern monitor` / `monitor-daily` 行 + `AGENTS.md` / `CLAUDE.md` + `deploy/launchd/README.md`；必须核对只读复用 certified 完成月、不自动补采/不回退旧月，最新完成月 as-of 宇宙与 certified `universe_count` 精确对账后区分合法退出与缺尾月，并覆盖整窗无 bar 的在册代码；五阳与初始 MA5 支撑只认完成月，目标月仅在 T 日前复权坐标下以前四个连续自然月月末收盘 + 当前 T 日收盘计算动态 MA5 当前资格硬门，`support_held=True` 才能进主清单，`False` 必须单列等待且不得被日/周 MACD 共振越过，`None` 必须单列不可判并把运行标为 `partial`，也不得用 as-of 目标月新增完成月种子；等待桶仅为同一目标月的无状态日频快照，跨月保留期限未定义前不得持久化成永久池；默认日期遵守上海 15:30 收盘安全线，目标日之后的日线/周线不得进入历史结果，日线与周线统一用 T 日前复权锚，MACD `above_zero` 与 `bullish_on_zero` 分开，5/13 均量只标现有系统辅助口径，目标日 ST 身份与可选行业/简称解耦且空/非法 ST 结果 fail-closed，历史行业无 as-of 时标未知，关键源缺失分 `blocked/partial` 而非正常空候选；手工 `monitor` 默认不落文件、不推送、不挂调度，只有显式 `--save-report` 才写本地 Markdown；自动 `monitor-daily` 禁止截断，只在当前已收盘开放日运行，初始化/日历/只读库故障也要落 blocked 健康收据，日历未确认时只落本地 pending 且不得推送；显式历史日默认仅预览且永不推送，只有同时带 `--no-push` 才保存并推进基线；日期文件只作 latest 摘要，每次调用须追加不可覆盖的 planned/delivery/final 审计 JSON，以本地原子快照+pending/sent 账本实现 complete 基线差异和事件流 at-least-once 通知，partial/blocked 不推进股票基线且健康指纹包含所有缺口身份/原因（含 `insufficient_history`）、首次完整快照与完成月翻页不制造批量事件；独立 launchd 用无时区 15 分钟 tick，由 runner 只在上海工作日 19:10（含）至 19:25（不含）执行一次，不进 schedule/APScheduler；两者均不改月线七表/池、不要求 `--input-by`、不写 TradeDraft/TradePlan/关注池、不提供买卖建议 |
| `scripts/cli/tail_scan.py` 或 `scripts/services/tail_scan/` | `market-tasks/SKILL.md` + `INDEX.md` 中 `tail-scan` 行 + `AGENTS.md` / `CLAUDE.md` |
| `scripts/cli/daily_leaders.py` 或 `scripts/services/daily_leaders/` | `market-tasks/SKILL.md` + `INDEX.md` 中 `daily-leaders` 行 + `AGENTS.md` / `CLAUDE.md`；核对申万二级板块口径、语义属性与板型分离、同板块同属性唯一、最终最多 15、LLM 失败仍按相同硬约束兜底，并保持展示内代码解析、非法后缀及显式代码冲突 fail-closed |
| `scripts/cli/emotion_leader.py` 或 `scripts/services/emotion_leader/` | `market-tasks/SKILL.md` + `INDEX.md` 中 `emotion-leader` 行 + `AGENTS.md` / `CLAUDE.md`；核对二板候选、三板/高度前二晋级、ST 排除、停牌日期间隔但高度逐级递增、人工第 5 步核心只读合并、启动日前收盘基准、全 OHLC 前复权、波段 `[判断]`、归档只改展示、此前 20 个开放日完整高度窗与 `height_breakthrough` 三态、打开高度 `[事实]` 与启动日节点候选 `[判断]`、增量刷新、`--full-refresh`、`ok/partial/source_failed` 及 dry-run/json 零写入 |
| `scripts/utils/pattern.py`，或 `scripts/cli/pattern_scan.py` / `scripts/services/pattern_scan/` | `market-tasks/SKILL.md`（形态篇小节）+ `INDEX.md` 中 `pattern-scan` 行 + `AGENTS.md` / `CLAUDE.md`；核对四条件缺一不可（多头排列严格递减、`zero_axis_bullish`=DIF 与 DEA **同时**>0 且 DIF>=DEA 而非只看 DIF、放量阳占比≥50% 且「放量阳→缩量阴」≥1 组且放量阴打断待成组、近 20 交易日未加速复用 `trend_leader.detectors.accel_threshold` 不得复制口径）、主线复用 `string_yang.mainline.judge_mainline(use_llm=False)` 不得另写一份、**均量线用 `vol` 且窗口 5/13**（区别于 `ma-breakout` 的成交额 5/10，两套故意不复用）、**前复权须 `apply_qfq(keys=OHLC_PRICE_KEYS)` 含 open**（判阴阳要 close/open 同坐标系，只复权 close 会在除权日把阳线判成阴线）且因子缺失整票剔除不硬算、两个 worker 有界并发但每票调用顺序/次数与输入结果顺序不变、样本不足与「形态不满足」分开计数不折叠、全宇宙取数失败必须 `source_failed` 不得报成空池、报告须声明前复权口径与「形态成立不等于应当买入」、排序为成交额降序非形态强弱排名、无池无状态不写库、22:45 per-task launchd 单一调度不进 `schedule`/APScheduler；回归 `scripts/tests/test_pattern.py` |
| `scripts/cli/ma_breakout.py` / `scripts/services/ma_breakout/` / `deploy/launchd/com.alyx.tradesystem.ma-breakout.plist` | `market-tasks/SKILL.md`（ma-breakout 小节）+ `INDEX.md` 中 `ma-breakout` 行 + `AGENTS.md` / `CLAUDE.md` + `deploy/launchd/README.md`；核对仅保留工作日14:50尾盘快照、CLI 14:45～15:00 且上海当日/交易日守卫、历史完成日线+当日实时价/累计成交额合成（新浪元→Tushare千元）、实时日期/新鲜度、`partial` 与 `source_failed` 分离、不回退上一交易日、不恢复21:35收盘版、不写SQLite/计划层/关注池；回归 `test_ma_breakout_{scanner,cli,launchd}.py` |
| `scripts/cli/morning_brief.py` 或 `scripts/services/morning_brief/`，或 `scripts/providers/*` 的 `get_market_announcements_range` capability | `market-tasks/SKILL.md`（盘前早报小节）+ `INDEX.md` 中 `morning-brief` 行 + `AGENTS.md` / `CLAUDE.md` + `deploy/launchd/README.md` + `Makefile` morning-brief 别名；核对三段式口径（隔夜行情逐标的失败隔离标缺 / 金十复用 `macro_flash.collector` 窗口=上一交易日 20:00→今 08:00 且 `morning_brief.keywords` 经 `load_keyword_config(config_key=)` 单一真源 / 巨潮公告自带分页+时间预算+降序校验守卫的早停，任一预算触发必须 `truncated` 不伪装全量）、上一交易日核实型解析（DB 日历优先、未核实必须落 gap 转 `partial`，不得静默回退昨日缩窗）、无 `--date` 时窗口终点当日 08:00 后钳制回 08:00、报告唯一临时文件原子写、金十失败=`source_failed` 推告警非零退出而公告/行情失败只降级 `partial`、非交易日守卫[dry-run 豁免]与连库即迁移、三档运行、工作日 08:00 per-task launchd 单一调度不进 `schedule`/APScheduler、转述事实层无 LLM 不扫红线关键词 |
| `scripts/cli/intraday_monitor.py` 或 `scripts/services/intraday_monitor/` | `market-tasks/SKILL.md` + `INDEX.md` 中 `intraday-monitor` 行 + `AGENTS.md` / `CLAUDE.md` + `deploy/launchd/README.md`；核对原有单标的规则语义不漂移；`value_mode=daily_pct_change` 必须用同一实时回包的最新值与昨收自行计算并稳定浮点边界，等于阈值不误触发，昨收/时间/布局缺证据 fail-closed；动态 `previous_close_ma` 必须由只读开放日历精确锁定样本、日线与复权因子逐日对齐并前复权，缺证据 fail-closed；横截面 `limit-up-amount-100b-before-1000` 精确为上海 `09:30<=quote_time<10:00` 最新价达到正式涨停价且累计成交额 `>=100亿元`，覆盖主板/ST/双创/北交所分币舍入、基金排除、沪深前五开放日及北交所首日无涨跌幅限制排除、同股当日一次；部分响应/时点不可判必须 `partial` 且已确认命中仍推，重复行情/多 active rule 不漏不重；保留行情失败重试一次、独立 pending/sent 状态、失败同日及窗口后重试、跨日过期、每5分钟+09:59补扫与双状态 transition guard（部署时分两次调用兼容旧 guard）；`e2e-test` 仍只验证单标的且必须单独授权；全链路不写 SQLite/计划层/持仓/关注池、不构成买卖建议 |
| `scripts/cli/intraday_summary.py` 或 `scripts/services/intraday_summary/` | `market-tasks/SKILL.md` + `INDEX.md` 中 `intraday-summary` 行 + `AGENTS.md` / `CLAUDE.md` + `deploy/launchd/README.md`；核对 09:30/13:00 基线与八个半小时推送槽位、只读交易日历 fail-closed、全市场≥4000且行情覆盖≥95%、当日/10分钟新鲜度、价格与累计成交额两点差分、午休不跨窗、宽基/申万缺失只降级对应段、上一槽位缺失须标 partial/未计算、报告先落盘且 pending 成功后才记 sent、同槽位去重与跨日过期、三档运行、独立 launchd 槽位后5分钟窗口；不写 SQLite/计划层/持仓/关注池、不预测方向、不构成买卖建议 |
| `scripts/cli/value_watch.py` 或 `scripts/services/value_watch/` | `market-tasks/SKILL.md` + `INDEX.md` 中 `value-watch` 行 + `AGENTS.md` / `CLAUDE.md`（reference 详情在 `market-tasks/references/market-observability.md`）；核对三层口径（红利回撤 episode 档位+2pp 迟滞 / 卖出阶梯 `entry_price`+`entry_date` 身份键与 `insufficient_identity` / 稀缺周线粘合+MACD 同周与 2 完成周失效）、`sent_events` 事件账本首发去重（enter 需仍成立、exit 迟到必补）、strict 日历闸门（目标日=最新已收盘交易日才推、blocked 不推）、陈旧守卫 `stale_source`、三档运行（裸/`--no-push`/`--dry-run` 全内存）与 21:45 per-task launchd 单一调度 |
| `scripts/cli/sector_crowding.py` 或 `scripts/services/sector_crowding/` | `market-tasks/SKILL.md` + `market-tasks/references/market-observability.md` + `INDEX.md` 中 `sector-crowding` CLI/API 行；核对申万 L2-only、当日采集按 `is_pub` + 可观察行情有效期构造目标日 as-of 宇宙，分类表双读一致并过已验证总码/发布码基线、回填使用自然日完整的 SSE 开放日独立脊柱，错码/越界/非开放日、有效期内空码及首端/内部/末端缺日均 fail-closed，空探针须连续3次稳定且合法生效前/退出后空窗不误杀；明确 `index_classify` 无官方生效/退出日导致的首末有效日推断边界；目标日可信 as-of 宇宙优先，遗留或脏元数据快照继承最近可信宇宙、全程无可信时按窗口内历史观察并集保守兜底，部分缺失 `status=partial`/全缺 `status=missing_l2` 且保留缺失项为 `null`、`close>MA144/MA233` 半年线/年线上 `[事实]`、最近10个交易快照日内 close/amount 同日双创此前20个交易快照日新高的价量共振 `[判断]`、最近事件证据、数据不足三态、读取时现算不落派生值、标签 GET 使用 SQLite `mode=ro` 且请求期不迁移，以及 API/Web 对齐 |
| `scripts/cli/macro_flash.py` 或 `scripts/services/macro_flash/` | `market-tasks/SKILL.md`（宏观快讯速读小节）+ `INDEX.md` 中 `macro-flash` 行；保留"只归档不入库、入库须走 record-notes 确认再 `db add-macro`"边界，以及独立 launchd（交易日盘后 20:00 / 周日 22:00 回溯 54h）不进 `main.py schedule`、同日 complete 幂等跳过、`show` 仅 complete+sha 校验通过才展示正文的语义 |
| `scripts/services/tail_scan/concept_context.py`，或 provider 的 `get_stock_concept_memberships` capability | `market-tasks/SKILL.md` + `INDEX.md` 中 `tail-scan` capability/消费者/字段用途 + `AGENTS.md` / `CLAUDE.md`；必须核对当前 `type=N` 快照（非历史 as-of）与 T-1 热概念分层、共享容器过滤、报告 5 个/2 个上限、完整归属不进粗分/PK、兼容热字段语义，以及 `source_failed` / `coverage_failed` / `member_failed` / `missing` 状态不混淆 |
| `scripts/utils/llm_cli.py` 或 LLM CLI/env 语义调整 | `market-tasks/SKILL.md` + `INDEX.md` 中 recommend/research-digest/cognition-digest 行 |
| `scripts/workflows/research-digest-workflow.mjs` / `scripts/workflows/huibo_helper.py` / 慧博 Antigravity 诊断语义调整 / `HUIBO_REPORT_PDF_DIR` 下载归档目录约定调整 | `market-tasks/SKILL.md` + `INDEX.md` 中 research-digest workflow 行 |
| `main.py` 的 `ingest *` | `ingest-inspector/SKILL.md` |
| `main.py` 的 `plan *` | `plan-workbench/SKILL.md` |
| `main.py` 的 `knowledge add-note/list/draft-*` | `knowledge-to-plan/SKILL.md` |
| `main.py` 的 `knowledge cognition-* / instance-* / review-*` | `cognition-evolution/SKILL.md`（若涉及观点结构化字段、`feedback_action` 或 `evolving_views_json` 聚合，也同步 `INDEX.md` 中 cognition-evolution / `/api/cognition/instances` 说明） |
| `main.py` 的 `executions import / list / audit-export` | `portfolio-manager/SKILL.md`（券商成交流水事实层，与 `db add-trade` 复盘维度分离） |
| `main.py` / `scripts/cli/review_factors.py` 的 `review factor-*`，或 `scripts/services/trinity_factor/` | `daily-review/SKILL.md`、`sector-projection-analysis/SKILL.md`、`INDEX.md` 与 `AGENTS.md`；必须同步独立客观来源族，`style_regime.board_break_realization` 只接受日期/覆盖/计数可对账的完整 `ok` 断板反馈，`partial/source_failed` 不计入；并精确区分 `promotion_realization` 仅校验 outcome 评分日、`prior_core_feedback` 显式 `popularity_provenance` 优先且非法 / 错位即拒绝、仅 provenance 键缺失的历史数据允许用同一 `promotion` 日期元数据 fallback；Step 5 人工最票 / 自动 leader 只能作 context 且不得抬高证据质量；同时保留评分/确认/回验仅接受开放交易日、每次 score（含 cache hit）追加 request audit 且 `input_by` 不进 cache key、主因子受控证据卡进入第 2 层、确认原子重建证据摘要并拒绝陈旧 run、后续评分输入写入自动清除旧决定、来源感知严格 T+1、canonical run 指标、append-only retry 与“不进入 TradeDraft/TradePlan”边界 |
| `scripts/cli/executions.py` 任意改动 | `portfolio-manager/SKILL.md` + `INDEX.md` 中 `executions ...` 行 |
| `scripts/services/broker_executions/` 任意改动 | `portfolio-manager/SKILL.md`（若行为契约变更）；任何 schema 字段/UNIQUE 调整还需同步 `INDEX.md` |
| `scripts/services/trade_thesis/` 或 `scripts/db/schema.py` 中 `trade_mode` 语义/枚举调整 | `portfolio-manager/SKILL.md` + `INDEX.md` 中 `thesis-*` 行 |
| 仓库维护工作流、CLI/API 对齐、巡检、文档/索引同步 | `repo-maintenance-workflows/SKILL.md` + `references/maintenance-checklist.md`；诊断、Review 与巡检默认只读，修改须按范围验证 |
| `api/routes/review.py` | `daily-review/SKILL.md`、`sector-projection-analysis/SKILL.md`（含 `POST /api/review/{date}/to-draft` 时也检查 `plan-workbench/SKILL.md`；若预填字段语义调整，如 `lead_stock` / `emotion_leader` / `capacity_leader`，或保存字段标准化语义调整，同步 Skill 文案；`step5_leaders` 身份校验失败必须返回 422，并原子回滚复盘与 tracking 写入） |
| `api/routes/review_factors.py` | `daily-review/SKILL.md`、`sector-projection-analysis/SKILL.md` 与 `INDEX.md`；评分/回验/metrics 路径必须与 `review factor-*` CLI 共用 service 语义 |
| `api/routes/planning.py` 中 `/api/plans/*` | `plan-workbench/SKILL.md` |
| `api/routes/planning.py` 中 `/api/knowledge/*` | `knowledge-to-plan/SKILL.md` |

> `monthly-pattern monitor` 还必须保持完成月种子的 AND 三态短路契约：结构损坏优先 `blocked`；月内复权形态未知但已有其他可信硬门失败时归 `not_matched` 并计 `shape_short_circuited_not_matched`；仅结论真正依赖未知形态时计 `blocked_price_shape`，且重分类不得扩大 `matched` 集合。

> `monthly-pattern facts-backfill` 必须同步 `market-tasks/SKILL.md`、`INDEX.md`、`AGENTS.md`、`CLAUDE.md` 与 `deploy/launchd/README.md`：dry-run 必须从严格只读连接复制到内存并先生成确认哈希，partial 仍给信息性预览；收据哈希须绑定 raw/manifest/既有派生事实水位，真实写入只接受同一重算收据且 unresolved/截断/漂移均为 0，并在 `BEGIN IMMEDIATE` 写锁内再次复核；A 股选股宇宙须显式排除并审计 B 股，五个主分类与种子数必须守恒；日线手/千元须换算为月线股/元后交叉验证，raw 月末复权因子也须一致；全市场月线源通过 as-of 覆盖门后才可证明 `certified_no_trade`，且证据哈希须绑定全市场月线与历史宇宙完整排序行摘要。确认后仅可在同一事务内运行派生两表专用 schema ensure，表、索引、防改触发器按 SQL 指纹验签，禁止从该入口调用全库 migrate；只追加独立派生事实与审计行，DDL 与事实共同回滚，禁止覆盖 raw 五表或把 `certified_no_trade` 合成为价格。

### 3.1 检查 `agents/openai.yaml` 是否仍匹配

若受影响的 skill 目录中存在 `agents/openai.yaml`：

- [ ] `display_name` 仍与 skill 目标一致
- [ ] `short_description` 仍能准确概括当前 SKILL.md
- [ ] `default_prompt` 仍显式引用 `$skill-name`
- [ ] 若 SKILL.md 已明显改义，重新生成或更新 `agents/openai.yaml`

### 4. 验证报告（每次修改后输出）

```
Skills 同步检查结果：
- [✅/❌] INDEX.md 已更新
- [✅/❌] test_cli_smoke.py 全部通过
- [✅/❌] 受影响的 SKILL.md 已检查并更新（如需）
- [✅/❌] 受影响的 agents/openai.yaml 已检查并更新（如需）
```

## Rules 文件真源 + IDE symlink 壳同步

`.agents/rules/<rule>.md` 是真源，`.claude/rules/<rule>.md` 与 `.cursor/rules/<rule>.mdc` 是 symlink 壳（让两个 IDE 都能加载到真源）。

### 新增 / 重命名 / 删除 `.agents/rules/` 文件时必须的连带操作

| 操作 | `.agents/rules/` | `.claude/rules/` | `.cursor/rules/` |
|---|---|---|---|
| 新增 `<rule>.md` | `git add .agents/rules/<rule>.md` | `ln -s ../../.agents/rules/<rule>.md .claude/rules/<rule>.md && git add` | `ln -s ../../.agents/rules/<rule>.md .cursor/rules/<rule>.mdc && git add` |
| 重命名 `A.md` → `B.md` | `git mv .agents/rules/A.md .agents/rules/B.md` | 删旧 symlink + 建新（路径变了） | 删旧 + 建新（文件名 + 扩展名都变） |
| 删除 `<rule>.md` | `git rm .agents/rules/<rule>.md` | `git rm .claude/rules/<rule>.md` | `git rm .cursor/rules/<rule>.mdc` |

### 验证

```bash
python3 -m pytest scripts/tests/test_agent_symlinks.py -v
```

测试覆盖：
- `.agents/` 真源目录存在
- `.cursor/skills` 与 `.claude/skills` 是符号链接指向 `.agents/skills/`
- `.agents/rules/*.md` 都有对应的 `.claude/rules/<>.md` 与 `.cursor/rules/<>.mdc` symlink

`pre-push` hook 也跑全套 pytest，会兜底 catch；但**别依赖 hook，新增 rule 时主动建好三处**，否则会被 pre-push 拒推 + 需要补一个 amend/follow-up commit。

### 同时必须做的两件事（容易遗漏）

1. **在 `CLAUDE.md` + `AGENTS.md` 的"AI 协作规则"表格里加索引行**：写明该规则的"作用"，让 agent 启动时能看到规则存在。
2. **如果新规则涉及代码触发条件**（如"修改 X 文件时触发 Y 检查"），考虑加进现有规则的 globs / 触发条件章节，或建明确触发链。

参见 [[karpathy-behavior]]（精准修改、清理影响面），[[implementation-plan]]（计划阶段的范围声明）。

## 背景说明

AI Agent（Claude Code / Codex / Cursor）通过 `.agents/skills/` 中的文档了解如何调用 CLI 和 API（`.cursor/skills/` 与 `.claude/skills/` 是 symlink 壳）。
如果 CLI 签名或 API 接口变更而 skill 文档未更新，Agent 将生成错误的命令，导致数据写入失败。
尤其是 `scripts/main.py` 中新增的 `review`、`ingest`、`plan`、`knowledge` 命令组，以及 `api/routes/review_factors.py` / `planning.py` 中的复盘评分、计划和资料接口，会直接影响影子评分 / observation / draft / plan / 采集诊断的协作流。
此规则确保每次底层变更时，skill 文档始终与实际接口保持同步。
