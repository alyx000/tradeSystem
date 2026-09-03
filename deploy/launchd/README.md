# macOS launchd 部署（盘前/盘后 + 行业推荐定时推送）

适用：用户本机长期开机的 macOS。如果在 VPS 上跑，看仓库 `deploy/systemd/`。

## 文件

- `recommend-runner.sh` — 包装脚本：cd 仓库根 → source 项目 env → 调 `python3 main.py recommend`
- `calendar-sync-runner.sh` — 包装脚本：每天预取未来 14 日宏观事件，原子更新 `tracking/calendar_auto.yaml` 并幂等同步 `calendar_events`
- `com.alyx.tradesystem.calendar-sync.plist` — 每天 06:30 触发，早于 07:00 盘前任务；未来 7 日覆盖不足时非零退出并写 `/tmp/tradesystem-calendar-sync.log`
- `com.alyx.tradesystem.recommend-daily.plist` — 工作日 07:10 触发（行业日报）
- `com.alyx.tradesystem.recommend-weekly.plist` — 周日 20:00 触发（行业周报）
- `volume-watch-runner.sh` — 包装脚本：cd 仓库根 → source `scripts/.env`(TUSHARE_TOKEN) + `~/.config/tradeSystem.env`(钉钉) → 调 `python3 main.py volume-watch daily`
- `com.alyx.tradesystem.volume-watch.plist` — 工作日 21:00 触发（成交额 Top20 板块集中度日报；非交易日无数据自动跳过）
- `string-yang-runner.sh` — 包装脚本：cd 仓库根 → source `scripts/.env`(TUSHARE_TOKEN) + `~/.config/tradeSystem.env`(钉钉/ANTIGRAVITY) → 调 `python3 main.py string-yang daily`
- `com.alyx.tradesystem.string-yang.plist` — 工作日 21:50 触发（LLM 融合主线板块/概念分支的串阳首阴股票池；只推已出现第一根阴线的确认票）
- `today-runner.sh` — 包装脚本：cd 仓库根 → source 项目 env → 调 `python3 main.py pre|post`
- `com.alyx.tradesystem.today-pre.plist` — 工作日 07:00 触发（盘前简报，含钉钉推送）
- `com.alyx.tradesystem.today-post.plist` — 工作日 20:00 触发（盘后报告，含钉钉推送）
- `research-digest-runner.sh` — 包装脚本：cd 仓库根 → source `scripts/.env`(TUSHARE_TOKEN) + `~/.config/tradeSystem.env`(钉钉/ANTIGRAVITY) → 判断 A 股交易日/交易日前一天 → 调 JS workflow
- `com.alyx.tradesystem.research-digest.plist` — 每天 22:00 触发（runner 仅在 A 股交易日或 A 股交易日前一天继续执行；研报速读：A股研报评级[巨潮] + 美股 yfinance 评级 → Top3）
- `cognition-digest-runner.sh` — 包装脚本（参数化，window 作为 `$1` 透传）：cd 仓库根 → source `scripts/.env` + `~/.config/tradeSystem.env`(钉钉/ANTIGRAVITY) → 调 `python3 main.py cognition-digest <window>`
- `com.alyx.tradesystem.cognition-digest-recent3d.plist` — 每交易日 18:30 触发（认知沉淀近 3 日汇总；日志 `/tmp/tradesystem-cognition-digest.log`）
- `com.alyx.tradesystem.cognition-digest-weekly.plist` — 周日 20:00 触发（认知沉淀周汇总；同一日志 `/tmp/tradesystem-cognition-digest.log`）
- `com.alyx.tradesystem.cognition-digest-monthly.plist` — 每月 1 号 09:00 触发（认知沉淀月汇总；同一日志 `/tmp/tradesystem-cognition-digest.log`）
- `board-break-runner.sh` — 包装脚本：cd 仓库根 → source `scripts/.env`(TUSHARE_TOKEN) + `~/.config/tradeSystem.env`(钉钉/ANTIGRAVITY) → 调 `python3 main.py board-break daily`
- `com.alyx.tradesystem.board-break.plist` — 工作日 21:20 触发（断板反包盘后扫描：昨日连板≥2 断板→八维度加权打分+LLM两两PK→双排序观察清单；日志 `/tmp/tradesystem-board-break.log`）
- `sector-crowding-runner.sh` — 包装脚本：cd 仓库根 → source `scripts/.env`(TUSHARE_TOKEN) + `~/.config/tradeSystem.env`(钉钉,仅手动 `--push` 场景需要) → 调 `python3 main.py sector-crowding daily`
- `com.alyx.tradesystem.sector-crowding.plist` — 工作日 21:30 触发（板块拥挤度采集，默认不推送，复盘时 `sector-crowding report` 查看；非交易日任务内守卫跳过；日志 `/tmp/tradesystem-sector-crowding.log`）
- `ma-breakout-runner.sh` — 包装脚本：cd 仓库根 → source `scripts/.env`(TUSHARE_TOKEN) + `~/.config/tradeSystem.env`(钉钉) → 调 `python3 main.py ma-breakout daily`
- `com.alyx.tradesystem.ma-breakout.plist` — 工作日 14:50 触发（4日均线二波尾盘实时快照；CLI 仅允许上海当日 14:45～15:00，休眠补触发会跳过；日志 `/tmp/tradesystem-ma-breakout.log`）
- `value-watch-runner.sh` — 包装脚本：cd 仓库根 → source `~/.config/tradeSystem.env`(钉钉；TUSHARE_TOKEN 由 `scripts/.env` 在 Python 侧加载) → 调 `python3 main.py value-watch daily`
- `com.alyx.tradesystem.value-watch.plist` — 工作日 21:45 触发（价值投资条件监控：红利回撤/卖出阶梯/稀缺周线，事件首发才推钉钉[sent_events 账本去重]；日志 `/tmp/tradesystem-value-watch.log`；Sleep policy: 错过可接受——次日运行按事件账本自动补齐）
- `daily-leaders-runner.sh` — 包装脚本：cd 仓库根 → source `~/.config/tradeSystem.env`(钉钉/LLM) → 调 `/usr/bin/python3 scripts/main.py daily-leaders propose --push`
- `com.alyx.tradesystem.daily-leaders.plist` — 工作日 22:30 触发（每日最票候选确认稿；stdout `/tmp/tradesystem-daily-leaders.out.log`，stderr `/tmp/tradesystem-daily-leaders.err.log`）
- `monthly-pattern-runner.sh` — 包装脚本：设置 PATH、cd 仓库根、source `scripts/.env` + `~/.config/tradeSystem.env`、打印脱敏环境诊断 → 调 `/usr/bin/python3 scripts/main.py monthly-pattern daily --input-by launchd`
- `com.alyx.tradesystem.monthly-pattern.plist` — 每月 2 日 23:10 单次触发（只使用带 certified 覆盖收据的完成月前复权月线 + 公告日 as-of 财务，维护三策略观察池；休眠错过可接受；日志 `/tmp/tradesystem-monthly-pattern.log`）
- `monthly-pattern-monitor-runner.sh` — 包装脚本：source 行情/钉钉 env → 调 `/usr/bin/python3 scripts/main.py monthly-pattern monitor-daily`
- `com.alyx.tradesystem.monthly-pattern-monitor.plist` — 每 15 分钟轻量 tick，runner 仅在上海工作日 19:10（含）至 19:25（不含）执行一次重任务（月线种子日频动态 5 月线 + 日/周 MACD 变化监控；不受 Mac 本机时区切换影响；日志 `/tmp/tradesystem-monthly-pattern-monitor.log`）
- `morning-brief-runner.sh` — 包装脚本：cd 仓库根 → source `~/.config/tradeSystem.env`(钉钉；TUSHARE_TOKEN 由 `scripts/.env` 在 Python 侧加载) → 调 `python3 main.py morning-brief daily`
- `com.alyx.tradesystem.morning-brief.plist` — 工作日 08:00 触发（盘前早报：隔夜行情+海外/国内要闻[金十]+上市公司公告[巨潮]；非交易日 CLI 内守卫跳过；日志 `/tmp/tradesystem-morning-brief.log`；Sleep policy: 错过可接受——可手动 `morning-brief daily` 补跑）
- `intraday-monitor-runner.sh` — 每 5 分钟 tick 的盘中门禁入口；上海 `09:30-11:30 / 13:00-15:00` 做常规检查，并保留 `15:01-15:05` 收盘终态补窗
- `com.alyx.tradesystem.intraday-monitor.plist` — 单标的阈值 + 09:30～10:00（不含10:00）百亿成交额涨停板横截面监控；除相对 300 秒节拍外固定 09:59 做最后补扫，日志 `/tmp/tradesystem-intraday-monitor.log`
- `intraday-summary-runner.sh` — 每分钟轻量 tick，只在上海半小时槽位后 5 分钟内调 `intraday-summary run`
- `com.alyx.tradesystem.intraday-summary.plist` — 全市场半小时快照差分摘要并推钉钉，日志 `/tmp/tradesystem-intraday-summary.log`

