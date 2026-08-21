# 交易系统 - Claude 协作入口

本文件仅保留总则与索引，具体规则请按任务加载对应主题文档。
权威入口与最新拆分结构以 [AGENTS.md](/Users/alyx/tradeSystem/AGENTS.md) 和 `.cursor/agent-context/` 为准；本文件作为 Claude / 兼容 Agent 的等价入口。

> **目录中性化声明**：`.agents/skills/` 与 `.agents/rules/` 是团队共享 agent context 真源，与具体 IDE 无关。`.cursor/skills`、`.cursor/rules/*.mdc` 是 Cursor IDE 的 symlink 壳；`.claude/skills`、`.claude/rules/*.md` 是 Claude Code 的 symlink 壳。**所有写入只动 `.agents/`**。本期 `.agents/` 仅承载 skills + rules，`agent-context` 暂留 `.cursor/agent-context/`，待 v2 一并迁出。

## 先读结论

1. 这是一个 A股/港股短线交易分析系统，AI 负责复盘、分析、整理与执行辅助，**不替代交易决策**。
2. Agent 写入统一走 CLI 标准入口，**禁止直接写 SQLite、YAML 或手工拼 JSON**。
3. 所有写入命令必须显式带 `--input-by`；Agent **不得绕过确认直接写 `confirmed` 的 `TradePlan`**。
4. 所有 AI 输出使用简体中文；涉及技术方案、执行计划、业务逻辑解析时，默认遵循 `.agents/rules/solution-format.md`。
5. 修改 `scripts/main.py`、`scripts/api/routes/*.py`、`.agents/skills/**/*.md` 后，必须同步更新 `.agents/skills/INDEX.md` 与 `.agents/rules/skills-sync.md`。

## 渐进式加载顺序


| 任务类型                             | 必读文件                                                                                                       | 按需补读                                                                                                     |
| -------------------------------- | ---------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| 任意任务                             | `CLAUDE.md` 或 `AGENTS.md`                                                                                  | 无                                                                                                        |
| 盘前/盘后/复盘/主线/情绪判断                 | [00-core-trading-framework.md](/Users/alyx/tradeSystem/.cursor/agent-context/00-core-trading-framework.md) | [20-architecture-and-data.md](/Users/alyx/tradeSystem/.cursor/agent-context/20-architecture-and-data.md) |
| CLI / API / DB / 计划流转 / Agent 写入 | [10-agent-collaboration.md](/Users/alyx/tradeSystem/.cursor/agent-context/10-agent-collaboration.md)       | [20-architecture-and-data.md](/Users/alyx/tradeSystem/.cursor/agent-context/20-architecture-and-data.md) |
| 架构、数据模型、事实层 / 草稿 / 计划状态流         | [20-architecture-and-data.md](/Users/alyx/tradeSystem/.cursor/agent-context/20-architecture-and-data.md)   | [10-agent-collaboration.md](/Users/alyx/tradeSystem/.cursor/agent-context/10-agent-collaboration.md)     |
| 命令执行、环境、推送、目录结构、文件修改规范           | [30-runtime-and-ops.md](/Users/alyx/tradeSystem/.cursor/agent-context/30-runtime-and-ops.md)               | [10-agent-collaboration.md](/Users/alyx/tradeSystem/.cursor/agent-context/10-agent-collaboration.md)     |
| 需要拆分对照或回滚老版本                     | [99-full-reference.md](/Users/alyx/tradeSystem/.cursor/agent-context/99-full-reference.md)                 | `00` 到 `30` 号主题文档                                                                                        |


## 红线

- 不做具体买卖建议
- 不预测具体价格目标
- 不在没有数据支撑时做主观判断
- 不将 `[判断]` 伪装成 `[事实]`
- 不替代用户的“看得懂”判断

## 标准写入语义

系统区分 **人工入口** 与 **Agent 标准入口**：

- **人工入口**：Web / API / CLI 都可用
- **Agent 标准入口**：统一通过 CLI 写入
- **统一语义层**：CLI / API / Web 必须共享同一 service、同一默认值、同一校验与状态流转

当前及后续标准命令组：

