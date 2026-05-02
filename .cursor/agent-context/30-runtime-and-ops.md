# 运行职责与仓库操作

## AI 辅助职责
1. **盘前简报**（每日 07:00 自动）：采集外盘、持仓公告、宏观新闻，生成简报并推送
2. **盘后数据报告**（每日 20:00 自动，命令 `main.py post`）：先执行晚间任务（溢价率回填、关注池行情与到价提醒、`review.yaml`→Obsidian），再采集行情、涨跌停、板块、北向资金等，生成全日盘后报告并推送；写盘 `post-market.yaml` 后自动同步 `daily_market` 至 SQLite（失败记入 `data/pending_writes.json`，可用 `main.py db sync` 重试），并将盘后 YAML 导出 Obsidian
3. **复盘辅助**：在盘后报告基础上，引导用户填写主观判断部分
4. **趋势跟踪**：更新 `tracking/` 下的主线、情绪周期、关注池
5. **数据整理**：统计风格化赚钱效应、溢价率等
6. **历史对比**：在需要时回顾历史数据，寻找类似模式
7. **提醒**：日历事件提醒、关注池到价提醒

## 推送渠道

**支持渠道**：Discord、QQ Bot、企业微信（多渠道并行推送）

**配置方式**：
1. 复制 `scripts/.env.example` 为 `scripts/.env`
2. 填入各渠道的 Webhook URL / 目标 ID
3. 在 `scripts/config.yaml` 中启用对应渠道

**渠道特性**：
- **Discord**：支持 Markdown 格式、表格、代码块，适合长报告
- **QQ Bot**：通过 OpenClaw `message` 工具推送，支持私聊/群聊
- **企业微信**：支持 Markdown，适合办公场景

**推送类型**：
- `pre_market`（盘前简报）：07:00 自动推送
- `post_market`（盘后报告）：20:00 自动推送
- `alerts`（实时告警）：触发条件时推送

报告同时存储到 `daily/` 目录和 Web 数据目录。

## AI 不应做的事
- 不做具体的买卖建议
- 不预测具体价格目标
- 不在没有数据支撑时做主观判断
- 不替代用户的「看得懂」判断——学技术是为了偶尔看懂，不是看不懂时硬猜
- 不将 `[判断]` 伪装为 `[事实]`

## 项目目录结构

```text
tradeSystem/
├── .cursor/
│   └── skills/               # 项目级 Cursor Skills（SKILL.md + INDEX.md）
├── scripts/                  # Python 业务脚本
│   ├── main.py               # CLI 入口（pre/post/db 子命令）
│   ├── db/                   # 数据库模块
│   │   ├── connection.py     # 连接管理（WAL 模式）
│   │   ├── schema.py         # 14 张表 + FTS + 触发器
│   │   ├── queries.py        # 查询封装
│   │   ├── migrate.py        # 版本迁移 + YAML 导入
│   │   ├── dual_write.py     # 双写 + 故障恢复
│   │   └── cli.py            # db 子命令定义
│   ├── api/                  # FastAPI 后端
│   │   ├── main.py           # 应用入口 + CORS
│   │   ├── deps.py           # 连接依赖注入
│   │   └── routes/           # 路由：review / search / crud
│   ├── collectors/           # 数据采集器
│   ├── providers/            # 数据源提供者
│   └── tests/                # 单元测试
├── web/                      # React 前端
│   └── src/
│       ├── pages/            # 页面组件
│       ├── lib/              # API 客户端 + 类型定义
│       └── __tests__/        # 前端测试
├── data/                     # 运行时数据（.gitignore）
│   ├── trade.db              # SQLite 数据库
│   └── attachments/          # 附件存储
├── daily/                    # 每日 YAML 归档
├── tracking/                 # 主线/情绪/关注池跟踪
├── config/                   # 静态配置
└── templates/                # 报告模板
```