## 前置条件

- `~/.config/tradeSystem.env` 已存在且含 `DINGTALK_WEBHOOK_TOKEN` + `DINGTALK_WEBHOOK_SECRET`（盘前/盘后/行业推荐共用同一对凭据）
- `python3` 在 `/usr/bin/python3`（或修改 runner 内的绝对路径）
- `agy` 在 PATH 中，或通过 `ANTIGRAVITY_BIN` 指向 Antigravity CLI

宏观日历模板不会自动安装或首次同步。确认业务数据写入后再执行：

```bash
chmod +x deploy/launchd/calendar-sync-runner.sh
cp deploy/launchd/com.alyx.tradesystem.calendar-sync.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.alyx.tradesystem.calendar-sync.plist
launchctl list | grep tradesystem.calendar-sync
```

## 安装（一次性）

```bash
# 1. 包装脚本可执行
chmod +x deploy/launchd/recommend-runner.sh

# 2. 复制 plist 到用户级 LaunchAgents
cp deploy/launchd/com.alyx.tradesystem.recommend-*.plist ~/Library/LaunchAgents/

# 3. 加载到 launchd
launchctl load ~/Library/LaunchAgents/com.alyx.tradesystem.recommend-daily.plist
launchctl load ~/Library/LaunchAgents/com.alyx.tradesystem.recommend-weekly.plist

# 4. 验证已加载
launchctl list | grep tradesystem
```

## 触发立即测试（不等到 07:10）

```bash
launchctl start com.alyx.tradesystem.recommend-daily
launchctl start com.alyx.tradesystem.recommend-weekly

# 看日志
tail -f /tmp/tradesystem-recommend-daily.log
tail -f /tmp/tradesystem-recommend-weekly.log
```

## 卸载

```bash
launchctl unload ~/Library/LaunchAgents/com.alyx.tradesystem.recommend-daily.plist
launchctl unload ~/Library/LaunchAgents/com.alyx.tradesystem.recommend-weekly.plist
rm ~/Library/LaunchAgents/com.alyx.tradesystem.recommend-*.plist
```

## 今日盘前/盘后定时（工作日 07:00 / 20:00）

行业推荐之外，独立挂载工作日盘前/盘后任务。两者共用同一 `~/.config/tradeSystem.env`、同一钉钉 webhook。

```bash
# 1. 包装脚本可执行
chmod +x deploy/launchd/today-runner.sh

# 2. 复制 plist
cp deploy/launchd/com.alyx.tradesystem.today-pre.plist  ~/Library/LaunchAgents/
cp deploy/launchd/com.alyx.tradesystem.today-post.plist ~/Library/LaunchAgents/

# 3. 加载
launchctl load ~/Library/LaunchAgents/com.alyx.tradesystem.today-pre.plist
launchctl load ~/Library/LaunchAgents/com.alyx.tradesystem.today-post.plist

# 4. 验证
launchctl list | grep tradesystem.today

# 5. 真触发立即测试（周末仅看 launchd 链路；钉钉抵达需等工作日自然触发）
launchctl start com.alyx.tradesystem.today-pre
tail -f /tmp/tradesystem-today-pre.log
launchctl start com.alyx.tradesystem.today-post
tail -f /tmp/tradesystem-today-post.log

# 卸载
launchctl unload ~/Library/LaunchAgents/com.alyx.tradesystem.today-pre.plist
launchctl unload ~/Library/LaunchAgents/com.alyx.tradesystem.today-post.plist
rm ~/Library/LaunchAgents/com.alyx.tradesystem.today-{pre,post}.plist
```

