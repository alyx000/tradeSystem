---
name: market-tasks
description: 手动触发或自动定时执行盘前/盘后行情采集任务、行业推荐推送、研报速读，并将结果摘要推送回 channel
version: "1.4"
---

# Skill: 市场数据任务（盘前 / 盘后采集）

## 使用场景

当用户说：

- 「帮我跑一下盘前采集」
- 「执行今天的盘后任务」
- 「补跑 2026-04-01 的盘后」
- 「打开市场看板 / 看盘后信封」
- 「行业推荐定时推送」/「最近值得看的行业」
- 「今天的研报速读」/「最近哪些股票被首次覆盖 / 评级上调」/「美股机构评级有什么变动」

时激活此 skill。

## 优先入口

优先使用仓库根目录：

```bash
make market-open DATE=YYYY-MM-DD
make market-json DATE=YYYY-MM-DD
make market-envelope DATE=YYYY-MM-DD
make today-open
make today-close
make today-pre DATE=YYYY-MM-DD
make today-post DATE=YYYY-MM-DD
```

需要底层命令时在 `scripts/` 目录运行：

```bash
python3 main.py pre --date YYYY-MM-DD
python3 main.py post --date YYYY-MM-DD
```

## 行业推荐定时推送

把最近 3 / 7 日数据按本质拆成**三段**推送钉钉自定义机器人，三段各归各位、不相互冒充：

- **📌 近期大盘判断** ← `teacher_notes.core_view`（note 级大盘观点，去重置顶）。可用 Antigravity CLI 提炼成 2-4 条要点；Antigravity 不可用 / 命中红线则降级展示最近 3 条原始观点。
- **🔥 行业热度榜（按老师提及）** ← `teacher_notes.sectors` 提及次数（`score = mentions × recency_decay`），仅排名 + 提及数。
- **💡 有具体催化的行业** ← `industry_info`（真行业逻辑，按 confidence → date 倒序）。

> 红线扫描只作用于 Antigravity 生成的大盘判断；降级原文与催化原文是用户录入的事实层，不扫（见 `formatter.py` 注释）。

定时入口（已挂 APScheduler）：

- 日报：工作日 07:10（与盘前 07:00 错峰 10 分钟）
- 周报：每周日 20:00（独占周日，与盘后 mon-fri 20:00 不冲突）

手动入口（CLI 优先用 `make`）：

```bash
# 仅打印不推送
make recommend-daily-dry      # 等价 python3 main.py recommend daily --dry-run
make recommend-weekly-dry     # 等价 python3 main.py recommend weekly --dry-run

# 真推送（需先 export DINGTALK_WEBHOOK_TOKEN / DINGTALK_WEBHOOK_SECRET）
make recommend-daily
make recommend-weekly

# 自定义窗口（直接调底层）
python3 main.py recommend daily  --lookback-days 5
python3 main.py recommend weekly --lookback-days 14
```

环境变量：

| 变量 | 默认 | 说明 |
|---|---|---|
| `DINGTALK_WEBHOOK_TOKEN` | — | 钉钉机器人 webhook access_token（必填，不入 git） |
| `DINGTALK_WEBHOOK_SECRET` | — | 钉钉机器人加签 secret（必填，不入 git） |
| `ANTIGRAVITY_BIN` | `agy` | Antigravity CLI 可执行路径 |
| `LLM_TIMEOUT_SECONDS` | `90` | LLM 调用超时（硬上限 180s） |
| `LLM_MODEL` | 空 | 指定模型，留空走 Antigravity 默认 |

## 成交额板块集中度监控（volume-watch）

每交易日 21:00 自动跑（launchd `com.alyx.tradesystem.volume-watch`），也可手动：

```bash
make volume-watch-daily        # = python3 main.py volume-watch daily（采集+落库+渲染+钉钉推送）
make volume-watch-daily-dry    # = ... --dry-run（仅打印,不落库不推送,预览用）
make volume-watch-trend        # = python3 main.py volume-watch trend（只读打印最近 30 日趋势）

# 指定日期 / 窗口（直接调底层）
python3 main.py volume-watch daily --date 2026-05-29 --dry-run
python3 main.py volume-watch trend --date 2026-05-29 --days 10

# 回填历史：--refetch 强制重拉，绕过 daily_market 陈旧缓存（如换算 fix 前采集的旧数据）
#   批量回填时建议 env -u DINGTALK_* 屏蔽推送，只落库不刷屏：
for d in 2026-05-27 2026-05-28 2026-05-29; do
  env -u DINGTALK_WEBHOOK_TOKEN -u DINGTALK_WEBHOOK_SECRET \
    python3 main.py volume-watch daily --date "$d" --refetch
done
```

