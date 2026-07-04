# Skills 依赖索引

本文件记录每个 skill 所依赖的 CLI 命令和 API 端点。
**修改 `scripts/db/cli.py` 或 `scripts/api/routes/` 后，必须检查此索引。**

## 统一入口约定

从 2026-04 起，仓库优先使用根目录统一入口：

- 检查优先用 `make check` / `make check-web` / `make check-scripts`
- 开发启动优先用 `make dev` / `make dev-api` / `make dev-web`
- 日常任务优先用 `make today-open` / `make today-close` / `make today-pre` / `make today-post`
- 若 `Makefile` 已提供别名，SKILL.md 中应优先展示 `make` 入口，再保留底层 `python3 main.py ...` 作为兼容说明

本索引中的 CLI 依赖表仍以真实底层子命令为准，因为 `test_cli_smoke.py` 校验的是实际 argparse 签名，而不是 `make` 别名。

## CLI 命令依赖表

| Skill | CLI 子命令 | 说明 |
|-------|-----------|------|
| `cognition-evolution` | `knowledge cognition-* / instance-* / review-* (含 review-list)` | 认知提炼 / 实例验证 / 周期复盘（手动闭环）；`scripts/services/cognition_service.py`（已落地 Phase 1b，部分降级项见 SKILL.md） |
| `record-notes` | `db add-note` | 录入老师观点（文字/图片/多附件）；可选 `--sync-watchlist-from-stocks`（用户确认入池后） |
| `record-notes` / `portfolio-manager` | `db stock-resolve` | 通过已配置 Provider 统一做证券简称/代码解析，供 Agent 补码与补名使用 |
| `record-notes` / `portfolio-manager` | `db watchlist-sync-from-note` | 按笔记 `mentioned_stocks` 写入关注池（两步确认后的第二步） |
| `record-notes` | `db add-industry` | 录入行业板块信息（写入须显式 `--input-by`） |
| `record-notes` | `db add-macro` | 录入宏观经济信息（写入须显式 `--input-by`） |
| `knowledge-to-plan` | `knowledge add-note` | 写入 `knowledge_assets`（新闻/课程/手动；不含老师观点） |
| `knowledge-to-plan` | `knowledge list` | 列出资料资产（不含 `teacher_note` 类型） |
| `knowledge-to-plan` | `knowledge draft-from-asset` | 从资料生成 `knowledge_asset` observation 和 `TradeDraft` |
| `knowledge-to-plan` | `knowledge draft-from-teacher-note` | 从 `teacher_notes` 生成 observation 和 `TradeDraft` |
| `knowledge-to-plan` | `db add-note` | 老师观点唯一事实源（与 record-notes 共用） |
| `portfolio-manager` | `db holdings-add [--thesis-id]` | 新增持仓，可关联 `trade_thesis` |
| `portfolio-manager` | `db holdings-remove` | 移除持仓（置 closed） |
| `portfolio-manager` | `db holdings-list` | 列出当前持仓 |
| `portfolio-manager` | `db holdings-refresh` | 回填持仓现价与技术快照（需数据源） |
| `portfolio-manager` | `db holdings-import-yaml` | 将 `tracking/holdings.yaml` 导入 SQLite |
| `portfolio-manager` | `db watchlist-add --code --name --tier --input-by` | 添加到关注池 |
| `portfolio-manager` | `db watchlist-remove --code --input-by` | 从关注池移除 |
| `portfolio-manager` | `db watchlist-update --code --input-by [--tier --status --note]` | 更新关注池标的 |
| `portfolio-manager` | `db watchlist-list` | 列出关注池 |
| `portfolio-manager` | `db watchlist-sync-from-note --note-id --input-by` | 从老师笔记 `mentioned_stocks` 同步关注池 |
| `portfolio-manager` | `db add-trade` | 录入交易记录（单条，复盘维度） |
| `portfolio-manager` | `python main.py executions import --file --input-by [--account] [--dry-run] [--allow-orphan-buy] [--no-auto-close]` | 导入券商成交流水（事实层，幂等去重，含冲突检测与归档；默认严格模式 + auto-close 联动 trade_thesis 中间层；导入后自动标记语义重复流水为 `is_void=1`，不删除原始行） |
| `portfolio-manager` | `python main.py executions list [--from --to --account --limit --json --include-void]` | 列出 `broker_executions` 事实层数据；默认排除 `is_void=1` 作废重复流水，需审计作废行时显式 `--include-void` |
| `portfolio-manager` | `python main.py executions audit-export --from --to [--account --out --include-void]` | 按时间范围导出 markdown 审计报告（总览/各股/余额轨迹/批次）；默认排除作废重复流水 |
| `portfolio-manager` | `python main.py executions repair-reconcile --from --to [--account] [--dry-run\|--apply] [--json]` | 修复历史流水与 thesis/holdings 状态漂移：标记语义重复流水 `is_void=1`、回填既有流水 `thesis_id`，并以券商最新 `balance_after` 修正当前持仓和 open thesis 状态；默认 dry-run，不删除原始流水 |
| `portfolio-manager` | `db thesis-open` | 新建 thesis（严格模式 11 必填:6 主观字段 + 5 元数据 ≡ stock_code/name/account/opened-at/entry-reason/trade-mode/failure-condition/planned-position-pct/sector/market-region/input-by；trade-mode 含 sentiment_relay=情绪接力） |
| `portfolio-manager` | `db thesis-close --id --closed-at --input-by` | 手动关闭一个 open thesis |
| `portfolio-manager` | `db thesis-fill --id [字段...]` | 补/改字段；closed 时主字段冻结,仅允许 notes/plan_id（要改主字段先 thesis-reopen）|
| `portfolio-manager` | `db thesis-list [--status --account --code --from --to --filter --without-review --reopened --json]` | 列表查询；--filter=placeholder/historical-orphan、--without-review、--reopened（>3 标黄）|
| `portfolio-manager` | `db thesis-suggest [--account]` | 三类待补输出:待 open / 待 close / 待 review |
| `portfolio-manager` | `db thesis-review --id --executed-as-planned --input-by [--exit-trigger --lessons --discipline-score]` | upsert thesis_review（增量更新,未传字段保留原值）|
| `portfolio-manager` | `db thesis-reopen --id --reason --reopened-at --input-by` | 重开 closed thesis（reopen_count++ + notes 追加 [reopen DATE] reason）|
| `portfolio-manager` | `db blacklist-add` | 加入黑名单 |
| `daily-review` | `db query-notes` | 搜索老师笔记（用于复盘预填充） |
| `sector-projection-analysis` | `db query-notes` | 搜索老师笔记，补充板块逻辑与老师视角 |
| `daily-review` | `db db-search` | 跨表关键词搜索 |
| `market-tasks` | `python main.py pre --date` | 盘前任务采集 |
| `market-tasks` | `python main.py post --date` | 盘后任务采集 |
| `market-tasks` | `python main.py recommend daily [--lookback-days N] [--top-k K] [--dry-run]` | 行业推荐日报（聚合 teacher_notes + industry_info，可选 Antigravity 点评，钉钉推送） |
| `market-tasks` | `python main.py recommend weekly [--lookback-days N] [--top-k K] [--dry-run]` | 行业推荐周报（深度版，默认 7 日 Top 8） |
| `market-tasks` | `python main.py volume-watch daily [--date YYYY-MM-DD] [--dry-run] [--refetch]` | 成交额 Top20 板块集中度日报（read-through 采集 + 申万二级打标 + 落库 `daily_volume_concentration` + 钉钉推送；报告含 Top20 个股明细表 + **成交额前50 区间涨幅排名段**[独立取成交额前50 → `get_stock_daily_range` 算 5/10/20 日涨幅 → **申万二级板块榜** + **同花顺概念题材榜**(多标签，复用 `get_ths_member` 反查 + 容器过滤≤300，concepts 落 `gain_universe_json`)，组按组内涨幅最大个股降序/平手比次大，三档独立榜，全 [事实] 守红线不出价位目标]；`--dry-run` 仅打印不落库不推送；`--refetch` 强制重拉绕过 `daily_market` 陈旧缓存，回填历史用） |
| `market-tasks` | `python main.py volume-watch trend [--date YYYY-MM-DD] [--days N]` | 只读打印最近 N 日集中度趋势（板块轮动 / 头部量级环比 / 个股留存），不采集不推送 |
| `market-tasks` | `python main.py sector-correlation daily [--date] [--windows 5,20,60] [--top-industries 15] [--top-concepts 10] [--activity-days 10] [--indices a,b] [--no-concept] [--dry-run]` | 板块相关性日报（Tushare 主源：多日活跃选板块[行业按成交额 / 概念按换手率] + 4 指数 → 多窗 5/20/60 原始相关 + 剔大盘超额相关 + β → 落 `sector_correlation_daily` + 钉钉；报告含近5日联动榜(短期共振)、板块×大盘同向/逆向、结构联动榜(60日)、反向榜双窗对照(5日/60日)；`--dry-run` 仅打印） |
| `market-tasks` | `python main.py sector-correlation matrix [--date] [--windows 20,60] [--top-industries N] [--top-concepts N] [--no-concept] [--refetch]` | 打印完整相关矩阵（板块×指数 + 板块×板块 原始/超额，逐窗）；缓存命中纯只读免初始化 Tushare，`--refetch` 强制现算；不推送 |
| `market-tasks` | `python main.py sector-correlation trend [--date] [--days N]` | 只读打印最近 N 日相关性漂移（板块数 / 样本数 / 强逆向对数演变），不采集不推送 |
| `market-tasks` | `python main.py market-timing daily [--date] [--pivot-index CODE --pivot-date YYYY-MM-DD] [--no-push] [--dry-run]` | 大盘择时观察：6 指数（上证 `000001.SH`/深成 `399001.SZ`/创业板 `399006.SZ`/科创50 `000688.SH`/中证2000 `932000.CSI` 微盘股代理/平均股价 `avg_price` 通达信880003 经 pytdx 日线）斐波那契时间周期变盘点（双向 swing 拐点起算，命中 5/8/13/21/34/55，多指数同日共振增强）+ 底分型生命周期（none/forming/confirmed/invalid 无状态从 bars 推导）+ 市场级客观上下文（两市成交额近20日地量分位/跌停家数/涨跌家数）→ 落 `market_timing_signal` + MD 观察清单 + 钉钉；全标 [判断] 守红线（不预判方向/不出价位/不给买卖建议）；三档=裸[落库+推]/`--no-push`[落库+打印]/`--dry-run`[内存不落不推]；`--pivot-index`+`--pivot-date` 手工 swing 覆盖须成对+合法+命中窗口（否则 fail-fast 非零退出） |
| `market-tasks` | `python main.py market-timing signals [--date] [--index CODE] [--limit N] [--json]` | 只读看最近择时信号（`--date` 看当日全部指数 / 无 date 看最近 N 行；`--index` 过滤指数；`--json` 输出 JSON），不采集不推送，供周日复盘回看 |
| `market-tasks` | `python main.py margin-index-correlation daily [--date] [--windows 5,20,60] [--divergence-windows 5,20] [--divergence-gap 0.5] [--max-lag 3] [--no-push] [--dry-run]` | 两融余额与指数联动性日报：`get_margin_series` 取两融区间序列（Tushare `pro.margin` 主源 / akshare 交易所官网降级，沪深北三市合计+分项），两融余额转日变化率(%)后与指数 `pct_chg` 同口径做四维分析 → ① 背离预警（指数涨两融降/指数跌两融升，近5/20日复利累计、指数交易日脊柱锁窗防稀疏日伪造）② 余额水位+趋势（绝对值/日环比/近20日分位/连增连降/偏离MA20）③ 领先/滞后（lagged corr，lag>0=两融滞后指数）④ 同步相关（5/20/60 窗 Pearson，复用 sector aggregator）；对照 total两融×多宽基(上证/创业板/沪深300/科创50) + 沪市两融×上证 + 深市两融×深成；落 `margin_index_correlation_daily` + 钉钉；全标 [判断] 守红线（不出价位/不给买卖建议/不写计划层）；三档=裸[落库+推]/`--no-push`[落库+打印]/`--dry-run`[内存不落不推]；非交易日守卫（仅 persist 时） |
| `market-tasks` | `python main.py margin-index-correlation signals [--date] [--days 30] [--json]` | 只读打印最近 N 日两融×指数联动快照（背离命中 + 合计余额趋势），`--json` 输出原始记录；不采集不推送，供周日复盘回看 |
| `market-tasks` | `python main.py research-digest daily [--date YYYY-MM-DD] [--dry-run] [--no-llm] [--huibo-mode desktop_terminal\|official_api\|off] [--huibo-window-days N] [--huibo-reader-cap N] [--huibo-reader-concurrency N] [--huibo-recommend-cap N] [--huibo-raw-retention-days N] [--huibo-summary-retention-days N] [--huibo-cleanup-only]` | 每日研报速读：A股研报评级（巨潮 cninfo `get_research_report_list`）+ 美股机构评级（yfinance `get_us_rating_changes`）+ 可选慧博深读增强（热点研报候选 URL 调慧博 `/redian/HotReport/GetList`→预筛 10-20 篇→优先接收慧博终端本地 PDF/`HUIBO_REPORT_PDF_DIR`[JS workflow 默认 `~/Downloads`]，JS 生产 workflow/launchd 默认刷新慧博终端当前会话并允许自动取 PDF，归档 raw PDF→Antigravity 每篇独立 reader 通过 `@PDF` 并发深读→所有 reader JSON 收齐后独立聚合/趋势/ranker→最多推荐 2 篇）→ MD 落盘 `data/reports/research-digest/` + 钉钉；`--dry-run` 仅打印不落盘不推送且不调 Antigravity、`--no-llm` 关闭 LLM；慧博 raw PDF 默认保留 30 天、summary 默认保留 180 天，正式任务结束自动清理，`--huibo-cleanup-only --dry-run` 仅预览清理对象；每天 22:00 launchd 触发，runner 过滤为 A 股交易日或 A 股交易日前一天 |
| `market-tasks` | `node scripts/workflows/research-digest-workflow.mjs daily [--date YYYY-MM-DD] [--reader-cap N] [--reader-concurrency N] [--reader-max-attempts N] [--llm-input-dir PATH] [--resume] [--retry-failed] [--preflight] [--no-aggregate-llm] [--publish] [--publish-dry-run] [--include-base-digest]` | 慧博深读 JS workflow：生产与 launchd 推荐入口；复用 Python helper 做候选/预筛/本地 PDF 归档/聚合/渲染/发布，JS 负责编排、JSONL 阶段日志、`state.json` 断点续跑、`invocation_id` 分组、`run_report.md` 阶段/报告状态总览、Antigravity PDF reader 并发池和失败自动重试（默认累计 2 次）；`--preflight` 或 `HUIBO_ANTIGRAVITY_PREFLIGHT=1` 先做 Antigravity 健康探针；正式 PDF 优先来自慧博终端本地下载目录或已归档 raw PDF，若本地缺失则默认刷新慧博终端当前会话并允许自动取 PDF，read 阶段复制单篇 PDF 到 `HUIBO_LLM_INPUT_DIR`/`--llm-input-dir` 指定 base 下的 `YYYY-MM-DD/` 子目录后再传给 Antigravity，resume 会重建缺失副本，cleanup 只删除带 marker 的本次子目录；Antigravity `quota/auth/startup` 全局不可用时停止后续 LLM，产物与正文显式标记 unavailable/fallback；reader 质量审计会把红线报告标 `quality_failed`，fallback 推荐带 `ranking_explanation`；产物落 `data/runs/research-digest/YYYY-MM-DD/`（state/events/candidates/prescreened/downloaded/reader/summary/report/run_report/published），`--publish` 同步 `report.md` 到 `data/reports/research-digest/YYYY-MM-DD.md` 并推钉钉；launchd 使用最近交易日并带 `--preflight --include-base-digest`，发布时合并 A股/美股基础研报段与慧博深读段，基础段失败会落 `base_digest_error` |
| `market-tasks` | `python main.py earnings-digest daily [--date YYYY-MM-DD] [--dry-run] [--lookback-days N] [--no-consensus]` | 业绩预告/快报速报：全市场 `get_earnings_forecast`（tushare `forecast_vip`，净利万元）+ `get_earnings_express`（`express_vip`，金额元）按公告日回看窗口（默认 3 自然日，env `EARNINGS_LOOKBACK_DAYS`/`--lookback-days` 可调）采集落 `raw_interface_payloads`（接口名 `earnings_forecast`/`earnings_express`，水位线只认 success 防迟到公告丢失）→ 水位线增量过滤 → 次日（=下一交易日）缺口验证（`get_market_daily_quotes` 全市场 OHLC+close，开盘跳空 ≥2% 触发[env `EARNINGS_DIGEST_GAP_THRESHOLD_PCT` 可调]，**市场投票方向取收盘涨跌符号**[收盘＝市场真实一票，高开低走自动翻利好不及预期；收平昨收=➖中性]，2×2：超预期确认/利好不及预期/利空出尽/暴雷确认 + 严格缺口/一字板按开盘口径标注，行并列「跳空→收盘」，按 |收盘涨跌| 降序截断）→ 五段渲染（持仓/关注命中、缺口验证、申万行业 Top5、分类计数、净利中值 ≥5000 万过滤 Top 榜[env `EARNINGS_DIGEST_MIN_PROFIT_WAN` 可调]）+ 口径三券商一致预期（命中/Top候选票 report_rc 全年预测×历史H1占比折算隐含中报预期，±10% 判超/符/低，标 [判断·H1占比折算]，`--no-consensus` 关）→ MD 落盘 `data/reports/earnings-digest/` + 钉钉；空窗口日不推送；`--dry-run` 仅打印不落盘不推送（采集落库照常，保「推送=存档同批」）；launchd 工作日+周日 22:00 单源调度，不进 `schedule`/APScheduler |
| `market-tasks` | `python main.py cognition-digest recent3d\|weekly\|monthly [--date YYYY-MM-DD] [--dry-run] [--no-llm]` | 交易认知沉淀只读汇总：只读认知三表（`trading_cognitions`/`cognition_instances`）按窗口算热度+共识+新增 Top-N → Antigravity 体系/方向建议（复用 Antigravity runner + `REDLINE_KEYWORDS` 红线护栏）→ 钉钉推送；只读不写库、不改 schema、不进 `main.py schedule`，由 3 个 per-task launchd（recent3d 日 18:30 / weekly 周日 20:00 / monthly 每月 1 号 09:00）独立调度；`--dry-run` 仅打印、`--no-llm` 走模板兜底关 LLM 叙事 |
| `market-tasks` | `python main.py trend-leader daily [--date YYYY-MM-DD] [--sectors '["半导体",...]'] [--top-k N] [--main-line l2\|l2+concept] [--top-concepts M] [--dry-run] [--no-push]` | 趋势主升漏斗扫描（盘后只读观察清单，对齐鞠磊）：候选 = 当日涨停（`get_limit_up_list`）∪ 双创(20cm)涨幅≥15% 加速（`get_market_daily_changes`，board-aware「20cm 涨15%+」）；主线 = `daily_volume_concentration` Top-K 申万二级 ∪ `--sectors`，`--main-line l2+concept` 时再 ∪ 同花顺概念净流入 Top-M（`get_concept_moneyflow_ths` + `get_ths_member`，成员数≤300 剔容器概念）→ 拉区间 OHLCV（`get_stock_daily_range`）→ 首次加速（board-aware）+ 缓涨入池、缩量回踩/贴MA5/乖离信号、趋势破坏退池（落 `trend_leader_pool` 状态机）→ 渲染 MD（全标 [判断]、守红线不出价位/不给买卖建议；概念分支票标「申万二级·分支:概念名」）+ 钉钉；默认 `l2`（零行为变化，概念分支 behind 开关）；`--dry-run` 内存副本跑不落池不推送（历史校准）、`--no-push` 落池仅打印 |
| `market-tasks` | `python main.py trend-leader pool [--status active\|exited] [--json]` | 只读看趋势主升观察池（在池天数/信号标记/退出原因），`--json` 结构化输出 |
| `market-tasks` | `python main.py string-yang daily [--date YYYY-MM-DD] [--top-k N] [--top-concepts N] [--teacher-lookback-days N] [--no-llm] [--dry-run] [--no-push]` | 主线板块串阳首阴股票池：主线判断=成交额集中度 Top-K 申万二级 + 同花顺概念资金分支（`get_concept_moneyflow_ths` + `get_ths_member`，成员数≤300 剔容器）+ 近 N 日老师观点（`teacher_notes`）→ LLM 只裁决主线申万二级/概念分支，失败或无有效裁决降级成交额 Top-K；候选=申万二级∈主线 或 概念∩主线概念 → 拉区间 OHLCV（`get_stock_daily_range`）→ 排除 ST/退市风险 → 只筛“昨日以前连续 ≥5 根阳线、串阳段无涨停且最大单日涨幅≤7%、最近20个交易日无涨停、首阴收盘价/MA60≤1.08、今日出现第一根放量阴线[今日成交额>前5个交易日最大成交额]”的确认票；概念分支票标「申万二级·分支:概念名」；按今日成交额/前5日最大成交额排序，渲染 MD `data/reports/string-yang/YYYY-MM-DD.md` + 钉钉；全标 [判断] 守红线（不出价位/不给买卖建议/不写计划层），不输出尚未出阴线的预备池；`--no-llm` 强制降级成交额 Top-K，`--dry-run` 仅打印不落报告不推送，`--no-push` 落报告不推送；工作日 21:50 launchd 单源调度 |
| `market-tasks` | `python main.py board-break daily [--date YYYY-MM-DD] [--dry-run] [--no-push] [--no-llm]` | 断板反包盘后扫描（无状态观察清单）：候选 = 昨日连板≥2 只当日断板（跌幅≤6% 且未跌停，10cm 主板剔 ST）→ 八维度加权打分（主线/增减持[减持按 250 日分位翻极性]/定增/公告/业绩/近10日涨幅/MACD，全 [判断] 附依据明细）→ `--no-llm` 未关时再跑 LLM 两两 PK 循环赛（熔断/红线过滤）→ 加权分排序 + PK 排序双榜渲染 MD 落盘 `data/reports/board-break/` + 钉钉；三档=裸[落盘+推]/`--no-push`[落盘+仅打印]/`--dry-run`[只打印不落盘不推送]；核心源失败时状态 `source_failed`，落失败报告 + 推告警 + 非零退出；无池无状态，隔日是否交易归用户判断 |
| `ingest-inspector` | `python main.py ingest run --stage --date` | 运行采集阶段任务，写 `ingest_runs` / `raw_interface_payloads` |
| `ingest-inspector` | `python main.py ingest run-interface --name --date` | 运行单接口采集，真实执行 provider 并记录失败 |
| `ingest-inspector` | `python main.py ingest list-interfaces` | 查看接口注册表 |
| `ingest-inspector` | `python main.py ingest inspect --date` | 查看采集状态、失败项与审计信息 |
| `ingest-inspector` | `python main.py ingest health --date --days` | 查看近 N 天采集健康摘要与稳定性状态 |
| `ingest-inspector` | `python main.py ingest retry` | 查看待重试分组摘要 |
| `ingest-inspector` | `python main.py ingest reconcile --stale-minutes` | 清理陈旧 running 采集记录 |
| `plan-workbench` | `python main.py plan draft --date` | 生成最小 `MarketObservation` + `TradeDraft` |
| `plan-workbench` | `python main.py plan draft --date --from-review --input-by` | 从已保存复盘生成 `review` observation 和次日 `TradeDraft` |
| `plan-workbench` | `python main.py plan show-draft --date/--draft-id` | 查看交易草稿 |
| `plan-workbench` | `python main.py plan confirm --date/--draft-id` | 确认交易计划并写入 `trade_plans` |
| `plan-workbench` | `python main.py plan diagnose --date/--plan-id` | 诊断交易计划并读取事实快照 |
| `plan-workbench` | `python main.py plan review --date/--plan-id` | 回写 `PlanReview` |
| `repo-maintenance-workflows` | `make check-scripts` | 运行脚本层检查，覆盖 skill 同步后的最小回归 |
| `repo-maintenance-workflows` | `python3 -m pytest scripts/tests/test_cli_smoke.py -v` | 快速验证 skills 依赖的 CLI 签名未漂移 |
| `repo-maintenance-workflows` | `make commands-doc` | 重新生成命令索引 |
| `repo-maintenance-workflows` | `make commands-check` | 校验命令索引与 Makefile 一致 |
| `instrument-agent` | 无固定业务 CLI | 为任意 agent 接入 Raindrop Workshop tracing；按目标仓库运行时选择 SDK/入口并验证 Workshop 可见性 |
| `setup-agent-replay` | 无固定业务 CLI | 为已 instrument 的 agent 配置本地 replay server 与 `.raindrop/agents.yaml`，供 Workshop 复放 |
| `daily-review` | `db add-calendar` | 手动录入投资日历事件（节假日/财经/财报等） |
| *(管理)* | `db init` | 初始化数据库 + 导入历史 YAML |
| *(管理)* | `db sync` | 重试 pending_writes 中的失败记录 |
| *(管理)* | `db reconcile` | 对账：YAML 与 DB 数据一致性比对 |