**时段冲突说明**：与 `recommend-daily`（07:10）间隔 10 分钟；与 `recommend-weekly`（周日 20:00）不撞工作日。SQLite 用 WAL，10 分钟通常够 pre 跑完；若观察到 `/tmp/tradesystem-today-pre.log` 出现 `database is locked` / `SQLITE_BUSY`，把 today-pre 改为 06:55（提前 5 分钟）即可。

**盘前不可错过 → 必须配套唤醒**：

```bash
# 工作日 06:55 唤醒，给 today-pre 07:00 留 5 分钟缓冲
sudo pmset repeat wakeorpoweron MTWRF 06:55:00
pmset -g sched   # 验证：含 "wakepoweron at 6:55AM MTWRF"

# 取消（如需）
sudo pmset repeat cancel
```

## 已知限制

- **macOS 休眠时 launchd 不触发**。若 07:00/07:10 Mac 在睡眠，错过本次推送，下次启动不补跑（plist 未配 `RunAtLoad`，避免每次开机骚扰）。盘前任务务必配 `sudo pmset repeat wakeorpoweron MTWRF 06:55:00`；盘后 20:00 通常机器在线，不必额外配。
- `launchctl load`/`unload` 在 macOS 13+ 标为 deprecated（仍向后兼容）。新写法：`launchctl bootstrap gui/$(id -u) <plist>` 安装、`launchctl bootout gui/$(id -u) <plist>` 卸载。本仓库统一沿用 `load/unload`，避免风格分裂；如未来 `load` 真被移除再迁。

## 排障

- 日志：`/tmp/tradesystem-recommend-*.log`、`/tmp/tradesystem-today-{pre,post}.log`
- 立即重载：`launchctl unload ... && launchctl load ...`
- 看 launchd 自身是否报错：`log show --predicate 'process == "launchd"' --info --last 1h | grep tradesystem`
- 钉钉凭据未注入：`today-runner.sh` 在 log 头打 `[env] DINGTALK_WEBHOOK_TOKEN=set DINGTALK_WEBHOOK_SECRET=set`；若任一为空 → 检查 `~/.config/tradeSystem.env` 路径、权限、行尾 CRLF

## 最近 4 个交易日交易复盘（已迁出 launchd）

该任务统一由 Codex `automation-2` 在工作日 22:30 调度，唯一业务实现为
`scripts/automations/four_trading_day_review.py`。本目录不再提供第二套 launchd 入口；
调整运行时间或任务说明时应更新现有 Codex 自动化，不要新增并行调度。

## 成交额 Top20 板块集中度（工作日 21:00）

盘后 20:00 任务之后、tushare 日线落地后,出当日 top20 板块集中度 + 趋势,推钉钉。
runner 同时 source `scripts/.env`(TUSHARE_TOKEN,`index_member_all` 申万成分需积分)与
`~/.config/tradeSystem.env`(钉钉);非交易日无成交额数据时任务内自动跳过,不写库不推送。

```bash
# 1. 包装脚本可执行
chmod +x deploy/launchd/volume-watch-runner.sh

# 2. 复制 plist
cp deploy/launchd/com.alyx.tradesystem.volume-watch.plist ~/Library/LaunchAgents/

# 3. 加载
launchctl load ~/Library/LaunchAgents/com.alyx.tradesystem.volume-watch.plist

# 4. 验证
launchctl list | grep tradesystem.volume-watch

# 5. 真触发立即测试（非交易日仅验 launchd 链路 + 凭据注入,无数据则跳过不推送）
launchctl start com.alyx.tradesystem.volume-watch
tail -f /tmp/tradesystem-volume-watch.log   # 看 [env] 三凭据 =set + 运行结果
```

卸载：

```bash
launchctl unload ~/Library/LaunchAgents/com.alyx.tradesystem.volume-watch.plist
rm ~/Library/LaunchAgents/com.alyx.tradesystem.volume-watch.plist
```

**时段**：21:00 在 today-post(20:00)之后、sector-correlation(21:15)之前，无冲突。

## 板块相关性（工作日 21:15）

错开 volume-watch(21:00) 15 分钟,降 Tushare 镜像并发。Tushare 主源拉多日活跃板块(行业按成交额 /
概念按换手率)+ 4 指数 → 双窗 20/60 原始相关 + 剔大盘超额相关 + β → 落 `sector_correlation_daily`
+ 推钉钉。runner source `scripts/.env`(TUSHARE_TOKEN)+`~/.config/tradeSystem.env`(钉钉);
非交易日/数据不足任务内自动跳过,不写库不推送。

```bash
# 1. 包装脚本可执行
chmod +x deploy/launchd/sector-correlation-runner.sh

# 2. 复制 plist
cp deploy/launchd/com.alyx.tradesystem.sector-correlation.plist ~/Library/LaunchAgents/

# 3. 加载
launchctl load ~/Library/LaunchAgents/com.alyx.tradesystem.sector-correlation.plist

# 4. 验证
launchctl list | grep tradesystem.sector-correlation

# 5. 真触发立即测试（非交易日仅验 launchd 链路 + 凭据注入,无数据则跳过不推送）
launchctl start com.alyx.tradesystem.sector-correlation
tail -f /tmp/tradesystem-sector-correlation.log   # 看 [env] 三凭据 =set + 运行结果
```

卸载：

```bash
launchctl unload ~/Library/LaunchAgents/com.alyx.tradesystem.sector-correlation.plist
rm ~/Library/LaunchAgents/com.alyx.tradesystem.sector-correlation.plist
```

**时段**：21:15 在 volume-watch(21:00)与 trend-leader(21:30)之间，无冲突。

## 串阳首阴股票池（工作日 21:50）

排在 volume-watch(21:00)、sector-correlation(21:15)、trend-leader(21:30)、market-timing(21:40)之后，读取当日成交额集中度、同花顺概念分支和老师观点，由 LLM 只裁决主线申万二级/概念分支（失败降级成交额 Top-K），再扫描“连续五阳后第一根阴线”的确认票，落 `data/reports/string-yang/YYYY-MM-DD.md` 并推钉钉。报告全标 `[判断]`，不出价位、不写交易计划层、不自动入关注池。

