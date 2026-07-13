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
- `python3 main.py volume-watch ...`（成交额 Top20 板块集中度：`daily` 采集+落库+渲染+钉钉推送 / `trend` 只读趋势；申万二级口径联动 `get_sector_rankings`，落 `daily_volume_concentration`；`daily` 报告额外含**成交额前50 区间涨幅排名**[独立取前50→`get_stock_daily_range` 算 5/10/20 日涨幅→**申万二级板块榜** + **同花顺概念题材榜**(多标签，复用 `get_ths_member` 反查 + 容器≤300 过滤，concepts 落 `gain_universe_json`)，组按组内涨幅最大个股降序/平手比次大，三档独立榜，全 [事实] 守红线]，经只读 API `/api/market/sector-gain-ranking/{date}`(`rankings`+`concept_rankings`) 在八步复盘「2.板块」双维度展示）
- `python3 main.py sector-correlation ...`（板块相关性：`daily` 采集+落库+渲染+钉钉 / `matrix` 完整矩阵只读 / `trend` 漂移趋势；Tushare 主源多日活跃选板块[行业成交额 / 概念换手率]+4 指数，多窗 5/20/60 原始相关+剔大盘超额相关+β，落 `sector_correlation_daily`）
- `python3 main.py market-timing daily|signals ...`（大盘择时观察：6 指数[上证/深成/创业板/科创50/中证2000(微盘股代理)/平均股价(通达信880003 经 pytdx 日线)] 斐波那契时间周期变盘点[双向 swing 拐点起算，命中 5/8/13/21/34/55，多指数同日共振增强] + 底分型生命周期[三K结构 none/forming/confirmed(放量中阳突破前高)/invalid，无状态从 bars 推导抗漏跑] + 市场级客观上下文[两市成交额近20日地量分位/跌停家数/涨跌家数] → 落 `market_timing_signal`[PK(trade_date,index_code) 重跑 refreshed] + MD 只读观察清单 + 钉钉；全标 [判断] 守红线[不预判方向/不出价位/不给买卖建议]；daily 三档=裸[落库+推]/`--no-push`[落库+打印]/`--dry-run`[内存不落不推，历史校准]，`--pivot-index`+`--pivot-date` 手工 swing 覆盖[D3 hybrid，未知指数/非法日期/日期不在窗口 fail-fast]，`signals` 只读看池[`--date`/`--index`/`--json`]；工作日+周日 21:40 per-task launchd[接 trend-leader 21:30 之后]，不进 `schedule`/APScheduler）
- `python3 main.py margin-index-correlation daily|signals ...`（两融余额与指数联动性：新增 `get_margin_series` 取两融区间序列[Tushare `pro.margin` 主源沪深北三市合计+分项，复用完整性逻辑只留应到交易所齐全的完整日 / akshare 官网降级仅沪深、新到旧迭代封顶防宕机时上百串行请求]，两融余额转**日变化率(%)** 后与指数 `pct_chg` 同口径做四维：① 背离预警[头条，近5/20日复利累计指数涨两融降/指数跌两融升，**指数交易日脊柱锁窗**两融缺日标「日期缺口」防稀疏日伪造] ② 余额水位+趋势[绝对值/日环比/近20日分位/连增连降/偏离MA20] ③ 领先/滞后[lagged corr，`lag>0`=两融滞后指数] ④ 同步相关[5/20/60窗 Pearson 复用 sector aggregator]；对照 total两融×多宽基(上证/创业板/沪深300/科创50)+沪市两融×上证+深市两融×深成 → 落 `margin_index_correlation_daily` + 钉钉；全标 [判断] 守红线[不出价位/不给买卖建议/不写计划层]；daily 三档=裸[落库+推]/`--no-push`[落库+打印]/`--dry-run`[内存不落不推]，非交易日守卫仅 persist 时；`signals` 只读最近 N 日[`--date`/`--days`/`--json`]；**随 `main.py post` 盘后采集一并执行**(折进 cmd_post 末尾、失败隔离不影响主流程；不单独挂 launchd、不进 `schedule`/APScheduler；cmd_post 工作日 20:00 触发，两融交易所盘后发布滞后故多为 T-1，报告标注 stale)；经只读 API `/api/market/margin-index-correlation/{date}`(`web_payload.build_daily_payload`) 在八步复盘「1.大盘」`MarginIndexCorrelation` 组件渲染四维；CLI `daily --date` 可手动补采/校准）
- `python3 main.py research-digest daily ...`（每日研报速读：A股研报评级[巨潮 cninfo `get_research_report_list`] + 美股机构评级[yfinance `upgrades_downgrades`，仅方向变动 init/up/down/reinit] → 鞠磊框架「首次覆盖」加权 Top3 → MD 落盘 + 钉钉；`--dry-run` 仅打印、`--no-llm` 关美股叙事；红线只约束 LLM 叙事不约束取数；生产定时入口已迁移到 Codex 自动化「每日慧博研报速读（Computer Use）」每天 22:00 触发，先用 Computer Use 读取慧博终端当前 HotReport URL 并在慧博终端下载候选 PDF 到本地目录，再运行 JS workflow 读取本地 PDF；旧 `com.alyx.tradesystem.research-digest` launchd 已停用，避免绕过 Computer Use；`trend` 子命令=研报覆盖·申万一级行业趋势[数据底座 `raw_interface_payloads.research_report_list` 随 `cmd_post` post_extended 每日落库，近N有效日 vs 前N有效日占比Δpp、份额口径免疫月末脉冲、`status='empty'` 真空日不进分母，`--backfill` 幂等回补缺失/`empty` 日(完成态=非空 success,迟到回填自动吃进)；全 [事实] 计数，复盘「2.板块」可引用]）
- `python3 main.py earnings-digest daily ...`（业绩预告/快报速报：全市场 `forecast_vip`/`express_vip` 按公告日回看窗口[默认3自然日]采集落 `raw_interface_payloads` + 水位线增量[只认 success] + 次日缺口验证[下一交易日开盘跳空≥2% 触发，**市场投票方向取收盘涨跌**(收盘才是市场对预告的真实一票，高开低走自动翻为利好不及预期；收平昨收=中性)，2×2] + 五段渲染[命中/缺口/申万行业Top5/分类计数/净利中值≥5000万Top榜]+口径三券商一致预期[全年预测×H1占比折算,标 [判断]] → MD 落盘 + 钉钉；空窗口日不推送；`--dry-run` 仅打印[采集落库照常]、`--lookback-days` 手动补采、`--no-consensus` 关一致预期；工作日+周日 22:00 launchd 单源调度，不进 `schedule`/APScheduler）
- `python3 main.py cognition-digest recent3d|weekly|monthly ...`（交易认知沉淀只读汇总：只读认知三表[`trading_cognitions`/`cognition_instances`]按窗口算热度+共识+新增 Top-N + gemini 体系/方向建议[复用 gemini runner + `REDLINE_KEYWORDS` 红线护栏] → 钉钉；`--dry-run` 仅打印、`--no-llm` 模板兜底；3 个 per-task launchd[recent3d 日 18:30 / weekly 周日 20:00 / monthly 每月 1 号 09:00]，不写库不改 schema 不进 `schedule`/APScheduler）
- `python3 main.py trend-leader daily|pool ...`（趋势主升漏斗扫描，对齐鞠磊：候选=当日涨停[`get_limit_up_list`]∪双创(20cm)涨幅≥15%加速[`get_market_daily_changes`，board-aware「20cm涨15%+」=GAP A] ∩ 主线板块[`daily_volume_concentration` Top-K 申万二级 ∪ `--sectors` ∪ 同花顺概念净流入 Top-M(`get_concept_moneyflow_ths`+`get_ths_member`，`--top-concepts`默认8；trend-leader 只查资金流前排有限概念成员，默认预取 max(40, Top-M*5)，成员数≤300剔容器概念=GAP B)]；默认 `--main-line hybrid` 会用 LLM 只过滤同花顺概念分支[不新增事实/不否决申万Top-K/不做买卖建议，异常或红线命中降级确定性概念分支]，`--no-llm` 关闭 LLM，`--main-line l2` 保留旧纯申万口径，`--main-line l2+concept` 为机械概念分支 → 区间 OHLCV[`get_stock_daily_range`] → 首次加速(board-aware)+主线缓涨入池、缩量回踩/贴MA5/乖离信号、趋势破坏[跌破MA10/连破MA5]退池，落 `trend_leader_pool` 状态机[派生信号层，池内身份=裸码归一] → 渲染盘后只读观察清单[全标 [判断]、守红线不出价位/不给买卖建议/不写计划层；触发列分涨停/双创15%加速，概念分支票标「二级·分支:概念名」] + 钉钉；`daily` 三档=裸[落池+推]/`--no-push`[落池+仅打印]/`--dry-run`[内存副本跑不落池不推，历史校准]，`pool` 只读看池[`--status`/`--json`]；同日重跑/推送失败重试 refreshed 仍合并展示不丢；`--top-k`/`--top-concepts` 须正整数；工作日 21:30 per-task launchd[接 volume-watch 21:00 之后]，不进 `schedule`/APScheduler）
- `python3 main.py string-yang daily ...`（主线板块串阳首阴股票池：主线判断=成交额集中度[`daily_volume_concentration` Top-K 申万二级] + 同花顺概念分支[`get_concept_moneyflow_ths`+`get_ths_member`，成员数≤300剔容器] + 近 N 日老师观点[`teacher_notes`] → LLM 只裁决主线申万二级/概念分支，不选股、不生成买卖建议，失败或无有效裁决降级成交额 Top-K；候选=申万二级∈主线 或 概念∩主线概念 → 区间 OHLCV[`get_stock_daily_range`] → 排除 ST/退市风险 → 只筛“昨日以前连续≥5根阳线、串阳段无涨停且最大单日涨幅≤7%、最近20个交易日无涨停、首阴收盘价/MA60≤1.08、今日出现第一根放量阴线[今日成交额>前5个交易日最大成交额]”的确认票，不输出尚未出阴线的预备池；概念分支票标「申万二级·分支:概念名」；按今日成交额/前5日最大成交额排序，MD 落 `data/reports/string-yang/YYYY-MM-DD.md` + 钉钉；`--no-llm` 强制降级成交额 Top-K，`--top-concepts`/`--teacher-lookback-days` 控制证据窗口；全标 [判断] 守红线[不出价位/不给买卖建议/不写计划层/不入关注池]；三档=裸[落报告+推]/`--no-push`[落报告不推]/`--dry-run`[仅打印不落不推]；工作日 21:50 per-task launchd[接 market-timing 21:40 之后]，不进 `schedule`/APScheduler）
- `python3 main.py daily-leaders propose|show|confirm ...`（每日最票候选确认流：`propose` 汇总复盘预填、趋势池、历史最票、老师观点与认知证据，生成 `data/reports/daily-leaders/` Markdown/JSON 确认稿；`--push` 推送钉钉 Markdown 草稿；`show` 只读查看；`confirm --date ... --input-by ...` 经用户确认后写入复盘第 5 步并同步 `leader_tracking`。v1 仅支持 DingTalk Markdown + Codex/CLI 确认，钉钉按钮 callback/直接写回 deferred 到 v2；全程守红线，不给买卖建议、不出价位；工作日 22:30 per-task launchd[接 post-market 派生任务之后]，不进 `schedule`/APScheduler）
- `python3 main.py board-break daily ...`（断板反包观察清单：昨日连板≥2 断板[≤6%未跌停，10cm 主板剔 ST] → 八维度加权打分[主线/增减持(减持按250日分位翻极性)/定增/公告/业绩/近10日涨幅/MACD，全 [判断] 附依据明细] + LLM 两两 PK 循环赛[熔断/红线过滤，`--no-llm` 关] → 双排序 MD 落盘 `data/reports/board-break/` + 钉钉；三档 裸/`--no-push`/`--dry-run` + `--date`；`source_failed` 落失败报告+推告警+非零退出；无池无状态，隔日交易归用户；工作日 21:20 per-task launchd[volume-watch 21:00/sector-correlation 21:15 之后、trend-leader 21:30 之前]，不进 `schedule`/APScheduler）
- `python3 main.py ma-breakout daily ...`（4日均线二波观察池：先从 `leader_tracking` 取目标日前近端、复盘第 5 步人工确认的历史龙头/最票宇宙[默认近 60 自然日，可用 `--leader-lookback-days` 调整，过久远龙头剔除；`trend_leader_pool` 自动趋势池不作为默认龙头来源] → 再用 `get_market_daily_quotes` 近 10 个有效行情日组装个股序列 → MA4 重新拐头向上[今日 MA4 > 昨日 MA4，且昨日 MA4 < 前日 MA4 < 前两日 MA4，要求上拐前至少两根 MA4 连续下行] + 今日成交额同时突破 5/10 日成交额均线 + 当日未涨停 → 按今日成交额降序渲染盘后只读观察清单[全标 [判断]、守红线不出价位/不给买卖建议/不写计划层] + 钉钉；`--windows` 可改双均量线周期，默认 `5,10`；`--dry-run`/`--no-push` 仅打印，`--json` 结构化输出且不推送；不写交易计划/关注池；工作日 21:35 per-task launchd[接 trend-leader 21:30 后、market-timing 21:40 前]，不进 `schedule`/APScheduler）
- `python3 main.py tail-scan daily ...`（盘中尾盘强势股扫描：14:40 单次快照全市场实时行情[`get_realtime_quotes`，单点脆弱源失败重试一次仍失败 → `source_failed`] → 三条件筛选[涨幅>7% ∩ 非ST ∩ 成交额>20亿，全 [事实]，阈值可用 `--min-pct`/`--min-amount` 调] → 四维事实卡[逻辑:T-1主线申万二级Top-K+同花顺概念资金流T-1Top-M+老师观点命中 / 三位一体:候选池涨幅名次+指数背景 / 节奏:近5日涨幅/MA上方/连涨天数/半日放量追平昨日全日节奏代理`first_surge` / 节点:距前高/是否破前高，单维度取数失败只降级不中断整批] + 产业逻辑增强[主营:Tushare `stock_company` 主源/AkShare `stock_zyjs_ths` 补缺，为扫描时当前公开静态资料、非历史 as-of，摘要优先级=`main_business`>`introduction`>`business_scope`；产业链位置仅基于申万二级+主营摘要+产品受控归纳；近30自然日催化只读 `teacher_notes` 精确代码/慧博精确名称/`industry_info` 基于申万二级/主营摘要/产品/已验证概念标签受控匹配，证据按 [事实]/[老师观点]/[研报观点]/[来源陈述] 分层，失败仅降级对应维度] → 粗权重分仅用于PK强池截断[`PK_POOL_MAX=12`]与排序破平[不进PK prompt] + LLM 两两 PK 循环赛[180s预算熔断/无效场率>50%熔断/红线过滤，`--no-llm` 关，候选<2只自走`status=skipped`] → 渲染 MD[排序为 [判断]，每票显示 `[事实·主营]`/`[判断·产业链位置]`/近期催化分层标签，含数据时效声明:实时快照 vs T-1逻辑/板块；本地 MD 全量，钉钉超长时≤18000 UTF-8 bytes 且最多展示前12个完整候选块并附完整报告路径] 落盘 `data/reports/tail-scan/` + 钉钉；三档 裸/`--no-push`/`--dry-run` + `--date`；`source_failed` 落失败报告+推告警+非零退出；无池无状态，不写交易计划/关注池；工作日 14:40 per-task launchd[`com.alyx.tradesystem.tail-scan`]，不进 `schedule`/APScheduler；休眠期间不触发[需盘中 mac 唤醒]）

