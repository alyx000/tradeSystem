# 命令索引

> 本文件由 `python3 scripts/generate_command_index.py` 自动生成，请勿手改。

统一入口优先使用仓库根目录的 `make` 目标；更底层的 `python3 main.py ...` 与 API/URL 入口保留给调试和细粒度控制。

## 每日高频

| 命令 | 用途 |
|------|------|
| `make bootstrap` | 首次安装依赖并启用本地 hooks |
| `make doctor` | 检查 Python / Node / .env / hooksPath |
| `make check` | 执行命令索引校验 + 前后端完整检查 |
| `make dev` | 同时启动 FastAPI 与 Vite 开发服务 |
| `make today-open` | 执行今日盘前流程 |
| `make today-close` | 执行今日盘后流程 |
| `make market-open DATE=YYYY-MM-DD` | 打开指定日期市场看板 |
| `make review-open DATE=YYYY-MM-DD` | 打开指定日期复盘工作台 |
| `make plan-open DATE=YYYY-MM-DD` | 打开指定日期计划工作台 |
| `make today-ingest-inspect` | 查看今日采集审计 |
| `make today-ingest-health` | 查看今日采集健康摘要 |

## 环境与检查

| 命令 | 说明 |
|------|------|
| `make bootstrap` | install deps and repo-local hooks |
| `make doctor` | print local toolchain and env status |
| `make check` | run web + scripts checks |
| `make check-web` | run web checks only |
| `make check-scripts` | run scripts checks only |
| `make hooks-install` | enable repo-local git hooks |

## 开发与页面

| 命令 | 说明 |
|------|------|
| `make dev` | run api + web dev servers |
| `make dev-api` | run api dev server only |
| `make dev-web` | run web dev server only |
| `make dashboard-open` | open dashboard in browser |
| `make search-open` | open search center in browser |
| `make market-open` | open market overview in browser |
| `make review-open` | open review workbench in browser |
| `make plan-open` | open plan workbench in browser |
| `make knowledge-open` | open knowledge workbench in browser |
| `make ingest-open` | open ingest workbench in browser |
| `make teachers-open` | open teacher notes page in browser |
| `make holdings-open` | open holdings page in browser |
| `make watchlist-open` | open watchlist page in browser |
| `make calendar-open` | open calendar page in browser |
| `make industry-open` | open industry page in browser |

## 数据与查询

| 命令 | 说明 |
|------|------|
| `make db-init` | initialize sqlite and import history |
| `make db-sync` | retry pending dual writes |
| `make db-reconcile` | reconcile YAML and DB |
| `make holdings` | list active holdings |
| `make watchlist` | list watchlist items |
| `make notes-search` | search teacher notes (requires KEYWORD) |
| `make db-search` | cross-table db search (requires KEYWORD) |
| `make market-json` | fetch market summary JSON for a date |
| `make market-envelope` | fetch post-market envelope JSON for a date |
| `make review-prefill` | fetch review prefill JSON for a date |

## 采集与计划

| 命令 | 说明 |
|------|------|
| `make ingest-list` | list ingest interfaces |
| `make ingest-run-post` | run post_core ingest for today |
| `make ingest-run-interface` | run one ingest interface (requires NAME) |
| `make ingest-inspect` | inspect ingest audit (DATE optional) |
| `make ingest-health` | show recent ingest health summary |
| `make wechat-teacher-should-run` | check strict trading-calendar phase gate (PHASE=) |
| `make wechat-teacher-doctor` | verify local WeRSS credentials and exact whitelist |
| `make wechat-teacher-collect` | archive one WeRSS phase (PHASE= INPUT_BY=) |
| `make wechat-teacher-show` | show unrecorded teacher-note candidates (DATE optional) |
| `make plan-draft` | create today's minimal trade draft |
| `make plan-show-draft` | show today's draft |
| `make plan-confirm` | confirm draft into plan (requires DRAFT_ID) |
| `make plan-diagnose` | example diagnose command (requires PLAN_ID) |
| `make plan-review` | review plan outcome (requires PLAN_ID) |
| `make knowledge-list` | list knowledge assets |
| `make knowledge-add-note` | example add-note command |
| `make knowledge-draft-from-asset` | example draft-from-asset command |

## 日常流程

| 命令 | 说明 |
|------|------|
| `make pre` | run pre-market report for today |
| `make post` | run post-market report for today |
| `make today-open` | alias for today's pre-market flow |
| `make today-close` | alias for today's post-market flow |
| `make today-pre` | run today's pre-market flow |
| `make today-post` | run today's post-market flow |
| `make today-evening` | run today's evening flow |
| `make today-watchlist` | run today's watchlist flow |
| `make today-obsidian` | export today's obsidian notes |
| `make today-ingest-inspect` | inspect today's ingest runs |
| `make today-ingest-health` | show today's 7-day ingest health summary |

## 未分类

| 命令 | 说明 |
|------|------|
| `make commands-check` | verify docs/commands.md is up to date |
| `make commands-doc` | regenerate docs/commands.md from Makefile |
| `make commands-open` | open commands center in browser |
| `make holdings-refresh` | refresh sqlite holding quotes for a date |
| `make ingest-reconcile` | reconcile stale running ingest records |
| `make knowledge-draft-from-teacher-note` | draft from teacher_notes (NOTE_ID=) |
| `make recommend-trace` | 行业推荐日报带 Raindrop 埋点（dry-run，进 Workshop 看 trace） |
| `make today-regulatory` | run today's regulatory monitor ingest |