## API 端点依赖表

当前 skills 直接引用的端点：

| Skill | API 端点 | 方法 | 说明 |
|-------|---------|------|------|
| `daily-review` / `sector-projection-analysis` | `/api/review/{date}/prefill` | GET | 拉取八步复盘预填充数据（含板块候选；返回 `emotion_leader` / `capacity_leader`，`lead_stock` 仅作兼容；v1.4+ 额外返回 `cognitions_by_step`：按八步聚合的 `status=active` 底层认知，每步最多 5 条） |
| `daily-review` | `/api/review/{date}` | GET | 读取已保存的复盘内容 |
| `daily-review` | `/api/review/{date}` | PUT | 提交复盘主观判断；兼容摘要式 `facts` / `judgement` / `plan` / `holdings` 并轻量映射到页面可见字段 |
| `daily-review` / `plan-workbench` | `/api/review/{date}/to-draft` | POST | 将复盘结果转成 `review` observation 与次日 `TradeDraft` |
| `plan-workbench` | `/api/plans/drafts` | POST | 创建 `TradeDraft` |
| `plan-workbench` | `/api/plans/drafts/{draft_id}` | GET | 查看 `TradeDraft` |
| `plan-workbench` | `/api/plans/{draft_id}/confirm` | POST | 从草稿确认正式计划 |
| `plan-workbench` | `/api/plans/{plan_id}` | GET | 查看 `TradePlan` |
| `plan-workbench` | `/api/plans/{plan_id}/diagnostics` | GET | 查看计划诊断 |
| `plan-workbench` | `/api/plans/{plan_id}/review` | POST | 回写 `PlanReview` |
| `ingest-inspector` | `/api/ingest/interfaces` | GET | 查看接口注册表 |
| `ingest-inspector` | `/api/ingest/inspect` | GET | 查看某日采集状态摘要 |
| `ingest-inspector` | `/api/ingest/health` | GET | 查看近 N 天采集健康摘要 |
| `ingest-inspector` | `/api/ingest/runs` | GET | 查看某日采集运行记录 |
| `ingest-inspector` | `/api/ingest/errors` | GET | 查看某日采集错误记录 |
| `ingest-inspector` | `/api/ingest/run` | POST | 运行指定 stage 采集 |
| `ingest-inspector` | `/api/ingest/run-interface` | POST | 运行单接口采集 |
| `ingest-inspector` | `/api/ingest/retry` | GET | 查看待重试分组摘要 |
| `knowledge-to-plan` | `/api/knowledge/assets` | POST | 新增资料资产（禁止 `teacher_note` / `course_note`，422） |
| `knowledge-to-plan` | `/api/knowledge/assets` | GET | 列出资料资产（limit/offset；asset_type 仅 news_note/manual_note；keyword/created_*） |
| `knowledge-to-plan` | `/api/knowledge/assets/{asset_id}` | DELETE | 删除资料资产 |
| `knowledge-to-plan` | `/api/knowledge/assets/{asset_id}/draft` | POST | 从资料生成 observation/draft（遗留 `teacher_note` 行 422，走 teacher-notes draft） |
| `knowledge-to-plan` | `/api/knowledge/teacher-notes/{note_id}/draft` | POST | 从老师笔记生成 observation/draft |
| `knowledge-to-plan` | `/api/teacher-notes` | GET/POST | 资料工作台老师观点列表与录入 |
| `cognition-evolution` | `/api/cognition/cognitions` | GET | 只读列出交易认知（`category` / `status` / `conflict_group` / `keyword` 过滤） |
| `cognition-evolution` | `/api/cognition/cognitions/{id}` | GET | 只读单条认知详情（JSON 字段已 parse） |
| `cognition-evolution` | `/api/cognition/instances` | GET | 只读列出认知实例（`cognition_id` / `outcome` / `teacher_id` / `date_from/to` 过滤） |
| `cognition-evolution` | `/api/cognition/reviews` | GET | 只读列出周期复盘 |
| `cognition-evolution` | `/api/cognition/reviews/{id}` | GET | 只读复盘详情（聚合 JSON 已 parse） |

