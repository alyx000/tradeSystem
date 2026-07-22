# 市场任务：市场统计与联动观察

命中 `volume-watch`、`new-high`、`sector-correlation`、`sector-crowding`、`market-timing`、`margin-index-correlation` 或 `value-watch` 时只读本文件。

## 成交额板块集中度监控（volume-watch）

每交易日 21:00 自动跑（launchd `com.alyx.tradesystem.volume-watch`），也可手动：

```bash
make volume-watch-daily        # = python3 main.py volume-watch daily（采集+落库+渲染+钉钉推送）
make volume-watch-daily-dry    # = ... --dry-run（仅打印,不落库不推送,预览用）
make volume-watch-trend        # = python3 main.py volume-watch trend（只读打印最近 30 日趋势）
# 历史回补（落库但不重推历史日报到钉钉）：三档裸/--no-push[落库+打印不推]/--dry-run[不落库]
python3 main.py volume-watch daily --date 2026-07-07 --no-push --refetch

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

## 前复权历史新高统计（new-high）

首版**不挂定时、不默认推送**。用于只读统计 A 股全市场创前复权历史新高个股数，按申万二级行业分组，并将完整结果落 SQLite：历史水位表 `stock_adjusted_high_watermark`，每日快照表 `daily_new_high_stats`。报告每个行业默认只展示 Top10，完整明细保留在 SQLite/JSON，方便后续 CLI/API/Web 读取。

```bash
make new-high-daily          # = python3 main.py new-high daily（落库 + 本地 MD/JSON，不推送）
make new-high-daily-dry      # = ... --dry-run（只打印，不落库不落报告不推送）
make new-high-trend          # = python3 main.py new-high trend（只读最近 30 日）
make new-high-backfill       # = python3 main.py new-high backfill（默认近 5 年建立水位）