- `python3 main.py db ...`（含 `db thesis-{open,close,fill,list,suggest,review,reopen}` 交易思路中间层 v24，半自动联动 broker_executions 与复盘）
- `python3 main.py ingest ...`
- `python3 main.py plan ...`
- `python3 main.py knowledge ...`
- `python3 main.py executions ...`（券商成交流水事实层：`import` / `list` / `audit-export`；`import` 默认严格 thesis 模式 + auto-close 联动）
- `python3 main.py prefetch-calendar --input-by USER [--days 14] [--from YYYY-MM-DD] [--json]`（未来宏观事件日历：provider 取数 → `tracking/calendar_auto.yaml` 同目录原子替换 → `calendar_events` 按 `(date,event)` 幂等同步；人工来源不覆盖，未来 7 日覆盖不足 `partial` 非零退出；仓库提供每天 06:30、早于盘前任务的 launchd 模板，但安装与首次真实同步须经用户确认）
- 2026-07-29 运维补充（优先于下方旧耗时注记）：`volume-watch` 将题材健康状态持久化为 `complete/healthy_sparse/source_failed/fallback_preserved`，成功但 4/50 等小交集不得报来源缺口；`trend-leader daily` 先原子落 `data/reports/trend-leader/YYYY-MM-DD.md` 再推送，`--no-push` 仍落报告、`--dry-run` 不落；`pattern-scan` 用两个 worker 有界并发处理股票，每票仍保持行情成功后才取复权因子的调用顺序与次数，并按输入顺序消费结果。
- `python3 main.py volume-watch ...`（成交额 Top20 板块集中度：`daily` 采集+落库+渲染+钉钉推送 / `trend` 只读趋势；申万二级口径联动 `get_sector_rankings`，落 `daily_volume_concentration`；`daily` 报告额外含**成交额前50 区间涨幅排名**[独立取前50→`get_stock_daily_range` 算 5/10/20 日涨幅→**申万二级板块榜** + **同花顺概念题材榜**(多标签，复用 `get_ths_member` 反查 + 容器≤300 过滤，concepts 落 `gain_universe_json`)，组按组内涨幅最大个股降序/平手比次大，三档独立榜，全 [事实] 守红线]，经只读 API `/api/market/sector-gain-ranking/{date}`(`rankings`+`concept_rankings`) 在八步复盘「2.板块」双维度展示）
- `python3 main.py value-watch daily|report ...`（价值投资条件监控，认知出处 `teacher_notes#391` 鞠磊价值投资年课[红利价值/稀缺价值]：Tushare `pro.sw_daily` 直连+`get_stock_daily_range`+active `holdings`+`trade_calendar` → 三层口径[①红利买入触发=银行指数 801780.SI(10/15% 档)与长电 600900.SH(10% 档)自 120 交易日滚动高点回撤 episode，进入≥档位、退出须<档位-2pp 迟滞防贴线抖动 ②卖出阶梯=active 持仓∩四大行+长电按 entry_price 算价格涨幅(raw close 未含分红)，10/15/20% 首触与 20 档回落事件，身份键 canonical:holding_id(entry_date 可修正不进键) ③稀缺价值=片仔癀周线 MA5/10/20 粘合≤3%且 MA5 向上(仅约束 MA5)+周 MACD(12/26/9) 上零轴同周成立，连续 2 完成周不满足才失效] → 落 `value_watch_daily` + **事件首发才推钉钉**[`sent_events` 账本去重，enter 需当前仍成立、exit 迟到必补；strict 日历闸门=目标日为最新已收盘交易日才推、blocked 不推；历史 `--date` 落库不推]；陈旧守卫 stale_source+单标的失败隔离+非交易日守卫[dry-run 豁免]；daily 三档=裸[落库+推]/`--no-push`[落库+打印候选]/`--dry-run`[全内存不落不推不写账本]，`report` 只读快照；工作日 21:45 per-task launchd[接 market-timing 21:40 之后]，不进 `schedule`/APScheduler；数字[事实]/解读[判断]·出处#391，非操作指令、不构成投资建议、不写计划层不入关注池）
- `python3 main.py sector-correlation ...`（板块相关性：`daily` 采集+落库+渲染+钉钉 / `matrix` 完整矩阵只读 / `trend` 漂移趋势；Tushare 主源多日活跃选板块[行业成交额 / 概念换手率]+4 指数，多窗 5/20/60 原始相关+剔大盘超额相关+β，落 `sector_correlation_daily`）
- `python3 main.py market-timing daily|signals ...`（大盘择时观察：6 指数[上证/深成/创业板/科创50/中证2000(微盘股代理)/平均股价(通达信880003 经 pytdx 日线)] 斐波那契时间周期变盘点[双向 swing 拐点起算，命中 5/8/13/21/34/55，多指数同日共振增强] + 底分型生命周期[三K结构 none/forming/confirmed(放量中阳突破前高)/invalid，无状态从 bars 推导抗漏跑] + **周/月均线状态与事件**[出处老师《交易系统》课程第一课方向判断体系：收盘 vs 5周线/5月线位置 + 5周线/5月线方向，事件=站上5周线(带放量 qualifier)/跌破5周线(第一次警戒)/5周线拐头向下且收于线下(第二次警戒)/月线阴线打穿5/10/20月线/周线与月线反包K线；周月聚合含当前未完成期(同看盘软件，区别于 value-watch 只认完成周)，方向=与上一完成期比，事件=今昨视角翻转不重复报，无状态从 740 自然日 bars 推导抗漏跑，历史不足落 None 不硬算；**课程仓位档位语义(五成/七成/动态满仓)不进系统输出**] + 市场级客观上下文[两市成交额近20日地量分位/跌停家数/涨跌家数] → 落 `market_timing_signal`[PK(trade_date,index_code) 重跑 refreshed，含 `ma_state_json`/`ma_events_json`] + MD 只读观察清单 + 钉钉；全标 [判断] 守红线[不预判方向/不出价位/不给买卖建议]；daily 三档=裸[落库+推]/`--no-push`[落库+打印]/`--dry-run`[内存不落不推，历史校准]，`--pivot-index`+`--pivot-date` 手工 swing 覆盖[D3 hybrid，未知指数/非法日期/日期不在窗口 fail-fast]，`signals` 只读看池[`--date`/`--index`/`--json`]；工作日+周日 21:40 per-task launchd[接 trend-leader 21:30 之后]，不进 `schedule`/APScheduler）
- `python3 main.py margin-index-correlation daily|signals ...`（两融余额与指数联动性：新增 `get_margin_series` 取两融区间序列[Tushare `pro.margin` 主源沪深北三市合计+分项，复用完整性逻辑只留应到交易所齐全的完整日 / akshare 官网降级仅沪深、新到旧迭代封顶防宕机时上百串行请求]，两融余额转**日变化率(%)** 后与指数 `pct_chg` 同口径做四维：① 背离预警[头条，近5/20日复利累计指数涨两融降/指数跌两融升，**指数交易日脊柱锁窗**两融缺日标「日期缺口」防稀疏日伪造] ② 余额水位+趋势[绝对值/日环比/近20日分位/连增连降/偏离MA20] ③ 领先/滞后[lagged corr，`lag>0`=两融滞后指数] ④ 同步相关[5/20/60窗 Pearson 复用 sector aggregator]；对照 total两融×多宽基(上证/创业板/沪深300/科创50)+沪市两融×上证+深市两融×深成 → 落 `margin_index_correlation_daily` + 钉钉；全标 [判断] 守红线[不出价位/不给买卖建议/不写计划层]；daily 三档=裸[落库+推]/`--no-push`[落库+打印]/`--dry-run`[内存不落不推]，非交易日守卫仅 persist 时；`signals` 只读最近 N 日[`--date`/`--days`/`--json`]；**随 `main.py post` 盘后采集一并执行**(折进 cmd_post 末尾、失败隔离不影响主流程；不单独挂 launchd、不进 `schedule`/APScheduler；cmd_post 工作日 20:00 触发，两融交易所盘后发布滞后故多为 T-1，报告标注 stale)；经只读 API `/api/market/margin-index-correlation/{date}`(`web_payload.build_daily_payload`) 在八步复盘「1.大盘」`MarginIndexCorrelation` 组件渲染四维；CLI `daily --date` 可手动补采/校准）
- `python3 main.py research-digest daily ...`（每日研报速读：A股研报评级[巨潮 cninfo `get_research_report_list`] + 美股机构评级[yfinance `upgrades_downgrades`，仅方向变动 init/up/down/reinit] → 鞠磊框架「首次覆盖」加权 Top3 → MD 落盘 + 钉钉；`--dry-run` 仅打印、`--no-llm` 关美股叙事；红线只约束 LLM 叙事不约束取数；生产定时入口已迁移到 Codex 自动化「每日慧博研报速读（Computer Use）」每天 22:00 触发，先用 Computer Use 读取慧博终端当前 HotReport URL 并在慧博终端下载候选 PDF 到本地目录，再运行 JS workflow 读取本地 PDF；旧 `com.alyx.tradesystem.research-digest` launchd 已停用，避免绕过 Computer Use；`trend` 子命令=研报覆盖·申万一级行业趋势[数据底座 `raw_interface_payloads.research_report_list` 随 `cmd_post` post_extended 每日落库，近N有效日 vs 前N有效日占比Δpp、份额口径免疫月末脉冲、`status='empty'` 真空日不进分母，`--backfill` 幂等回补缺失/`empty` 日(完成态=非空 success,迟到回填自动吃进)；每行业另附 `streak_up` 连续上行有效日数[滚动 recent_n 窗份额逐窗严格递增的尾部步数，完整窗<2 为 null=数据不足不伪造 0]，供复盘作「覆盖持续上行」观察信号；全 [事实] 计数，复盘「2.板块」可引用，仍守「机构议程背景、禁作短线方向先验」定位]）
- `python3 main.py earnings-digest daily ...`（业绩预告/快报速报：全市场 `forecast_vip`/`express_vip` 按公告日回看窗口[默认3自然日]采集落 `raw_interface_payloads` + 水位线增量[只认 success] + 次日缺口验证[下一交易日开盘跳空≥2% 触发，**市场投票方向取收盘涨跌**(收盘才是市场对预告的真实一票，高开低走自动翻为利好不及预期；收平昨收=中性)，2×2] + 五段渲染[命中/缺口/申万行业Top5/分类计数/净利中值≥5000万Top榜]+口径三券商一致预期[全年预测×H1占比折算,标 [判断]] → MD 落盘 + 钉钉；空窗口日不推送；`--dry-run` 仅打印[采集落库照常]、`--lookback-days` 手动补采、`--no-consensus` 关一致预期；工作日+周日 22:00 launchd 单源调度，不进 `schedule`/APScheduler）
- `python3 main.py cognition-digest recent3d|weekly|monthly ...`（交易认知沉淀只读汇总：只读认知三表[`trading_cognitions`/`cognition_instances`]按窗口算热度+共识+新增 Top-N + gemini 体系/方向建议[复用 gemini runner + `REDLINE_KEYWORDS` 红线护栏] → 钉钉；`--dry-run` 仅打印、`--no-llm` 模板兜底；3 个 per-task launchd[recent3d 日 18:30 / weekly 周日 20:00 / monthly 每月 1 号 09:00]，不写库不改 schema 不进 `schedule`/APScheduler）
- `python3 main.py trend-leader daily|pool ...`（趋势主升漏斗扫描，对齐鞠磊：候选=当日涨停[`get_limit_up_list`]∪双创(20cm)涨幅≥15%加速[`get_market_daily_changes`，board-aware「20cm涨15%+」=GAP A] ∩ 主线板块[截至目标日最近最多3个有效 `daily_volume_concentration` 快照的 Top-K 申万二级（空记录/全部 UNCLASSIFIED 不计；2～3个快照至少命中2次，仅1个时命中1次）∪ `--sectors` 手工板块（直接保留、不受持续性门槛）∪ 同花顺概念净流入 Top-M(`get_concept_moneyflow_ths`+`get_ths_member`，`--top-concepts`默认8；只查资金流前排有限概念成员，默认预取 max(40, Top-M*5)，成员数≤300剔容器概念=GAP B)]；默认 `--main-line hybrid` 用 LLM 只过滤同花顺概念分支[不新增事实/不否决稳定申万主线/不做买卖建议，异常、超时、非法输出或红线命中时关闭概念分支并标 `fallback_l2`]；`hybrid --no-llm` 与 `--main-line l2+concept` 明确使用机械概念分支，`--main-line l2` 为纯申万口径 → 区间 OHLCV[`get_stock_daily_range`] → 首次加速(board-aware)+主线缓涨入池、缩量回踩/贴MA5/乖离信号、趋势破坏[跌破MA10/连破MA5]退池，落 `trend_leader_pool` 状态机[派生信号层，池内身份=裸码归一] → 渲染盘后只读观察清单[展示有效快照数、命中门槛、来源/降级状态与 LLM 状态；全标 [判断]、守红线不出价位/不给买卖建议/不写计划层；触发列分涨停/双创15%加速，概念分支票标「二级·分支:概念名」] + 钉钉；候选展示 **5/144/233 日均线位置**[`utils.ma_position`，出处课程第一课「同题材优先选站上三线的票」；纯 [事实] 算术标注，样本不足标 `·` 不硬算，**不计分/不进 PK 输入契约/不作硬过滤门槛**，**前复权口径**[`utils.qfq`；未复权跨除权会把方向标反，实测 1.9% 分红即可翻转 MA233 结论；因子取不到标 `—` 不硬算]，扩窗 400 自然日、检测器仍吃 90 自然日切片故行为不变[长窗失败退回短窗]，每日重算随 `last_signal_json` 落池[存结构不存渲染串]]；本次口径只影响新扫描，不回溯清理历史池；`daily` 三档=裸[落池+推]/`--no-push`[落池+仅打印]/`--dry-run`[内存副本跑不落池不推，历史校准]，`pool` 只读看池[`--status`/`--json`]；同日重跑/推送失败重试 refreshed 仍合并展示不丢；`--top-k`/`--top-concepts` 须正整数；工作日 21:30 per-task launchd[接 volume-watch 21:00 之后]，不进 `schedule`/APScheduler）
- `python3 main.py string-yang daily ...`（主线板块串阳首阴股票池：主线判断=成交额集中度[`daily_volume_concentration` Top-K 申万二级] + 同花顺概念分支[`get_concept_moneyflow_ths`+`get_ths_member`，成员数≤300剔容器] + 近 N 日老师观点[`teacher_notes`] → LLM 只裁决主线申万二级/概念分支，不选股、不生成买卖建议，失败或无有效裁决降级成交额 Top-K；候选=申万二级∈主线 或 概念∩主线概念 → 区间 OHLCV[`get_stock_daily_range`] → 排除 ST/退市风险 → 只筛“昨日以前连续≥5根阳线、串阳段无涨停且最大单日涨幅≤7%、最近20个交易日无涨停、首阴收盘价/MA60≤1.08、今日出现第一根放量阴线[今日成交额>前5个交易日最大成交额]”的确认票，不输出尚未出阴线的预备池；概念分支票标「申万二级·分支:概念名」；按今日成交额/前5日最大成交额排序，MD 落 `data/reports/string-yang/YYYY-MM-DD.md` + 钉钉；`--no-llm` 强制降级成交额 Top-K，`--top-concepts`/`--teacher-lookback-days` 控制证据窗口；全标 [判断] 守红线[不出价位/不给买卖建议/不写计划层/不入关注池]；三档=裸[落报告+推]/`--no-push`[落报告不推]/`--dry-run`[仅打印不落不推]；工作日 21:50 per-task launchd[接 market-timing 21:40 之后]，不进 `schedule`/APScheduler）
- `python3 main.py daily-leaders propose|show|confirm ...`（每日最票候选确认流：`propose` 汇总复盘预填、趋势池、历史最票、老师观点与认知证据，生成 `data/reports/daily-leaders/` Markdown/JSON 确认稿；`--push` 推送钉钉 Markdown 草稿；`show` 只读查看；`confirm --date ... --input-by ...` 经用户确认后写入复盘第 5 步并同步 `leader_tracking`。v1 仅支持 DingTalk Markdown + Codex/CLI 确认，钉钉按钮 callback/直接写回 deferred 到 v2；全程守红线，不给买卖建议、不出价位；工作日 22:30 per-task launchd[接 post-market 派生任务之后]，不进 `schedule`/APScheduler）
  - `daily-leaders propose` 优先按当前申万二级归板块，未映射票标「未分类」且概念只保留为来源证据；属性固定为趋势中军/连板核心/前排活跃/弹性前排，10/20/30cm 独立为板型。LLM 仅复核预收敛后的最多 30 只，必须完整覆盖且不得夹带池外股票；程序强制同板块同属性仅 1 只、股票全局唯一、最终最多 15 只。`--max-candidates` 仅接受 1..15；LLM 失败或 `--no-llm` 仍按相同硬约束确定性兜底。`confirm` 复用提案层 Unicode 空白压缩板块键并在事务前重复校验三项硬约束，旧稿不合规直接拒绝；展示内股票代码支持有/无空白及合法交易所后缀，非法后缀或与显式 `stock_code` 冲突时 fail-closed；合法股票代码以裸 6 位写入第 5 步并优先作为 `leader_tracking` 身份，旧 payload 无代码才回退名称；同股同规范板块属性的旧名称型 tracking 行仅在全局及同批名称映射无歧义时于事务内迁移/合并。