```bash
# 1. 包装脚本可执行
chmod +x deploy/launchd/string-yang-runner.sh

# 2. 复制 plist
cp deploy/launchd/com.alyx.tradesystem.string-yang.plist ~/Library/LaunchAgents/

# 3. 加载
launchctl load ~/Library/LaunchAgents/com.alyx.tradesystem.string-yang.plist

# 4. 验证
launchctl list | grep tradesystem.string-yang

# 5. 真触发立即测试（非交易日任务内守卫；dry-run 可手动跑 CLI）
launchctl start com.alyx.tradesystem.string-yang
tail -f /tmp/tradesystem-string-yang.log
```

卸载：

```bash
launchctl unload ~/Library/LaunchAgents/com.alyx.tradesystem.string-yang.plist
rm ~/Library/LaunchAgents/com.alyx.tradesystem.string-yang.plist
```

**时段**：21:50 在 market-timing(21:40)之后、research-digest/earnings-digest(22:00)之前，避免与主线板块和趋势扫描高峰并发。

## 4日均线二波尾盘观察池（中国时间工作日 14:50）

近 60 自然日人工确认历史龙头/最票宇宙 → 目标日前 9 个完成交易日日线 + 目标日 14:50 实时价/累计成交额临时 bar → MA4 重新拐头向上 + 累计成交额同时突破 5/10 日均额线 + 快照时未涨停 → 尾盘只读观察清单落 `data/reports/ma-breakout/YYYY-MM-DD.{md,json}` + 钉钉。实时价尚未收盘确认；新浪累计成交额从元换算到 Tushare 日线的千元量纲后再进入原检测器。

runner source `scripts/.env`(TUSHARE_TOKEN)+`~/.config/tradeSystem.env`(钉钉)。CLI 只允许上海当日 14:45～15:00，非交易日、非当日或时间窗外均跳过且不落盘不推送；行情日期必须为当日且最多陈旧 10 分钟。plist 仅列周一至周五 14:50，不再配置周日或 21:35 收盘版。Mac 休眠错过可接受；launchd 合并补触发会被 CLI 时间窗安全拦截。

```bash
# 1. 包装脚本可执行
chmod +x deploy/launchd/ma-breakout-runner.sh

# 2. 复制 plist
cp deploy/launchd/com.alyx.tradesystem.ma-breakout.plist ~/Library/LaunchAgents/

# 3. 加载
launchctl load ~/Library/LaunchAgents/com.alyx.tradesystem.ma-breakout.plist

# 4. 验证
launchctl list | grep tradesystem.ma-breakout

# 5. launchd 链路检查（仅在上海 14:45～15:00 的交易日会真跑，其余时间安全跳过）
launchctl start com.alyx.tradesystem.ma-breakout
tail -f /tmp/tradesystem-ma-breakout.log

# 6. 尾盘时间窗内手工验证真实扫描链路（--dry-run 不推送）
deploy/launchd/ma-breakout-runner.sh --dry-run
```

卸载：

```bash
launchctl unload ~/Library/LaunchAgents/com.alyx.tradesystem.ma-breakout.plist
rm ~/Library/LaunchAgents/com.alyx.tradesystem.ma-breakout.plist
```

**时段**：目标业务时间为中国时间 14:50，晚间不再重复运行。macOS launchd 按系统时区触发，本机应保持 Asia/Shanghai；runner 同时显式设置 `TZ=Asia/Shanghai`，CLI 再以该时区校验当日与 14:45～15:00 时间窗。

## 每日最票候选确认稿（工作日 22:30）

汇总复盘预填、趋势池、历史最票、老师观点与认知证据 → 生成复盘第 5 步「龙头 / 最票」候选确认稿 → 落本地 Markdown/JSON → 推送钉钉 Markdown。v1 只支持钉钉草稿 + Codex/CLI 确认；钉钉按钮 callback / 直接写回 deferred 到 v2。

```bash
# 1. 包装脚本可执行
chmod +x deploy/launchd/daily-leaders-runner.sh

# 2. 复制 plist
cp deploy/launchd/com.alyx.tradesystem.daily-leaders.plist ~/Library/LaunchAgents/

# 3. 加载
launchctl load ~/Library/LaunchAgents/com.alyx.tradesystem.daily-leaders.plist

# 4. 验证
launchctl list | grep tradesystem.daily-leaders

# 5. 真触发立即测试（会执行 propose --push；确认当前允许推送再运行）
rm -f /tmp/tradesystem-daily-leaders.out.log /tmp/tradesystem-daily-leaders.err.log
launchctl start com.alyx.tradesystem.daily-leaders
tail -f /tmp/tradesystem-daily-leaders.out.log
tail -f /tmp/tradesystem-daily-leaders.err.log
```

卸载：

```bash
launchctl unload ~/Library/LaunchAgents/com.alyx.tradesystem.daily-leaders.plist
rm ~/Library/LaunchAgents/com.alyx.tradesystem.daily-leaders.plist
```

**时段**：22:30 在 `board-break`(21:20)、`trend-leader`(21:30)、`market-timing`(21:40) 等盘后派生任务之后；`ma-breakout` 已提前到 14:50 尾盘快照。供用户在 Codex 中确认后执行 `python3 main.py daily-leaders confirm --date YYYY-MM-DD --input-by codex`。本任务不自动写复盘、不写交易计划、不提供买卖建议或价位目标。

## 完成月月线模式观察池（每月 2 日 23:10）

