# macOS launchd 部署（盘前/盘后 + 行业推荐定时推送）

适用：用户本机长期开机的 macOS。如果在 VPS 上跑，看仓库 `deploy/systemd/`。

## 文件

- `recommend-runner.sh` — 包装脚本：cd 仓库根 → source 项目 env → 调 `python3 main.py recommend`
- `com.alyx.tradesystem.recommend-daily.plist` — 工作日 07:10 触发（行业日报）
- `com.alyx.tradesystem.recommend-weekly.plist` — 周日 20:00 触发（行业周报）
- `volume-watch-runner.sh` — 包装脚本：cd 仓库根 → source `scripts/.env`(TUSHARE_TOKEN) + `~/.config/tradeSystem.env`(钉钉) → 调 `python3 main.py volume-watch daily`
- `com.alyx.tradesystem.volume-watch.plist` — 工作日 21:00 触发（成交额 Top20 板块集中度日报；非交易日无数据自动跳过）
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
- `ma-breakout-runner.sh` — 包装脚本：cd 仓库根 → source `scripts/.env`(TUSHARE_TOKEN) + `~/.config/tradeSystem.env`(钉钉) → 调 `python3 main.py ma-breakout daily`
- `com.alyx.tradesystem.ma-breakout.plist` — 中国时间工作日 21:35 触发（Pacific 本机 05:35/06:35 双触发 + runner 时间窗守卫；4日均线二波观察池；日志 `/tmp/tradesystem-ma-breakout.log`）
- `daily-leaders-runner.sh` — 包装脚本：cd 仓库根 → source `~/.config/tradeSystem.env`(钉钉/LLM) → 调 `/usr/bin/python3 scripts/main.py daily-leaders propose --push`
- `com.alyx.tradesystem.daily-leaders.plist` — 工作日 22:30 触发（每日最票候选确认稿；stdout `/tmp/tradesystem-daily-leaders.out.log`，stderr `/tmp/tradesystem-daily-leaders.err.log`）

## 前置条件

- `~/.config/tradeSystem.env` 已存在且含 `DINGTALK_WEBHOOK_TOKEN` + `DINGTALK_WEBHOOK_SECRET`（盘前/盘后/行业推荐共用同一对凭据）
- `python3` 在 `/usr/bin/python3`（或修改 runner 内的绝对路径）
- `agy` 在 PATH 中，或通过 `ANTIGRAVITY_BIN` 指向 Antigravity CLI

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

## 最近 4 个交易日交易复盘（工作日 22:30）

生成完整 Markdown 报告并推送钉钉短摘要：

```bash
# 1. 包装脚本可执行
chmod +x deploy/launchd/four-trading-day-review-runner.sh

# 2. 复制 plist
cp deploy/launchd/com.alyx.tradesystem.four-trading-day-review.plist ~/Library/LaunchAgents/

# 3. 加载
launchctl load ~/Library/LaunchAgents/com.alyx.tradesystem.four-trading-day-review.plist

# 4. 验证
launchctl list | grep tradesystem.four-trading-day-review

# 5. 真触发立即测试
launchctl start com.alyx.tradesystem.four-trading-day-review
tail -f /tmp/tradesystem-four-trading-day-review.log
```

卸载：

```bash
launchctl unload ~/Library/LaunchAgents/com.alyx.tradesystem.four-trading-day-review.plist
rm ~/Library/LaunchAgents/com.alyx.tradesystem.four-trading-day-review.plist
```

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

**时段**：21:00 在 today-post(20:00)与 four-trading-day-review(22:30)之间,无冲突。

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

**时段**：21:15 在 volume-watch(21:00)与 four-trading-day-review(22:30)之间,无冲突。

## 4日均线二波观察池（中国时间工作日 21:35）

近 60 自然日历史龙头/最票宇宙 → 近 10 个有效行情日 → MA4 重新拐头向上（今日 MA4 上行，且上拐前至少两根 MA4 连续下行）+ 今日成交额同时突破 5/10 日成交额均线 + 当日未涨停 → 只读二波观察清单 + 钉钉。
runner source `scripts/.env`(TUSHARE_TOKEN)+`~/.config/tradeSystem.env`(钉钉);非交易日任务内自动跳过,不推送。

```bash
# 1. 包装脚本可执行
chmod +x deploy/launchd/ma-breakout-runner.sh

# 2. 复制 plist
cp deploy/launchd/com.alyx.tradesystem.ma-breakout.plist ~/Library/LaunchAgents/

# 3. 加载
launchctl load ~/Library/LaunchAgents/com.alyx.tradesystem.ma-breakout.plist

# 4. 验证
launchctl list | grep tradesystem.ma-breakout

# 5. launchd 链路测试（窗口外会被 runner 时间窗守卫跳过）
launchctl start com.alyx.tradesystem.ma-breakout
tail -f /tmp/tradesystem-ma-breakout.log

# 6. 手工验证真实扫描链路（绕过时间窗守卫；--dry-run 不推送）
MA_BREAKOUT_FORCE=1 deploy/launchd/ma-breakout-runner.sh --dry-run
```

卸载：

```bash
launchctl unload ~/Library/LaunchAgents/com.alyx.tradesystem.ma-breakout.plist
rm ~/Library/LaunchAgents/com.alyx.tradesystem.ma-breakout.plist
```

**时段**：目标业务时间为中国时间 21:35，在 trend-leader(21:30) 与 market-timing(21:40) 之间,无冲突。macOS launchd 本身按机器本地时区触发；当前 Pacific 主机用 05:35/06:35 双触发覆盖 PDT/PST，runner 只允许中国时间 21:20-22:05 继续执行，另一个季节性触发会自动跳过。

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

**时段**：22:30 在 `board-break`(21:20)、`trend-leader`(21:30)、`ma-breakout`(21:35)、`market-timing`(21:40) 等盘后派生任务之后，供用户在 Codex 中确认后执行 `python3 main.py daily-leaders confirm --date YYYY-MM-DD --input-by codex`。本任务不自动写复盘、不写交易计划、不提供买卖建议或价位目标。

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
