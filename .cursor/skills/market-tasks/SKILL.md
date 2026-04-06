---
name: market-tasks
description: 手动触发或自动定时执行盘前/盘后行情采集任务，并将结果摘要推送回 channel
version: "1.1"
---

# Skill: 市场数据任务（盘前/盘后采集）

## 使用场景

当以下情况触发时激活此 skill：
1. **用户手动触发**：「帮我跑一下盘前采集」「执行今天的盘后任务」
2. **定时自动触发**：07:00 执行盘前，20:00 执行盘后（由 launchd/scheduler 唤醒 agent）
3. **补跑某一天**：「补跑 2026-04-01 的盘后」

## 工作流程

## 优先入口

若仓库根目录 `Makefile` 可用，优先使用：

```bash
make market-open DATE=YYYY-MM-DD
make market-json DATE=YYYY-MM-DD
make market-envelope DATE=YYYY-MM-DD
make today-open
make today-close
make today-pre DATE=YYYY-MM-DD
make today-post DATE=YYYY-MM-DD
```

底层 `python3 main.py ...` 仍保留，供调试、定时器和无 `make` 环境使用。

其中：

- `make market-open` 用于直接打开 `/market/:date` 页面
- `make market-json` 用于查看 `/api/market/{date}` 摘要数据
- `make market-envelope` 用于查看 `/api/post-market/{date}` 整包盘后信封

### Step 1：确认参数

**任务类型**：pre（盘前）或 post（盘后）

**日期**：
- 默认使用今日日期
- 非交易日（周末/节假日）时，询问用户是否使用最近的交易日
- 用户也可指定历史日期补跑

### Step 2：手动执行前先确认影响

对于**用户手动触发**或**历史日期补跑**场景，先展示执行摘要，再运行命令。自动调度场景可直接执行。

建议确认以下信息：
- 任务类型：`pre` / `post`
- 执行日期
- 是否属于历史补跑
- 是否可能覆盖已有 `daily/{date}/*.yaml`
- 预期运行时间（盘前约 5 分钟，盘后约 10 分钟）

可参考以下确认模板。建议统一按“类型/动作 + 关键参数 + 影响 + 确认语句”的结构展示：

```text
即将执行市场任务：
  类型: 行情采集任务
  动作: make today-post DATE=YYYY-MM-DD
  任务类型: post
  日期: 2026-04-01
  模式: 手动触发
  影响: 将重新生成 daily/2026-04-01/post-market.yaml
  说明: 若当天已有盘后文件，补跑会覆盖 YAML，并更新/补写 DB
  预估耗时: 约 10 分钟

确认执行？(是/否)
```

若用户要求“补跑某一天”，必须显式提醒覆盖影响后再执行。

### Step 3：执行采集任务

优先在仓库根目录运行：

```bash
# 盘前简报（07:00 左右执行）
cd /path/to/tradeSystem
make today-pre DATE=YYYY-MM-DD

# 盘后报告（20:00 左右执行）
cd /path/to/tradeSystem
make today-post DATE=YYYY-MM-DD
```

若需直接使用底层命令，则在 `scripts/` 目录下运行：

```bash
# 盘前简报（07:00 左右执行）
cd /path/to/tradeSystem/scripts
python3 main.py pre --date YYYY-MM-DD

# 盘后报告（20:00 左右执行）
cd /path/to/tradeSystem/scripts
python3 main.py post --date YYYY-MM-DD
```

**超时设置**：
- 盘前任务：最长 5 分钟
- 盘后任务：最长 10 分钟（数据量更大）

### Step 4：解析输出结果

从命令输出中提取关键信息：

**盘前成功输出示例：**
```
[PRE] 2026-04-01 盘前简报生成完成
  外盘：美股收跌，纳指 -0.8%
  持仓公告：宁德时代无重大公告
  推送：Discord ✅
  文件：daily/2026-04-01/pre-market.yaml
```

**盘后成功输出示例：**
```
[POST] 2026-04-01 盘后报告生成完成
  上证：+1.23%，成交额 8234亿
  涨停：45只，跌停：3只，炸板率：22%
  主力板块：AI算力 +5.2%
  推送：Discord ✅ / QQ ✅
  文件：daily/2026-04-01/post-market.yaml
```

### Step 5：错误处理