- `python3 main.py pattern-scan daily ...`（形态篇选股形态观察清单，出处 `teacher_notes#444` 鞠磊《形态篇（第一节技术课程）》、认知 `cog_3b32e660`：板块优先——主线复用 `string_yang.mainline.judge_mainline(use_llm=False)`[成交额集中度 Top-K 申万二级 ∪ 同花顺概念分支，**本命令不接 LLM**，四条件全机械判定，主线口径与 string-yang 单一真源] → 板块内全量剔 ST → 逐票拉 300 自然日 OHLCV[`get_stock_daily_range`]+复权因子[`get_stock_adj_factor_range`] → **前复权**[`utils.qfq.apply_qfq(keys=OHLC_PRICE_KEYS)`，**含 open**：判 K 线阴阳须 close/open 同坐标系，只复权 close 会在除权日把阳线判成阴线；因子取不到整票剔除计 `qfq_failed`，绝不退回未复权硬算] → 四条件共振[`utils.pattern`，缺一不可]：①均线多头排列 MA5/10/20/30/55 严格递减[恰等于不算排列，与 `ma_position`「站上」同口径] ②MACD 零轴上方金叉**或零上运行**[判定字段 `zero_axis_bullish`=DIF 与 DEA **同时**>0 且 DIF>=DEA；只看 DIF 会把「DIF 翻正而 DEA 仍零下」的纠结段误判为零上，与课程「零下金叉难成主升浪」相悖] ③量能节奏[近 20 交易日放量阳线(当日 `vol` 同时站上 5/13 日**均量线**)占阳线≥50%(课程说「大部分」不是「每根」)，且完整「放量阳→缩量阴」≥1 组，放量阴线打断待成组的放量阳；**用 `vol` 非 `amount`、窗口 5/13 非 ma-breakout 的成交额 5/10，两套故意不复用**] ④尚未加速[近 20 交易日无涨停/双创15%+，复用 `trend_leader.detectors.accel_threshold` board-aware 单一真源；前三条成立但已加速单列 `already_accelerated`] → 按今日成交额降序渲染 MD 落 `data/reports/pattern-scan/YYYY-MM-DD.md` + 钉钉；样本不足与「形态不满足」分开计数不折叠[次新股不得被误报为形态破坏]；全宇宙取数失败 → `source_failed` 落失败报告+推告警+非零退出，报告显式写「不代表已完成筛选后的空池」；全标 [判断] 守红线[不出价位/不给买卖建议/不写计划层/不入关注池]，报告显式声明「形态成立不等于应当买入」，课程举例个股只作认知实例历史样本不进推送；三档=裸[落报告+推]/`--no-push`[落报告不推]/`--dry-run`[仅打印]；无池无状态不写库；工作日 22:45 per-task launchd[接 daily-leaders 22:30 之后，逐票双调用实测 883 只 23 分 12 秒故单独占窗口，避开 string-yang 21:50 / earnings-digest 22:00 并发]，不进 `schedule`/APScheduler）
- `python3 main.py board-break daily ...`（断板反包观察清单：昨日连板≥2 断板[≤6%未跌停，10cm 主板剔 ST] → 八维度加权打分[主线/增减持(减持按250日分位翻极性)/定增/公告/业绩/近10日涨幅/MACD，全 [判断] 附依据明细] + LLM 两两 PK 循环赛[熔断/红线过滤，`--no-llm` 关] → 双排序 MD 落盘 `data/reports/board-break/` + 钉钉；三档 裸/`--no-push`/`--dry-run` + `--date`；候选展示 **5/144/233 日均线位置**[`utils.ma_position`，出处课程第一课「同题材优先选站上三线的票」；纯 [事实] 算术标注，样本不足标 `·` 不硬算，**不计分/不进 PK 输入契约/不作硬过滤门槛**，前复权口径随打分依据明细末行展示，末根非 T 日与乖离/MACD 一并降级]；`source_failed` 落失败报告+推告警+非零退出；无池无状态，隔日交易归用户；工作日 21:20 per-task launchd[volume-watch 21:00/sector-correlation 21:15 之后、trend-leader 21:30 之前]，不进 `schedule`/APScheduler）
- `python3 main.py emotion-leader daily ...`（情绪核心生命周期只读监控：从 `daily_market.raw_data` 重建非 ST 连板，二板候选、三板或当日高度前二晋级，合并复盘第5步人工核心；以启动日前收盘为基准，用全 OHLC 前复权统计最大/区间涨幅、距峰值、最高连板与当日状态；固定比较目标日与此前20个开放日非ST最高连板，严格创新高时输出打开高度核心启动日，高度对比属 `[事实]`、启动日节点候选属 `[判断]`，窗口或启动日缺口=`missing_data`；波段属 `[判断]`，归档只改展示；默认读取最近同口径日报增量刷新，`--full-refresh` 强制全量；目标日涨跌停事实不完整=`source_failed`，历史/行业/单票行情复权缺口=`partial`，不得报成空池；三档=裸[原子落MD+JSON并推]/`--no-push`[只落]/`--dry-run`或`--json`[不落不推]；不写 SQLite/持仓/关注池/计划层）
- `python3 main.py ma-breakout daily ...`（4日均线二波尾盘观察池：只保留工作日14:50单次实时快照，CLI仅允许上海当日14:45～15:00且交易日执行；历史龙头宇宙仍只取近60自然日人工确认的 `leader_tracking`，`trend_leader_pool` 不作默认来源；历史9个完成交易日用 `get_market_daily_quotes`，目标日用 `get_realtime_quotes` 实时价和累计成交额合成临时bar[新浪元→Tushare千元]，复用MA4重新拐头+成交额MA5/MA10突破+快照时未涨停条件；实时行情当日且最多陈旧10分钟，全部不可判=`source_failed`、部分缺失/陈旧=`partial`；按累计成交额降序输出尾盘只读观察清单并标尚未收盘确认；非当日/非时间窗/非交易日跳过，不回退上一交易日、不运行21:35收盘版；不写SQLite/计划层/关注池，不进`schedule`/APScheduler）
- `python3 main.py intraday-monitor check [--dry-run] [--json]` / `intraday-monitor e2e-test [--rule-id RULE_ID] --input-by USER --confirm-real-push [--json]`（可扩展盘中实时监控与独立每5分钟 launchd；长期规则为新浪实时 `000001.SH` 上证指数从 `<3955` 站上 `>=3955`；临时规则在 2026-08-21～24 自然日期窗口内监控 `000688.SH` 科创50严格 `>1700` 及 `002821.SZ` 凯莱英严格 `>172.26`，结合只读交易日历实际覆盖 8 月 21/24 两个开放交易日，首次已命中会推、等值不触发、持续去重、回落后再次突破可重推。三只断板规则与旧科创50跌破/收复规则保持下线，新增规则使用独立 rule id；行情非当日或陈旧超过10分钟 fail-closed；pending 先原子落状态、发送失败同交易日重试、跨日或退役规则过期；`e2e-test` 缺少真实推送确认或规则已过期均在初始化行情源前拒绝，授权后才推送明确测试消息且不读写正式状态；不写 SQLite/TradeDraft/TradePlan/持仓/关注池，不构成买卖建议；Mac 休眠期间不触发）
- `python3 main.py morning-brief daily [--date YYYY-MM-DD] [--dry-run|--no-push]`（盘前早报三段式：①隔夜行情快照[标普/纳指/道指+纳斯达克中国金龙(PGJ代理)+黄金/原油/铜，registry 逐标的失败隔离标缺] ②海外/国内要闻[复用 `macro_flash.collector` 金十采集器，窗口=上一交易日 20:00→今 08:00 接盘后档不重叠，`config.yaml` `morning_brief.keywords` 双主题词表归组+important「其他要闻」兜底，每主题15/其他5条] ③上市公司公告[新 capability `get_market_announcements_range`=akshare 侧直连巨潮 `hisAnnouncement/query` 自带分页/时间预算+整页早于窗口起点即早停(不复用 akshare 无预算封装，年报季单日可上万条)，窗口=上一交易日 15:00→今 08:00，噪音标题排除(法律意见书/保荐书等从属文件)→七组关键词分类(停复牌/风险与监管/业绩/再融资与重组/增减持与回购/重大合同/投资与经营)→同股同组只留最新→每组10条] → 落 `data/reports/morning-brief/YYYY-MM-DD.md` 原子写 + 钉钉[18KB 整块截断复用 macro-flash formatter]；金十失败=`source_failed` 落失败报告+推告警+非零退出，公告/单标的行情失败只降级对应段=`partial` 缺口在报告头显式列出；非交易日守卫[dry-run 豁免]；三档=裸[落报告+推]/`--no-push`[落报告不推]/`--dry-run`[仅打印]；全为转述事实层无 LLM 生成段、不扫红线关键词[红线约束生成不约束事实]，不写 SQLite 业务表/计划层/关注池、不构成买卖建议；工作日 08:00 per-task launchd[接 calendar-sync 06:30 / today-pre 07:00 之后]，不进 `schedule`/APScheduler）
- `python3 main.py tail-scan daily ...`（盘中尾盘强势股扫描：14:40 单次快照全市场实时行情[`get_realtime_quotes`，单点脆弱源失败重试一次仍失败 → `source_failed`] → 三条件筛选[涨幅>7% ∩ 非ST ∩ 成交额>20亿，全 [事实]，阈值可用 `--min-pct`/`--min-amount` 调] → 四维事实卡[逻辑:T-1主线申万二级Top-K+同花顺概念资金流T-1Top-M+老师观点命中 / 三位一体:候选池涨幅名次+指数背景 / 节奏:近5日涨幅/MA上方/连涨天数/半日放量追平昨日全日节奏代理`first_surge` / 节点:距前高/是否破前高，单维度取数失败只降级不中断整批] + 产业逻辑增强[主营:Tushare `stock_company` 主源/AkShare `stock_zyjs_ths` 补缺，为扫描时当前公开静态资料、非历史 as-of，摘要优先级=`main_business`>`introduction`>`business_scope`；产业链位置仅基于申万二级+主营摘要+产品受控归纳；近30自然日催化只读 `teacher_notes` 精确代码/慧博精确名称/`industry_info` 基于申万二级/主营摘要/产品/已验证概念标签受控匹配，证据按 [事实]/[老师观点]/[研报观点]/[来源陈述] 分层，失败仅降级对应维度] → 粗权重分仅用于PK强池截断[`PK_POOL_MAX=12`]与排序破平[不进PK prompt] + LLM 两两 PK 循环赛[180s预算熔断/无效场率>50%熔断/红线过滤，`--no-llm` 关，候选<2只自走`status=skipped`] → 渲染 MD[排序为 [判断]，每票显示 `[事实·主营]`/`[判断·产业链位置]`/近期催化分层标签，含数据时效声明:实时快照 vs T-1逻辑/板块；本地 MD 全量，钉钉超长时≤18000 UTF-8 bytes 且最多展示前12个完整候选块并附完整报告路径] 落盘 `data/reports/tail-scan/` + 钉钉；候选展示 **5/144/233 日均线位置**[`utils.ma_position`，出处课程第一课「同题材优先选站上三线的票」；纯 [事实] 算术标注，样本不足标 `·` 不硬算，**不计分/不进 PK 输入契约/不作硬过滤门槛**，**前复权口径**[`utils.qfq`；未复权跨除权会把方向标反，实测 1.9% 分红即可翻转 MA233 结论；因子取不到标 `—` 不硬算]，历史收盘+实时价比较[实时价缺失也标 `—` 不拿 T-1 冒充盘中]，扩窗 400 自然日、原有指标仍吃 40 自然日切片故行为不变[长窗失败退回短窗]]；三档 裸/`--no-push`/`--dry-run` + `--date`；`source_failed` 落失败报告+推告警+非零退出；无池无状态，不写交易计划/关注池；工作日 14:40 per-task launchd[`com.alyx.tradesystem.tail-scan`]，不进 `schedule`/APScheduler；休眠期间不触发[需盘中 mac 唤醒]）

  - `tail-scan` 概念层固定分两层同时展示：`get_stock_concept_memberships` 按候选反查同花顺 `type=N` 的扫描时当前快照（非历史 as-of），复用共享成员数 `<=300` 过滤，报告归属概念最多展示 5 个并保留总数；T-1 热概念严格取上一交易日资金流，先按 `company_num<=300` 剔除容器再补足 Top8，每票最多展示 2 个命中。完整归属仅供报告与产业证据，不进入粗分或 PK；兼容 `concept_names` / `in_hot_concept` 仍表示热命中；`source_failed` / `coverage_failed` / `member_failed` / `missing` 必须分别表达，不得把失败写成未命中。