全市场完成月月线 + `adj_factor` 前复权 → 外部历史 as-of 宇宙覆盖认证 → 三策略检测 → 公告日 as-of 财务校验 → 维护月线观察状态 → 落 `data/reports/monthly-pattern/YYYY-MM-DD.md` + 钉钉。外部宇宙取股票基础资料 `L/D/P` 全状态并按 `list_date/delist_date` 还原目标月，只有写入 `monthly_pattern_bar_manifests` certified 收据的月线事实才参与扫描；供应商因证券代码迁移同时返回新旧影子代码时，仅在同交易所、完整且有效的月线九字段与复权因子一致、唯一对应宇宙内 canonical code 且窗口内无角色反转/链式映射时抑制重复并留版本化 manifest 收据，缓存复用校验九字段摘要并将持久化六字段+复权逐项对照 canonical bar，schema/摘要/计数/事实损坏即 cache miss，其他情况继续 fail-closed，本层不拼接分段行情或迁移旧 episode；历史行业映射没有 as-of 能力时题材策略 fail-closed。财务同公告日修订按内容哈希追加，无法证明修订独立公开日时从本机首次观测日起才可见；最近法定到期报告期与最近法定年报的三组件必须分别覆盖达标，年报才可形成 verified，中报仅作 pre_screen。基本面 verified 需后续严格下一完成月仍满足条件才可转 active；active 转弱进入 risk，risk 仅在技术严格重新转强且对应财务/主线资格恢复后回 active；从未 active 的基本面 episode 从 risk 恢复时先回 verified 重走下一完成月确认。最近两个严格相邻完成月均收于月 MA5 下方后进入 episode 终态 exited，后续重新命中另开新 episode。扫描日早于既有 run/pool 状态水位时 fail-closed，历史更正须从可信检查点重建完整后缀。共落 `monthly_pattern_bars` / `monthly_pattern_bar_manifests` / `monthly_pattern_financial_snapshots` / `monthly_pattern_runs` / `monthly_pattern_pool` 五表；主线只是申万二级成交额稳定前排 `[判断]`。不写 `TradeDraft` / `TradePlan` / 关注池，不给买卖建议。

仓库只提供以下 launchd 模板，本次代码变更不会自动复制到 `~/Library/LaunchAgents` 或调用 `launchctl`：

```bash
# 安装前先验证格式与 dry-run 业务链路
plutil -lint deploy/launchd/com.alyx.tradesystem.monthly-pattern.plist
python3 scripts/main.py monthly-pattern daily --input-by codex --dry-run

# 确认后人工安装
chmod +x deploy/launchd/monthly-pattern-runner.sh
cp deploy/launchd/com.alyx.tradesystem.monthly-pattern.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.alyx.tradesystem.monthly-pattern.plist
launchctl list | grep tradesystem.monthly-pattern

# 真触发会执行默认 daily（落库、报告并推钉钉）
launchctl start com.alyx.tradesystem.monthly-pattern
tail -f /tmp/tradesystem-monthly-pattern.log
```

卸载：

```bash
launchctl unload ~/Library/LaunchAgents/com.alyx.tradesystem.monthly-pattern.plist
rm ~/Library/LaunchAgents/com.alyx.tradesystem.monthly-pattern.plist
```

**时段**：每月 2 日 23:10 单次运行，在当月第一个自然日后的晚间处理上一个完成月，避免原工作日频率对同一完成月重复刷新和推送。它是低频观察任务，休眠错过可接受，不配 pmset 唤醒；`RunAtLoad=false`，不做开机补跑。**调度唯一入口=per-task launchd**，不进 `main.py schedule` / APScheduler。

## 月线指标日频变化监控（工作日 19:10）

`monthly-pattern monitor-daily` 与上面的月度业务池任务完全独立：它用 SQLite `mode=ro` 调用手工 `monitor` 的事实计算，生产禁止 `max_seeds` 截断；默认仅在“最近已收盘开放日=上海自然日今天”时运行，周末、节假日和盘中不回退重跑上一开放日。初始化、交易日历或只读库故障也会落 `blocked` 健康收据，而不是只留 launchd 日志；日历未确认时只落本地并把健康事件保留在 pending，绝不越过交易日推送闸门。完整 latest 快照保存到 `data/runs/monthly-pattern-monitor/snapshots/YYYY-MM-DD.json`，latest 报告保存到 `data/reports/monthly-pattern-monitor/YYYY-MM-DD.md`；每次实际写入另在 `data/runs/monthly-pattern-monitor/attempts/YYYY-MM-DD/` 原子追加不可覆盖的 `planned`、逐批 `delivery` 和 `final` JSON，保留完整事件、发送状态与失败原因。`state.json` 只保存通知基线与 pending/sent/suppressed 水位，不是业务数据库或观察池。

只有同一完成月的两次 `complete` 快照才比较个股状态。`partial/blocked` 只产生运行健康事件且不推进股票基线；健康指纹包含所有导致 partial 的缺口股票身份与原因（含 `insufficient_history`），等量换票也不会静默。首次完整运行只初始化基线，完成月翻页只报 rollover 并重建基线，避免把整批种子变化误报为进出。动态 5 月线收复/失守、日/周 MACD 双线零上与零上运行、日线重回和日周共振等状态变化才进入通知候选。事件先原子写入 pending，钉钉成功后才记 sent；失败保留下次按事件流 at-least-once 重试。显式 `--date` 默认是零写入预览且永不推送；只有同时带 `--no-push` 才保存、推进本地基线并明确抑制本轮新通知；`--dry-run` 同样不写任何文件。

安装与真实验证：

```bash
chmod +x deploy/launchd/monthly-pattern-monitor-runner.sh
plutil -lint deploy/launchd/com.alyx.tradesystem.monthly-pattern-monitor.plist
bash -n deploy/launchd/monthly-pattern-monitor-runner.sh

# 历史完整日只建初始基线，不推送
python3 scripts/main.py monthly-pattern monitor-daily \
  --date 2026-07-24 --no-push --json

cp deploy/launchd/com.alyx.tradesystem.monthly-pattern-monitor.plist \
  ~/Library/LaunchAgents/
launchctl bootstrap "gui/$(id -u)" \
  ~/Library/LaunchAgents/com.alyx.tradesystem.monthly-pattern-monitor.plist

# 必须真触发；当前不在上海 19:10-19:25 窗口时 runner 应轻量 skip、退出 0
launchctl kickstart -k \
  "gui/$(id -u)/com.alyx.tradesystem.monthly-pattern-monitor"
launchctl print \
  "gui/$(id -u)/com.alyx.tradesystem.monthly-pattern-monitor"
tail -100 /tmp/tradesystem-monthly-pattern-monitor.log
```

卸载：

```bash
launchctl bootout \
  "gui/$(id -u)/com.alyx.tradesystem.monthly-pattern-monitor"
rm ~/Library/LaunchAgents/com.alyx.tradesystem.monthly-pattern-monitor.plist
```