- `daily`：read-through 读 `daily_market.top_volume_stocks`（缺则重拉）→ 申万二级打标（三级降级：申万成分命中 → `stock_basic` 兜 name → 「未分类」）→ 聚合 → 落 `daily_volume_concentration` → 渲染（含 **Top20 个股明细表**：名称(代码)/申万二级行业/成交额/带符号涨跌，成交额降序）→ 钉钉。非交易日无数据自动跳过（不写库不推送）。
- `--refetch`：跳过 read-through，强制走 provider 重拉 top20。用于**回填历史**——库里 `top_volume_stocks` 可能是某次换算 fix（如 `/1e4`→`/1e5`）之前采集的陈旧值，read-through 命中即用会灌坏数据；`--refetch` 用当前（已修复）provider 代码重取。
- `trend`：只读最近 N 日（默认 30），输出板块轮动 / 头部量级环比 / 个股连续在榜；不采集、不落库、不推送。
- **报告结构 v2**（全事实层，守红线，钉钉手机端友好）：头部摘要(合计/占两市 + 涨跌分布红/绿/平+均值+最强弱) → CR3 行(环比 pp + 窗口分位 + 连升/连降) → 板块集中度(**列表非表格**——钉钉手机端不渲染 markdown 表格) → 📈 板块区间涨幅排名(成交额前50,5/10/20 日三档) → 🔥 板块热度趋势(各行业占 Top20 比重 vs 前期,🔴升温/🟢降温,A股红涨绿跌) → 💰 头部资金(量级 vs 近期均值放/缩量 + 新陈代谢核心/今日新进 + 今日新进资金流向行业) → 🔄 异动个股(今日新进带行业+涨跌 / 退出,替代逐只罗列 Top20) → 📌 连续在榜(streak≥2,Top8)。不足 2 交易日出兜底文案、不渲染跨日块。
- **📈 区间涨幅排名（成交额前50，独立于上方 Top20 集中度）—— 申万板块榜 + 同花顺题材榜双维度**：独立取**成交额前50**个股 → `get_stock_daily_range` 算 5/10/20 日区间涨幅(`(close[-1]/close[-1-N]-1)*100`，历史不足/末根非榜单日/NaN→None) → 出两份榜：
  - **申万板块榜**：按申万二级单标签分组，剔「未分类」，`build_sector_gain_ranking`。
  - **同花顺题材榜**：复用共享 `concept_tags.build_stock_concept_map`(`get_ths_member` 反查 + 容器概念≤300 过滤)给每票打 `concepts`，**多标签**(一票进它每个概念)、概念在 universe 内成员 ≥2 才出，`build_concept_gain_ranking`。
  - 两份均**组按组内涨幅最大个股降序、平手比次大**(向量字典序降序)，5/10/20 各一份独立榜。原始集(含 industry + concepts + gains)落 `daily_volume_concentration.gain_universe_json`(v34 增列 + ALTER 兜底；concepts 为 JSON 增键无新列)。纯函数被 Markdown 与 API 共用。健壮性：gains/概念取数失败各自 fail-closed(不拖垮主日报)，降级重跑按覆盖判据保留库内既有榜单不抹(coverage-aware 幂等)。全客观区间涨幅(属 [事实])，守红线不出价位目标/不给买卖建议。经只读 API `GET /api/market/sector-gain-ranking/{date}`(`rankings`+`concept_rankings`) 在八步复盘「2.板块」(`SectorGainRanking` 组件，申万/题材维度切换 + 三档周期 Tab)展示。慧博/同花顺概念依赖 `TUSHARE_TOKEN` 积分(`ths_member`)。
- 行业口径=**申万二级**（联动 `get_sector_rankings`）；「未分类」（次新等）不计入前3行业集中度，报告标 `industry_coverage`。
- 依赖 env：`TUSHARE_TOKEN`（`scripts/.env`，`index_member_all` 需积分）、`DINGTALK_WEBHOOK_TOKEN/SECRET`（`~/.config/tradeSystem.env`，daily 推送）。

## 板块相关性监控（sector-correlation）

每交易日 21:15 自动跑（launchd `com.alyx.tradesystem.sector-correlation`，错开 volume-watch 21:00），也可手动：

```bash
python3 main.py sector-correlation daily --date 2026-05-29 --dry-run   # 预览,不落库不推送
python3 main.py sector-correlation matrix --date 2026-05-29            # 打印完整矩阵(不推送)
python3 main.py sector-correlation trend --date 2026-05-29 --days 20   # 只读漂移趋势
```

- **数据源 Tushare 主源**（镜像 `tushare.xyz`，区间拉取）：指数 `index_daily`(`pct_chg`)、申万二级 `sw_daily`(`pct_change`/`amount`)、同花顺概念 `ths_daily`(`pct_change`/`turnover_rate`，**无成交额列**)。akshare/eastmoney 降为 fallback（实测当前不通）。
- **多日活跃选板块（固定配额）**：行业按**多日平均成交额** Top-`--top-industries`(默认15)、概念按**多日平均换手率** Top-`--top-concepts`(默认10)，各排各的；逐日快照取近 `--activity-days`(默认10) 天。概念名撞行业名自动加 `(概念)` 后缀防丢数据。
- **多窗 5/20/60**：原始日涨跌幅 Pearson（联动）+ **剔除上证后的残差超额相关**（真跷跷板，逆向以此为准）+ 板块对 4 指数（上证/创业板/沪深300/科创50）的相关与 β。日报头条出**近5日联动榜**（短期共振）+ **结构联动榜 60 日**；反向榜双窗对照取 **5日/60日**（两窗都显著=稳定跷跷板，仅 5 日显著=近期偶然，5 个点相关噪声大需结合 60 日）。20 日窗仍入库 / matrix。
- 行业口径=申万二级（同 volume-watch）；概念=同花顺，名撞行业时自动加 `(概念)` 后缀防丢数据。
- `daily`：采集→落 `sector_correlation_daily`（一天一行 JSON 列）→ 渲染→钉钉；`matrix`：读当天(缓存命中纯只读免初始化 Tushare，`--refetch` 强制现算)打印完整矩阵不推送；`trend`：只读最近 N 日漂移。无指数/有效板块<5/各窗有效列<5 → 跳过(不落库不推送)。
- **守红线**：报告标注"相关为同期统计共现，非因果、非买卖建议"；窗口/样本天数显式展示。
- 依赖 env 同 volume-watch（`TUSHARE_TOKEN` + 钉钉）。详见 [`sector-projection-analysis`](../sector-projection-analysis/SKILL.md) 的定性推演可调取本定量证据。

## 两融余额与指数联动性（margin-index-correlation）

**随 `main.py post` 盘后采集一并执行**（折进 `cmd_post` 末尾，由 `today-post` launchd 工作日 20:00 触发；不单独挂 launchd、不进 schedule/APScheduler）。注意：两融由交易所盘后发布、20:00 多未发全 → `get_margin_series` 回退到 T-1 完整日，报告标注「两融为 T-1」（联动按 5/20/60 窗，差一天影响极小）。失败隔离不影响主盘后流程。也可手动：

