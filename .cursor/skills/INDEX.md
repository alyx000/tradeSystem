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
| `record-notes` / `portfolio-manager` | `db watchlist-sync-from-note` | 按笔记 `mentioned_stocks` 写入关注池（两步确认后的第二步） |
| `record-notes` | `db add-industry` | 录入行业板块信息 |
| `record-notes` | `db add-macro` | 录入宏观经济信息 |
| `knowledge-to-plan` | `knowledge add-note` | 写入 `knowledge_assets`（新闻/课程/手动；不含老师观点） |
| `knowledge-to-plan` | `knowledge list` | 列出资料资产（不含 `teacher_note` 类型） |
| `knowledge-to-plan` | `knowledge draft-from-asset` | 从资料生成 `knowledge_asset` observation 和 `TradeDraft` |
| `knowledge-to-plan` | `knowledge draft-from-teacher-note` | 从 `teacher_notes` 生成 observation 和 `TradeDraft` |
| `knowledge-to-plan` | `db add-note` | 老师观点唯一事实源（与 record-notes 共用） |
| `portfolio-manager` | `db holdings-add` | 新增持仓 |
| `portfolio-manager` | `db holdings-remove` | 移除持仓（置 closed） |
| `portfolio-manager` | `db holdings-list` | 列出当前持仓 |
| `portfolio-manager` | `db holdings-refresh` | 回填持仓现价与技术快照（需数据源） |
| `portfolio-manager` | `db holdings-import-yaml` | 将 `tracking/holdings.yaml` 导入 SQLite |
| `portfolio-manager` | `db watchlist-add` | 添加到关注池 |
| `portfolio-manager` | `db watchlist-remove` | 从关注池移除 |
| `portfolio-manager` | `db watchlist-update` | 更新关注池标的 |
| `portfolio-manager` | `db watchlist-list` | 列出关注池 |
| `portfolio-manager` | `db add-trade` | 录入交易记录 |
| `portfolio-manager` | `db blacklist-add` | 加入黑名单 |
| `daily-review` | `db query-notes` | 搜索老师笔记（用于复盘预填充） |
| `sector-projection-analysis` | `db query-notes` | 搜索老师笔记，补充板块逻辑与老师视角 |
| `daily-review` | `db db-search` | 跨表关键词搜索 |
| `market-tasks` | `python main.py pre --date` | 盘前任务采集 |
| `market-tasks` | `python main.py post --date` | 盘后任务采集 |
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
| `daily-review` | `db add-calendar` | 手动录入投资日历事件（节假日/财经/财报等） |
| *(管理)* | `db init` | 初始化数据库 + 导入历史 YAML |
| *(管理)* | `db sync` | 重试 pending_writes 中的失败记录 |
| *(管理)* | `db reconcile` | 对账：YAML 与 DB 数据一致性比对 |

## API 端点依赖表

当前 skills 直接引用的端点：

| Skill | API 端点 | 方法 | 说明 |
|-------|---------|------|------|
| `daily-review` / `sector-projection-analysis` | `/api/review/{date}/prefill` | GET | 拉取八步复盘预填充数据（含板块候选；返回 `emotion_leader` / `capacity_leader`，`lead_stock` 仅作兼容） |
| `daily-review` | `/api/review/{date}` | GET | 读取已保存的复盘内容 |
| `daily-review` | `/api/review/{date}` | PUT | 提交复盘主观判断 |
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
| `sector-projection-analysis` | [sector-projection-analysis/references/methodology.md](sector-projection-analysis/references/methodology.md) | 《0524板块推演术》提炼后的板块推演方法论 |

`repo-maintenance-workflows` 不绑定固定业务 API；它会按受影响的 CLI / API / skill / 文档入口就近检查，并在修改 `scripts/main.py`、`scripts/api/routes/*.py`、`.cursor/skills/**/*.md` 后强制同步 `INDEX.md` 与 `skills-sync.mdc`。

## 可用 API 总览（供开发新 Skill 参考）

所有端点由 FastAPI 自动生成文档，启动后可访问 `http://localhost:8000/docs`。
下表为静态索引，方便离线查阅。标注 `★` 的端点已被现有 skill 引用。

### 复盘（`routes/review.py`，前缀 `/api/review`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/review/{date}` ★ | 读取指定日期复盘（含 exists 标志） |
| GET | `/api/review/{date}/prefill` ★ | 预填充数据（行情+笔记+持仓+日历） |
| PUT | `/api/review/{date}` ★ | 保存/更新复盘主观判断 |
| POST | `/api/review/{date}/to-draft` ★ | 将已保存复盘转换为 `review` observation / `TradeDraft` |

### 老师观点（`routes/crud.py`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/teachers` | 列出所有老师 |
| GET | `/api/teacher-notes` | 查询笔记列表（keyword/teacher/from/to；limit 默认 200、最大 500；offset 分页） |
| GET | `/api/teacher-notes/{note_id}` | 读取单条笔记 |
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

`scripts/tests/test_cli_smoke.py` 会验证上表中所有 **`db` 子命令**与架构命令 `ingest` / `plan` / `knowledge` 的 argparse 签名（不启动子进程、不连库）。`main.py pre` / `post` 仍由 `market-tasks` 文档与人工/定时流程保证。

每次 `pytest scripts/tests/test_cli_smoke.py` 都会同步检查：
- 依赖表所列 `db` 子命令名未被重命名
- 必需参数未被删除或改名
- choices 集合未缩减

## 变更流程

1. 修改 `cli.py` 或 API routes 时，同步更新此 INDEX.md
2. 优先运行 `make check-scripts`；若仅需检查 CLI 签名，可运行 `python3 -m pytest scripts/tests/test_cli_smoke.py -v`
3. 若命令参数有不向后兼容的变更，更新对应 SKILL.md 中的示例
4. 修改 `scripts/main.py` 新增/调整 `ingest`、`plan`、`knowledge` 命令时，同步更新相关 SKILL.md 与 AGENTS.md
