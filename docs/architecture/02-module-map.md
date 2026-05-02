# 模块清单（Module Map）

> 本文档是**模块级**清单，回答「系统里有哪些模块、各自职责、入口在哪、依赖什么、当前状态」。
>
> - **总览拓扑** → 见 [01-system-blueprint.md](./01-system-blueprint.md)
> - **迭代时间轴** → 见 [03-roadmap.md](./03-roadmap.md)
>
> **维护规则**：新增模块必须先在本表登记并分配 **模块 ID**（如 `M-SVC-PLAN`），路线图条目必须引用 ID 才能立项。

## 模块 ID 命名规范

格式：`M-<领域>-<名称>`

| 前缀 | 领域 | 对应代码位置 |
|---|---|---|
| `M-PROV-` | 数据源 Provider | `scripts/providers/` |
| `M-COL-` | Collector 采集器 | `scripts/collectors/` |
| `M-ING-` | Ingest 治理 | `scripts/ingest/` |
| `M-DB-` | 数据库与存储 | `scripts/db/` |
| `M-SVC-` | 语义/服务层 | `scripts/services/` |
| `M-ANL-` | 分析器 | `scripts/analyzers/` |
| `M-GEN-` | 报告/导出生成器 | `scripts/generators/` |
| `M-PUSH-` | 推送通道 | `scripts/pushers/` |
| `M-API-` | HTTP API | `scripts/api/` |
| `M-CLI-` | CLI 命令 | `scripts/main.py` |
| `M-WEB-` | 前端工作台 | `web/src/` |
| `M-OPS-` | 运维/调度/可观测 | `scripts/launchd/`、`scripts/utils/`、外部 |
| `M-DOC-` | 规则/文档基础设施 | `.cursor/`、`docs/`、`AGENTS.md` |

状态徽章：`✅ 已建` / `🚧 在建` / `🟡 中期` / `🔵 长期` / `⚪ 占位`

---

## Provider 层

| ID | 模块 | 职责 | 入口文件 | 依赖 | 状态 |
|---|---|---|---|---|---|
| M-PROV-BASE | DataProvider 基类 | 统一接口；新数据源继承即可 | `scripts/providers/base.py` | — | ✅ |
| M-PROV-REG | Provider 注册表 | 多源注册、自动降级 | `scripts/providers/registry.py` | M-PROV-BASE | ✅ |
| M-PROV-TS | Tushare Provider | 主行情源（10000 积分） | `scripts/providers/tushare_provider.py` | M-PROV-BASE | ✅ |
| M-PROV-AK | AKShare Provider | 免费补充、降级 fallback | `scripts/providers/akshare_provider.py` | M-PROV-BASE | ✅ |
| M-PROV-NEWS | 新闻/政策 Provider | 半自动事实链路 | _未建_ | M-PROV-BASE | 🟡 |
| M-PROV-HKEX | 港股专用 Provider | 港股行情/资金流 | _未建_ | M-PROV-BASE | 🔵 |

## Collector 层

| ID | 模块 | 职责 | 入口文件 | 状态 |
|---|---|---|---|---|
| M-COL-MARKET | 市场快照采集 | 指数、涨跌停、板块、个股 K 线 | `scripts/collectors/market.py` | ✅ |
| M-COL-HOLD | 持仓采集 | 持仓池快照 | `scripts/collectors/holdings.py` | ✅ |
| M-COL-WATCH | 关注池采集 | 关注池分层（tier1/2/3） | `scripts/collectors/watchlist.py` | ✅ |
| M-COL-REG | 监管信息采集 | 公告、问询、处罚 | `scripts/collectors/regulatory.py` | ✅ |
| M-COL-PREM | 溢价/资金面 | 北向、龙虎榜、ETF 溢价 | `scripts/collectors/premium.py` | ✅ |
| M-COL-TEACH | 老师笔记采集 | teacher_notes 草稿入库 | `scripts/collectors/teacher_collector.py` | ✅ |

## Ingest 治理层

| ID | 模块 | 职责 | 入口文件 | 状态 |
|---|---|---|---|---|
| M-ING-REG | 接口注册表 | 接口/参数策略/任务选择 | `scripts/ingest/registry.py` | ✅ |
| M-ING-LBL | 标签体系 | 数据质量标注（事实/判断/置信度） | `scripts/ingest/labels.py` | ✅ |
| M-ING-AUDIT | 采集审计 | `ingest_runs` / `ingest_errors` | （在 schema 中） | ✅ |

## DB 层