## Skill 参考附录（非 CLI/API）

| Skill | 路径 | 说明 |
|-------|------|------|
| `daily-review` | [daily-review/references/eight-step-prompt-templates.md](daily-review/references/eight-step-prompt-templates.md) | 八步复盘分步提问话术模板（配合 SKILL 速查） |
| `cognition-evolution` | [cognition-evolution/references/cognition-candidate-rules.md](cognition-evolution/references/cognition-candidate-rules.md) | 强候选最小标准、不建议落库条件、refine 默认动作与自检清单 |
| `sector-projection-analysis` | [sector-projection-analysis/references/methodology.md](sector-projection-analysis/references/methodology.md) | 《0524板块推演术》提炼后的板块推演方法论 |

`repo-maintenance-workflows` 不绑定固定业务 API；它会按受影响的 CLI / API / skill / 文档入口就近检查，并在修改 `scripts/main.py`、`scripts/api/routes/*.py`、`.agents/skills/**/*.md` 后强制同步 `INDEX.md` 与 `skills-sync.md`。

## 可用 API 总览（供开发新 Skill 参考）

所有端点由 FastAPI 自动生成文档，启动后可访问 `http://localhost:8000/docs`。
下表为静态索引，方便离线查阅。标注 `★` 的端点已被现有 skill 引用。