**时段**：plist 用 `StartInterval=900` 做无时区 tick，runner 用 `TZ=Asia/Shanghai` 只放行 19:10（含）至 19:25（不含）窗口内的一次调用；因此 Mac 切换本机时区不会把任务漂移到上海次日。该窗口距 18:30 cognition-digest 至少 40 分钟、距 20:00 today-post 至少 35 分钟，避开 21:00 后的密集派生任务。休眠错过可接受；下次完整运行会与最近完整基线比较，未发送 pending 继续重试。该任务不写 SQLite 七表、月线池、关注池、TradeDraft 或 TradePlan，不进 `main.py schedule` / APScheduler。

## 研报速读（已迁移到 Codex 自动化）

研报速读不再使用 macOS launchd。生产定时入口是 Codex 自动化「每日慧博研报速读（Computer Use）」：每天 22:00 触发，自动化先按 A 股交易日/交易日前一天判断是否继续，然后必须通过 Computer Use 操作慧博终端进入「热点研报追踪」、获取当前 HotReport URL、按预筛候选在慧博终端下载 PDF 到本地目录，再运行 JS workflow 读取这些本地 PDF 并发布。正式自动化不使用旧 `HUIBO_HOT_REPORT_URL` 兜底，也不走裸 URL 直连下载 PDF。

旧 `com.alyx.tradesystem.research-digest` launchd 已停用；仓库中的 plist/runner 仅保留为历史排障参考，不再安装。若本机仍残留旧任务，按以下方式卸载：

```bash
launchctl unload ~/Library/LaunchAgents/com.alyx.tradesystem.research-digest.plist
rm ~/Library/LaunchAgents/com.alyx.tradesystem.research-digest.plist
```

排障时可以手工运行 JS workflow，但必须显式提供当天通过 Computer Use 获取的 HotReport URL，并把 `HUIBO_REPORT_PDF_DIR` 指向慧博终端实际下载的 PDF 目录；正式排障也应保持 `HUIBO_ALLOW_DIRECT_PDF_DOWNLOAD=0`，只有定位 404/token 问题时才临时打开直连下载。

## 交易认知沉淀汇总（recent3d 工作日 18:30 / weekly 周日 20:00 / monthly 每月1号 09:00）

参数化 runner（window 作为 `$1` 透传）+ 3 个 plist 各自触发一个窗口。只读认知三表 → 热度 + 共识 +
新增 → Antigravity 建议 → 推钉钉。三个任务共用同一 runner、同一 `~/.config/tradeSystem.env`(钉钉/ANTIGRAVITY)
与合并日志 `/tmp/tradesystem-cognition-digest.log`；非交易日 / 窗口内无认知数据时任务内自动标注，不报错、不冒充。

```bash
# 1. 包装脚本可执行
chmod +x deploy/launchd/cognition-digest-runner.sh

# 2. 复制 plist（3 个一起拷）
cp deploy/launchd/com.alyx.tradesystem.cognition-digest-*.plist ~/Library/LaunchAgents/

# 3. 加载
launchctl load ~/Library/LaunchAgents/com.alyx.tradesystem.cognition-digest-recent3d.plist
launchctl load ~/Library/LaunchAgents/com.alyx.tradesystem.cognition-digest-weekly.plist
launchctl load ~/Library/LaunchAgents/com.alyx.tradesystem.cognition-digest-monthly.plist

# 4. 验证
launchctl list | grep tradesystem.cognition-digest

# 5. 真触发立即测试（先 dry-run 验产物再真推；非交易日仅验 launchd 链路 + 凭据注入）
launchctl start com.alyx.tradesystem.cognition-digest-weekly
tail -f /tmp/tradesystem-cognition-digest.log   # 看 [env] DINGTALK/ANTIGRAVITY =set + 运行结果
```

卸载：

```bash
launchctl unload ~/Library/LaunchAgents/com.alyx.tradesystem.cognition-digest-recent3d.plist
launchctl unload ~/Library/LaunchAgents/com.alyx.tradesystem.cognition-digest-weekly.plist
launchctl unload ~/Library/LaunchAgents/com.alyx.tradesystem.cognition-digest-monthly.plist
rm ~/Library/LaunchAgents/com.alyx.tradesystem.cognition-digest-*.plist
```

**时段**：recent3d 18:30 在 today-post(20:00) 之前、空档无冲突；weekly 周日 20:00 与 recommend-weekly(周日 20:00) 同点但互不依赖、均短 I/O 任务可接受；monthly 每月 1 号 09:00 为非交易时段无争用。认知沉淀错过可接受(非交易决策),不配 pmset 唤醒。**调度唯一入口=launchd per-task plist**,不进 `main.py schedule`/APScheduler(避免双触发)。

## 盘中实时阈值监控（每 5 分钟）

新增临时规则：方盛制药 `603998.SH >=11.11元`，2026-09-03～09-16（首尾包含，共14自然日）有效。新浪实时行情；等于触发、首次已命中提醒、同日持续命中去重、回落后重新触达提醒；9月17日起不再为该规则取数或推送。沿用下面的独立 runtime、双状态切换 guard 与300秒调度，不新增任务。

监控引擎、CLI、launchd 与状态机继续保留。长期生产规则包括：新浪实时 `000001.SH` 上证指数从 3955 点下方站上 3955 点（等于 3955 即命中）；同花顺官方 `realhead_v6` 实时 `883421.THS` 同花顺全A（沪深）相对昨收的单日涨跌幅严格 `<-4.00%`（等于不触发）。后者由同一回包的最新点位和昨收自行计算，要求 `updateTime` 为上海当天且不超过 10 分钟，布局漂移、昨收非法或行情陈旧均 fail-closed。当前临时生产规则包括：仅 2026-08-31 生效的 `300285.SZ` 国瓷材料严格跌破 67.22 元，以及 2026-08-31～09-02 生效的 `688361.SH` 中科飞测严格跌破前 5 个已收盘交易日的前复权 MA5；两者等于阈值均不触发、首次已在阈值下方会推送、持续命中去重，恢复后再次跌破可重推。MA5 样本由生产 SQLite 只读交易日历锁定，日线和复权因子必须逐日完整对齐，并用实时 `pre_close` 锚到当日盘口价格坐标；日历、行情、因子、实时前收盘或样本不足均 fail-closed，不推伪信号。历史科创50/凯莱英临时规则保留审计，金健米业、红四方、京粮控股及旧科创50跌破/收复规则保持下线；新规则均使用独立 rule id，不继承旧状态。