| ID | 模块 | 职责 | 入口文件 | 状态 |
|---|---|---|---|---|
| M-DB-CONN | 连接管理 | WAL 模式、并发读写 | `scripts/db/connection.py` | ✅ |
| M-DB-SCHEMA | Schema 定义 | 全部表结构 | `scripts/db/schema.py` | ✅ |
| M-DB-MIG | 版本迁移 | 向前兼容迁移 | `scripts/db/migrate.py` | ✅ |
| M-DB-Q | 查询封装 | 业务查询 API | `scripts/db/queries.py` | ✅ |
| M-DB-DW | 双写支持 | YAML→SQLite 同步 + 失败重试 | `scripts/db/dual_write.py` | ✅ |
| M-DB-CLI | DB 子命令 | `python3 main.py db ...` | `scripts/db/cli.py` | ✅ |

## Service / 语义层

| ID | 模块 | 职责 | 入口文件 | 依赖 | 状态 |
|---|---|---|---|---|---|
| M-SVC-PLAN | 计划服务 | Observation→Draft→Plan→Review 全流程 | `scripts/services/planning_service.py` | M-DB-* | ✅ |
| M-SVC-COG | 认知服务 | LLM 推理、事实/判断分流辅助 | `scripts/services/cognition_service.py` | M-SVC-PLAN | 🚧 |
| M-SVC-KNOW | 知识服务 | 资料→Observation 提炼 | `scripts/services/knowledge_service.py` | M-SVC-PLAN | 🚧 |
| M-SVC-HOLD | 持仓信号 | 持仓→watch_items 触发 | `scripts/services/holding_signals.py` | M-SVC-PLAN | ✅ |
| M-SVC-INGEST | 采集协调 | Collectors 编排 + Ingest 治理 | `scripts/services/ingest_service.py` | M-COL-*、M-ING-* | ✅ |
| M-SVC-RESOLVE | 标的解析 | 模糊→规范代码 | `scripts/services/stock_resolver.py` | M-DB-Q | ✅ |
| M-SVC-AGENTPLAN | Agent 协作 Plan | `agent_assisted` 来源生成草稿 | _扩展 M-SVC-COG_ | M-SVC-COG | 🟡 |
| M-SVC-BACKTEST | 回测引擎 | 历史模式匹配、命中率统计 | _未建_ | M-DB-*、M-ANL-* | 🔵 |

## Analyzer 层

| ID | 模块 | 职责 | 入口文件 | 状态 |
|---|---|---|---|---|
| M-ANL-NODE | 节点信号 | 关键时间节点判定 | `scripts/analyzers/node_signals.py` | 🚧 |
| M-ANL-SECT | 板块节奏 | 板块轮动节奏 | `scripts/analyzers/sector_rhythm.py` | 🚧 |
| M-ANL-STYLE | 风格因子 | 大小盘/成长价值/红利 | `scripts/analyzers/style_factors.py` | 🚧 |
| M-ANL-PATTERN | 历史模式匹配 | 形态识别、相似日召回 | _未建_ | 🔵 |
| M-ANL-FEATURE | 因子库 | 跨日特征沉淀 | _未建_ | 🔵 |

## Generator / 输出层

| ID | 模块 | 职责 | 入口文件 | 状态 |
|---|---|---|---|---|
| M-GEN-REPORT | 报告生成 | 盘前/盘后/复盘 Markdown + YAML | `scripts/generators/report.py` | ✅ |
| M-GEN-OBS | Obsidian 导出 | 归档到 vault | `scripts/generators/obsidian_export.py` | ✅ |
| M-GEN-TPL | 推送模板系统 | 可自定义消息格式 | _未建_ | 🟡 |
| M-GEN-SUM | 长报告自动摘要 | 推送内容压缩 | _未建_ | 🟡 |

## Pusher 层

| ID | 模块 | 职责 | 入口文件 | 状态 |
|---|---|---|---|---|
| M-PUSH-BASE | MessagePusher 基类 | 统一接口 | `scripts/pushers/base.py` | ✅ |
| M-PUSH-MULTI | 多渠道并行 | 一键多通道 | `scripts/pushers/multi.py` | ✅ |
| M-PUSH-DC | Discord | Webhook + Markdown 表格代码块 | `scripts/pushers/discord_pusher.py` | ✅ |
| M-PUSH-QQ | QQ Bot | OpenClaw `message` 工具 | `scripts/pushers/qqbot_pusher.py` | ✅ |
| M-PUSH-WX | 企业微信 | Webhook | `scripts/pushers/wechat_pusher.py` | ✅ |
| M-PUSH-RETRY | 失败重试 + 优先级 | 主渠道失败切备用 | _未建_ | 🟡 |
| M-PUSH-WS | WebSocket 实时推送 | 浏览器实时事件流 | _未建_ | 🔵 |
| M-PUSH-METRICS | 推送效果分析 | 打开率、阅读率 | _未建_ | 🔵 |

## API / CLI / Web 入口层

