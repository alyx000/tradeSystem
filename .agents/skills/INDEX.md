# Skills 依赖索引

本文件只记录每个 skill 依赖的 CLI / API 签名与简要用途；详细行为按需读取对应 `SKILL.md` 或 reference。
**修改 CLI、API、service、workflow、launchd 或 skill 行为契约后，必须按 `.agents/rules/skills-sync.md` 判断是否同步此索引。**

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
| `cognition-evolution` | `knowledge cognition-* / instance-* / review-* (含 review-list)` | 认知提炼 / 实例验证 / 周期复盘（手动闭环）；`instance-add` 支持观点 `[事实]/[判断]` 拆分、因子快照、可证伪假设，`validate` 支持 `feedback_action` 回写交易系统反馈；`review-generate` 聚合 `evolving_views_json`（部分降级项见 SKILL.md） |
| `record-notes` | `db add-note` | 录入老师观点（文字/图片/多附件）；可选 `--sync-watchlist-from-stocks`（用户确认入池后） |
| `record-notes` | `db add-note --source-platform --source-url --source-article-id --published-at --fetched-at --content-sha256 --raw-content-file --input-by` | 经用户确认后录入带来源审计的老师观点；来源包与受控原文必须完整，幂等重复返回已有 ID，不重复附件/关注池副作用 |
| `record-notes` | `db update-note` / `db delete-note` | 修订或删除已有老师观点；删除必须显式 `--yes`，用于经确认后的纠错重写 |
| `record-notes` / `portfolio-manager` | `db stock-resolve` | 通过已配置 Provider 统一做证券简称/代码解析，供 Agent 补码与补名使用 |
| `record-notes` / `portfolio-manager` | `db watchlist-sync-from-note` | 按笔记 `mentioned_stocks` 写入关注池（两步确认后的第二步） |
| `record-notes` | `db add-industry` | 录入行业板块信息（写入须显式 `--input-by`） |
| `record-notes` | `db update-industry` / `db delete-industry` | 修订或删除已有行业板块信息；删除必须显式 `--yes`，用于经确认后的纠错重写 |
| `record-notes` | `db add-macro` | 录入宏观经济信息（写入须显式 `--input-by`） |
| `knowledge-to-plan` | `knowledge add-note` | 写入 `knowledge_assets`（新闻/课程/手动；不含老师观点） |
| `knowledge-to-plan` | `knowledge list` | 列出资料资产（不含 `teacher_note` 类型） |
| `knowledge-to-plan` | `knowledge draft-from-asset` | 从资料生成 `knowledge_asset` observation 和 `TradeDraft` |
| `knowledge-to-plan` | `knowledge draft-from-teacher-note` | 从 `teacher_notes` 生成 observation 和 `TradeDraft` |
| `knowledge-to-plan` | `db add-note` | 老师观点唯一事实源（与 record-notes 共用） |
| `portfolio-manager` | `db holdings-add --input-by [--entry-date YYYY-MM-DD] [--thesis-id]` | 新增持仓，可关联 `trade_thesis`；`--input-by` 必填（写入审计）；`--entry-date` 缺省 None——新建持仓落当日、更新已有持仓保留原值 |
| `portfolio-manager` | `db holdings-remove` | 移除持仓（置 closed） |
| `portfolio-manager` | `db holdings-list` | 列出当前持仓 |
| `portfolio-manager` | `db holdings-refresh` | 回填持仓现价与技术快照（需数据源） |
| `portfolio-manager` | `db holdings-import-yaml --input-by` | 将 `tracking/holdings.yaml` 导入 SQLite；`--input-by` 必填，随每条 upsert 落审计 |
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
| `daily-review` / `sector-projection-analysis` | `python main.py review factor-score --date YYYY-MM-DD --input-by USER [--steps-file PATH] [--no-llm] [--retry-of-run-id RUN_ID] [--json]` | 三位一体双层影子评分：第 1 层固定四因子，程序选出主因子后把该因子的受控证据卡传给最多 6 个 `core` 板块做第 2 层评分；`style_regime` 独立客观来源族=指数相对强弱、10/20/30cm 板型混合、已实现溢价；`leader_signal` 独立客观来源族=剔 ST 连板梯队、晋级实现、前高标回验，其中 `promotion_realization` 当前只保证 `promotion.trade_date=评分日`，`prior_core_feedback` 来源日必须等于 `trade_calendar` 严格上一开放日（不得以 `prev_market` 最近行情替代），并优先显式 `popularity_provenance`（键存在但非法 / 错位即拒绝且不 fallback），仅 provenance 键缺失的历史数据可用同一 `style_factors.promotion.prev_date / trade_date` 兼容 fallback；来源日期缺失或错位、Step 5 人工最票与自动 leader 名称都不得抬高质量，后两者仅作 `[判断]` context。规则门/证据质量/总分由程序控制，LLM 分仅表示相对重要度，不是概率、胜率或建议。只接受开放交易日；每次请求追加 `daily_review_factor_score_requests`，cache hit 也记录当前 `input_by`，但请求者不参与 cache key、旧 run diagnostics 不改；显式 retry 追加子 run；不生成 `TradeDraft` / `TradePlan` |
| `daily-review` | `python main.py review factor-confirm --date YYYY-MM-DD --run-id RUN_ID --decision-file PATH --input-by USER [--json]` | 人工确认系统建议：`accepted` / `overridden`（必须 `override_reason`，辅助因子最多 2 个）/ `undetermined`；在原子事务内按数据库当前预填与第 1～6 步重建证据摘要，若与 run 不同则必须先保存步骤并重跑评分；写 `daily_reviews.step8_plan.factor_decision` 并镜像 `key_factor / secondary_factors`，保留第 8 步其余字段；后续实际改写第 1～6 步会自动清除旧决定；不写计划层 |
| `daily-review` | `python main.py review factor-evaluate --date T_PLUS_1 --source-date SOURCE_DATE [--run-id RUN_ID] --outcome hit\|partial\|miss\|missing_data\|not_applicable --input-by USER [--note TEXT] [--json]` | 对来源日 run 做严格下一开放交易日 T+1 回验；程序按主因子用来源证据快照与次日客观事实做确定性方向/连续性比较，人工确认后写 evaluation；`missing_data` / `not_applicable` 不进入表现样本分母 |
| `daily-review` / `sector-projection-analysis` | `python main.py review factor-metrics [--days 20] [--json]` | 影子期指标：只统计开放交易日，每日优先人工决定引用的 canonical run（避免失败 retry 覆盖已确认父 run），汇总成功/非法输出/规则降级/覆盖、人工接受与改选、T+1 结果及分组；默认 20 日 |
| `market-tasks` | `python main.py pre --date` | 盘前任务采集 |
| `market-tasks` | `python main.py post --date` | 盘后任务采集 |
| `market-tasks` / `record-notes` | `python main.py wechat-teacher-feed should-run\|doctor\|collect\|show ...` | 本机 WeRSS 微信公众号白名单归档与候选查看；双 phase 严格日历，collect 只落 manifest/原文且须 `--input-by`，确认前不写 teacher_notes、不入池 |
| `market-tasks` | `python main.py recommend daily [--lookback-days N] [--top-k K] [--dry-run]` | 行业推荐日报（聚合 teacher_notes + industry_info，可选 Antigravity 点评，钉钉推送） |
| `market-tasks` | `python main.py recommend weekly [--lookback-days N] [--top-k K] [--dry-run]` | 行业推荐周报（深度版，默认 7 日 Top 8） |
| `market-tasks` | `python main.py volume-watch daily [--date YYYY-MM-DD] [--dry-run] [--no-push] [--refetch]` | 成交额 Top20 板块集中度日报（read-through 采集 + 申万二级打标 + 落库 `daily_volume_concentration` + 钉钉推送；报告含 Top20 个股明细表 + **成交额前50 区间涨幅排名段**[独立取成交额前50 → `get_stock_daily_range` 算 5/10/20 日涨幅 → **申万二级板块榜** + **同花顺概念题材榜**(多标签，复用 `get_ths_member` 反查 + 容器过滤≤300，concepts 落 `gain_universe_json`)，组按组内涨幅最大个股降序/平手比次大，三档独立榜，全 [事实] 守红线不出价位目标]；三档=裸[落库+推]/`--no-push`[落库+打印不推，历史回补避免重推]/`--dry-run`[不落库仅打印]；`--refetch` 强制重拉绕过 `daily_market` 陈旧缓存，回填历史用） |
| `market-tasks` | `python main.py volume-watch trend [--date YYYY-MM-DD] [--days N]` | 只读打印最近 N 日集中度趋势（板块轮动 / 头部量级环比 / 个股留存），不采集不推送 |
| `market-tasks` | `python main.py value-watch daily [--date YYYY-MM-DD] [--dry-run] [--no-push]` | 价值投资条件监控日报（鞠磊红利/稀缺价值框架，认知出处 `teacher_notes#391`）：Tushare `pro.sw_daily` 直连（银行指数 `801780.SI`）+ `get_stock_daily_range`（个股日线）+ `holdings`（active 持仓）+ `trade_calendar` → 三层口径（①红利买入触发=120 交易日滚动高点回撤 episode[进入≥档位、退出须<档位-2pp 迟滞] ②卖出阶梯=active 持仓∩四大行+长电按 `entry_price` 价格涨幅 10/15/20% 首触与 20 档回落 ③稀缺价值=片仔癀周线 MA5/10/20 粘合≤3%且 MA5 向上+周 MACD 上零轴）→ 落 `value_watch_daily` 快照；**事件首发才推钉钉**（`sent_events` 账本去重，enter 需当前仍成立、exit 迟到必补），历史 `--date` 落库不推；三档=裸[落库+推]/`--no-push`[落库+打印候选]/`--dry-run`[全内存不落库不推送不写账本，豁免非交易日守卫]；全标 [判断] 守红线 |
| `market-tasks` | `python main.py value-watch report [--date YYYY-MM-DD]` | 只读渲染已落库 `value_watch_daily` 快照（默认最新一日；不采集、不现算、不推送）；要看“现在的最新状态”用 `daily --dry-run` |
| `market-tasks` | `python main.py new-high daily [--date YYYY-MM-DD] [--top-n 10] [--dry-run] [--push] [--json]` | 前复权历史新高统计日报：全市场 `get_market_daily_quotes` + `get_adj_factor` 计算 `high*adj_factor`，严格突破截至昨日历史水位才计新高；按申万二级聚合，水位落 `stock_adjusted_high_watermark`，每日快照落 `daily_new_high_stats`，MD/JSON 落仓库根 `data/reports/new-high/`。生产随 `main.py post` / `cmd_post` 工作日 20:00 执行，复用 `today-post`，无独立 launchd/APScheduler、不自动推送；仅在 schema 完整、stats/watermark 尾日基线相等且自然日日历完整时按开放日升序补缺；申万源失败/空、重复代码、非 100% 有效 join、有效行情<4000、行情/复权宇宙覆盖<95%、申万二级覆盖<99% 或相邻 canonical 市场数比率超出 98%～102% 均失败且不落库；canonical 日只追加，单日两表同事务，`BEGIN IMMEDIATE` 后二次查重 + 尾日 CAS，尾日变化动态重规划，成功前缀保留、失败日停止并可续跑；目标日报告使用原子替换，`already_complete` 保留有效文件并修复缺失/损坏文件，失败隔离不影响 `cmd_post` / margin。持久化 `daily` 复用连续补缺协调器，`backfill` 强刷范围年度日历、已有日期只跳过且拒绝跳过尾日后开放日；历史更正必须从可信 checkpoint 重建后缀，禁止用未来水位原地重算；`daily` 默认不推送，`--push` 才推钉钉，`--dry-run` 不落库不落报告不推送；报告每行业默认 Top10，完整明细保留 SQLite/JSON；全 [事实] 守红线不出价位目标/不给买卖建议/不写计划层 |
| `market-tasks` | `python main.py new-high trend [--date YYYY-MM-DD] [--days N] [--json]` | 只读查看最近 N 个已统计交易日的新高数量趋势，不采集、不落库、不推送 |
| `market-tasks` | `python main.py new-high backfill [--start-date YYYY-MM-DD] [--end-date YYYY-MM-DD] [--top-n 10] [--dry-run]` | 默认近 5 年按日期正序建立前复权历史高点水位和每日统计；强制刷新范围内各年份交易日历以修复已有年份缺口，休市日不采行情；已有 canonical 日期直接跳过，只允许按开放日连续追加新后缀，请求范围跳过当前尾日后的任一开放日即 `historical_gap` 且不写，开放日来源失败即停，永不推送；空库首日仍需通过有效行情≥4000、重复/有效 join/复权宇宙/申万覆盖门禁。未建立基线时，持久化 `daily` 会要求先 backfill；历史校正需从可信 checkpoint 重建后缀，不支持单日原地重算 |
| `market-tasks` | `python main.py sector-correlation daily [--date] [--windows 5,20,60] [--top-industries 15] [--top-concepts 10] [--activity-days 10] [--indices a,b] [--no-concept] [--dry-run]` | 板块相关性日报（Tushare 主源：多日活跃选板块[行业按成交额 / 概念按换手率] + 4 指数 → 多窗 5/20/60 原始相关 + 剔大盘超额相关 + β → 落 `sector_correlation_daily` + 钉钉；报告含近5日联动榜(短期共振)、板块×大盘同向/逆向、结构联动榜(60日)、反向榜双窗对照(5日/60日)；`--dry-run` 仅打印） |
| `market-tasks` | `python main.py sector-correlation matrix [--date] [--windows 20,60] [--top-industries N] [--top-concepts N] [--no-concept] [--refetch]` | 打印完整相关矩阵（板块×指数 + 板块×板块 原始/超额，逐窗）；缓存命中纯只读免初始化 Tushare，`--refetch` 强制现算；不推送 |
| `market-tasks` | `python main.py sector-correlation trend [--date] [--days N]` | 只读打印最近 N 日相关性漂移（板块数 / 样本数 / 强逆向对数演变），不采集不推送 |
| `market-tasks` | `python main.py sector-crowding daily [--date] [--dry-run] [--push]` | 板块拥挤度日报（申万 L1 全量 + L2 成交额占全市场比 + 资金流代理 → 落 `sector_crowding_daily`；默认不推送，`--push` 才推钉钉；`--dry-run` 不落库不推送并豁免非交易日守卫；派生指标[分位/双高]读取时现算不落库；详见 [market-observability](market-tasks/references/market-observability.md)） |
| `market-tasks` | `python main.py sector-crowding report [--date]` | 只读：交易/斜率拥挤度 + 历史分位 + 双高拥挤清单（现算，不采集不推送） |
| `market-tasks` | `python main.py sector-crowding trend --sector CODE [--date] [--days 60]` | 只读单板块拥挤度时间序列（建议用申万代码查询） |
| `market-tasks` | `python main.py sector-crowding backfill --start [--end]` | 一次性历史回填（fail-closed：任一码失败/空返回整体中止不落库，重跑即重试；单片 ≥2000 行判截断报错；默认 2019-01-01 起） |
| `market-tasks` | `python main.py market-timing daily [--date] [--pivot-index CODE --pivot-date YYYY-MM-DD] [--no-push] [--dry-run]` | 大盘择时观察：6 指数（上证 `000001.SH`/深成 `399001.SZ`/创业板 `399006.SZ`/科创50 `000688.SH`/中证2000 `932000.CSI` 微盘股代理/平均股价 `avg_price` 通达信880003 经 pytdx 日线）斐波那契时间周期变盘点（双向 swing 拐点起算，命中 5/8/13/21/34/55，多指数同日共振增强）+ 底分型生命周期（none/forming/confirmed/invalid 无状态从 bars 推导）+ 市场级客观上下文（两市成交额近20日地量分位/跌停家数/涨跌家数）→ 落 `market_timing_signal` + MD 观察清单 + 钉钉；全标 [判断] 守红线（不预判方向/不出价位/不给买卖建议）；三档=裸[落库+推]/`--no-push`[落库+打印]/`--dry-run`[内存不落不推]；`--pivot-index`+`--pivot-date` 手工 swing 覆盖须成对+合法+命中窗口（否则 fail-fast 非零退出） |
| `market-tasks` | `python main.py market-timing signals [--date] [--index CODE] [--limit N] [--json]` | 只读看最近择时信号（`--date` 看当日全部指数 / 无 date 看最近 N 行；`--index` 过滤指数；`--json` 输出 JSON），不采集不推送，供周日复盘回看 |
| `market-tasks` | `python main.py margin-index-correlation daily [--date] [--windows 5,20,60] [--divergence-windows 5,20] [--divergence-gap 0.5] [--max-lag 3] [--no-push] [--dry-run]` | 两融余额与指数联动性日报：`get_margin_series` 取两融区间序列（Tushare `pro.margin` 主源 / akshare 交易所官网降级，沪深北三市合计+分项），两融余额转日变化率(%)后与指数 `pct_chg` 同口径做四维分析 → ① 背离预警（指数涨两融降/指数跌两融升，近5/20日复利累计、指数交易日脊柱锁窗防稀疏日伪造）② 余额水位+趋势（绝对值/日环比/近20日分位/连增连降/偏离MA20）③ 领先/滞后（lagged corr，lag>0=两融滞后指数）④ 同步相关（5/20/60 窗 Pearson，复用 sector aggregator）；对照 total两融×多宽基(上证/创业板/沪深300/科创50) + 沪市两融×上证 + 深市两融×深成；落 `margin_index_correlation_daily` + 钉钉；全标 [判断] 守红线（不出价位/不给买卖建议/不写计划层）；三档=裸[落库+推]/`--no-push`[落库+打印]/`--dry-run`[内存不落不推]；非交易日守卫（仅 persist 时） |
| `market-tasks` | `python main.py margin-index-correlation signals [--date] [--days 30] [--json]` | 只读打印最近 N 日两融×指数联动快照（背离命中 + 合计余额趋势），`--json` 输出原始记录；不采集不推送，供周日复盘回看 |
| `market-tasks` | `python main.py research-digest daily [--date YYYY-MM-DD] [--dry-run] [--no-llm] [--huibo-mode desktop_terminal\|official_api\|off] [--huibo-window-days N] [--huibo-reader-cap N] [--huibo-reader-concurrency N] [--huibo-recommend-cap N] [--huibo-raw-retention-days N] [--huibo-summary-retention-days N] [--huibo-cleanup-only]` | 每日研报速读：A股研报评级（巨潮 cninfo `get_research_report_list`）+ 美股机构评级（yfinance `get_us_rating_changes`）+ 可选慧博深读增强（Codex 自动化先用 Computer Use 读取慧博终端当前 HotReport URL→候选 URL 调慧博 `/redian/HotReport/GetList`→预筛 10-20 篇→按候选在慧博终端下载 PDF 并归档到独立目录 `data/reports/huibo/downloaded/YYYY-MM-DD/`，只放经标题校验与 sha256 去重的候选 PDF，再归档 raw PDF；若读不到当前终端 URL 或本地 PDF 为 0 则失败，不回退旧 URL、不发布空慧博深读→Antigravity 每篇独立 reader 通过 `@PDF` 并发深读→所有 reader JSON 收齐后独立聚合/趋势/ranker→最多推荐 2 篇）→ MD 落盘 `data/reports/research-digest/` + 钉钉；`--dry-run` 仅打印不落盘不推送且不调 Antigravity、`--no-llm` 关闭 LLM；慧博 raw PDF 默认保留 30 天、summary 默认保留 180 天，正式任务结束自动清理，`--huibo-cleanup-only --dry-run` 仅预览清理对象；每天 22:00 由 Codex 自动化「每日慧博研报速读（Computer Use）」触发，过滤为 A 股交易日或 A 股交易日前一天；旧 research-digest launchd 已停用 |
| `market-tasks` / `daily-review` | `python main.py research-digest trend [--days N] [--recent-n N] [--top N] [--backfill N] [--json]` | 研报覆盖·申万一级行业趋势：读 `raw_interface_payloads.research_report_list`（随 `cmd_post` post_extended 每日落库，registry `enabled_by_default=True`）→ 近 N 有效日 vs 前 N 有效日覆盖占比 Δpp（份额口径免疫月末批量脉冲；`status='empty'` 合法真空日不进分母）；`--backfill N` 幂等回补最近 N 个交易日中缺失或 `empty` 的日（完成态=非空 success，empty 每轮重采以吃进迟到回填；逐日 cninfo，单日失败跳过不中断）；行业标注复用 `get_stock_sw_industry_map`（失败降级全「未分类」）；全 [事实] 计数、不构成买卖建议，复盘「2.板块」可引用 |
| `market-tasks` | `node scripts/workflows/research-digest-workflow.mjs daily [--date YYYY-MM-DD] [--reader-cap N] [--reader-concurrency N] [--reader-max-attempts N] [--llm-input-dir PATH] [--resume] [--retry-failed] [--preflight] [--no-aggregate-llm] [--publish] [--publish-dry-run] [--include-base-digest]` | 慧博深读 JS workflow：生产执行入口，调度由 Codex 自动化负责；自动化必须先用 Computer Use 获取慧博终端当前 HotReport URL，并把慧博终端逐篇下载、去重后的候选 PDF 放入 `HUIBO_REPORT_PDF_DIR`，生产约定为独立目录 `data/reports/huibo/downloaded/YYYY-MM-DD/`，以 `HUIBO_HOT_REPORT_URL=<当前URL> HUIBO_REPORT_PDF_DIR=<终端下载绝对目录> HUIBO_REFRESH_URL_FROM_APP=0 HUIBO_ALLOW_DIRECT_PDF_DOWNLOAD=0` 运行 workflow。复用 Python helper 做候选/预筛/本地 PDF 归档/聚合/渲染/发布，JS 负责编排、JSONL 阶段日志、`state.json` 断点续跑、`invocation_id` 分组、`run_report.md` 阶段/报告状态总览、Antigravity PDF reader 并发池和失败自动重试（默认累计 2 次）；正式自动化不带 `--resume`，避免沿用旧 run 状态；`HUIBO_REPORT_PDF_DIR` 会按仓库根绝对化再传给 helper，`report_id` 优先使用慧博稳定字段 `DId/DocName`，避免终端 token 变化导致已下载 PDF 无法匹配；`--preflight` 或 `HUIBO_ANTIGRAVITY_PREFLIGHT=1` 先做 Antigravity 健康探针；read 阶段复制单篇 PDF 到 `HUIBO_LLM_INPUT_DIR`/`--llm-input-dir` 指定 base 下的 `YYYY-MM-DD/` 子目录后再传给 Antigravity；Antigravity `quota/auth/startup` 全局不可用时停止后续 LLM，产物与正文显式标记 unavailable/fallback；reader 质量审计会把红线报告标 `quality_failed`，fallback 推荐带 `ranking_explanation`；产物落 `data/runs/research-digest/YYYY-MM-DD/`（state/events/candidates/prescreened/downloaded/reader/summary/report/run_report/published），`--publish` 同步 `report.md` 到 `data/reports/research-digest/YYYY-MM-DD.md` 并推钉钉；发布时合并 A股/美股基础研报段与慧博深读段，基础段失败会落 `base_digest_error` |
| `market-tasks` | `python main.py earnings-digest daily [--date YYYY-MM-DD] [--dry-run] [--lookback-days N] [--no-consensus]` | 业绩预告/快报速报：全市场 `get_earnings_forecast`（tushare `forecast_vip`，净利万元）+ `get_earnings_express`（`express_vip`，金额元）按公告日回看窗口（默认 3 自然日，env `EARNINGS_LOOKBACK_DAYS`/`--lookback-days` 可调）采集落 `raw_interface_payloads`（接口名 `earnings_forecast`/`earnings_express`，水位线只认 success 防迟到公告丢失）→ 水位线增量过滤 → 次日（=下一交易日）缺口验证（`get_market_daily_quotes` 全市场 OHLC+close，开盘跳空 ≥2% 触发[env `EARNINGS_DIGEST_GAP_THRESHOLD_PCT` 可调]，**市场投票方向取收盘涨跌符号**[收盘＝市场真实一票，高开低走自动翻利好不及预期；收平昨收=➖中性]，2×2：超预期确认/利好不及预期/利空出尽/暴雷确认 + 严格缺口/一字板按开盘口径标注，行并列「跳空→收盘」，按 |收盘涨跌| 降序截断）→ 五段渲染（持仓/关注命中、缺口验证、申万行业 Top5、分类计数、净利中值 ≥5000 万过滤 Top 榜[env `EARNINGS_DIGEST_MIN_PROFIT_WAN` 可调]）+ 口径三券商一致预期（命中/Top候选票 report_rc 全年预测×历史H1占比折算隐含中报预期，±10% 判超/符/低，标 [判断·H1占比折算]，`--no-consensus` 关）→ MD 落盘 `data/reports/earnings-digest/` + 钉钉；空窗口日不推送；`--dry-run` 仅打印不落盘不推送（采集落库照常，保「推送=存档同批」）；launchd 工作日+周日 22:00 单源调度，不进 `schedule`/APScheduler |
| `market-tasks` | `python main.py cognition-digest recent3d\|weekly\|monthly [--date YYYY-MM-DD] [--dry-run] [--no-llm]` | 交易认知沉淀只读汇总：只读认知三表（`trading_cognitions`/`cognition_instances`）按窗口算热度+共识+新增 Top-N → Antigravity 体系/方向建议（复用 Antigravity runner + `REDLINE_KEYWORDS` 红线护栏）→ 钉钉推送；只读不写库、不改 schema、不进 `main.py schedule`，由 3 个 per-task launchd（recent3d 日 18:30 / weekly 周日 20:00 / monthly 每月 1 号 09:00）独立调度；`--dry-run` 仅打印、`--no-llm` 走模板兜底关 LLM 叙事 |
| `market-tasks` | `python main.py trend-leader daily [--date YYYY-MM-DD] [--sectors '["半导体",...]'] [--top-k N] [--main-line hybrid\|l2\|l2+concept] [--top-concepts M] [--no-llm] [--dry-run] [--no-push]` | 趋势主升漏斗扫描（盘后只读观察清单，对齐鞠磊）：候选 = 当日涨停（`get_limit_up_list`）∪ 双创(20cm)涨幅≥15% 加速（`get_market_daily_changes`，board-aware「20cm 涨15%+」）；自动申万主线读取截至目标日最近最多 3 个有效 `daily_volume_concentration` 快照的 Top-K（空/全部 UNCLASSIFIED 快照不计；2～3 个快照至少命中 2 次，仅 1 个时命中 1 次），`--sectors` 手工板块直接保留且不受持续性门槛；默认 `hybrid` 再并入同花顺概念净流入 Top-M（`get_concept_moneyflow_ths` 排序 + 有限窗口 `get_ths_member`，默认预取 `max(40, Top-M*5)`，成员数≤300 剔容器概念），由 LLM 只过滤概念分支（不新增事实/不否决稳定申万主线/不做买卖建议；异常、超时、非法输出或红线命中时关闭概念分支并标 `fallback_l2`）；只有 `hybrid --no-llm` 与 `l2+concept` 明确使用机械概念分支，`l2`=纯稳定申万口径 → 拉区间 OHLCV（`get_stock_daily_range`）→ 首次加速（board-aware）+ 缓涨入池、缩量回踩/贴MA5/乖离信号、趋势破坏退池（落 `trend_leader_pool` 状态机）→ 渲染 MD（展示快照数、命中门槛、来源/降级状态和 LLM 状态；全标 [判断]、守红线不出价位/不给买卖建议；概念分支票标「申万二级·分支:概念名」）+ 钉钉；新口径不回溯清理历史池；`--dry-run` 内存副本跑不落池不推送（历史校准）、`--no-push` 落池仅打印 |
| `market-tasks` | `python main.py trend-leader pool [--status active\|exited] [--json]` | 只读看趋势主升观察池（在池天数/信号标记/退出原因），`--json` 结构化输出 |
| `market-tasks` | `python main.py string-yang daily [--date YYYY-MM-DD] [--top-k N] [--top-concepts N] [--teacher-lookback-days N] [--no-llm] [--dry-run] [--no-push]` | 主线板块串阳首阴股票池：主线判断=成交额集中度 Top-K 申万二级 + 同花顺概念资金分支（`get_concept_moneyflow_ths` + `get_ths_member`，成员数≤300 剔容器）+ 近 N 日老师观点（`teacher_notes`）→ LLM 只裁决主线申万二级/概念分支，失败或无有效裁决降级成交额 Top-K；候选=申万二级∈主线 或 概念∩主线概念 → 拉区间 OHLCV（`get_stock_daily_range`）→ 排除 ST/退市风险 → 只筛“昨日以前连续 ≥5 根阳线、串阳段无涨停且最大单日涨幅≤7%、最近20个交易日无涨停、首阴收盘价/MA60≤1.08、今日出现第一根放量阴线[今日成交额>前5个交易日最大成交额]”的确认票；概念分支票标「申万二级·分支:概念名」；按今日成交额/前5日最大成交额排序，渲染 MD `data/reports/string-yang/YYYY-MM-DD.md` + 钉钉；全标 [判断] 守红线（不出价位/不给买卖建议/不写计划层），不输出尚未出阴线的预备池；`--no-llm` 强制降级成交额 Top-K，`--dry-run` 仅打印不落报告不推送，`--no-push` 落报告不推送；工作日 21:50 launchd 单源调度 |
| `daily-review` / `market-tasks` | `python main.py daily-leaders propose [--date YYYY-MM-DD] [--push] [--no-llm] [--max-candidates 1..15]` / `python main.py daily-leaders show --date YYYY-MM-DD [--json]` / `python main.py daily-leaders confirm --date YYYY-MM-DD --input-by USER [--leaders-file PATH]` | 每日最票候选确认流：`propose` 汇总复盘预填、历史最票、当日行情强度、资金流、老师观点和认知证据，先过滤成交额低于 20 亿或缺少可验证成交额的个股；优先用当前申万二级成分映射归板块，未命中时标「未分类」，概念/资金流原板块仅保留为 `source_sector` 辅助证据；股票身份按合法代码优先、规范名称兜底；语义属性固定为「趋势中军/连板核心/前排活跃/弹性前排」，10/20/30cm 独立作板型事实；LLM 只复核确定性预收敛后的最多 30 只，必须完整覆盖且不得夹带池外股票，程序强制同板块同属性只留 1 只、股票全局唯一、最终最多 15 只（`--max-candidates` 默认 15 且仅接受 1..15）；LLM 失败、非法输出或 `--no-llm` 仍按同约束确定性兜底。`--push` 推送钉钉 Markdown 草稿；`show` 只读；`confirm` 必须显式 `--input-by`，复用提案层 Unicode 空白压缩板块键，在事务前再次拒绝超 15、规范股票身份重复或同板块同属性重复，旧稿不合规则显式失败；展示内代码支持紧凑格式与合法交易所后缀，非法后缀或与显式代码冲突直接拒绝；确认后写复盘第 5 步并同步 `leader_tracking`，合法 `stock_code` 规范为裸 6 位且 tracking 优先按代码识别，旧 payload 无代码时才回退展示名；同股同规范板块属性的旧名称型 tracking 行仅在全局及同批名称映射无歧义时于事务内迁移/合并，避免历史身份误归或分叉；不实现钉钉按钮回调，不给买卖建议、不出价位 |
| `market-tasks` | `python main.py board-break daily [--date YYYY-MM-DD] [--dry-run] [--no-push] [--no-llm]` | 断板反包盘后扫描（无状态观察清单）：候选 = 昨日连板≥2 只当日断板（跌幅≤6% 且未跌停，10cm 主板剔 ST）→ 八维度加权打分（主线/增减持[减持按 250 日分位翻极性]/定增/公告/业绩/近10日涨幅/MACD，全 [判断] 附依据明细）→ `--no-llm` 未关时再跑 LLM 两两 PK 循环赛（熔断/红线过滤）→ 加权分排序 + PK 排序双榜渲染 MD 落盘 `data/reports/board-break/` + 钉钉；三档=裸[落盘+推]/`--no-push`[落盘+仅打印]/`--dry-run`[只打印不落盘不推送]；核心源失败时状态 `source_failed`，落失败报告 + 推告警 + 非零退出；无池无状态，隔日是否交易归用户判断 |
| `market-tasks` | `python main.py tail-scan daily [--date YYYY-MM-DD] [--min-pct 7] [--min-amount 20] [--dry-run] [--no-push] [--no-llm]` | 盘中尾盘强势股扫描（无状态只读观察清单，14:40 单次快照）：全市场实时行情（`get_realtime_quotes`，单点脆弱源失败重试一次仍失败 → `source_failed`）→ 三条件筛选（涨幅>7% ∩ 非ST ∩ 成交额>20亿，全 [事实]）→ 四维事实卡（逻辑：T-1 主线申万二级 Top-K + 同花顺概念资金流 T-1 Top-M + 老师观点命中；三位一体：候选池内涨幅名次 + 指数背景；节奏：近5日涨幅/MA上方/连涨天数/半日放量追平昨日全日节奏代理 `first_surge`；节点：距前高/是否破前高，单维度取数失败只降级该维度不中断整批）+ 产业逻辑增强（每票 `[事实·主营]`：provider capability `get_stock_business_profiles`，Tushare `stock_company` 主源/AkShare `stock_zyjs_ths` 补缺，为扫描时当前公开静态资料、非历史 as-of，摘要优先级=`main_business`>`introduction`>`business_scope`；`[判断·产业链位置]`：仅基于申万二级+主营摘要+产品受控归纳；近30自然日催化：只读 `teacher_notes` 精确代码/慧博精确名称/`industry_info` 基于申万二级/主营摘要/产品/已验证概念标签受控匹配，按 [事实]/[老师观点]/[研报观点]/[来源陈述] 分层，失败仅降级对应维度）→ 粗权重分仅用于 PK 强池截断（`PK_POOL_MAX=12`）与排序破平，**不进 PK prompt**；PK 只喂边界受控字段（产业链位置显式保留程序 [判断]，近期催化保留事实/观点/来源标签）→ `--no-llm` 未关时跑 LLM 两两 PK 循环赛（180s 预算熔断/无效场率>50%熔断/红线过滤，镜像 board-break）→ 本地 MD 全量落盘，钉钉超长时限制为 ≤18000 UTF-8 bytes、最多前12个完整候选块并附完整报告路径；三档=裸[落盘+推]/`--no-push`[落盘+仅打印]/`--dry-run`[只打印不落盘不推送]；核心源失败落失败报告 + 推告警 + 非零退出；无池无状态，不写交易计划/关注池 |
| `market-tasks`（`tail-scan` 内部依赖） | Provider capability `get_stock_concept_memberships(ts_codes)`（非 CLI） | 消费者为 `services.tail_scan.concept_context`（由 `scorer` 接线，`industry_logic` / `renderer` 消费）：按候选反查同花顺 `type=N` 当前快照，复用共享成员数 `<=300` 过滤。`stock_concept_names` / `stock_concept_total` / `stock_concept_status` / `stock_concept_source` / `stock_concept_snapshot_at` 表示过滤后的完整当前归属，仅供报告（最多 5 个 + 总数）和产业证据，不进粗分/PK；`concept_names` / `concept_status` / `in_hot_concept` 保持上一交易日资金流热命中兼容语义（先按 `company_num<=300` 过滤容器再补足 Top8，报告最多 2 个），仍只有 `in_hot_concept` 参与原粗分。归属当前快照不是历史 as-of；`source_failed` / `coverage_failed` / `member_failed` / `missing` 分别保留，不能把失败或覆盖不足写成确定未命中 |
| `market-tasks` | `python main.py ma-breakout daily [--date YYYY-MM-DD] [--windows 5,10] [--leader-lookback-days 60] [--top-n N] [--dry-run] [--no-push] [--json]` | 4日均线二波观察池（盘后只读观察清单）：先从 `leader_tracking` 取目标日前近端、复盘第 5 步人工确认的历史龙头/最票宇宙（默认近 60 自然日，剔除过久远龙头；`trend_leader_pool` 自动趋势池不作为默认龙头来源）→ `get_market_daily_quotes` 近 10 个有效行情日组装个股序列 → MA4 重新拐头向上（今日 MA4 > 昨日 MA4，且昨日 MA4 < 前日 MA4 < 前两日 MA4，要求上拐前至少两根 MA4 连续下行）+ 今日成交额同时突破 5/10 日成交额均线 + 当日未涨停 → 按今日成交额降序渲染 MD/JSON 落 `data/reports/ma-breakout/YYYY-MM-DD.{md,json}`（全标 [判断]、守红线不出价位/不给买卖建议、不写计划层）+ 钉钉；未显式 `--date` 且当前交易日尚未收盘（上海时间 15:30 前）或当天为交易日前一天时自动回退到最近已完成交易日；`--windows` 可改双均量线周期，默认 `5,10`；`--no-push` 落盘但不推送，`--dry-run`/`--json` 不落盘不推送；不写交易计划/关注池；工作日+周日 21:35 launchd 调度 |
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
| `repo-maintenance-workflows` | `make commands-doc` | 在确认索引需要更新且已获修改授权后，重新生成命令索引 |
| `repo-maintenance-workflows` | `make commands-check` | 只读校验命令索引与 Makefile 一致；应先于 `commands-doc` 执行 |
| `repo-maintenance-workflows` | `db backup --output PATH --input-by USER [--json]` | 用 SQLite backup API 生成 `0600` 完整快照及 SHA-256 回执；v40 生产迁移前必须先停写并执行 |
| `repo-maintenance-workflows` | `db migrate --require-backup PATH --input-by USER [--json]` | 仅在备份权限、版本及当前源库规范快照 SHA 全部一致时，原子激活/修复 teacher_notes v40 来源列与三组 partial unique 索引；普通 API/CLI 不会隐式跨越 v39→v40 |
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
| `daily-review` / `plan-workbench` | `/api/review/{date}/to-draft` | POST | 将既有复盘事实/判断转成 `review` observation 与次日 `TradeDraft`；当前转换只消费 `step1_market` / `step2_sectors`，忽略 `step8_plan` 及 `key_factor / secondary_factors` 兼容镜像，三位一体 factor score / decision / evaluation / metrics 明确不复制进 draft |
| `daily-review` / `sector-projection-analysis` | `/api/review-factors/{date}/score` | POST | 执行双层影子评分；body 可含 `steps` / `no_llm` / `retry_of_run_id`，`input_by` 运行时必填（缺失、空值或仅空白返回 422），Web 显式传 `web`；`input_by` 不进入 cache key；结果落 append-only score run，每次请求另落 append-only request audit（含 cache hit）；不写 `TradeDraft` / `TradePlan` |
| `daily-review` | `/api/review-factors/{date}/evaluation` | GET | 只读生成严格 T+1 客观回验建议；query 可含 `source_date` / `score_run_id`，不写库 |
| `daily-review` | `/api/review-factors/{date}/evaluation` | PUT | 重新生成严格 T+1 建议并人工确认；body 含 `confirmed_outcome / input_by`，可选 `source_date / score_run_id / evaluation_note` |
| `daily-review` / `sector-projection-analysis` | `/api/review-factors/metrics?days=20` | GET | 查看最近 N 个有效交易日影子指标；`days` 取 1～250，默认 20 |
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
| `cognition-evolution` | `/api/cognition/instances` | GET | 只读列出认知实例（`cognition_id` / `outcome` / `teacher_id` / `date_from/to` 过滤；结构化观点/因子/假设/反馈 JSON 已 parse） |
| `cognition-evolution` | `/api/cognition/reviews` | GET | 只读列出周期复盘 |
| `cognition-evolution` | `/api/cognition/reviews/{id}` | GET | 只读复盘详情（聚合 JSON 已 parse） |

