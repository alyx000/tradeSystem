# 技术课盘中量价与题材成分采集

| 对象 | 来源与执行时点 | 归档 |
|---|---|---|
| 上证指数、深证成指、创业板指、科创50 | 现有四指数盘中监控复用当次东方财富 5 分钟行情，逐次累积已闭合的 K 线。每个完整交易日每个指数 48 根。上午、下午分别聚合 30 分钟，六根全部有效才生成。 | `data/reports/index-volume-monitor/ohlcv-5m/YYYY-MM-DD.json` |
| 前夜核心与同题材对照股 | 使用上一开放日资料和本日开盘前可核验的版本确定名单。原 20:00 定时入口在常规盘后采集结束后，逐只采集 09:31—11:30、13:01—15:00 的闭合分钟。保存开高低收、成交量、成交额及单股来源，分别核验同日参考昨收、涨跌停价和停牌记录。 | `data/reports/course-core-minutes/YYYY-MM-DD.json` |
| 前夜重点细分题材 | 常规盘后任务从前夜有效的手写复盘选题，通过同花顺概念身份核验后留存当日成员。优先复用同日已有成员，掉出当日前十仍跟踪前夜选定的对象。比较基准取严格上一开放日。 | `daily/YYYY-MM-DD/post-market.yaml` 中的 `raw_data.tracked_theme_members`，随原报告双写 |
| 逐股炸板明细 | 常规盘后任务保存东财炸板池的代码、首次封板时间、炸板次数、金额和价格。来源失败时封板率与炸板率保留缺口。 | `raw_data.broken_board_pool`，独立查验可存至 `data/reports/broken-board-pool/YYYY-MM-DD.json` |
| 前夜核心股与同题材昨日涨停股的 09:25 竞价 | 盘后按事实日查询 Tushare 开盘前最终集合竞价成交，逐只保存竞价成交价、成交量和成交额。实际撮合时点、上游返回和本机取得时点分开。 | `data/reports/course-opening-auction/YYYY-MM-DD.json` |
| 通达信平均股价、微盘股 | 盘中按已有时槽查询 880003、880823 各自 5 分钟的开高低收，记录原始行情时间。每个指数的缺口和取数尝试可独立查验。 | `data/reports/tdx-index-5m/YYYY-MM-DD.json` |
| 题材盘中扩散 | 盘后使用前夜已知的成分与同日逐股报价快照。每批按实际采样时刻计算上涨占比、涨跌幅中位数；相邻同半场快照可核验时计算区间成交额。手写点名样本单列覆盖范围。 | `data/reports/theme-intraday-breadth/YYYY-MM-DD.json` |
| 非 ST 跌停触价与收盘状态 | 复用盘后日线、实际跌停价、事实日身份及停牌资料，统计最低成交价触及跌停后，多少只收盘仍在跌停、多少只已离开。 | `raw_data.limit_down_touch`；独立复核归档在 `data/reports/limit-down-touch/` |
| 跌停价附近的卖一盘口 | 前一开放日跌停及本日新触价的可观察对象，复用实际 10 分钟批次，留存有来源的卖一价量、最新成交价及报价时间。两个采样点之间的变化只定位到时间区间。 | `data/reports/limit-down-orderbook/YYYY-MM-DD.json` |
| 重点题材催化 | 使用开盘前可核验的题材，与金十报道匹配。媒体报道时间、原文发布时间、盘中程序首次取得、从盘后档能确认的最迟已知时间分别记录。 | `data/reports/theme-catalysts/YYYY-MM-DD.json` |
| 外盘同刻报价 | 对纳指 100 期货、新加坡 A50 期货、恒生科技现货逐品种核验。按上海采样时点对照原始报价时间，同窗涨跌要求同品种同合约且基准有效。 | `data/reports/course-cross-market/YYYY-MM-DD/` |
| 宽基 ETF 分钟量价 | 512100、510300 按五分钟收盘时间留存基金自身成交量额，核对同刻标的指数、微盘以及邻近十分钟槽的逐股涨跌家数。每个参照各有实际采样时间与覆盖。 | `data/reports/broad-etf-minutes/YYYY-MM-DD/` |
| 重点涨停股的买一封单 | 开盘前的核心和锚点、盘中已见触及真实涨停价的股票，沿用本批买一、卖一和真实报价时间，核对相邻时点的排队金额变化。 | `data/reports/limit-up-orderbook/YYYY-MM-DD.json` |
| 境内股指期货 | 20:00 按 IF、IM 具体合约留存收盘、结算、成交、持仓及公开会员榜，与同日沪深 300、中证 1000 现货收盘计算基差。 | `data/reports/domestic-index-futures/YYYY-MM-DD.json` |
| 银行与券商盘中强弱 | 对当日冻结的四类申万二级银行及证券Ⅱ按等权样本统计上涨家数和涨跌幅中位数，20:00 汇总，正式复盘②板块展示。 | `data/reports/bank-broker-intraday/YYYY-MM-DD.json` |