- `python3 main.py monthly-pattern daily|pool|monitor|monitor-daily|backfill|facts-backfill ...`（完成月月线模式观察池：全市场月线 + `adj_factor` 前复权，只在月末完成后扫描“基本面月线趋势 / 题材月线进攻 / 月线再加速”三策略；每个完成月以股票基础资料 `L/D/P` 全状态按 `list_date/delist_date` 做历史 as-of 外部宇宙覆盖认证，只有带 `monthly_pattern_bar_manifests` certified 收据的月线事实可扫描；供应商新旧证券影子代码重复仅在同交易所、完整且有效的月线九字段与复权因子一致、唯一对应宇宙内 canonical code 且窗口内无角色反转/链式映射时抑制并留版本化 manifest 收据，缓存复用校验九字段摘要并将持久化六字段+复权逐项对照 canonical bar，schema/摘要/计数/事实损坏即 cache miss，否则 fail-closed，不拼接分段行情或迁移旧 episode；`daily` / `backfill` 必须显式带 `--input-by`；财务按公告日 as-of，同公告日修订按内容哈希追加保存，无法证明独立公开日的版本只能从首次观测日起可见；年报可标 `fundamental_verified`、中报只作 `pre_screen`，财务源缺失时保守停在技术候选层；基本面 verified 只有后续严格下一完成月仍满足条件才转 active；active 转弱进入 risk，risk 仅在完成月技术严格重新转强且对应财务/主线资格恢复后回 active；最近两个严格相邻完成月均收于月 MA5 下方后进入 episode 终态 exited，后续重新命中另开新 episode；历史行业映射无 as-of 能力时题材策略 fail-closed，不以当前行业穿越回放；主线仅取申万二级成交额稳定前排并明确标 `[判断]`；原始五表[`monthly_pattern_bars` / `monthly_pattern_bar_manifests` / `monthly_pattern_financial_snapshots` / `monthly_pattern_runs` / `monthly_pattern_pool`]与 `data/reports/monthly-pattern/` 保持原契约，另有两张独立派生事实/审计表；默认落库+报告+钉钉，`--no-push` 落库报告不推，`--dry-run` 内存副本不落不推；每月 2 日 23:10 per-task launchd 单次运行，休眠错过可接受，不进 `schedule`/APScheduler；不写 `TradeDraft` / `TradePlan` / 关注池，不给买卖建议）
  - 日报重点漏斗：全量技术候选只保留后台计数；只有 `fundamental_verified` / `active` 且财务证据 verified 的股票进入固定 100 分综合评分（技术30、主线25、基本面20、行业估值10、分行业合同负债/研发投入10、数据完整度5）。估值只读目标日前最近成功 `daily_basic`，最多 7 天且市场覆盖≥90%；金融行业优先 PB，其他行业优先正 PE(TTM)，缺失回退 PS(TTM)/PB，只在同申万二级+同指标、样本≥5 的组内算分位。日报按申万二级聚合展示板块内降序 Top10，并从无 risk、行业明确、数据完整度≥4 的股票中给全局 Top3；评分只改变报告重点层，不写回池状态、不生成计划或买卖建议。
  - 历史顺序硬门：扫描日早于现有 `monthly_pattern_runs` 或 pool episode 的最大状态日期时必须 fail-closed；历史更正只能从可信检查点重建完整后缀，禁止在 live pool 上把未来状态回灌旧月份。
  - 手工 `monitor` 为无状态只读影子任务：只复用已有 certified 完成月，最新月 as-of 宇宙须与 manifest 分母精确对账后排除合法退出代码，其余每票必须覆盖全局最新完成月且整窗无 bar 也计缺失；筛“前序连续阳月≥5 + 回踩月 MA5/10/20 多头且 low≤MA5≤close”，再用 T 日统一前复权日线以前四个连续自然月月末收盘 + 当前 T 日收盘计算动态 MA5，`close>=dynamic_ma5` 才进入当前主清单，失守的历史种子单列等待且不得晋级日/周共振，动态 MA5 无法判定的单列不可判并将运行标为 `partial`；目标月 as-of 值不用于新增完成月种子。等待桶仅为同一目标月的无状态日频快照，月度翻页后按新完成月重建，不持久化没有失效期限定义的旧等待项。随后计算日/周 MACD；默认日期遵守上海 15:30 收盘安全线，目标日 ST 身份独立于简称/可选行业源且空/非法结果 fail-closed；5/13 均量只作现有系统辅助事实，板块只作 `[判断]` 背景。默认只打印，`--save-report` 才落本地 Markdown；不写月线五表/池，不推送、不挂定时、不要求 `--input-by`。RSR/RSI、9/9/9、60 分钟背离、牛熊硬门和“低位”未确认前不得自动化。
  - `monitor-daily` 是独立自动编排：per-task launchd 每 15 分钟轻量 tick，runner 只在上海工作日 19:10（含）至 19:25（不含）执行一次重任务，避免 Mac 切换本机时区后漂移；只在最近已收盘开放日等于上海自然日今天时运行。生产禁截断；显式历史日期默认仅预览且永不推送，只有同时带 `--no-push` 才保存并推进基线。日期文件是 latest 摘要，每次调用另追加不可覆盖的 planned/delivery/final 审计记录；文件型 pending/sent 账本只落 `data/runs/monthly-pattern-monitor/` 和 `data/reports/monthly-pattern-monitor/`。只有同完成月的 `complete→complete` 比较个股，`partial/blocked` 不推进股票基线，健康指纹含所有缺口身份/原因，首次完整快照与完成月翻页只建基线；交易日历/初始化故障也落 blocked 健康收据，日历未确认时只落本地并挂 pending、不推送。动态 5 月线、日/周 MACD 与观察阶段变化才推钉钉，发送成功才记 sent，失败下次 at-least-once 重试；不写 SQLite 七表、月线池、关注池、TradeDraft 或 TradePlan，不进 schedule/APScheduler。

  - 完成月种子采用 AND 三态短路：结构损坏、非法 close、月份缺口仍优先 `blocked`；月内复权形态未知时，若任一其他可信硬门已明确失败则归 `not_matched` 并计 `shape_short_circuited_not_matched`，只有没有已知失败且最终结论仍依赖未知形态时才计 `blocked_price_shape`，且不得因此扩大 `matched` 种子集合。

  - `facts-backfill` 仅手工审计回补：`--dry-run` 从严格只读连接复制到内存副本，同票合并真正 blocked 的缺口与已知形态异常并生成 `receipt_hash`，partial 也给信息性预览；收据绑定 raw/manifest/既有派生事实输入水位，真实写入必须显式带相同 `--expect-receipt-hash`，且 unresolved、截断和事实漂移均为 0，并在 `BEGIN IMMEDIATE` 写锁内再次复核。A 股选股宇宙显式排除并审计 `200/201/900` 沪深 B 股，五个主分类与种子数须守恒；日线手/千元换算为月线股/元后做交叉验签，raw 月末复权因子也须一致；全市场月线源通过 as-of 覆盖门后才可认证 `certified_no_trade`，证据哈希绑定全市场月线与历史宇宙完整排序行摘要。确认后只在同一事务运行派生两表专用 schema ensure，表、索引、防改触发器按 SQL 指纹验签，DDL 与事实共同提交或回滚，禁止调用全库 migrate；只追加 `monthly_pattern_derived_month_facts` 与 `monthly_pattern_derived_fact_runs`，不覆盖 raw 五表，不伪造月 K，不挂 launchd、不推送。