## Skill 参考附录（非 CLI/API）

| Skill | 路径 | 说明 |
|-------|------|------|
| `daily-review` | [daily-review/references/eight-step-prompt-templates.md](daily-review/references/eight-step-prompt-templates.md) | 八步复盘分步提问话术模板（配合 SKILL 速查） |
| `daily-review` | [daily-review/references/multi-agent-review.md](daily-review/references/multi-agent-review.md) | 9 路完整采集、精简正文、Claim 唯一归属、容量/板块矩阵/滚动新高/事件窗硬门、折叠证据与 HTML 预算/拒绝生成契约 |
| `cognition-evolution` | [cognition-evolution/references/cognition-candidate-rules.md](cognition-evolution/references/cognition-candidate-rules.md) | 强候选最小标准、不建议落库条件、refine 默认动作与自检清单 |
| `repo-maintenance-workflows` | [repo-maintenance-workflows/references/maintenance-checklist.md](repo-maintenance-workflows/references/maintenance-checklist.md) | 只读诊断、Review、跨入口对齐、每日巡检、同步与验证检查清单 |
| `repo-maintenance-workflows` | [repo-maintenance-workflows/references/teacher-notes-v40-migration.md](repo-maintenance-workflows/references/teacher-notes-v40-migration.md) | teacher_notes v40 停写、0600 备份、SHA 绑定与显式迁移门禁 |
| `sector-projection-analysis` | [sector-projection-analysis/references/methodology.md](sector-projection-analysis/references/methodology.md) | 《0524板块推演术》提炼后的板块推演方法论 |
| `repo-maintenance-workflows` | [repo-maintenance-workflows/references/maintenance-checklist.md](repo-maintenance-workflows/references/maintenance-checklist.md) | 只读诊断、Review、跨入口对齐、每日巡检、同步与验证检查清单 |
| `repo-maintenance-workflows` | [repo-maintenance-workflows/references/teacher-notes-v40-migration.md](repo-maintenance-workflows/references/teacher-notes-v40-migration.md) | teacher_notes v40 停写、0600 备份、SHA 绑定与显式迁移门禁 |
| `market-tasks` | [market-tasks/references/base-market-runs.md](market-tasks/references/base-market-runs.md) | 基础盘前/盘后副作用、历史补跑风险与 legacy schedule 查重边界 |
| `market-tasks` | [market-tasks/references/ingestion-and-feeds.md](market-tasks/references/ingestion-and-feeds.md) | 公众号白名单、行业推荐与相关推送契约 |
| `market-tasks` | [market-tasks/references/market-observability.md](market-tasks/references/market-observability.md) | 市场统计、历史新高、相关性、择时与两融联动契约 |
| `market-tasks` | [market-tasks/references/stock-scanners.md](market-tasks/references/stock-scanners.md) | 股票扫描、观察池与候选确认契约 |
| `market-tasks` | [market-tasks/references/research-and-digests.md](market-tasks/references/research-and-digests.md) | 研报、业绩与认知摘要契约 |