## 查验与单独重试

以下命令从项目根目录执行，补采日期取所需的已收盘开放日。历史分钟取数受供应商可见窗口限制。

```bash
python3 scripts/tools/course_core_minutes.py --date 2026-11-27 --input-by USER --json
python3 scripts/tools/index_bar_archive.py --date 2026-11-27 --input-by USER
python3 scripts/tools/tracked_theme_members_replay.py --date 2026-11-27 --input-by USER --json
python3 scripts/tools/broken_board_pool.py --date 2026-11-27 --input-by USER --save
python3 scripts/tools/course_opening_auction.py --date 2026-11-27 --input-by USER --json
python3 scripts/tools/tdx_index_archive.py --date 2026-11-27 --input-by USER
python3 scripts/tools/theme_intraday_breadth.py --date 2026-11-27 --input-by USER --save
python3 scripts/tools/limit_down_touch.py --date 2026-11-27 --input-by USER --save
python3 scripts/tools/limit_down_orderbook.py --date 2026-11-27 --input-by USER
python3 scripts/tools/theme_catalysts.py --archive --date 2026-11-27 --input-by USER
python3 scripts/tools/course_cross_market.py --replay --date 2026-11-27 --slot 10:02 --input-by USER
python3 scripts/tools/course_cross_market.py --replay-day --date 2026-11-27 --input-by USER
python3 scripts/tools/broad_etf_minutes.py --replay-day --date 2026-11-27 --input-by USER
python3 scripts/tools/limit_up_orderbook.py --date 2026-11-27 --input-by USER
python3 scripts/tools/domestic_index_futures.py --date 2026-11-27 --input-by USER
python3 scripts/tools/bank_broker_intraday.py --date 2026-11-27 --input-by USER --save
```

四指数使用本机已加载的 `com.alyx.tradesystem.intraday-monitor` 运行目录。读取当日结果时先看 `valid_count` 和各指数缺失时刻。个股先看冻结名单和 `coverage`，再看每只股票来源。240 根齐全和交易参考资料完整分别计数，尾盘已有 230 根只代表相应时段有证据。定时入口带 `--retry-previous`，完成当日本轮取数后，对严格上一开放日已落档但分钟缺失的对象再试一次。之后取得的历史数据按实际取得时刻留痕。

题材成员按事实日保存。前夜原文、概念身份、当日成员、前一日对照各有独立覆盖限制。只在两日成员都核实后计算新增与退出。历史缺少成员证据时不能使用现在的成分替补。选题依据、概念代码和重跑规则见[重点细分题材成分](tracked-theme-members.md)。

东财原始炸板池与按日线最高价触及涨停后收盘回落的逐股结果分别展示，后者只能核对曾经触价。见[逐股炸板池](broken-board-pool.md)。

09:25 竞价保存按日查询的实际成交，样本在 09:15 前确定。通达信两个指数各有独立的 5 分钟覆盖；回执同时给出本次返回根数和累积存档根数。题材扩散分别列前夜概念完整成分和手写点名样本。见[竞价成交口径](course-opening-auction.md)、[通达信分时](tdx-index-5m.md)、[题材扩散](theme-intraday-breadth.md)。

低位堆量的同日行业证据复用盘后行业块和同日停牌归档，细节见[低位连续堆量与板块背景](low-volume-cluster.md)。

宽基放量与市场涨跌的时间对照见[宽基 ETF 分钟量价](broad-etf-minutes.md)。盘中盘口的股数、金额及相邻时点限制见[涨停买一封单](limit-up-orderbook.md)。境内股指的交割月、基差方向与会员披露范围见[境内股指期货](domestic-index-futures.md)。银行与券商使用的行业名称、分母和完整度见[盘中行业强弱对照](bank-broker-intraday.md)。

复盘时可以用[盘后跌停触价](limit-down-touch.md)核对当日有多少只收盘已离开跌停价，再在[跌停盘口](limit-down-orderbook.md)看重点股在哪两个采样点之间发生了变化。[题材快讯](theme-catalysts.md)保存报道内容及当时能够看到它的时间。[外盘报价](course-cross-market.md)分别保留盘中实收与盘后按相同分钟补取的结果，盘后取得的证据只供回看。

四指数盘中监控覆盖可用 `python3 scripts/tools/index_bar_archive.py --coverage --start-date 2026-11-23 --date 2026-11-27` 只读查看。每个 5 分钟槽位按当时最后一次回执分类，上午、下午分别统计，盘后补得的原始 K 线不参与实时完整率。