### 复盘（`routes/review.py`，前缀 `/api/review`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/review/{date}` ★ | 读取指定日期复盘（含 exists 标志） |
| GET | `/api/review/{date}/prefill` ★ | 预填充数据（行情+笔记+持仓+日历+ v1.4+ `cognitions_by_step`） |
| PUT | `/api/review/{date}` ★ | 保存/更新复盘主观判断；保存时轻量标准化常见摘要字段到 Web 表单可见字段 |
| POST | `/api/review/{date}/to-draft` ★ | 将已保存复盘转换为 `review` observation / `TradeDraft` |

### 老师观点（`routes/crud.py`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/teachers` | 列出所有老师 |
| GET | `/api/teacher-notes` | 查询笔记列表（keyword/teacher/from/to；limit 默认 200、最大 500；offset 分页）。**列表为减负不返回 `raw_content` 全文**，仅给 `has_raw_content` 布尔 + `raw_content_preview`（前 200 字）；需全文走详情端点 |
| GET | `/api/teacher-notes/{note_id}` | 读取单条笔记（含 `raw_content` 全文） |
| POST | `/api/teacher-notes` | 新建笔记（含 teacher_name 自动创建老师）；可选 `sync_watchlist_from_mentions: true` 同步关注池（默认不同步） |
| PUT | `/api/teacher-notes/{note_id}` | 更新笔记 |
| DELETE | `/api/teacher-notes/{note_id}` | 删除笔记 |