`repo-maintenance-workflows` 不绑定固定业务 API；它会按受影响的 CLI / API / service / workflow / launchd / skill 入口就近检查，并按 `.agents/rules/skills-sync.md` 核对 `INDEX.md`、对应 SKILL/reference 与同步映射。

Raindrop 的 `instrument-agent` / `setup-agent-replay` 是官方 `raindrop-ai/workshop` 随 CLI 分发的通用工具，不作为本仓库团队 skill 发放。新机器需要时安装 Raindrop 后只运行 `raindrop setup --global`，由 `~/.raindrop/bundles/current/skills/` 安装到用户级 `~/.agents/skills/`；**不要在本仓库运行 `raindrop setup --local`**，否则会重新生成项目内重复 skill。截至 2026-07-19，本机 CLI 为 `0.1.12`，已核对的官方 `v0.1.15` 中两份 skill 内容仍一致。仓库 tracing 与 Workshop MCP 由 `Makefile`、`.mcp.json` 和运行时代码维护，不依赖项目内这两个向导。

## 常用 API 总览（按需）

### 复盘（`routes/review.py`，前缀 `/api/review`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/review/{date}` ★ | 读取指定日期复盘（含 exists 标志） |
| GET | `/api/review/{date}/prefill` ★ | 预填充数据（行情+笔记+持仓+日历+ v1.4+ `cognitions_by_step`） |
| PUT | `/api/review/{date}` ★ | 保存/更新复盘主观判断；保存时轻量标准化常见摘要字段到 Web 表单可见字段；`step5_leaders` 的展示内代码若含非法后缀或与显式 `stock_code` 冲突，返回 422 并原子回滚复盘与 tracking 写入；若含 `step8_plan.factor_decision`，在同一写事务内用本次第 1～6 步与当前预填校验 score run 证据摘要，输入变化返回 422、要求重跑评分；未提交新决定但当前证据已变化时自动清除旧决定及兼容镜像；不进入 `TradeDraft` |
| POST | `/api/review/{date}/to-draft` ★ | 将已保存复盘转换为 `review` observation / `TradeDraft`；只消费第 1、2 步，不复制第 8 步 factor decision 或兼容镜像 |

