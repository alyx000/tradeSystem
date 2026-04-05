# 交易系统 - A股/港股短线交易分析

基于「三位一体」+「四维度短线交易法」体系的交易分析系统。
与 OpenClaw（云服务器）协作共建。

## 目录结构

```
tradeSystem/
├── CLAUDE.md                 # AI协作规则 & 交易体系完整说明
├── README.md                 # 本文件
├── docs/                     # 交易体系理论文档（PDF课件等）
├── templates/                # 模板文件
│   ├── daily-review.yaml     # 八步复盘法模板
│   └── trade-log.yaml        # 交易记录模板
├── config/                   # 配置文件
│   ├── sectors.yaml          # 板块定义与分类
│   ├── styles.yaml           # 风格化指标定义
│   └── calendar.yaml         # 投资日历/财报季
├── daily/                    # 每日复盘数据（YAML 归档）
│   └── YYYY-MM-DD/
├── tracking/                 # 持续跟踪数据
│   ├── main-theme.yaml       # 主线板块跟踪
│   ├── emotion-cycle.yaml    # 情绪周期跟踪
│   └── watchlist.yaml        # 关注票池
├── data/                     # 运行时数据（.gitignore）
│   ├── trade.db              # SQLite 数据库（主存储）
│   └── attachments/          # 附件（图片等）
├── scripts/                  # Python 业务脚本
│   ├── main.py               # CLI 入口（pre / post / db / ingest / plan / knowledge）
│   ├── db/                   # 数据库模块（schema / queries / 迁移 / 双写）
│   ├── ingest/               # 接口注册表与采集元数据
│   ├── services/             # ingest / planning / knowledge 服务层
│   ├── api/                  # FastAPI 后端
│   │   ├── main.py           # 应用入口
│   │   ├── deps.py           # 依赖注入
│   │   └── routes/           # review / search / crud / planning
│   ├── collectors/           # 数据采集器
│   ├── providers/            # 数据源提供者
│   └── tests/                # 单元测试
└── web/                      # React 前端（Vite + Tailwind CSS）
    └── src/
        ├── pages/            # 页面组件
        ├── lib/              # API 客户端 + 类型定义
        └── __tests__/        # 前端测试
```

## 与 OpenClaw 协作流程

### 日常协作方式

1. **盘前**：OpenClaw 提醒日历事件、昨日遗留关注点
2. **盘中**：你输入实时观察，OpenClaw 辅助记录和结构化
3. **盘后复盘**：
   - 你提供原始数据（指数、涨跌停、板块表现等）
   - OpenClaw 按模板生成结构化复盘
   - 你审核并补充主观判断（情绪定性、节点判断等）
4. **交易记录**：每笔交易后记录逻辑，OpenClaw 辅助归类
5. **周末回顾**：汇总本周数据，更新 tracking 文件

### 协作原则

- **你做判断，AI做记录和整理**
- AI 不做具体买卖建议
- 所有主观定性（情绪周期、板块节奏）由你决定
- AI 负责数据一致性检查、历史对比、模式匹配

### Git 协作规范

```bash
# 每日复盘提交
git add daily/2026-03-28/
git commit -m "复盘: 2026-03-28 [简要描述]"

# 更新跟踪数据
git add tracking/
git commit -m "跟踪更新: 2026-03-28"

# 配置变更
git add config/
git commit -m "配置: [变更说明]"
```

## 快速开始

### 1. 安装依赖

```bash
# Python 后端
pip install -r scripts/requirements.txt

# React 前端
cd web && npm install && cd ..
```

### 2. 初始化数据库

首次使用需初始化 SQLite 数据库并导入历史 YAML 数据：

```bash
cd scripts && python3 main.py db init && cd ..
```

### 3. 本地开发启动

需要同时运行后端和前端两个服务：

```bash
# 终端 1 — FastAPI 后端（端口 8000）
cd scripts && uvicorn api.main:app --reload --port 8000

# 终端 2 — React 前端（端口 5173）
cd web && npm run dev
```

打开浏览器访问 **http://localhost:5173** 即可使用。

| 地址 | 说明 |
|------|------|
| http://localhost:5173 | Web 前端（仪表盘、市场看板、复盘工作台、计划工作台、资料工作台、查询中心等） |
| http://localhost:8000/docs | FastAPI 自动生成的 API 文档 |
| http://localhost:8000/api/health | 健康检查 |