## 规则与模板入口

### AI 协作规则（真源 `.agents/rules/`）


| 规则文件                      | 作用                                                                    |
| ------------------------- | --------------------------------------------------------------------- |
| `language.md`             | 所有 AI 输出使用简体中文，代码标识符保持英文                                              |
| `karpathy-behavior.md`    | 行为基线：先校验假设、简洁优先、精准修改、目标驱动验证，减少 Agent 常见失误                            |
| `dev-workflow.md`         | 开发三阶段流程：设计验证方案 → 实现（含单测）→ 执行验证并报告                                     |
| `implementation-plan.md`  | 实施计划必须含范围、分层测试、验收命令与完成标准；不强制固定多 Agent 分组                         |
| `solution-format.md`      | 技术方案 / 执行计划 / 业务逻辑解析默认使用结构化章节、表格与纯 Mermaid 图表输出                       |
| `test-design.md`          | 分层测试设计：金字塔原则、隔离原则、自底向上执行                                              |
| `post-dev-review.md` | 实质性代码改动后的审查门：先按触碰路径**定档**（双门 / 单门 / 单门+前端），高风险面才跑 `/code-review`（门1）∥ codex adversarial-review（门2）**并行**，findings 合并后一次性修；3 条结束条件 + 2 轮上限 |
| `skills-sync.md`          | CLI / API / Skills 变更后同步 `INDEX.md`、跑 `test_cli_smoke`、检查受影响 SKILL.md；新增顶层 subparser 必加 `ARCHITECTURE_COMMANDS` 参数化 |
| `launchd-deploy.md`       | macOS launchd 定时任务部署规范：包装脚本必须 set PATH + source env；安装后必须 launchctl start 真触发验证；LLM 任务超时建议 180s+ |
| `tdd-commit-strategy.md`  | TDD 实施完成后按功能层次切 commit（不每个 R/G 一个、不全 squash）；commit message 标 What/Why/TDD 轮数；`git add` 用具体路径不用 `-A` |