python3 main.py new-high daily --date 2026-07-08
python3 main.py new-high daily --date 2026-07-08 --dry-run
python3 main.py new-high daily --date 2026-07-08 --push
python3 main.py new-high trend --date 2026-07-08 --days 30 --json
python3 main.py new-high backfill --start-date 2021-07-08 --end-date 2026-07-08
```

- **口径**：当日 `high * adj_factor` 严格大于该股票截至昨日历史最大 `high * adj_factor`，计为创前复权历史新高；首日新股/首次出现股票只初始化水位，不计新高。
- **回填用途**：`backfill` 不是为了推送历史报告，而是为了先建立“截至昨日”的历史前复权高点基准；未回填直接跑 `daily` 只能初始化水位，不能宣称完整全历史口径。
- **运行语义**：`daily` 默认落库并写 `data/reports/new-high/YYYY-MM-DD.{md,json}`，不推送；`--push` 才推钉钉；`--dry-run` 不落库、不落报告、不推送；`trend` 只读；`backfill` 永不推送。
- **守红线**：全 [事实] 统计，不出价位目标、不做买卖建议、不写交易计划层、不入关注池。
- **依赖 env**：`TUSHARE_TOKEN`（全市场 `daily` + `adj_factor` + 申万行业映射）；钉钉 env 仅在显式 `--push` 时需要。

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
- 依赖 env 同 volume-watch（`TUSHARE_TOKEN` + 钉钉）。详见 [`sector-projection-analysis`](../../sector-projection-analysis/SKILL.md) 的定性推演可调取本定量证据。

## 板块拥挤度每日采集（sector-crowding）

每交易日 21:30 自动跑（launchd `com.alyx.tradesystem.sector-crowding`，错开 volume-watch 21:00 / sector-correlation 21:15），**默认不推送**（复盘时 `report` 查看；多 agent 复盘路2 固定引用 `report` 输出作 `sector_crowding_snapshot` 素材，见 [multi-agent-review](../../daily-review/references/multi-agent-review.md)），也可手动：

```bash
python3 main.py sector-crowding daily --date 2026-07-17 --dry-run    # 预览,不落库不推送(豁免非交易日守卫)
python3 main.py sector-crowding daily --date 2026-07-17 --push       # 落库+显式推钉钉(默认不推)
python3 main.py sector-crowding report --date 2026-07-17             # 只读:三部分全景+历史分位+双高清单(现算)
python3 main.py sector-crowding trend --sector 801080.SI --days 60   # 只读单板块序列(建议用申万代码查,回填行无中文名)
python3 main.py sector-crowding backfill --start 2019-01-01          # 一次性历史回填(fail-closed)
```

- **三部分口径**（认知来源：卞老师「拥挤度三维度」框架；采集/落库=申万 L1 全量 + L2 全量，报告 L2 只展示 TOP10，L1/L2 严格按 level 隔离不混排双计）：

| 部分 | 口径 | 输出 |
|---|---|---|
| 交易拥挤度 | `行业当日成交额 ÷ 全市场总成交额`（全市场总额缺失/异常时 share 置空 + meta 标 `missing_data`，不落假值） | 占比% + 行业自身历史分位 + 绝对参考线 |
| 斜率拥挤度 | 5/20/60 日累计涨幅（close 序列，末根 close 日期必须等于目标交易日，否则该窗口置空）+ 自身历史分位 | 三窗口涨幅+分位；20 日分位 ≥90% 标「高斜率」 |
| 资金流代理 | 行业主力资金流连续净流入 + ETF 份额 5 日变化 + 两融余额变化 | **每行强制标注「非公募持仓真值」，不参与双高评分**；ETF 份额单次跳变超存量 30%（疑拆分）标「勿直读」 |

  持仓拥挤度的公募季报**真值**属 v2 独立任务，v1 只有上表第三行的代理信号。

- **与 volume-watch 的口径边界（防混用）**——两任务都涉及「成交额集中」，命名严格区分：

| 任务 | 口径 | 报告命名 |
|---|---|---|
| `volume-watch` | 成交额 Top20 个股按申万二级聚合，share = 占 Top20 内部比重 | 「Top20 主线集中度」 |
| `sector-crowding` | 申万行业指数成交额 ÷ 全市场总成交额 | 「全行业交易拥挤度」 |

- **派生指标不落库、读取时现算**：`sector_crowding_daily` 一天一行 JSON 快照只存原始事实（close/amount/share_pct）；历史分位与双高清单（交易拥挤度分位与 20 日斜率分位同时 ≥90%，属 `[判断]` 层）一律在读取时从自身历史序列滚动计算——避免回填/修正历史后已落库的派生值过期。
- **绝对参考线**：交易拥挤度 ≥30% 提示、≥40% 历史极值区（2020-21 白酒 ~42%，本轮电子/TMT 到过 47%）。
- **运行语义**：`daily` 默认落库不推送，`--push` 才推钉钉，`--dry-run` 仅打印（不落库不推送，豁免非交易日守卫）；非交易日守卫 + 采集结果日期一致性校验；`report` / `trend` 只读。
- **回填 fail-closed**：`backfill` 任一行业码失败/空返回则**整体中止不落库**（重跑即重试，UPSERT 幂等）；单片返回恰 2000 行视为镜像静默截断直接报错；两阶段流程（按码分片采集→内存按日期聚合→整日一次性 UPSERT）防同日互相覆盖；默认 2019-01-01 起（覆盖白酒极值基准）。
- **守红线**：事实呈现 + `[判断]` 标注，不出价位、不给买卖建议、不写计划层、不入关注池。调度唯一入口=per-task launchd（21:30），不进 `main.py schedule`/APScheduler。
- 依赖 env：`TUSHARE_TOKEN`（`sw_daily` 等）；钉钉凭据仅显式 `--push` 时需要。

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

## 大盘择时观察（market-timing）

用于 6 指数斐波那契时间周期、底分型生命周期与市场级客观上下文的只读观察；所有结论标为 `[判断]`，不预判方向、不出价位、不给买卖建议。

```bash
python3 main.py market-timing daily --date YYYY-MM-DD
python3 main.py market-timing daily --date YYYY-MM-DD --dry-run
python3 main.py market-timing daily --no-push
python3 main.py market-timing daily --pivot-index 000001.SH --pivot-date YYYY-MM-DD
python3 main.py market-timing signals --date YYYY-MM-DD --index 000001.SH --limit 30 --json
```

- `daily` 三档：裸命令=落库+推送；`--no-push`=落库+打印；`--dry-run`=内存计算、不落库不推送。
- 手工 pivot 必须同时给 `--pivot-index` 与 `--pivot-date`；未知指数、非法日期或日期不在窗口时 fail-fast。
- 时间周期从双向 swing 拐点起算，命中 `5/8/13/21/34/55/89`；多指数同日命中只增强“共振”观察，不推断方向。
- 底分型从 bars 无状态推导，生命周期固定为 `none/forming/confirmed/invalid`，避免漏跑造成状态漂移。
- 信号落 `market_timing_signal`；同日同指数重跑刷新。`signals` 支持 `--date`、`--index`、`--limit` 与 `--json`。工作日与周日 21:40 的 per-task launchd 运行，不进入 `schedule` / APScheduler。
- 指数范围：上证、深成、创业板、科创50、中证2000与平均股价；报告同时呈现成交额近20日地量分位、跌停家数和涨跌家数。

## 价值投资条件监控（value-watch）

认知出处：`teacher_notes#391`（鞠磊价值投资年课：红利价值 / 稀缺价值）。工作日 21:45 per-task launchd `com.alyx.tradesystem.value-watch`（接 market-timing 21:40 之后），不进 `schedule`/APScheduler。