```bash
python3 main.py margin-index-correlation daily --date 2026-06-19 --dry-run    # 内存跑,不落库不推送
python3 main.py margin-index-correlation daily --no-push                       # 落库但仅打印
python3 main.py margin-index-correlation daily --divergence-windows 5,20 --divergence-gap 0.5 --max-lag 3
python3 main.py margin-index-correlation signals --date 2026-06-19 --days 30    # 只读最近趋势
python3 main.py margin-index-correlation signals --json                         # 原始记录 JSON
```

- **数据源**：`get_margin_series(start,end)` 取两融**区间序列**——Tushare `pro.margin` 主源（沪深北三市合计+分项，复用 `get_margin_data` 完整性逻辑只保留应到交易所齐全的完整日）/ akshare 交易所官网降级（仅沪深，无北交所，新到旧迭代封顶 `max_days` 防 Tushare 宕机时上百次串行请求）；指数复用 sector 的 `fetch_index_series`（`index_daily` 的 `pct_chg`）。
- **核心口径（锁死）**：两融余额是**水位**，必须先 `margin_returns` 转**日变化率(%)** 再与指数 `pct_chg` 同口径做 Pearson——漏做 pct_change 直接拿绝对余额算相关=伪高相关。
- **四维**：① **背离预警**（头条）：近 5/20 日**复利累计**口径，指数涨两融降 / 指数跌两融升；以**指数交易日脊柱锁窗**，两融缺窗内交易日标「日期缺口」不评估（防稀疏日当连续日伪造预警）。② **余额水位+趋势**：绝对值/日环比/近20日分位/连增连降/偏离MA20。③ **领先/滞后**：lagged corr，`lag>0`=两融滞后指数、`lag<0`=两融领先。④ **同步相关**：5/20/60 窗 Pearson（复用 sector `align_panel`/`raw_correlation`）。
- **对照**：total两融 × 多宽基（上证/创业板/沪深300/科创50）+ 沪市两融 × 上证 + 深市两融 × 深成（满足「上证为主 + 多宽基 + 沪深各自对照」）。
- `daily`：采集→落 `margin_index_correlation_daily`（一天一行 JSON 列）→渲染→钉钉；三档=裸[落库+推]/`--no-push`[落库+打印]/`--dry-run`[内存不落不推]；**非交易日守卫仅 persist 时生效**（dry-run 豁免）。两融盘后发布滞后时取最近完整日，`meta.stale` 标记 + 报告提示「两融为 T-1」。
- **守红线**：全标 `[判断]`，不出价位、不给买卖建议、不写计划层；脚注声明不构成投资建议。
- **复盘网站**：经只读 API `GET /api/market/margin-index-correlation/{date}`（`web_payload.build_daily_payload` 读 `margin_index_correlation_daily`，无记录 `available:false`）在八步复盘「1.大盘」`MarginIndexCorrelation.tsx` 组件渲染四维（背离头条 + 余额水位表 + 领先滞后 + 同步相关多窗表，stale 时提示两融为 T-1）。
- 依赖 env 同 volume-watch（`TUSHARE_TOKEN` + 钉钉）。

## 趋势主升漏斗扫描（trend-leader）

每交易日 21:30 自动跑（launchd `com.alyx.tradesystem.trend-leader`，排在 volume-watch 21:00 + sector-correlation 21:15 之后，确保主线池 `daily_volume_concentration` 已落库），也可手动：

```bash
# 日报：涨停∩主线→检测→入池/退池→渲染→钉钉
python3 main.py trend-leader daily
python3 main.py trend-leader daily --date 2026-06-12 --dry-run    # 内存副本跑：不落池、不推送（历史校准用）
python3 main.py trend-leader daily --no-push                       # 落池但仅打印，不推送（软上线/排障）
python3 main.py trend-leader daily --sectors '["半导体","玻璃玻纤"]' --top-k 8  # 手工主线 ∪ 自动 Top-K（top-k 须正整数）
python3 main.py trend-leader daily --main-line l2+concept --top-concepts 8       # 主线再 ∪ 同花顺概念分支（默认 l2 仅二级）

# 只读看池（在池天数 / 信号标记 / 退出原因）
python3 main.py trend-leader pool --status active
python3 main.py trend-leader pool --status exited --json
```