### 持仓池（`routes/crud.py`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/holdings` | 列出持仓（?status=active/closed/all） |
| GET | `/api/holdings/{hid}` | 读取单条持仓 |
| POST | `/api/holdings` | 新建/更新持仓（upsert） |
| PUT | `/api/holdings/{hid}` | 更新持仓字段 |
| DELETE | `/api/holdings/{hid}` | 删除持仓记录 |

### 关注池（`routes/crud.py`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/watchlist` | 列出关注池（?tier=&status=watching） |
| GET | `/api/watchlist/{wid}` | 读取单条关注标的 |
| POST | `/api/watchlist` | 添加到关注池 |
| PUT | `/api/watchlist/{wid}` | 更新关注标的（层级/状态/备注） |
| DELETE | `/api/watchlist/{wid}` | 删除关注标的 |

### 黑名单（`routes/crud.py`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/blacklist` | 列出黑名单 |
| POST | `/api/blacklist` | 加入黑名单 |
| DELETE | `/api/blacklist/{bid}` | 从黑名单移除 |

### 行业信息（`routes/crud.py`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/industry` | 列出行业信息（?keyword= 触发全文搜索） |
| POST | `/api/industry` | 新建行业信息 |
| PUT | `/api/industry/{iid}` | 更新行业信息 |
| DELETE | `/api/industry/{iid}` | 删除行业信息 |