### 复盘因子评分（`routes/review_factors.py`，前缀 `/api/review-factors`）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/review-factors/{date}/score` ★ | 双层影子评分；body=`steps? / no_llm? / retry_of_run_id? / input_by`，`input_by` 运行时必填（缺失、空值或仅空白返回 422），Web 显式传 `web`；`input_by` 不进入 cache key，同 key 默认复用，显式 retry 建 append-only 子 run |
| GET | `/api/review-factors/{date}/evaluation` ★ | 只读生成严格 T+1 建议；query=`source_date? / score_run_id?` |
| PUT | `/api/review-factors/{date}/evaluation` ★ | 人工确认 T+1 结果；body=`confirmed_outcome / input_by` + 可选来源日、run、备注 |
| GET | `/api/review-factors/metrics` ★ | 影子期汇总，`days` 默认 20、范围 1～250；缺数/不适用与表现样本分开 |

### 老师观点（`routes/crud.py`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/teachers` | 列出所有老师 |
| GET | `/api/teacher-notes` | 查询笔记列表（keyword/teacher/from/to；limit 默认 200、最大 500；offset 分页）。**列表为减负不返回 `raw_content` 全文**，仅给 `has_raw_content` 布尔 + `raw_content_preview`（前 200 字）；需全文走详情端点 |
| GET | `/api/teacher-notes/{note_id}` | 读取单条笔记（含 `raw_content` 全文） |
| POST | `/api/teacher-notes` | 新建笔记（含 teacher_name 自动创建老师）；自动来源须传完整六字段 provenance bundle、原文与 `input_by`，按文章 ID / URL / 内容 fallback 幂等返回已有 ID；可选 `sync_watchlist_from_mentions: true` 同步关注池（默认不同步，派生关注项继承同一 `input_by`） |
| PUT | `/api/teacher-notes/{note_id}` | 更新笔记 |
| DELETE | `/api/teacher-notes/{note_id}` | 删除笔记 |