> 前端已配置代理，`/api/*` 请求自动转发到后端，无需手动处理跨域。
> 两个服务都支持热重载——修改代码后自动生效，无需重启。

当前新增的工作台页面：

- `计划工作台`：围绕 `TradeDraft / TradePlan / PlanReview`
- `资料工作台`：围绕 `knowledge_assets -> observation -> draft`
- `计划工作台` 现已支持直接编辑 `observation`、`draft`、`plan`，并可结构化维护 `watch_items`、`fact_checks`、`judgement_checks`、`trigger_conditions`、`invalidations`
- `计划工作台` 现已支持对 `watch_items` 和 `fact_checks` 做显式排序与优先级维护：可上移/下移，并可直接编辑 `priority`
- JSON 兜底仍保留，但已折叠为“高级 JSON 编辑”；默认应优先使用结构化表单维护计划内容

### 4. 常用 CLI 命令

```bash
cd scripts

# 盘前简报
python3 main.py pre --date 2026-04-01

# 盘后报告（含晚间任务）；成功后会把当日 post-market 同步进 SQLite daily_market
python3 main.py post --date 2026-04-01

# 采集底座
python3 main.py ingest list-interfaces
python3 main.py ingest run --stage post_core --date 2026-04-01
python3 main.py ingest inspect --date 2026-04-01 --json

# 交易计划
python3 main.py plan draft --date 2026-04-01
python3 main.py plan show-draft --date 2026-04-01
python3 main.py plan confirm --date 2026-04-02 --draft-id draft_xxx
python3 main.py plan diagnose --date 2026-04-02 --plan-id plan_xxx --json
python3 main.py plan review --date 2026-04-02 --plan-id plan_xxx

`plan diagnose` 当前优先读取 `market_fact_snapshots`，并已支持从 `daily_market` 补充判断市场成交额环比、板块涨跌幅与板块涨停家数。
API 侧 `/api/plans/{plan_id}/diagnostics` 已复用同一诊断逻辑；可用时会自动启用 provider fallback，不可用时退回快照/DB 诊断。

# 资料提炼
python3 main.py knowledge add-note --title "老师观点" --content "机器人回流，关注 002594.SZ"
python3 main.py knowledge list
python3 main.py knowledge draft-from-asset --asset-id asset_xxx --date 2026-04-02

# 数据库操作
python3 main.py db init              # 初始化 + 导入历史
python3 main.py db sync              # 重试失败的写入
python3 main.py db reconcile         # YAML ↔ DB 对账
python3 main.py db add-note ...      # 添加老师观点
python3 main.py db query-notes ...   # 查询老师观点
python3 main.py db watchlist-add ... # 添加关注票
```

### 5. 运行测试

```bash
# Python 后端测试（从仓库根目录执行）
python3 -m pytest scripts/tests/ -v

# React 前端测试
cd web && npx vitest run
```

### 6. 计划 / 资料 API

当前最小可用接口：

- `GET /api/ingest/interfaces`
- `GET /api/ingest/inspect?date=YYYY-MM-DD`
- `GET /api/ingest/runs?date=YYYY-MM-DD`
- `GET /api/ingest/errors?date=YYYY-MM-DD`
- `POST /api/ingest/run`
- `POST /api/ingest/run-interface`
- `GET /api/ingest/retry`
- `POST /api/knowledge/assets`
- `GET /api/knowledge/assets`
- `POST /api/knowledge/assets/{asset_id}/draft`
- `POST /api/plans/drafts`
- `GET /api/plans/drafts/{draft_id}`
- `POST /api/plans/{draft_id}/confirm`
- `GET /api/plans/{plan_id}`
- `GET /api/plans/{plan_id}/diagnostics`
- `POST /api/plans/{plan_id}/review`

### 7. 每日复盘（手动方式）

```bash
mkdir -p daily/$(date +%Y-%m-%d)
cp templates/daily-review.yaml daily/$(date +%Y-%m-%d)/review.yaml
cp templates/trade-log.yaml daily/$(date +%Y-%m-%d)/trades.yaml
```

或者直接告诉 OpenClaw："开始今天的复盘"，它会自动创建文件并引导你填写。
也可以通过 Web 前端的「八步复盘工作台」页面直接填写。

## VPS / 定时任务（生产）

仓库根目录下，推荐 **两个定时点**（上海时区，工作日）：