### 宏观信息（`routes/crud.py`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/macro` | 列出宏观信息（?keyword= 触发全文搜索） |
| POST | `/api/macro` | 新建宏观信息 |
| PUT | `/api/macro/{mid}` | 更新宏观信息 |
| DELETE | `/api/macro/{mid}` | 删除宏观信息 |

### 投资日历（`routes/crud.py`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/calendar` | 最近 100 条日历事件 |
| GET | `/api/calendar/range` | 按日期区间查询（?from=&to=&impact=&category=） |
| POST | `/api/calendar` | 新建日历事件 |
| PUT | `/api/calendar/{cid}` | 更新日历事件 |
| DELETE | `/api/calendar/{cid}` | 删除日历事件 |

### 交易记录（`routes/crud.py`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/trades` | 查询交易记录（?from=&to=&stock_code=） |
| GET | `/api/trades/{tid}` | 读取单条交易 |
| POST | `/api/trades` | 新建交易记录 |
| PUT | `/api/trades/{tid}` | 更新交易记录 |
| DELETE | `/api/trades/{tid}` | 删除交易记录 |

### 行情（`routes/crud.py`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/market/history` | 近 N 日行情摘要（`?days=`，不含 raw_data） |
| GET | `/api/market/concentration/history` | 成交额 Top20 板块集中度趋势（`?days=`，库内最新 N 日 CR3/头部成交额/占两市/板块占比序列 + 最新日连续在榜/异动快照，供盘面概览图表） |
| GET | `/api/market/sector-gain-ranking/{date}` | 成交额前50 区间涨幅排名（读 `daily_volume_concentration.gain_universe` → `rankings`=申万二级板块榜 + `concept_rankings`=同花顺概念题材榜[多标签]，5/10/20 日三档，组按组内涨幅最大个股降序/平手比次大，全客观区间涨幅守红线不出价位目标；无记录/旧记录返三档空列表；供八步复盘「2.板块」步骤双维度展示） |
| GET | `/api/market/timing/{date}` | 大盘择时观察某交易日（读 `market_timing_signal`：6 指数斐波那契变盘点 + 底分型状态 + 市场上下文[共振数/成交额地量分位/涨跌家数/跌停]，全 [判断]；无数据 `available:false`，供盘面概览 MarketTimingPanel） |
| GET | `/api/market/margin-index-correlation/{date}` | 两融余额与指数联动性某交易日（读 `margin_index_correlation_daily` 经 `web_payload.build_daily_payload`：背离预警/余额水位+趋势/领先滞后/同步相关四维，全 [判断] 守红线；无记录 `available:false`，供八步复盘「1.大盘」MarginIndexCorrelation 组件渲染） |
| GET | `/api/market/timing/history` | 大盘择时市场级序列（`?days=&to_date=`，按日去重的共振指数数 + 成交额近20日地量分位升序序列，供盘面概览趋势图；`to_date` 给定时只取该日及之前，复盘历史日期不带出未来数据[前瞻偏差]） |
| GET | `/api/market/research-coverage` | 区间研报覆盖排行（`?days=&limit=`，合并最近 N 日篇数排行 `items`（仅 `stock_code/stock_name/report_count`，**per-stock 不含行业**）+ 按申万一级行业汇总 `industry`：`[{industry, stock_count, report_count}]`，缺成分降级未分类；行业统计只看 `industry` 字段） |
| GET | `/api/market/{date}` | 读取指定日期全市场行情摘要（扁平列 + 部分从 raw_data 展开） |
| GET | `/api/post-market/{date}` | 整包盘后信封（与 post-market.yaml / DB raw_data 一致） |