- **漏斗**：候选 = 当日涨停（`get_limit_up_list`）∪ 双创(20cm)涨幅≥15% 加速（`get_market_daily_changes`）→ 映射申万二级（`get_stock_sw_industry_map`）→ ∩ 主线池 → 拉区间 OHLCV（`get_stock_daily_range`）→ 检测器判定 → 入池/维护/退池（落 `trend_leader_pool` 状态机）。
- **board-aware 加速（GAP A，对齐鞠磊「主板涨停 / 20cm 涨15%+」）**：主板加速=涨停≈9.8%，双创(300/301/688/689)加速=15%（不必全 20% 涨停）；候选源因此并入双创 15-19.9% 的首次加速票，入池表「触发」列区分`涨停`/`双创15%加速`，「首次加速日」为 board-aware 口径。
- **主线口径 `--main-line`（GAP B，对齐鞠磊「主线或其分支」）**：默认 `l2`=仅申万二级 Top-K（零行为变化）；`l2+concept`=再 ∪ 同花顺概念**资金净流入 Top-M**（`--top-concepts`，默认 8）。概念分支 = 同花顺概念（CPO/PCB/液冷服务器…），**非申万一级**（一级太宽混杂已被全月验证否定）。个股命中`二级∈主线` **或** `概念∩主线概念` 即入候选；经概念分支入池的票在报告标「申万二级·分支:概念名」。**成员数闸 `CONCEPT_MAX_MEMBERS=300`**：净流入排序前先剔容器概念（融资融券/深股通/华为概念等几千成员的资格类标签，聚合净流入霸榜但非窄分支），全月 27 天验证零容器、~88% 真分支。降级可观测：`source_errors` 含 `concept_flow`/`ths_member`/`concept_coverage`（取数成功却热概念无成员=ths_member 部分截断）。**默认仍 `l2`，翻默认前须补弱市防御篮子收紧 + sw_l2 回填**（见代码注释 defer）。
- **入池门槛**：首次加速（board-aware，近 60 日除今日外无同级加速）+ 主线缓涨（不含贴MA5——加速日必远离 MA5，贴MA5 是入池后回踩信号）。**退出只在客观趋势破坏**（收盘跌破 MA10 / 连续 2 日跌破 MA5 且跌幅扩大）；远离 MA5 只打「乖离过大」标记，不退池。
- **三档运行**：裸 `daily`（落池+推）/ `--no-push`（落池+仅打印）/ `--dry-run`（内存副本跑，不落池不推送，历史校准安全）。
- **池内唯一身份=裸代码**：Tushare `600552.SH` 与 AkShare `600552` 归一，避免重复 active 行。同日重跑/推送失败重试：已 active 票归 `refreshed`，报告仍合并展示在「今日新入池」不丢。
- **守红线**：盘后只读观察清单，全部标 `[判断]`、不出价位、不给买卖建议、不写交易计划层；临盘买点用户自行判断。信号 label 中性化（缩量回踩/贴MA5/乖离过大），渲染层零业务决策。
- 依赖 env 同 volume-watch（`TUSHARE_TOKEN` + 钉钉）。架构归位见 [`tradesystem-blueprint`](../../../docs/architecture/tradesystem-blueprint.md)「派生信号层」+「盘后只读监控族」。

## 串阳首阴股票池（string-yang）

每交易日 21:50 自动跑（launchd `com.alyx.tradesystem.string-yang`，排在 volume-watch 21:00、sector-correlation 21:15、trend-leader 21:30、market-timing 21:40 之后），也可手动：

```bash
make string-yang-daily       # = python3 main.py string-yang daily（落 MD + 钉钉）
make string-yang-daily-dry   # = python3 main.py string-yang daily --dry-run（仅打印）

python3 main.py string-yang daily --date 2026-06-12 --dry-run
python3 main.py string-yang daily --top-k 8 --no-push
python3 main.py string-yang daily --top-concepts 10 --teacher-lookback-days 10 --no-push
python3 main.py string-yang daily --no-llm --dry-run
```

- **只推确认票**：只输出“今日已经出现第一根阴线”的票；连续五阳但尚未出阴线的预备池不输出。
- **主线口径**：默认把 `daily_volume_concentration` 当日 Top-K 申万二级（默认 5）、同花顺概念资金分支 Top-N（默认 8，`get_concept_moneyflow_ths` + `get_ths_member`，成员数≤300 剔容器概念）、近 N 日老师观点（默认 7 天，`teacher_notes`）交给 LLM；LLM 只裁决主线申万二级/概念分支，不选股、不生成买卖建议；失败或无有效裁决降级成交额 Top-K。`--no-llm` 强制只用成交额 Top-K。
- **筛选口径**：个股所属申万二级 ∈ 主线 或 个股同花顺概念 ∩ 主线概念 → 拉区间 OHLCV（`get_stock_daily_range`）→ 排除 ST/退市风险 → 昨日以前连续 ≥5 根阳线 → 串阳段无涨停且最大单日涨幅 ≤7% → 最近 20 个交易日无涨停 → 首阴收盘价 / MA60 ≤ 1.08 → 今日 `close < open` 第一根阴线且今日成交额 > 前5个交易日最大成交额。概念分支票在报告标「申万二级·分支:概念名」。
- **排序口径**：按今日成交额 / 前5日最大成交额优先，其次今日成交额；报告同时展示今日额 / 昨日成交额。
- **三档运行**：裸 `daily`（写 `data/reports/string-yang/YYYY-MM-DD.md` + 推钉钉）/ `--no-push`（落报告不推送）/ `--dry-run`（仅打印，不落报告不推送）。非交易日守卫仅在非 dry-run 时生效。
- **守红线**：全标 `[判断]`，不出价位、不给买卖建议、不写交易计划层、不自动入关注池。
- 依赖 env 同 volume-watch（`TUSHARE_TOKEN` + 钉钉），LLM 额外依赖 `ANTIGRAVITY_BIN`/`AGY_BIN`、可选 `LLM_MODEL`、`LLM_TIMEOUT_SECONDS`（默认 180）。
## 断板反包盘后扫描（board-break）

每交易日 21:20 自动跑（launchd `com.alyx.tradesystem.board-break`，排在 volume-watch 21:00 + sector-correlation 21:15 之后、trend-leader 21:30 之前），也可手动：

```bash
python3 main.py board-break daily
python3 main.py board-break daily --date 2026-06-12 --dry-run   # 只打印，不落盘不推送（历史校准）
python3 main.py board-break daily --no-push                      # 落盘但不推送
python3 main.py board-break daily --no-llm                       # 跳过 LLM 两两 PK，只出加权分排序
```