### 持仓池（`routes/crud.py`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/holdings` | 列出持仓（?status=active/closed/all） |
| GET | `/api/holdings/{hid}` | 读取单条持仓 |
| POST | `/api/holdings` | 新建/更新持仓（upsert；`input_by` 缺省 `web`） |
| PUT | `/api/holdings/{hid}` | 更新持仓字段（`input_by` 缺省 `web`；**禁改 `status`**——含 `status` 返回 422；closed 行不可变返回 409） |
| DELETE | `/api/holdings/{hid}` | **soft close**：置 `status='closed'` 保留行与 `input_by` 审计，不物理删除；重新开仓走 POST 新行 |

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

`scripts/tests/test_cli_smoke.py` 会验证上表中所有 **`db` 子命令**（`ALL_SKILL_COMMANDS`）与 `main.py` 顶层架构命令（`ARCHITECTURE_COMMANDS`：`pre` / `post` / `schedule` / `review factor-*` / `ingest` / `plan` / `knowledge` / `executions` / `recommend` / `volume-watch` / `new-high` / `sector-correlation` / `sector-crowding` / `market-timing` / `margin-index-correlation` / `research-digest` / `earnings-digest` / `cognition-digest` / `trend-leader` / `string-yang` / `daily-leaders` / `board-break` / `ma-breakout` / `tail-scan` / `wechat-teacher-feed`）的 argparse 签名（不启动子进程、不连库）。Makefile 对显式盘前/盘后 `DATE` 的传递另由 `test_makefile_market_targets.py` 验证。

每次 `pytest scripts/tests/test_cli_smoke.py` 都会同步检查：
- 依赖表所列 `db` 子命令名未被重命名
- 必需参数未被删除或改名
- choices 集合未缩减

## 变更流程

1. 修改 `cli.py` 或 API routes 时，同步更新此 INDEX.md
2. 优先运行 `make check-scripts`；若仅需检查 CLI 签名，可运行 `python3 -m pytest scripts/tests/test_cli_smoke.py -v`
3. 若命令参数有不向后兼容的变更，更新对应 SKILL.md 中的示例
4. 修改 `scripts/main.py` 新增/调整顶层命令（`pre` / `post` / `schedule` / `review factor-*` / `ingest` / `plan` / `knowledge` / `executions` / `recommend` / `volume-watch` / `new-high` / `sector-correlation` / `sector-crowding` / `market-timing` / `margin-index-correlation` / `*-digest` / `trend-leader` / `string-yang` / `daily-leaders` / `board-break` / `ma-breakout` / `tail-scan` 等）时，须在 `test_cli_smoke.py` 的 `ARCHITECTURE_COMMANDS` 加参数化用例，并同步更新相关 SKILL.md 与 AGENTS.md（见 `.agents/rules/skills-sync.md` §2.1）