### 计划与资料（`routes/planning.py`）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/knowledge/assets` ★ | 新建资料资产（禁止 `teacher_note` / `course_note`） |
| GET | `/api/knowledge/assets` ★ | 列出资料资产（asset_type 仅 news_note/manual_note） |
| DELETE | `/api/knowledge/assets/{asset_id}` ★ | 删除资料资产 |
| POST | `/api/knowledge/assets/{asset_id}/draft` ★ | 从资料资产生成 observation / draft（遗留 `teacher_note` 不可用） |
| POST | `/api/knowledge/teacher-notes/{note_id}/draft` ★ | 从老师笔记生成 observation / draft |
| POST | `/api/plans/drafts` ★ | 创建 `TradeDraft` |
| GET | `/api/plans/drafts/{draft_id}` ★ | 查看 `TradeDraft` |
| POST | `/api/plans/{draft_id}/confirm` ★ | 从 draft 确认正式计划 |
| GET | `/api/plans/{plan_id}` ★ | 查看 `TradePlan` |
| GET | `/api/plans/{plan_id}/diagnostics` ★ | 查看计划诊断 |
| POST | `/api/plans/{plan_id}/review` ★ | 回写 `PlanReview` |

### 采集底座（`routes/ingest.py`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/ingest/interfaces` ★ | 查看接口注册表 |
| GET | `/api/ingest/inspect` ★ | 查看某日采集状态摘要 |
| GET | `/api/ingest/runs` ★ | 查看某日采集运行记录 |
| GET | `/api/ingest/errors` ★ | 查看某日采集错误记录 |
| POST | `/api/ingest/run` ★ | 运行指定 stage 采集 |
| POST | `/api/ingest/run-interface` ★ | 运行单接口采集 |
| GET | `/api/ingest/retry` ★ | 查看待重试分组摘要 |