```bash
python3 main.py value-watch daily [--date YYYY-MM-DD] [--dry-run | --no-push]
python3 main.py value-watch report [--date YYYY-MM-DD]

make value-watch-daily        # = value-watch daily（采集+落库+事件推送）
make value-watch-daily-dry    # = value-watch daily --dry-run（全内存,不落库不推送不写账本）
make value-watch-report       # = value-watch report（只读已落库快照）
```

- **三层口径**：
  - ① **红利买入触发**：银行指数 `801780.SI`（10/15% 两档）与长江电力 `600900.SH`（仅 10% 档）自 120 交易日滚动高点的回撤 episode——进入须回撤 ≥ 档位、退出须回撤 < 档位-2pp（迟滞防贴线抖动把同一轮回撤拆成多次事件）。
  - ② **卖出阶梯**：active 持仓 ∩ 四大行（工/建/农/中）+ 长电，按 `entry_price` 计算**价格涨幅**（raw close，未含分红），10/15/20% 各档首触与 20 档后回落事件；事件身份键 `canonical:entry_date:holding_id`。
  - ③ **稀缺价值**：片仔癀 `600436.SH` 周线 MA5/10/20 粘合 ≤3% **且 MA5 高于上一完成周**（「粘合再向上」，仅约束 MA5）+ 周 MACD(12/26/9) 上零轴**同周成立**；signaled 后连续 2 个完成周不满足才失效（去抖）。
- **运行语义**：落 `value_watch_daily` 快照（一天一行，同日重跑 UPSERT）；**事件账本 `sent_events` 去重**——同一事件键只推一次，enter 类补推需当前仍成立、exit 类迟到必补；**strict 日历闸门**——目标日 = 最新已收盘交易日才推送，日历 blocked 时一律不推（落库照常）；**陈旧守卫** `stale_source`——源数据停在上一交易日时该标的本日不评估（与 `source_failed` 分开呈现）；单标的失败隔离不中断整批；非交易日守卫（`--dry-run` 豁免）。三档 = 裸 `daily`[落库+推] / `--no-push`[落库+打印候选] / `--dry-run`[全内存不落库不推送不写账本]；历史 `--date` 落库但绝不推；`report` 只读已落库快照。
- **调度**：工作日 21:45 per-task launchd `com.alyx.tradesystem.value-watch`（接 market-timing 21:40 之后），不进 `schedule`/APScheduler；错过可接受——次日运行按事件账本自动补齐。
- **守红线**：数字标 `[事实]`、解读标 `[判断]` 并注出处 `#391`；非操作指令、不构成投资建议、不写计划层、不入关注池。
- 依赖 env：`TUSHARE_TOKEN`（`pro.sw_daily` 直连）+ 钉钉凭据 `DINGTALK_WEBHOOK_TOKEN/SECRET`（推送时）。