## 规则与模板入口

### AI 协作规则（真源 `.agents/rules/`）


| 规则文件                      | 作用                                                                    |
| ------------------------- | --------------------------------------------------------------------- |
| `language.md`             | 所有 AI 输出使用简体中文，代码标识符保持英文                                              |
| `karpathy-behavior.md`    | 行为基线：先校验假设、简洁优先、精准修改、目标驱动验证，减少 Agent 常见失误                            |
| `dev-workflow.md`         | 开发三阶段流程：设计验证方案 → 实现（含单测）→ 执行验证并报告                                     |
| `implementation-plan.md`  | 实施计划必须含测试验证方案 + 复杂任务多 Agent 并行分组                                      |
| `solution-format.md`      | 技术方案 / 执行计划 / 业务逻辑解析默认使用结构化章节、表格与纯 Mermaid 图表输出                       |
| `test-design.md`          | 分层测试设计：金字塔原则、隔离原则、自底向上执行                                              |
| `code-review-gate.md` | 每轮实质性代码改动后先 `/simplify` 清理 → `/code-review`（门1，默认 medium，替代旧本地 Explore）；4 条结束条件 + 软上限 2 轮                                  |
| `post-dev-codex-review.md` | 实质性代码改动后必须跑 codex 原生 adversarial-review 审查（方案级 codex 独立意见才走 codex:codex-rescue freeform；不替代 Explore CreatePlan 门）；6 条二值结束条件 + 3 轮上限防无限循环 |
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