- **候选筛选（[事实]）**：昨日连板数≥2 只当日断板——断板日涨幅 ≤6%（用户既定 6% 参考位机械换算，非价格预测）且未跌停；仅 10cm 主板参与，剔除 ST。
- **八维度加权打分（[判断]，附依据明细）**：主线板块（volume-watch 当日 Top-K 归属）/ 增持回购 / 定增 / 减持（**250 日分位翻极性**——低位减持记正分、高位减持记负分、中位不计分）/ 其他重大公告（利好/利空）/ 业绩（预告快报方向）/ 近10日涨幅过高 / MACD 零轴上下；单维度取数失败只降级该维度（`missing`/`source_failed`），不中断整批。
- **LLM 两两 PK 循环赛（`--no-llm` 关闭）**：只喂 [事实] 卡（不含加权分），按加权分先截断强池再 `itertools.combinations` 循环赛；单场超时不重试记无效场，其它失败重试 1 次；无效场占比超阈值 → 熔断（`status="melted"`）；PK 理由段经红线过滤后渲染。
- **输出**：双排序（加权分排名 + PK 胜场排名，PK 未跑/熔断显 `—`）+ 每票八维度依据明细 + 脚注（剔除统计/数据完整性/PK 熔断状态）；MD 落盘 `data/reports/board-break/` + 钉钉。
- **三档运行**：裸 `daily`（落盘+推）/ `--no-push`（落盘+仅打印）/ `--dry-run`（只打印，不落盘不推送）。
- **核心源失败**：不产出正常候选清单，落 `source_failed` 失败报告 + 推告警 + 非零退出（launchd 场景可观测）。
- **无池无状态**：不落库、不建观察池；隔日盘中是否突破 6% 交易由用户自行判断，本清单不跟踪。
- 依赖 env 同 volume-watch（`TUSHARE_TOKEN` + 钉钉）；PK 依赖 Antigravity CLI（同 research-digest/cognition-digest 家族，独立 runner），可调 env 见上文表格：`ANTIGRAVITY_BIN`/`AGY_BIN`、`LLM_MODEL`、`LLM_TIMEOUT_SECONDS`（launchd 下建议 180+，见 `launchd-deploy.md`）；红线过滤复用 `services.recommend.formatter.REDLINE_KEYWORDS`。

## 4日均线二波观察池（ma-breakout）

每交易日 21:35 自动跑（launchd `com.alyx.tradesystem.ma-breakout`，排在 trend-leader 21:30 后、market-timing 21:40 前），也可手动：

```bash
python3 main.py ma-breakout daily
python3 main.py ma-breakout daily --date 2026-06-12 --dry-run
python3 main.py ma-breakout daily --windows 5,10 --leader-lookback-days 60 --top-n 30 --no-push
python3 main.py ma-breakout daily --json
```

- **口径**：用于二波，不找当前才冒出来的强票，也不找太久远的老龙头。先从 `trend_leader_pool`（自动趋势主升观察池）+ `leader_tracking`（复盘第 5 步人工确认最票/龙头）取目标日前近端历史龙头宇宙，默认近 60 自然日（可用 `--leader-lookback-days` 调整；若首次出现较早但最近仍被跟踪，也保留）。再对该宇宙内股票用 `get_market_daily_quotes` 近 10 个有效行情日组装序列；MA4 今日拐头向上 = 今日 MA4 > 昨日 MA4，且昨日 MA4 ≤ 前日 MA4；成交额突破 = 今日成交额同时大于两条成交额均线（默认 MA5/MA10，可用 `--windows` 改）；当日涨停的股票剔除，不进入观察池。
- **输出**：按今日成交额降序渲染盘后只读观察清单，表格展示历史龙头依据与首次出现日期，全部标 `[判断]`；不出价位、不做买卖建议、不写交易计划层/关注池。
- **运行语义**：无状态、不落库；裸 `daily` 打印并推钉钉；`--dry-run` / `--no-push` 仅打印；`--json` 输出结构化结果且不推送。
- **依赖 env**：`TUSHARE_TOKEN` + 钉钉 `DINGTALK_WEBHOOK_TOKEN/SECRET`。

## 研报速读（research-digest）

每天 22:00 由 Codex 自动化「每日慧博研报速读（Computer Use）」触发一次；自动化先通过 Computer Use 读取慧博终端当前 `HotReport` URL，再按预筛候选在慧博终端下载 PDF 到本地目录，最后显式传给 JS workflow 读取这些本地 PDF。workflow 仍会先按 **A 股交易日** 或 **A 股交易日前一天** 判断是否继续执行；其它日期只记录 skip，也可手动：

```bash
make research-digest-dry       # = python3 main.py research-digest daily --dry-run（仅打印,不调 Antigravity/不落盘/不推送）
make research-digest           # = python3 main.py research-digest daily（采集+渲染+落盘+钉钉）

# 指定 A股交易日 / 关美股叙事（直接调底层）
python3 main.py research-digest daily --date 2026-05-29 --dry-run
python3 main.py research-digest daily --no-llm

# 慧博深读增强（默认 desktop_terminal，可显式关闭）
python3 main.py research-digest daily --huibo-mode desktop_terminal --huibo-window-days 5 --huibo-reader-cap 20 --huibo-reader-concurrency 4 --huibo-recommend-cap 2
python3 main.py research-digest daily --huibo-mode off --dry-run
python3 main.py research-digest daily --huibo-cleanup-only --dry-run

# 慧博深读生产 workflow（Codex 自动化使用：Computer Use 先取得当前 HotReport URL，并在慧博终端下载候选 PDF）
HUIBO_HOT_REPORT_URL='<Computer Use 取得的当前 URL>' HUIBO_REPORT_PDF_DIR='/Users/alyx/tradeSystem/data/reports/huibo/terminal-downloads/2026-06-06' HUIBO_REFRESH_URL_FROM_APP=0 HUIBO_ALLOW_DIRECT_PDF_DOWNLOAD=0 node scripts/workflows/research-digest-workflow.mjs daily --date 2026-06-06 --reader-cap 20 --reader-concurrency 20 --reader-max-attempts 2 --preflight --publish --include-base-digest
node scripts/workflows/research-digest-workflow.mjs daily --date 2026-06-06 --retry-failed --reader-max-attempts 4
node scripts/workflows/research-digest-workflow.mjs daily --date 2026-06-06 --llm-input-dir /private/tmp/huibo-llm-input --resume
```