| ID | 模块 | 职责 | 入口文件 | 状态 |
|---|---|---|---|---|
| M-API-MAIN | FastAPI 应用 | OpenAPI 自动生成 | `scripts/api/main.py` | ✅ |
| M-API-PLAN | 计划路由 | `/plans/*` | `scripts/api/routes/planning.py` | ✅ |
| M-API-REVIEW | 复盘路由 | `/reviews/*` | `scripts/api/routes/review.py` | ✅ |
| M-API-CRUD | CRUD 路由 | 实体增删改查 | `scripts/api/routes/crud.py` | ✅ |
| M-API-INGEST | 采集路由 | 触发 + 状态查询 | `scripts/api/routes/ingest.py` | ✅ |
| M-API-COG | 认知路由 | LLM 推理触发 | `scripts/api/routes/cognition.py` | ✅ |
| M-API-SEARCH | 跨实体搜索 | 全文 + 实体聚合 | `scripts/api/routes/search.py` | ✅ |
| M-API-REG | 监管监控路由 | regulatory_monitor | `scripts/api/routes/regulatory_monitor.py` | ✅ |
| M-API-META | 元信息路由 | 健康检查、版本 | `scripts/api/routes/meta.py` | ✅ |
| M-CLI-MAIN | CLI 总入口 | `python3 main.py ...` | `scripts/main.py` | ✅ |
| M-CLI-DB | `db` 子命令组 | 标准写入 | `scripts/db/cli.py` | ✅ |
| M-CLI-INGEST | `ingest` 子命令组 | 采集触发 | （主入口集成） | ✅ |
| M-CLI-PLAN | `plan` 子命令组 | 计划生命周期 + diagnose | （主入口集成） | ✅ |
| M-CLI-KNOW | `knowledge` 子命令组 | 知识资产管理 | （主入口集成） | 🚧 |
| M-WEB-APP | Web Workbench | React 19 + Vite + TS + Tailwind | `web/src/` | ✅ |
| M-WEB-MARKET | 市场看板 | 摘要 + 盘后信封 + 历史下拉 | `web/src/pages/MarketOverview.tsx` 等 | ✅ |
| M-WEB-PLAN | 计划工作台 | 草稿/计划/复盘三联 | `web/src/pages/PlanWorkbench.tsx` | ✅ |
| M-WEB-REVIEW | 八步复盘工作台 | 预填充 + localStorage | `web/src/pages/ReviewWorkbench.tsx` | ✅ |
| M-WEB-COG | 认知工作台 | LLM 协作面板 | `web/src/pages/CognitionWorkbench.tsx` | 🚧 |
| M-WEB-KNOW | 知识工作台 | 资料库浏览/检索 | `web/src/pages/KnowledgeWorkbench.tsx` | 🚧 |
| M-WEB-MOBILE | 移动端适配 | 响应式 / PWA | _未建_ | 🔵 |

## 运维 / 可观测层

| ID | 模块 | 职责 | 入口文件 | 状态 |
|---|---|---|---|---|
| M-OPS-LAUNCHD | macOS 定时调度 | launchd plist | `scripts/launchd/` | ✅ |
| M-OPS-SYNC | 数据同步脚本 | 增量重跑 + 重试 | `scripts/sync_data.sh` | ✅ |
| M-OPS-CHECK | 健康检查 | 配置/连通性 | `scripts/check.sh` + `main.py check` | ✅ |
| M-OPS-AUDIT | 采集审计落库 | ingest_runs/errors | （在 schema） | ✅ |
| M-OPS-METRICS | 服务级 metrics | 采集成功率、推送成功率 SLO | _未建_ | 🟡 |
| M-OPS-TRACE | 全链路追踪 | 请求→采集→落库→推送 | _未建_ | 🔵 |

## 文档 / 规则基础设施

| ID | 模块 | 职责 | 入口文件 | 状态 |
|---|---|---|---|---|
| M-DOC-AGENTS | Agent 协作入口 | `AGENTS.md` / `CLAUDE.md` | 仓库根 | ✅ |
| M-DOC-CTX | Agent 主题上下文 | 渐进式加载文档 | `.cursor/agent-context/` | ✅ |
| M-DOC-RULES | Agent 行为规则 | `.cursor/rules/*.mdc` | `.cursor/rules/` | ✅ |
| M-DOC-SKILLS | Skills 索引 | `.cursor/skills/INDEX.md` | `.cursor/skills/` | ✅ |
| M-DOC-ARCH | 架构蓝图（本套） | 01/02/03 三件套 | `docs/architecture/` | ✅（本次新增） |
| M-DOC-TPL | 文档模板 | technical-design / execution-plan / api-contract | `docs/templates/` | ✅ |

---

## 模块依赖原则

1. **下层不依赖上层**：Provider ⟵ Collector ⟵ Ingest ⟵ Service ⟵ Entry。
2. **跨入口共享 service**：CLI / API / Web 必须经由同一 `services/*` 写入，禁止入口层直接操作 DB。
3. **新增模块必须**：① 在本表登记 ② 在 `01-system-blueprint.md` 对应 L3 子图加节点 ③ 若涉及业务语义同步更新 `20-architecture-and-data.md`。