**数据源超时或失败：**
```
⚠️ tushare 连接超时，已自动降级到 akshare
   部分数据（北向资金）可能不完整
```
→ 属于正常降级，继续推送，在报告末尾注明数据来源

**关键采集全部失败：**
```
❌ 行情数据采集失败：tushare token 无效 / 网络不通
```
→ 向用户报告错误，建议手动检查 `.env` 中的 token 配置

**推送失败：**
```
⚠️ Discord 推送失败：Webhook URL 无效
```
→ 数据已保存到 YAML，推送问题不影响数据完整性，但需提醒用户

### Step 6：推送摘要到 Channel

采集完成后，向用户所在的 channel 推送简要摘要：

**盘前摘要（推送到用户 channel）：**
```
🌅 2026-04-01 盘前简报已生成
  美股：道指 -0.3%，纳指 -0.8%（科技股承压）
  人民币：6.85（偏弱）
  今日关注：AI算力 / 锂电（关注池标的有公告）
  完整报告：daily/2026-04-01/pre-market.yaml
```

**盘后摘要（推送到用户 channel）：**
```
🌆 2026-04-01 盘后报告已生成
  上证 +1.23%（8234亿），创业板 +1.56%
  涨停 45 / 跌停 3 / 炸板 22%
  主线：AI算力 +5.2%（持续）
  完整报告：daily/2026-04-01/post-market.yaml
  提示：可使用 daily-review skill 开始复盘
```

## 定时调度配置

### 方式一：launchd 常驻守护进程（推荐 macOS 生产环境）

使用 `scripts/launchd/com.tradesystem.schedule.plist`（已创建）：

```bash
# 加载（开机自动启动调度器）
cp scripts/launchd/com.tradesystem.schedule.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.tradesystem.schedule.plist

# 手动启动/停止
launchctl start com.tradesystem.schedule
launchctl stop com.tradesystem.schedule

# 查看状态
launchctl list | grep tradesystem
```

调度器运行后：
- 每天 07:00 自动执行 `python3 main.py pre --date {today}`
- 每天 20:00 自动执行 `python3 main.py post --date {today}`
- 崩溃后自动重启（`KeepAlive: true`）

### 方式二：crontab（跨平台）

```cron
0 7 * * 1-5  cd /path/to/tradeSystem/scripts && python3 main.py pre --date $(date +\%Y-\%m-\%d) >> /tmp/pre.log 2>&1
0 20 * * 1-5 cd /path/to/tradeSystem/scripts && python3 main.py post --date $(date +\%Y-\%m-\%d) >> /tmp/post.log 2>&1
```

### 方式三：Agent 自带调度（开发/测试环境）

OpenClaw/Copaw 可在自身的 cron/scheduler 中设置，在指定时间主动调用此 skill。

## 手动补跑说明

如某日任务失败需补跑：

```bash
cd /path/to/tradeSystem

# 补跑盘前
make today-pre DATE=2026-03-31

# 补跑盘后
make today-post DATE=2026-03-31
```

底层等价命令：

```bash
cd /path/to/tradeSystem/scripts
python3 main.py pre --date 2026-03-31
python3 main.py post --date 2026-03-31
```

补跑会覆盖已有的 YAML 文件，DB 中会创建新记录（或更新已有记录）。
因此手动补跑前，应先向用户确认日期和覆盖影响。

## 注意事项

- 盘前任务在 07:00 执行，此时交易所数据尚未开盘，主要采集外盘和持仓公告
- 盘后任务在 20:00 执行，涵盖当日完整行情数据
- 非交易日执行时，命令会自动跳过行情采集，仅执行其他任务
- 采集失败不影响 YAML 历史记录，但会在 `pending_writes` 中标记，等待下次 `db sync` 重试
- 手动触发和补跑场景，应先确认任务类型、日期及覆盖影响，再执行命令

## 与新架构的关系

第一阶段后，市场任务 skill 仍负责：

- `pre`
- `post`
- `evening`
- `watchlist`

但采集底座的标准入口会逐步沉淀到：

```bash
python3 main.py ingest run --stage pre_core --date YYYY-MM-DD
python3 main.py ingest run --stage post_core --date YYYY-MM-DD
python3 main.py ingest inspect --date YYYY-MM-DD
```

若用户在问“哪些接口失败了”“某个接口单独补跑”，应优先切换到 `ingest-inspector` skill。
