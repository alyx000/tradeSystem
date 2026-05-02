# 领域分层与数据语义

## 领域分层（架构蓝图）

详细蓝图见 `docs/architecture/`（三件套，按需加载）：

- [01-system-blueprint.md](../../docs/architecture/01-system-blueprint.md)：C4 风格分层图 + 模块状态徽章
- [02-module-map.md](../../docs/architecture/02-module-map.md)：模块清单（含模块 ID、入口、依赖）
- [03-roadmap.md](../../docs/architecture/03-roadmap.md)：近期/中期/长期迭代路线

核心分层：

1. **采集底座层**：接口注册、参数策略、任务选择、采集执行、幂等写入、采集审计
2. **原始事实层**：`raw_interface_payloads`、`market_fact_snapshots`、`fact_entities`、`ingest_runs`、`ingest_errors`
3. **交易计划层**：`MarketObservation -> TradeDraft -> TradePlan -> PlanReview`
4. **协作入口层**：人工 Web/API/CLI；Agent CLI Only

## 交易计划层协作规范

### `MarketObservation`

`MarketObservation` 是“还未形成正式计划前的结构化观察输入”。

允许来源：

- `review`：盘后复盘自动生成
- `knowledge_asset`：从资料提炼触发生成
- `teacher_note`：由 `teacher_notes` 经老师笔记草稿接口生成
- `manual`：人工手工创建
- `system_prefill`：系统基于事实层自动预填充
- `agent_assisted`：AI/Agent 协作触发

### `TradeDraft`

`TradeDraft` 是结构化但可带歧义的交易草稿：

- 可以由多个 `MarketObservation` 聚合生成
- 可以包含候选检查项、歧义和缺失项
- 不能直接作为正式计划执行

### `TradePlan`

`TradePlan` 是正式次日计划：

- 必须由人工确认
- `fact_checks` 只允许保存确认后的客观条件
- 主观交易语义默认进入 `judgement_checks`

### `PlanReview`

`PlanReview` 是盘后对 `TradePlan` 的回写复盘，用于沉淀命中、偏差与失效原因。

## `watch_items` / `fact_checks` / `judgement_checks`

`watch_items` 是计划中的执行清单，每项必须说明：

- 观察对象
- 观察理由
- 触发条件
- 失效条件
- 客观检查项
- 主观判断项

`fact_checks`：

- 表示系统可自动验证的客观条件
- 最终由人确认
- 系统与 Agent 只生成候选项
- 当前已实现的诊断优先读取 `market_fact_snapshots`；事实快照不足时应返回 `missing_data`，而不是伪装为通过/失败
- CLI 侧 `plan diagnose` 可在缺快照时降级查询 provider，当前先覆盖个股均线、单日/五日涨跌幅、公告存在性等客观条件
- 对 `market_amount_gte_prev_day`、`sector_change_positive`、`sector_limit_up_count_gte`，当前优先复用 `daily_market` 与盘后信封里的板块扩展字段进行诊断

`judgement_checks`：

- 表示只能人工确认的判断项
- 如“诚意反包”“主线确认”“首阴价值”“带动性”等

严禁：

- 将 `[判断]` 伪装为 `[事实]`
- 将未确认的候选 `fact_checks` 直接当成正式计划写入

## 数据存储

系统采用 **SQLite + YAML 混合架构**：

- **SQLite**（`data/trade.db`）：所有结构化业务数据的主存储，支持跨日查询、全文搜索、多 Agent 并发读写（WAL 模式）
- **YAML**（`daily/`）：每日行情/复盘快照归档，双写期间同步写入 SQLite
- **YAML**（`config/` + `templates/`）：静态配置与报告模板，保持 Git 版本控制

| 存储位置 | 内容 |
|---------|------|
| `data/trade.db` | 每日行情、八步复盘、交易记录、老师观点、投资日历、持仓池、关注池、行业/宏观信息、情绪周期、主线跟踪 |
| `data/attachments/` | 老师观点相关图片等二进制附件 |
| `daily/YYYY-MM-DD/` | 盘前/盘后/复盘 YAML 快照归档 |
| `config/` + `templates/` | 静态配置与报告模板 |

## 数据采集架构

- Python 脚本在 `scripts/` 目录
- 数据源采用可插拔架构：tushare（主力，10000积分）+ akshare（免费补充）
- 自动降级：tushare 失败时自动切换到 akshare
- 采集阶段（`pre`/`post`/`evening`/`watchlist`/`check` 等）默认临时清除 `HTTP_PROXY`/`HTTPS_PROXY` 等，避免 Tushare 误走本机代理超时；推送前恢复，Discord 等仍可走代理。需采集也走代理时设置 `TRADESYSTEM_USE_HTTP_PROXY=1`
- 新数据源只需继承 `DataProvider` 基类并注册即可
- 数据库模块 `scripts/db/`：连接管理（`connection.py`）、Schema 定义（`schema.py`）、查询封装（`queries.py`）、版本迁移（`migrate.py`）、双写支持（`dual_write.py`）
- 双写模式：Collector 写完 YAML 后同步插入 SQLite，DB 写失败不影响 YAML，失败记录自动重试

## 本地 Web 应用

- **后端**：FastAPI（`scripts/api/`），与现有 Python 脚本共享代码，自动生成 OpenAPI 文档
- **前端**：React + Vite + Tailwind CSS（`web/`），TanStack Query 管理服务端状态
- **启动**：后端 `cd scripts && uvicorn api.main:app --reload --port 8000`，前端 `cd web && npm run dev`
- **核心页面**：仪表盘（`/`）、市场数据看板（`/market/:date`，摘要看板 + **盘后信封**整包 JSON、历史日期下拉、指数/涨跌停/板块/趋势图）、八步复盘工作台（`/review/:date`，支持预填充 + localStorage 草稿）、信息查询中心（`/search`，跨实体聚合 + Markdown 导出）、老师观点、持仓池、关注池、投资日历

## AI Agent 标准化写入接口

OpenClaw/Copaw 通过 CLI 命令写入数据库：

```bash
python3 main.py db add-note --teacher "小鲍" --date 2026-04-01 --title "..." --input-by openclaw
python3 main.py db query-notes --keyword "锂电"
python3 main.py db watchlist-add --code 300750 --name "宁德时代" --tier tier1_core
python3 main.py db init          # 初始化数据库 + 导入历史 YAML
python3 main.py db sync          # 重试失败的写入
python3 main.py db reconcile     # YAML 与 DB 对账
```

## 数据质量规范

每条信息必须标注：

- **类型**：`[事实]` / `[判断]` / `[传闻]` / `[观点]`
- **置信度**：`★★★`(高) / `★★☆`(中) / `★☆☆`(低)
- **时效性**：`[实时]` / `[近期]` / `[滞后]` / `[历史]`
- **来源**：具体数据源名称

自动采集的行情数据标注为 `[事实] ★★★ [实时]`。
新闻搜索结果需要区分事实与判断，标注置信度。
AI 自身的分析结论标注为 `[判断]`，并注明依据。

## 数据分层

| 层级 | 方式 | 内容 |
|------|------|------|
| 全自动 | Python脚本定时运行 | 指数、成交额、涨跌停、板块排名、个股K线、北向资金、龙虎榜 |
| 半自动 | AI联网搜索+用户确认 | 新闻政策、互动易、研报、调研 |
| 纯手动 | 用户判断 | 情绪定性、最票识别、三位一体结论、买卖决策 |