| 时间 | 命令 | 说明 |
|------|------|------|
| 07:00 | `python3 scripts/main.py pre` | 盘前简报 |
| 20:00 | `bash scripts/sync_data.sh` | `git pull` → `main.py post`（含晚间任务）→ 提交并推送 `daily/`、`tracking/` |

也可在 `scripts/` 下长期运行 `python3 main.py schedule`，由 APScheduler 执行上述两个时刻（`post` 已内含原 `evening` 流程，无需再配 18:00）。

**由 OpenClaw 调度时**：不必部署 systemd；在 OpenClaw 里按上表配置 **工作日 07:00 / 20:00** 执行相同命令即可，**工作目录设为仓库根目录**（与 `sync_data.sh` 的路径约定一致）。请勿与 `main.py schedule` 或本机 `deploy/systemd/` 定时器同时启用，以免重复推送、重复写文件。`sync_data.sh` 会 `git push`，运行环境需已配置 SSH deploy key 或等价凭据。

**环境**：复制 `scripts/.env.example` 为 `scripts/.env`，填入 `TUSHARE_TOKEN`、Discord Webhook 等；Obsidian 导出目录可用环境变量 **`OBSIDIAN_DIR`**（未设置时见 `scripts/generators/obsidian_export.py` 默认路径）。**采集**（`pre`/`post`/`evening`/`watchlist`/`check` 等）会临时清除 `HTTP_PROXY`/`HTTPS_PROXY`，避免 Tushare 误走本机代理；推送阶段恢复环境，Discord 等仍可走代理。若采集也必须走代理，设置 **`TRADESYSTEM_USE_HTTP_PROXY=1`**。Vault 一般在仓库外，不由 `sync_data.sh` 提交。

**一次性**：若需板块节奏分析有足够历史，可在 VPS 上按需执行 `python3 scripts/backfill_sectors.py`（参数见脚本说明）。

---

## 📢 推送渠道配置

系统支持多渠道并行推送：Discord、QQ Bot、企业微信。

### 1. Discord Webhook

**获取 Webhook URL**：
1. Discord 频道设置 → 集成 → Webhooks → 新建 Webhook
2. 复制 Webhook URL

**配置**：
```bash
# scripts/.env
DISCORD_WEBHOOK_PRE=https://discord.com/api/webhooks/xxx
DISCORD_WEBHOOK_POST=https://discord.com/api/webhooks/xxx
DISCORD_WEBHOOK_ALERT=https://discord.com/api/webhooks/xxx
```

```yaml
# scripts/config.yaml
push:
  discord:
    enabled: true
    channels:
      pre_market: "盘前简报"
      post_market: "盘后报告"
      alerts: "交易告警"
```

### 2. QQ Bot（通过 OpenClaw）

**配置**：
```yaml
# scripts/config.yaml
push:
  qq:
    enabled: true
    channels:
      pre_market: "user:openid_xxx"    # 私聊
      post_market: "group:group_xxx"   # 群聊
      alerts: "user:openid_xxx"
```

> **注意**：QQ Bot 通过 OpenClaw `message` 工具推送，无需配置 API Key。目标格式：`user:openid`（私聊）或 `group:group_id`（群聊）。

### 3. 企业微信机器人

**获取 Webhook URL**：
1. 企业微信 → 工作台 → 机器人 → 添加
2. 复制 Webhook 地址

**配置**：
```bash
# scripts/.env
WECHAT_WEBHOOK=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx
```

```yaml
# scripts/config.yaml
push:
  wechat:
    enabled: true
```

### 多渠道并行

可同时启用多个渠道，系统会自动向所有启用的渠道推送：

```yaml
push:
  discord:
    enabled: true
  qq:
    enabled: true
  wechat:
    enabled: false
```

## 核心概念速查

| 概念 | 说明 |
|------|------|
| 三位一体 | 大势 + 板块 + 个股，综合判断 |
| 最票 | 个股在板块中某属性下的第一名 |
| 情绪周期 | 启动→发酵→高潮→分歧→衰退→启动 |
| 重点因子 | 当下最影响走势的因子（动态变化） |
| 风格化 | 当前市场审美偏好（大/小盘、趋势/连板等） |
| 诚意反包 | 在人们不相信中走出的反包才有价值 |
| 首阴价值 | 大势+板块初期→有价值；充分演绎→没价值 |
| 节点 | 情绪/板块/大盘的关键转折点 |

## 体系来源

- 三位一体教程
- 四维度短线交易法体系课（第1-26节）