> **Rules 激活差**：Cursor 通过 `alwaysApply` / `globs` 自动注入；Claude Code 不解析这两个字段，全量加载 `.claude/rules/*.md`。`skills-sync.md` 在 Cursor 仅命中 `globs` 时触发，在 Claude Code 视为常驻提示。

### Skills 入口（Codex / Claude Code / Cursor 共用，真源在 `.agents/skills/`）

| Skill | 路径 | 何时加载 |
| --- | --- | --- |
| cognition-evolution | .agents/skills/cognition-evolution/SKILL.md | 提炼认知 / 验证 / 复盘 |
| daily-review | .agents/skills/daily-review/SKILL.md | 八步盘后复盘 |
| ingest-inspector | .agents/skills/ingest-inspector/SKILL.md | 采集诊断与重试 |
| knowledge-to-plan | .agents/skills/knowledge-to-plan/SKILL.md | 资料转草稿（新闻 / 课程 / 笔记） |
| market-tasks | .agents/skills/market-tasks/SKILL.md | 盘前 / 盘后采集任务 |
| plan-workbench | .agents/skills/plan-workbench/SKILL.md | 草稿 / 确认 / 诊断 / 回写 |
| portfolio-manager | .agents/skills/portfolio-manager/SKILL.md | 持仓 / 关注池 / 黑名单 |
| record-notes | .agents/skills/record-notes/SKILL.md | 录入老师观点 / 行业 / 宏观 |
| repo-maintenance-workflows | .agents/skills/repo-maintenance-workflows/SKILL.md | CLI / API 对齐与索引同步 |
| sector-projection-analysis | .agents/skills/sector-projection-analysis/SKILL.md | 板块推演 |

CLI / API 依赖对照见 `.agents/skills/INDEX.md`（唯一真源）。Codex CLI / Claude Code 在执行任务前按关键词命中读对应 SKILL.md，再调用 CLI。

### 模板入口

- [technical-design.md](/Users/alyx/tradeSystem/docs/templates/technical-design.md)
- [execution-plan.md](/Users/alyx/tradeSystem/docs/templates/execution-plan.md)
- [api-contract.md](/Users/alyx/tradeSystem/docs/templates/api-contract.md)

## 主题索引

1. [AGENTS.md](/Users/alyx/tradeSystem/AGENTS.md)
2. [00-core-trading-framework.md](/Users/alyx/tradeSystem/.cursor/agent-context/00-core-trading-framework.md)
3. [10-agent-collaboration.md](/Users/alyx/tradeSystem/.cursor/agent-context/10-agent-collaboration.md)
4. [20-architecture-and-data.md](/Users/alyx/tradeSystem/.cursor/agent-context/20-architecture-and-data.md)
5. [30-runtime-and-ops.md](/Users/alyx/tradeSystem/.cursor/agent-context/30-runtime-and-ops.md)
6. [99-full-reference.md](/Users/alyx/tradeSystem/.cursor/agent-context/99-full-reference.md)