### 交易认知（`routes/cognition.py`，前缀 `/api/cognition`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/cognition/cognitions` ★ | 列出 `trading_cognitions`（过滤：category/sub_category/status/evidence_level/conflict_group/keyword；limit 默认 100、offset 分页） |
| GET | `/api/cognition/cognitions/{id}` ★ | 单条认知（JSON 字段已 parse 成数组/对象） |
| GET | `/api/cognition/instances` ★ | 列出 `cognition_instances`（过滤：cognition_id/outcome/teacher_id/source_type/date_from/date_to；分页） |
| GET | `/api/cognition/reviews` ★ | 列出 `periodic_reviews`（过滤：period_type/review_scope/status/date_from/date_to；分页） |
| GET | `/api/cognition/reviews/{id}` ★ | 单条周期复盘（聚合 JSON 已 parse） |

### 搜索与分析（`routes/search.py`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/search/unified` | 跨表全文搜索（?q=&types=&from=&to=） |
| GET | `/api/search/export` | 搜索结果导出为 Markdown（同参数，返回纯文本） |
| GET | `/api/teachers/{teacher_id}/timeline` | 指定老师的笔记时间线 |
| GET | `/api/stock/{code}/mentions` | 个股被提及记录（跨笔记/行业/宏观） |
| GET | `/api/style-factors/series` | 风格因子时序数据（?metrics=&from=&to=） |

## 自动化检查

`scripts/tests/test_cli_smoke.py` 会验证上表中所有 **`db` 子命令**（`ALL_SKILL_COMMANDS`）与 `main.py` 顶层架构命令（`ARCHITECTURE_COMMANDS`：`ingest` / `plan` / `knowledge` / `executions` / `recommend` / `volume-watch` / `sector-correlation` / `market-timing` / `margin-index-correlation` / `research-digest` / `earnings-digest` / `cognition-digest` / `trend-leader` / `string-yang` / `board-break`）的 argparse 签名（不启动子进程、不连库）。`main.py pre` / `post` 仍由 `market-tasks` 文档与人工/定时流程保证。

每次 `pytest scripts/tests/test_cli_smoke.py` 都会同步检查：
- 依赖表所列 `db` 子命令名未被重命名
- 必需参数未被删除或改名
- choices 集合未缩减

## 变更流程

1. 修改 `cli.py` 或 API routes 时，同步更新此 INDEX.md
2. 优先运行 `make check-scripts`；若仅需检查 CLI 签名，可运行 `python3 -m pytest scripts/tests/test_cli_smoke.py -v`
3. 若命令参数有不向后兼容的变更，更新对应 SKILL.md 中的示例
4. 修改 `scripts/main.py` 新增/调整顶层命令（`ingest` / `plan` / `knowledge` / `executions` / `recommend` / `volume-watch` / `sector-correlation` / `market-timing` / `margin-index-correlation` / `*-digest` / `trend-leader` / `string-yang` / `board-break` 等）时，须在 `test_cli_smoke.py` 的 `ARCHITECTURE_COMMANDS` 加参数化用例，并同步更新相关 SKILL.md 与 AGENTS.md（见 `.agents/rules/skills-sync.md` §2.1）