- **A股段** ← 巨潮 cninfo `get_research_report_list`（provider registry，主源 akshare `stock_rank_forecast_cninfo`）：取评级 + **评级变化 / 前次评级 / 目标价区间**；按机构聚合去重，`_cn_score` 排序——**鞠磊框架（teacher_notes#91）「首次覆盖」权重最高**（首次覆盖 +3.0 > 调高/上调 +1.5 > 调低 -1.0），signal 标签含 `首次覆盖 / 评级上调 / 多家覆盖`。
- **美股段** ← yfinance `get_us_rating_changes`（registry，新增 capability）：**只取方向变动** `init/up/down/reinit`（剔除 maintain/reiterate），按精选股票池（`RESEARCH_DIGEST_US_TICKERS` 或内置龙头池）逐 ticker 拉 `upgrades_downgrades`，美东窗口过滤（默认近 5 日）。源时效稀疏，**窗口内无变动 → 显式标注「无符合条件」，不冒充**（429/全失败也降级成空段，日志可区分、MD 暂不区分，留 v2）。
- **慧博深读增强** ← 默认 `--huibo-mode desktop_terminal`，通过 Codex 自动化的 Computer Use 前置步骤读取慧博终端当前 `HotReport` URL；URL 源只用于调用页面背后的 `/redian/HotReport/GetList` 拿候选列表，HTML 表格解析只做回退。PDF 必须来自**慧博终端实际下载/导出的本地文件**：候选 `PDF路径`、payload `pdfs/report_pdfs`，或 `HUIBO_REPORT_PDF_DIR` 指向的终端下载目录；生产自动化会逐篇打开候选并确认当前预览标题与 `candidate.title` 一致后再下载，下载前后用本地文件快照/sha256 去重，重复下载同一篇只记 `duplicate_current_report`、不计入下一候选；下载后复制为 `{report_id}.pdf`（`report_id` 优先使用慧博稳定字段 `DId/DocName`，不受 `abc/def/keyy/xyz` token 变化影响），并可额外保留安全化标题文件名兜底。运行 JS workflow 时建议使用绝对 `HUIBO_REPORT_PDF_DIR`；workflow 内也会把该目录按仓库根转成绝对路径再传给 helper，避免 helper 在 `scripts/` cwd 下误读。正式生产保持 `HUIBO_ALLOW_DIRECT_PDF_DOWNLOAD=0`；只有排障 404/token 时才临时显式设置为 `1`。如果无法通过 Computer Use 读到当前 URL 或下载 PDF，自动化必须失败并说明原因，不使用旧 `HUIBO_HOT_REPORT_URL`、不发布空慧博深读。可选 `HUIBO_REPORT_TEXT_DIR` 补充本地预览文本；后续如有官方 API 切 `--huibo-mode official_api` + `HUIBO_API_BASE_URL`/`HUIBO_API_TOKEN`，不改后续 reader/aggregator/ranker。预筛保留深度/专题/首次覆盖/行业策略/产业链/系列/跟踪，降权周报/日报/早报/晨会/点评/简评；首次覆盖/首次评级、`重点关注/重点跟踪/重点推荐/核心推荐/首推/建议关注` 等提示词加权；同主题限流防单一热点占满候选池。Antigravity 分工固定为**每篇一个 `report_reader`**，reader 优先通过 Antigravity CLI `@PDF路径` 读取 raw PDF，reader 默认并发数 `--huibo-reader-concurrency 4`（`HUIBO_READER_CONCURRENCY` 可覆盖；需要一篇 PDF 一个 Antigravity 同时读时可设为 reader cap），`report_text` 只是首页/核心观点/目录预筛摘录和兜底；没有 raw PDF 的候选只记录缺失状态，不派 reader、不进入最终推荐；用户已授权本项目把慧博 raw PDF 交给外部 Antigravity 阅读，默认启用，需关闭时设置 `HUIBO_ALLOW_EXTERNAL_PDF_LLM=0`；再由独立 `industry_aggregator` / `trend_aggregator` / `ranker` 在所有 reader JSON 收齐后执行，只读 reader JSON 和历史 summary JSON，聚合 agent 不接收原文/PDF。
- **慧博 JS workflow** ← `scripts/workflows/research-digest-workflow.mjs` 是慧博深读生产执行入口，调度由 Codex 自动化负责，JS 负责编排和可观测性，Python helper 复用既有采集/预筛/本地 PDF 归档/聚合/渲染/发布能力。阶段固定为 `collect → prescreen → download → read → finalize → cleanup`，带 `--preflight` 或 `HUIBO_ANTIGRAVITY_PREFLIGHT=1` 时在采集前追加 Antigravity 健康探针，带 `--publish` 时追加 `publish`；run 目录默认 `data/runs/research-digest/YYYY-MM-DD/`，包含 `state.json`、`events.jsonl`、`candidates.json`、`prescreened.json`、`downloaded.json`、`reader/*.json`、`summary.json`、`report.md`、`run_report.md`，发布后还有 `published.json`。每次启动都会生成 `invocation_id` 写入 `state.json.currentInvocation` 与当次 `events.jsonl`，便于 resume/retry 分组；`workflow_summary` 会汇总 `llm_status`、reader 成功/失败/跳过数、`ranker_status` 与基础段合并状态，`run_report.md` 额外列阶段耗时和每篇报告状态。`--resume` 会跳过已完成阶段和已完成 reader JSON；正式 Codex 自动化不带 `--resume`，避免沿用旧 run 状态；`--retry-failed` 只重跑失败 reader，并刷新 finalize/publish。reader 并发由 `--reader-concurrency` 控制，可设为 reader cap 以实现一篇 PDF 一个 Antigravity 同时读。正式 raw PDF 仍归档/去重在 `--raw-dir`（默认 `data/reports/huibo/raw`），read 阶段会把单篇 PDF 复制到 LLM 临时输入目录后再传 `@PDF`；`HUIBO_LLM_INPUT_DIR` / `--llm-input-dir` 表示临时输入 base 目录，workflow 实际使用其中的 `YYYY-MM-DD/` 子目录并写入 marker，cleanup 只删除带 marker 的本次子目录。`state.json` 同时记录 `pdfPath` 与 `llmPdfPath`，resume 时如临时副本缺失会从 raw 重新复制。reader 失败会在同一次 read 阶段自动重试，`--reader-max-attempts` 默认 2（累计尝试次数，`HUIBO_READER_MAX_ATTEMPTS` 可覆盖）；如果失败项已经达到上限，后续继续重试需显式提高上限，如 `--retry-failed --reader-max-attempts 4`。Antigravity 的 `quota_exhausted` / `auth_required` / `startup_failed` 会标记为全局不可用并停止后续 LLM 调用，未启动 reader 记为 `skipped_llm_unavailable`，finalize/publish 仍产出报告且在 `summary.json.meta`、`published.json` 与正文标明 Antigravity 不可用和 ranker fallback；`timeout` / `parse_failed` 仅按单篇失败处理。finalize 会对 reader JSON 做质量审计，命中目标价/买入卖出/仓位等红线的报告标 `quality_failed` 并退出推荐池；本地 fallback 推荐会在 `ranking_explanation` 写明 read_score、prescreen_score、质量扣分和命中原因。JS workflow 默认直接 `spawn` Antigravity CLI；`--publish` 会把 `report.md` 同步到 `data/reports/research-digest/YYYY-MM-DD.md` 并推钉钉；发布时按最近交易日重新采集 A股/美股基础段并合并慧博深读段，若基础段渲染失败则只发布慧博正文并在 `published.json` 写 `base_digest_error` / `base_digest_duration_ms`。
- **Top3** ← `ranker.pick_top3`：A股 / 美股各软保证 ≥1，某侧空则全给另一侧（不编造）；美股侧 `init`（首次覆盖）优先。
- **LLM 叙事（受控）**：仅对**已拉到的真实条目**补 `theme/one_liner` 两软字段（Antigravity，独立 `build_antigravity_runner`，超时/缺失/解析失败自动降级纯结构化）。**A股默认不叙事**（`cn_narrate=False`），美股默认叙事；事实为主键，LLM 不得新增/改 ticker/code/firm（三级 fallback 防幻觉）。
- **守红线**：红线只扫 LLM 生成的 `theme/one_liner`（含中英目标价/买入等关键词 + `neutralize_rating` 把"买入/Buy"中性化为"偏多档"）；**用户/源侧事实层（评级原文、目标价区间）不扫**——红线约束 AI 生成、不约束取数。
- **慧博本地存储清理**：raw 目录默认 `data/reports/huibo/raw`（`HUIBO_RAW_DIR` 可覆盖，保存/复制原始 PDF）保留 30 天，summary 目录默认 `data/reports/huibo/summaries`（`HUIBO_SUMMARY_DIR` 可覆盖）保留 180 天；正式任务结束后自动清理，`--huibo-cleanup-only` 只清理，配合 `--dry-run` 只展示将清理对象、不删除。
- 依赖 env：`DINGTALK_WEBHOOK_TOKEN/SECRET`（`~/.config/tradeSystem.env`，推送）、可选 `ANTIGRAVITY_BIN`/`LLM_MODEL`/`LLM_TIMEOUT_SECONDS`（默认 180，launchd 下 LLM 启动慢；`LLM_MODEL` 不设时由 Antigravity CLI 使用默认/自动模型）、`RESEARCH_DIGEST_US_TICKERS`（美股池，不设走内置）、慧博可选 `HUIBO_MODE`/`HUIBO_HOT_REPORT_JSON`/`HUIBO_HOT_REPORT_URL`/`HUIBO_REPORT_TEXT_DIR`/`HUIBO_REPORT_PDF_DIR`（慧博终端下载目录）/`HUIBO_READER_CONCURRENCY`/`HUIBO_ALLOW_EXTERNAL_PDF_LLM`/`HUIBO_ALLOW_DIRECT_PDF_DOWNLOAD`（默认关闭，仅兼容旧 URL 下载）/`HUIBO_API_BASE_URL`/`HUIBO_API_TOKEN`。A股 cninfo 免 key、yfinance 免 key；`--dry-run` 对齐现有语义，不调 Antigravity。