## 文件修改规范
- 每日数据一旦归档（次日开盘后），不再修改
- `tracking/` 文件每日更新，保留历史记录
- `config/` 文件变更需说明原因
- `scripts/.env` 含敏感 token，不提交到 Git
- `data/trade.db` 和 `data/attachments/` 不提交到 Git（已在 `.gitignore`）
- `web/node_modules/` 和 `web/dist/` 不提交到 Git

## 仓库操作速查表

优先使用仓库根目录的统一入口，而不是手敲零散命令。

- 首次安装：`make bootstrap`
- 环境检查：`make doctor`
- 全量检查：`make check`
- 只查前端：`make check-web`
- 只查后端：`make check-scripts`
- 生成命令索引：`make commands-doc`
- 安装本地 hooks：`make hooks-install`
- 启动前后端：`make dev`
- 只起后端：`make dev-api`
- 只起前端：`make dev-web`
- 打开仪表盘：`make dashboard-open`
- 打开查询中心：`make search-open`
- 打开命令中心：`make commands-open`
- 打开计划工作台：`make plan-open DATE=YYYY-MM-DD`
- 打开资料工作台：`make knowledge-open`
- 打开采集工作台：`make ingest-open`
- 打开老师观点页：`make teachers-open`
- 打开持仓页：`make holdings-open`
- 打开关注池页：`make watchlist-open`
- 打开日历页：`make calendar-open`
- 打开行业页：`make industry-open`
- 今日盘前：`make today-open`
- 今日盘后：`make today-close`
- 今日晚间任务：`make today-evening`
- 今日关注池：`make today-watchlist`
- 今日 Obsidian 导出：`make today-obsidian`
- 今日采集审计：`make today-ingest-inspect`
- 今日采集健康：`make today-ingest-health`
- 近 7 天采集健康：`make ingest-health DAYS=7`
- 清理陈旧采集记录：`make ingest-reconcile STALE_MINUTES=5`
- 初始化数据库：`make db-init`
- 重试失败写入：`make db-sync`
- YAML/DB 对账：`make db-reconcile`
- 当前持仓：`make holdings`
- 安全回填持仓现价与技术快照：`make holdings-refresh DATE=YYYY-MM-DD`
- 当前关注池：`make watchlist`
- 老师笔记搜索：`make notes-search KEYWORD=主线`
- 跨表搜索：`make db-search KEYWORD=情绪`
- 打开市场看板：`make market-open DATE=YYYY-MM-DD`
- 查看市场摘要：`make market-json DATE=YYYY-MM-DD`
- 查看盘后信封：`make market-envelope DATE=YYYY-MM-DD`
- 打开复盘工作台：`make review-open DATE=YYYY-MM-DD`
- 查看复盘预填充：`make review-prefill DATE=YYYY-MM-DD`
- 八步复盘分步提问话术附录（Skill，供 AI 逐步引导）：[eight-step-prompt-templates.md](/Users/alyx/tradeSystem/.agents/skills/daily-review/references/eight-step-prompt-templates.md)
- 单接口采集：`make ingest-run-interface NAME=block_trade`
- 查看采集审计：`make ingest-inspect DATE=YYYY-MM-DD`
- 查看采集健康：`make ingest-health DATE=YYYY-MM-DD DAYS=7`
- 清理陈旧 running 采集记录：`make ingest-reconcile`
- 计划草稿：`make plan-draft`
- 查看今日草稿：`make plan-show-draft`
- 确认正式计划：`make plan-confirm DRAFT_ID=draft_xxx`
- 计划诊断：`make plan-diagnose PLAN_ID=plan_xxx`
- 回写计划复盘：`make plan-review PLAN_ID=plan_xxx`
- 查看资料：`make knowledge-list`

完整命令表见 [docs/commands.md](/Users/alyx/tradeSystem/docs/commands.md)，机器可读清单见 [docs/commands.json](/Users/alyx/tradeSystem/docs/commands.json)。新增 `make` 目标后，优先运行 `make commands-doc` 更新索引；`make check` / pre-push 会自动执行 `make commands-check`。
