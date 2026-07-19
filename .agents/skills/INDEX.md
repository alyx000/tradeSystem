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
| `daily-review` / `sector-projection-analysis` | `python main.py review factor-score --date YYYY-MM-DD --input-by USER [--steps-file PATH] [--no-llm] [--retry-of-run-id RUN_ID] [--json]` | 三位一体双层影子评分：第 1 层固定四因子，程序选出主因子后把该因子的受控证据卡传给最多 6 个 `core` 板块做第 2 层评分；`style_regime` 独立客观来源族=指数相对强弱、10/20/30cm 板型混合、已实现溢价；`leader_signal` 独立客观来源族=剔 ST 连板梯队、晋级实现、前高标回验，其中 `promotion_realization` 当前只保证 `promotion.trade_date=评分日`，`prior_core_feedback` 来源日必须等于 `trade_calendar` 严格上一开放日（不得以 `prev_market` 最近行情替代），并优先显式 `popularity_provenance`（键存在但非法 / 错位即拒绝且不 fallback），仅 provenance 键缺失的历史数据可用同一 `style_factors.promotion.prev_date / trade_date` 兼容 fallback；来源日期缺失或错位、Step 5 人工最票与自动 leader 名称都不得抬高质量，后两者仅作 `[判断]` context。规则门/证据质量/总分由程序控制，LLM 分仅表示相对重要度，不是概率、胜率或建议。只接受开放交易日；每次请求追加 `daily_review_factor_score_requests`，cache hit 也记录当前 `input_by`，但请求者不参与 cache key、旧 run diagnostics 不改；显式 retry 追加子 run；不生成 `TradeDraft` / `TradePlan` |
| `daily-review` | `python main.py review factor-confirm --date YYYY-MM-DD --run-id RUN_ID --decision-file PATH --input-by USER [--json]` | 人工确认系统建议：`accepted` / `overridden`（必须 `override_reason`，辅助因子最多 2 个）/ `undetermined`；在原子事务内按数据库当前预填与第 1～6 步重建证据摘要，若与 run 不同则必须先保存步骤并重跑评分；写 `daily_reviews.step8_plan.factor_decision` 并镜像 `key_factor / secondary_factors`，保留第 8 步其余字段；后续实际改写第 1～6 步会自动清除旧决定；不写计划层 |
| `daily-review` | `python main.py review factor-evaluate --date T_PLUS_1 --source-date SOURCE_DATE [--run-id RUN_ID] --outcome hit\|partial\|miss\|missing_data\|not_applicable --input-by USER [--note TEXT] [--json]` | 对来源日 run 做严格下一开放交易日 T+1 回验；程序按主因子用来源证据快照与次日客观事实做确定性方向/连续性比较，人工确认后写 evaluation；`missing_data` / `not_applicable` 不进入表现样本分母 |
| `daily-review` / `sector-projection-analysis` | `python main.py review factor-metrics [--days 20] [--json]` | 影子期指标：只统计开放交易日，每日优先人工决定引用的 canonical run（避免失败 retry 覆盖已确认父 run），汇总成功/非法输出/规则降级/覆盖、人工接受与改选、T+1 结果及分组；默认 20 日 |
| `market-tasks` | `python main.py pre [--date YYYY-MM-DD]` | 盘前真实采集：会覆盖归档并可推送，无 `--dry-run` / `--no-push`；详见 [契约](market-tasks/references/base-market-runs.md)。 |
| `market-tasks` | `python main.py post [--date YYYY-MM-DD]` | 盘后复合流程：含晚间任务、归档/落库/ingest/两融与多路推送，无安全预览档；详见 [契约](market-tasks/references/base-market-runs.md)。 |
| `market-tasks` | `python main.py schedule` | legacy 阻塞 APScheduler；会真实触发 pre/post/recommend，禁止与同功能 per-task launchd 并行；详见 [契约](market-tasks/references/base-market-runs.md)。 |
| `market-tasks` / `record-notes` | `python main.py wechat-teacher-feed should-run\|doctor\|collect\|show ...` | 公众号白名单采集；collect 只落来源包，确认前不写老师笔记或关注池；详见 [契约](market-tasks/references/ingestion-and-feeds.md)。 |
| `market-tasks` | `python main.py recommend daily [--lookback-days N] [--top-k K] [--dry-run]` | 行业推荐日报；详见 [契约](market-tasks/references/ingestion-and-feeds.md)。 |
| `market-tasks` | `python main.py recommend weekly [--lookback-days N] [--top-k K] [--dry-run]` | 行业推荐周报；详见 [契约](market-tasks/references/ingestion-and-feeds.md)。 |
| `market-tasks` | `python main.py volume-watch daily [--date YYYY-MM-DD] [--dry-run] [--no-push] [--refetch]` | 成交额集中度日报；保留裸命令、`--no-push`、`--dry-run` 三档；详见 [契约](market-tasks/references/market-observability.md)。 |
| `market-tasks` | `python main.py volume-watch trend [--date YYYY-MM-DD] [--days N]` | 成交额集中度只读趋势；详见 [契约](market-tasks/references/market-observability.md)。 |
| `market-tasks` | `python main.py new-high daily [--date YYYY-MM-DD] [--top-n 10] [--dry-run] [--push] [--json]` | 前复权历史新高日报；默认落库落报告、不推送；详见 [契约](market-tasks/references/market-observability.md)。 |
| `market-tasks` | `python main.py new-high trend [--date YYYY-MM-DD] [--days N] [--json]` | 前复权历史新高只读趋势；详见 [契约](market-tasks/references/market-observability.md)。 |
| `market-tasks` | `python main.py new-high backfill [--start-date YYYY-MM-DD] [--end-date YYYY-MM-DD] [--top-n 10] [--dry-run]` | 前复权历史水位回填；永不推送；详见 [契约](market-tasks/references/market-observability.md)。 |
| `market-tasks` | `python main.py sector-correlation daily [--date] [--windows 5,20,60] [--top-industries 15] [--top-concepts 10] [--activity-days 10] [--indices a,b] [--no-concept] [--dry-run]` | 板块相关性日报；详见 [契约](market-tasks/references/market-observability.md)。 |
| `market-tasks` | `python main.py sector-correlation matrix [--date] [--windows 5,20,60] [--top-industries N] [--top-concepts N] [--no-concept] [--refetch]` | 板块相关性完整矩阵只读输出；详见 [契约](market-tasks/references/market-observability.md)。 |
| `market-tasks` | `python main.py sector-correlation trend [--date] [--days N]` | 板块相关性漂移趋势；详见 [契约](market-tasks/references/market-observability.md)。 |
| `market-tasks` | `python main.py sector-crowding daily [--date] [--dry-run] [--push]` | 申万 L1/L2 拥挤度采集落 `sector_crowding_daily`；默认不推送，`--push` 才推钉钉，`--dry-run` 不落库不推送（豁免非交易日守卫）；详见 [契约](market-tasks/references/market-observability.md)。 |
| `market-tasks` | `python main.py sector-crowding report [--date]` | 只读：交易/斜率拥挤度 + 历史分位 + 双高清单（派生指标读取时现算，不落库）；详见 [契约](market-tasks/references/market-observability.md)。 |
| `market-tasks` | `python main.py sector-crowding trend --sector CODE [--date] [--days 60]` | 只读单板块拥挤度序列（建议用申万代码查，回填行无中文名）；详见 [契约](market-tasks/references/market-observability.md)。 |
| `market-tasks` | `python main.py sector-crowding backfill --start [--end]` | 一次性历史回填（fail-closed：码失败/空返回整体中止不落库，重跑即重试；单片恰 2000 行截断报错；默认 2019-01-01 起）；详见 [契约](market-tasks/references/market-observability.md)。 |
| `market-tasks` | `python main.py market-timing daily [--date] [--pivot-index CODE --pivot-date YYYY-MM-DD] [--no-push] [--dry-run]` | 大盘择时观察；全标 `[判断]`，三档运行；详见 [契约](market-tasks/references/market-observability.md)。 |
| `market-tasks` | `python main.py market-timing signals [--date] [--index CODE] [--limit N] [--json]` | 大盘择时信号只读查询；详见 [契约](market-tasks/references/market-observability.md)。 |
| `market-tasks` | `python main.py margin-index-correlation daily [--date] [--windows 5,20,60] [--divergence-windows 5,20] [--divergence-gap 0.5] [--max-lag 3] [--no-push] [--dry-run]` | 两融与指数联动日报；随盘后流程执行并显式标 stale；详见 [契约](market-tasks/references/market-observability.md)。 |
| `market-tasks` | `python main.py margin-index-correlation signals [--date] [--days 30] [--json]` | 两融与指数联动只读查询；详见 [契约](market-tasks/references/market-observability.md)。 |
| `market-tasks` | `python main.py research-digest daily [--date YYYY-MM-DD] [--dry-run] [--no-llm] [--huibo-mode desktop_terminal\|official_api\|off] [--huibo-window-days N] [--huibo-reader-cap N] [--huibo-reader-concurrency N] [--huibo-recommend-cap N] [--huibo-raw-retention-days N] [--huibo-summary-retention-days N] [--huibo-cleanup-only]` | 研报速读；生产自动化周日至周五 21:00 触发并先过 `should-run` 日历门禁；详见 [契约](market-tasks/references/research-and-digests.md)。 |
| `market-tasks` / `daily-review` | `python main.py research-digest trend [--days N] [--recent-n N] [--top N] [--backfill N] [--json]` | 研报覆盖行业趋势；默认只读，显式 `--backfill` 会幂等写入事实层；详见 [契约](market-tasks/references/research-and-digests.md)。 |
| `market-tasks` | `node scripts/workflows/research-digest-workflow.mjs daily [--date YYYY-MM-DD] [--reader-cap N] [--reader-concurrency N] [--reader-max-attempts N] [--llm-input-dir PATH] [--resume] [--retry-failed] [--preflight] [--no-aggregate-llm] [--publish] [--publish-dry-run] [--include-base-digest]` | 慧博本地 PDF 深读工作流；详见 [契约](market-tasks/references/research-and-digests.md)。 |
| `market-tasks` | `python main.py earnings-digest daily [--date YYYY-MM-DD] [--dry-run] [--lookback-days N] [--no-consensus]` | 业绩预告/快报摘要与次日缺口验证；`--dry-run` 仍采集落事实层，只是不落报告、不推送；详见 [契约](market-tasks/references/research-and-digests.md)。 |
| `market-tasks` | `python main.py cognition-digest recent3d\|weekly\|monthly [--date YYYY-MM-DD] [--dry-run] [--no-llm]` | 交易认知只读周期摘要；详见 [契约](market-tasks/references/research-and-digests.md)。 |
| `market-tasks` | `python main.py trend-leader daily [--date YYYY-MM-DD] [--sectors '["半导体",...]'] [--top-k N] [--main-line hybrid\|l2\|l2+concept] [--top-concepts M] [--no-llm] [--dry-run] [--no-push]` | 趋势主升漏斗扫描；裸命令与 `--no-push` 都会写观察池，`--dry-run` 才是不落池不推送；详见 [契约](market-tasks/references/stock-scanners.md)。 |
| `market-tasks` | `python main.py trend-leader pool [--status active\|exited] [--json]` | 趋势主升观察池只读查询；详见 [契约](market-tasks/references/stock-scanners.md)。 |
| `market-tasks` | `python main.py string-yang daily [--date YYYY-MM-DD] [--top-k N] [--top-concepts N] [--teacher-lookback-days N] [--no-llm] [--dry-run] [--no-push]` | 串阳首阴观察清单；详见 [契约](market-tasks/references/stock-scanners.md)。 |
| `daily-review` / `market-tasks` | `python main.py daily-leaders propose [--date YYYY-MM-DD] [--push] [--no-llm] [--max-candidates N]` / `python main.py daily-leaders show --date YYYY-MM-DD [--json]` / `python main.py daily-leaders confirm --date YYYY-MM-DD --input-by USER [--leaders-file PATH]` | 每日最票候选生成、查看与人工确认；`confirm` 经确认后写复盘第 5 步并同步 `leader_tracking`；详见 [契约](market-tasks/references/stock-scanners.md)。 |
| `market-tasks` | `python main.py board-break daily [--date YYYY-MM-DD] [--dry-run] [--no-push] [--no-llm]` | 断板反包观察清单；裸命令=落报告+推送，`--no-push`=落报告不推送，`--dry-run`=不落不推；详见 [契约](market-tasks/references/stock-scanners.md)。 |
| `market-tasks` | `python main.py tail-scan daily [--date YYYY-MM-DD] [--min-pct 7] [--min-amount 20] [--dry-run] [--no-push] [--no-llm]` | 尾盘强势股实时扫描；裸命令=落报告+推送，`--no-push`=落报告不推送，`--dry-run`=不落不推；详见 [契约](market-tasks/references/stock-scanners.md)。 |
| `market-tasks`（`tail-scan` 内部依赖） | Provider capability `get_stock_concept_memberships(ts_codes)`（非 CLI） | 尾盘扫描当前概念归属能力；状态与 T-1 热概念严格分层；详见 [契约](market-tasks/references/stock-scanners.md)。 |
| `market-tasks` | `python main.py ma-breakout daily [--date YYYY-MM-DD] [--windows 5,10] [--leader-lookback-days 60] [--top-n N] [--dry-run] [--no-push] [--json]` | 4 日均线二波观察清单；裸命令=落报告+推送，`--no-push`=落报告不推送，`--dry-run`/`--json`=不落不推；详见 [契约](market-tasks/references/stock-scanners.md)。 |
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
| `cognition-evolution` | [cognition-evolution/references/cognition-candidate-rules.md](cognition-evolution/references/cognition-candidate-rules.md) | 强候选最小标准、不建议落库条件、refine 默认动作与自检清单 |
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

开发新 skill 或核对未绑定端点时读取 [references/api-overview.md](references/api-overview.md)；现有 skill 的直接依赖仍以上方 CLI / API 表为准。

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