## 业绩预告/快报速报（earnings-digest）

全市场业绩预告（`forecast_vip`）+ 业绩快报（`express_vip`）每日采集存档 + 推送，**工作日 + 周日 22:00** 自动跑（launchd `com.alyx.tradesystem.earnings-digest`，周日档供周日复盘；周六不跑——周六公告由周日 3 日回看窗口覆盖；**不进 `main.py schedule` APScheduler**），也可手动：

```bash
make earnings-digest-dry       # = python3 main.py earnings-digest daily --dry-run（仅打印；采集落库照常，不落盘 MD/不推送）
make earnings-digest           # = python3 main.py earnings-digest daily（采集存档+缺口验证+落盘+钉钉）

# 指定日期 / 手动补采（直接调底层）
python3 main.py earnings-digest daily --date 2026-06-12 --dry-run
python3 main.py earnings-digest daily --lookback-days 7    # 连续漏跑后扩窗补采
```

要点：

- **采集层零筛选**：8 类预告 type + 修正公告 + 快报全量整行落 `raw_interface_payloads`（接口 `earnings_forecast` / `earnings_express`），筛选只发生在渲染层 Top 榜。
- **水位线增量**：只推 `ann_date` 晚于上次 success 采集日的新公告（周日推过的周末公告周一不重复；empty/failed 不推进水位线防镜像迟到公告丢失）。
- **次日缺口验证（着重段）**：「次日」=预告后的下一交易日；**开盘跳空 ≥2% 触发**（env `EARNINGS_DIGEST_GAP_THRESHOLD_PCT` 可调，保留「缺口」身份）；**市场投票方向取「次日收盘涨跌」符号**（收盘价才是市场对预告投出的真实一票，开盘脉冲常盘中回补/反转——高开低走的票按开盘会被误标超预期确认，按收盘正确翻为⚠️利好不及预期）输出 2×2 标签（✅超预期确认 / ⚠️利好不及预期 / 💡利空出尽 / ❌暴雷确认；收盘恰平昨收＝缺口完全回补→➖中性标签保留可见）；行渲染并列「跳空X% → 收盘Y%」；严格缺口 / 一字板按**开盘**口径标注；缺口段按 |收盘涨跌| 降序截断；交易日行情故障会渲染可见警示，不静默装空日。
- **五段渲染**：① 持仓/关注命中（不筛）② 缺口验证（截 30 条+尾注）③ 申万行业 Top5 ④ 分类计数 ⑤ 净利中值 ≥5000 万 Top 榜（env `EARNINGS_DIGEST_MIN_PROFIT_WAN` 可调；forecast/express 分开排名）。快报附「vs 此前预告区间」位置标签（90 天历史存档回看）。①⑤ 命中/Top 候选票附「vs 一致预期」列（口径三：券商全年预测中值×历史 H1 占比折算隐含中报预期，±10% 判超/符/低，标 `[判断·H1占比折算]`；亏损/无覆盖显示暂无；`--no-consensus` 关闭，每股 2 次外网调用）。
- **空窗口日不推送**（淡季静音）；MD 落 `data/reports/earnings-digest/YYYY-MM-DD.md`。
- 依赖 env：`TUSHARE_TOKEN` + 钉钉两变量；红线口径：预告数字属事实层取数（非 AI 生成），不做关键词过滤。