```bash
RUNTIME_ROOT=/Users/alyx/tradeSystem/.worktrees/intraday-monitor-runtime
REVIEWED_COMMIT=<REVIEWED_COMMIT>
set -euo pipefail

# 停任务前先验证 commit 存在，并确认既有 runtime 没有代码改动。
git -C /Users/alyx/tradeSystem rev-parse --verify "$REVIEWED_COMMIT^{commit}" >/dev/null
if [ -e "$RUNTIME_ROOT/.git" ]; then
  test -z "$(git -C "$RUNTIME_ROOT" status --porcelain --untracked-files=no)"
fi
# 切换前必须确认目标提交认识所有未送达事件。旧 runtime 尚无 guard 时仅允许
# pending 为空；一旦部署本版本，后续回滚也会被同一检查机械阻断，避免旧代码
# 把新规则发送失败的 pending 当成退役事件永久丢弃。
STATE_PATH=/Users/alyx/tradeSystem/data/runs/intraday-monitor/state.json
MARKET_SCAN_STATE_PATH=/Users/alyx/tradeSystem/data/runs/intraday-monitor/market-scan-state.json
TRANSITION_GUARD="$RUNTIME_ROOT/deploy/launchd/intraday_monitor_transition_guard.py"
if [ -f "$TRANSITION_GUARD" ]; then
  # 分两次调用，兼容首次升级前只接受单个 --state-path 的旧 guard；新 guard
  # 同样支持。不得合成一次重复参数调用，否则旧 argparse 只保留最后一路。
  /usr/bin/python3 "$TRANSITION_GUARD" \
    --repo /Users/alyx/tradeSystem \
    --target-commit "$REVIEWED_COMMIT" \
    --state-path "$STATE_PATH"
  /usr/bin/python3 "$TRANSITION_GUARD" \
    --repo /Users/alyx/tradeSystem \
    --target-commit "$REVIEWED_COMMIT" \
    --state-path "$MARKET_SCAN_STATE_PATH"
else
  /usr/bin/python3 -c 'import json, pathlib, sys; ps=[pathlib.Path(x) for x in sys.argv[1:]]; bad=[str(p) for p in ps if p.exists() and (json.loads(p.read_text()).get("pending_events") or [])]; assert not bad, f"旧 runtime 无切换 guard，pending 非空，禁止切换: {bad}"' "$STATE_PATH" "$MARKET_SCAN_STATE_PATH"
fi
# 已加载的任务必须先停掉，避免切换 detached worktree 时与正在启动的 Python 竞态。
if launchctl print "gui/$(id -u)/com.alyx.tradesystem.intraday-monitor" >/dev/null 2>&1; then
  launchctl bootout "gui/$(id -u)" \
    "$HOME/Library/LaunchAgents/com.alyx.tradesystem.intraday-monitor.plist"
fi
if [ -e "$RUNTIME_ROOT/.git" ]; then
  git -C "$RUNTIME_ROOT" switch --detach "$REVIEWED_COMMIT"
else
  git worktree add --detach "$RUNTIME_ROOT" "$REVIEWED_COMMIT"
  ln -s /Users/alyx/tradeSystem/data "$RUNTIME_ROOT/data"
fi
test "$(git -C "$RUNTIME_ROOT" rev-parse HEAD)" = "$REVIEWED_COMMIT"
test -z "$(git -C "$RUNTIME_ROOT" status --porcelain --untracked-files=no)"
test "$(readlink "$RUNTIME_ROOT/data")" = /Users/alyx/tradeSystem/data

chmod +x "$RUNTIME_ROOT/deploy/launchd/intraday-monitor-runner.sh"
plutil -lint "$RUNTIME_ROOT/deploy/launchd/com.alyx.tradesystem.intraday-monitor.plist"
bash -n "$RUNTIME_ROOT/deploy/launchd/intraday-monitor-runner.sh"

cp "$RUNTIME_ROOT/deploy/launchd/com.alyx.tradesystem.intraday-monitor.plist" \
  ~/Library/LaunchAgents/
launchctl enable gui/$(id -u)/com.alyx.tradesystem.intraday-monitor
launchctl bootstrap gui/$(id -u) \
  ~/Library/LaunchAgents/com.alyx.tradesystem.intraday-monitor.plist
launchctl list | grep tradesystem.intraday-monitor

# 手工启动一次 tick；仅在盘中、开放交易日且行情新鲜时检查
launchctl kickstart -k gui/$(id -u)/com.alyx.tradesystem.intraday-monitor
tail -f /tmp/tradesystem-intraday-monitor.log
```

以上步骤使用 `set -euo pipefail`，任何验签、切换、lint 或加载失败都会立即停止，禁止带错继续。若失败发生在 `bootout` 之后，应先修复报错并从 `git switch --detach "$REVIEWED_COMMIT"` 继续完成加载；不得在未通过 HEAD、干净状态、data 链接、plist 与 shell 语法校验时恢复任务。

卸载：

```bash
launchctl unload ~/Library/LaunchAgents/com.alyx.tradesystem.intraday-monitor.plist
launchctl disable gui/$(id -u)/com.alyx.tradesystem.intraday-monitor
rm ~/Library/LaunchAgents/com.alyx.tradesystem.intraday-monitor.plist
```

模板固定把 `ProgramArguments` 指向 `.worktrees/intraday-monitor-runtime`。该目录必须是从已审查 commit 创建的 detached worktree，并把生产 `data/` 目录链接进去，以复用只读交易日历和正式 pending/sent 状态。runner 会从自身文件位置推导代码根目录，不能把模板改回仍含未提交代码的主工作区。`launchctl enable` 用于清除 macOS 持久化的 disabled 覆盖，仅执行 `load` 不足以证明任务已启用。