## 交易认知沉淀定时推送（cognition-digest）

按窗口对 `cognition-evolution` 沉淀下来的交易认知做**只读**汇总推送钉钉，三个独立周期各挂自己的 per-task launchd（**不进 `main.py schedule` APScheduler**）：

- **recent3d**（`com.alyx.tradesystem.cognition-digest-recent3d`）：每天 18:30，近 3 日窗口。
- **weekly**（`com.alyx.tradesystem.cognition-digest-weekly`）：每周日 20:00，近 7 日窗口。
- **monthly**（`com.alyx.tradesystem.cognition-digest-monthly`）：每月 1 号 09:00，近 30 日窗口。

手动入口（在 `scripts/` 目录运行）：

```bash
# 仅打印不推送（预览）
python3 main.py cognition-digest recent3d --dry-run
python3 main.py cognition-digest weekly --dry-run
python3 main.py cognition-digest monthly --dry-run

# 真推送（需先 export DINGTALK_WEBHOOK_TOKEN / DINGTALK_WEBHOOK_SECRET）
python3 main.py cognition-digest recent3d

# 指定锚点日期 / 关 LLM 叙事走模板兜底
python3 main.py cognition-digest weekly --date 2026-05-29 --dry-run
python3 main.py cognition-digest monthly --no-llm
```

- **取数**：只读认知三表（`trading_cognitions` / `cognition_instances`），按日历日窗口（非交易日）算**热度**（窗口内实例数）+ **共识**（不同老师数，按 name 归并防重复计数）+ **新增**（`created_at` 落窗口），排序后取各窗口 Top-N。`total_instances` / `teacher_names` 只数非弃用认知的窗口实例。
- **Antigravity 建议（受控）**：对汇总结果补「体系 / 方向」两类建议，复用 `build_antigravity_runner` + `REDLINE_KEYWORDS` 红线护栏（structural L1 校验 + 非字符串 bullet 丢弃 + 关键词中性化）；`--no-llm` 或 Antigravity 不可用 / 命中红线 → 整段模板兜底。
- **只读边界**：用 SQLite `mode=ro` URI 连接，**不 migrate / 不 commit / 不改 schema / 不写 user_version**；与 `cognition-evolution` skill 的**手动写入闭环**严格区分（本命令永不写库）。
- **空窗口**：窗口内无活跃认知 → 跳过推送（对齐钉钉减负），`--dry-run` 仍打印。
- 依赖 env：`DINGTALK_WEBHOOK_TOKEN/SECRET`（推送）、可选 `ANTIGRAVITY_BIN`/`LLM_MODEL`/`LLM_TIMEOUT_SECONDS`（默认 180）。

## 核心流程

1. 先确认任务类型、日期和是否属于历史补跑。
2. 手动补跑前先提醒覆盖影响，确认后再执行。
3. 运行后提取关键信息：
   - 文件输出
   - 推送状态
   - 关键市场摘要
4. 若失败属于 ingest 层问题，再切到 ingest 诊断。

## 禁止事项

- 不要在未提醒风险的情况下直接补跑历史日期。
- 不要直接手改 `daily/` 或 DB 伪造结果。
- 不要把 provider 降级误报为任务失败。
- 不要把复盘、计划问题混入采集执行本身。

## 最小验证

- `make market-json DATE=YYYY-MM-DD` 或 `make market-envelope DATE=YYYY-MM-DD` 能读取产物。
- 若执行了 `pre` / `post`，确认 `daily/YYYY-MM-DD/` 下对应文件存在。
- 若任务失败，明确记录失败点并建议切换 [`ingest-inspector/SKILL.md`](../ingest-inspector/SKILL.md)。

## 切换条件

- 若用户要继续做复盘，切到 [`daily-review/SKILL.md`](../daily-review/SKILL.md)。
- 若问题落在单接口、重试或健康检查，切到 [`ingest-inspector/SKILL.md`](../ingest-inspector/SKILL.md)。
- 若任务本身命令 / 文档 / 调度逻辑漂移，切到 [`repo-maintenance-workflows/SKILL.md`](../repo-maintenance-workflows/SKILL.md)。

## 结果汇报格式

1. 已执行的任务、日期与模式
2. 关键市场摘要与产物路径
3. 验证结果
4. 剩余风险或后续建议