`data/runs/intraday-monitor/state.json` 用于单标的阈值状态，`market-scan-state.json` 用于横截面规则的 pending/sent 去重。事件先原子落 pending，推送成功才记 sent；发送失败同一交易日下个 tick 重试，横截面待发送事件在 10:00 后可继续重试但不再抓行情，跨日过期。transition guard 切换前同时检查两份状态。全链路不写 SQLite、持仓、关注池或计划层。**Mac 休眠时 launchd 不执行**，这是盘中提醒的真实可用性边界；若要求覆盖整个交易时段，应配置盘中唤醒或迁到 VPS。

注册新的生产规则并经用户授权做真实链路验收时，只在交易时段执行一次：

```bash
python3 scripts/main.py intraday-monitor e2e-test --rule-id RULE_ID \
  --input-by USER --confirm-real-push --json
```

该命令只在用户明确授权后执行，并要求一次性显式参数 `--confirm-real-push`；缺少确认时在初始化行情源前返回 `authorization_required`，不会访问日历、行情或钉钉。授权后使用当日新鲜真实行情和仅本次测试线发送醒目标注“测试”的钉钉消息，且不读写正式状态。只有退出码为 0、`status=complete` 且 `pushed=true` 才能视为真实链路验收成功。

## 全市场盘中半小时扫描

09:30 与 13:00 保存基线；10:00、10:30、11:00、11:30、13:30、14:00、14:30、15:00 用两点全市场实时快照生成最近半小时摘要并推钉钉。任务只认本地只读交易日历中的开放日；股票清单少于 4000、实时覆盖低于 95% 或行情陈旧时 fail-closed。缺少上一槽位时只发当前快照并明确标 `partial / 最近半小时未计算`。

```bash
chmod +x deploy/launchd/intraday-summary-runner.sh
plutil -lint deploy/launchd/com.alyx.tradesystem.intraday-summary.plist
bash -n deploy/launchd/intraday-summary-runner.sh

cp deploy/launchd/com.alyx.tradesystem.intraday-summary.plist \
  ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.alyx.tradesystem.intraday-summary.plist
launchctl list | grep tradesystem.intraday-summary
tail -f /tmp/tradesystem-intraday-summary.log
```

滚动状态/outbox 为 `data/runs/intraday-summary/state.json`，报告为 `data/reports/intraday-summary/YYYY-MM-DD/HHMM.md`。钉钉失败会在同槽位 5 分钟窗口内重试；成功后同槽位不重复抓取或推送。Mac 休眠或错过 5 分钟窗口时不补发陈旧摘要。

## 断板反包盘后扫描（工作日 21:20）

昨日连板≥2 只当日断板（≤6%未跌停，10cm主板剔ST）→ 八维度加权打分（主线/增减持/定增/公告/业绩/近10日涨幅/MACD，全 [判断] 附依据明细）+ LLM 两两 PK 循环赛（`--no-llm` 关）→ 双排序观察清单 MD 落盘 `data/reports/board-break/` + 推钉钉。
runner source `scripts/.env`(TUSHARE_TOKEN) + `~/.config/tradeSystem.env`(钉钉/ANTIGRAVITY)；非交易日任务内自动跳过（不落盘、不推送）；核心源失败（`source_failed`）不产出正常候选清单，落失败报告 + 推告警 + 非零退出。

```bash
# 1. 包装脚本可执行
chmod +x deploy/launchd/board-break-runner.sh

# 2. 复制 plist
cp deploy/launchd/com.alyx.tradesystem.board-break.plist ~/Library/LaunchAgents/

# 3. 加载
launchctl load ~/Library/LaunchAgents/com.alyx.tradesystem.board-break.plist

# 4. 验证
launchctl list | grep tradesystem.board-break

# 5. 真触发立即测试（非交易日仅验 launchd 链路 + 凭据注入，无候选则跳过不推送）
rm -f /tmp/tradesystem-board-break.log
launchctl start com.alyx.tradesystem.board-break
tail -f /tmp/tradesystem-board-break.log   # 看 [env] 三凭据 =set + 运行结果
```

卸载：

```bash
launchctl unload ~/Library/LaunchAgents/com.alyx.tradesystem.board-break.plist
rm ~/Library/LaunchAgents/com.alyx.tradesystem.board-break.plist
```

**时段**：21:20 在 sector-correlation(21:15) 与 trend-leader(21:30) 之间，主线板块归属取 `daily_volume_concentration` 当日快照，无冲突。断板反包是盘后只读观察清单（非交易决策），错过可接受，不配 pmset 唤醒。**调度唯一入口=per-task launchd**，不进 `main.py schedule`/APScheduler。

## 月线派生事实回补（仅手工，不挂 launchd）

`monthly-pattern facts-backfill` 是针对历史月线事实缺口的审计修复入口，不是日常定时任务。必须先运行 `--dry-run`，核对严格只读真实库所生成的内存逐项收据、未决计数和 `receipt_hash`；只有用户确认该批收据后，才可在不带 `--dry-run` 的命令中显式传入相同 `--expect-receipt-hash`。真实执行会重新拉取并重算，哈希绑定 raw/manifest/既有派生事实水位，`certified_no_trade` 还绑定全市场月线与历史宇宙的完整排序行摘要；事实水位、未决项、截断或 A 股分类守恒任一不一致即不写派生事实。确认后只在 `BEGIN IMMEDIATE` 同一事务内运行派生两表专用 schema ensure，并按 SQL 指纹验证表、索引和防改触发器，禁止调用全库 migrate；DDL、运行收据与事实行共同提交或回滚。`monitor_preview` 仅作信息性预览，不属于事实写入确认哈希。

该入口只允许写 `monthly_pattern_derived_month_facts` 和追加式 `monthly_pattern_derived_fact_runs`，不覆盖原始月线五表、不修改观察池/关注池/计划层、不推送。A 股选股宇宙中的缺口才进入回补；沪深 B 股代码 `200/201/900` 单列审计，不当作 A 股缺口。日线 `vol=手`、`amount=千元` 统一换算为月线 `股`、`元` 后才允许交叉验签。`certified_no_trade` 是“在册但整月两层行情均无成交记录”的证据，不得转换为平盘月 K。由于其运行依赖人工确认且可能耗时数分钟，禁止配置 launchd、APScheduler 或其他自动重试。
