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
- `research-digest-runner.sh` — 包装脚本：cd 仓库根 → source `scripts/.env`(TUSHARE_TOKEN) + `~/.config/tradeSystem.env`(钉钉/GEMINI) → 调 `python3 main.py research-digest daily`
- `com.alyx.tradesystem.research-digest.plist` — 工作日 06:42 触发（研报速读：A股研报评级[巨潮] + 美股 yfinance 评级 → Top3；非交易日/窗口内无变动自动标注，不报错）
- `cognition-digest-runner.sh` — 包装脚本（参数化，window 作为 `$1` 透传）：cd 仓库根 → source `scripts/.env` + `~/.config/tradeSystem.env`(钉钉/GEMINI) → 调 `python3 main.py cognition-digest <window>`
- `com.alyx.tradesystem.cognition-digest-recent3d.plist` — 每交易日 18:30 触发（认知沉淀近 3 日汇总；日志 `/tmp/tradesystem-cognition-digest.log`）
- `com.alyx.tradesystem.cognition-digest-weekly.plist` — 周日 20:00 触发（认知沉淀周汇总；同一日志 `/tmp/tradesystem-cognition-digest.log`）
- `com.alyx.tradesystem.cognition-digest-monthly.plist` — 每月 1 号 09:00 触发（认知沉淀月汇总；同一日志 `/tmp/tradesystem-cognition-digest.log`）

## 前置条件

- `~/.config/tradeSystem.env` 已存在且含 `DINGTALK_WEBHOOK_TOKEN` + `DINGTALK_WEBHOOK_SECRET`（盘前/盘后/行业推荐共用同一对凭据）
- `python3` 在 `/usr/bin/python3`（或修改 runner 内的绝对路径）
- `gemini` 在 `/opt/homebrew/bin/gemini`（或修改 PATH，仅行业推荐用到）

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

## 研报速读（工作日 06:42）

盘前最早一档,早于 today-pre(07:00)/recommend-daily(07:10)。A股取最近交易日研报评级(巨潮 cninfo,鞠磊「首次覆盖」加权),
美股按美东窗口拉 yfinance 评级方向变动(init/up/down/reinit)→ Top3 → MD 落盘 `data/reports/research-digest/` + 推钉钉。
runner source `scripts/.env`(TUSHARE_TOKEN)+`~/.config/tradeSystem.env`(钉钉/GEMINI);
非交易日 / 窗口内无评级变动时,任务内显式标注「无符合条件」,不报错、不冒充。

```bash
# 1. 包装脚本可执行
chmod +x deploy/launchd/research-digest-runner.sh

# 2. 复制 plist
cp deploy/launchd/com.alyx.tradesystem.research-digest.plist ~/Library/LaunchAgents/

# 3. 加载
launchctl load ~/Library/LaunchAgents/com.alyx.tradesystem.research-digest.plist

# 4. 验证
launchctl list | grep tradesystem.research-digest

# 5. 真触发立即测试（非交易日仅验 launchd 链路 + 凭据注入；先 dry-run 验产物再真推）
launchctl start com.alyx.tradesystem.research-digest
tail -f /tmp/tradesystem-research-digest.log   # 看 [env] DINGTALK/GEMINI =set + 运行结果
```

卸载：

```bash
launchctl unload ~/Library/LaunchAgents/com.alyx.tradesystem.research-digest.plist
rm ~/Library/LaunchAgents/com.alyx.tradesystem.research-digest.plist
```

**时段**：06:42 为盘前最早一档,与 today-pre(07:00)、recommend-daily(07:10) 错峰,均 I/O 短任务无资源争用。研报错过可接受(非交易决策),不配 pmset 唤醒。**调度唯一入口=launchd per-task plist**,不进 `main.py schedule`/APScheduler(避免双触发)。

## 交易认知沉淀汇总（recent3d 工作日 18:30 / weekly 周日 20:00 / monthly 每月1号 09:00）

参数化 runner（window 作为 `$1` 透传）+ 3 个 plist 各自触发一个窗口。只读认知三表 → 热度 + 共识 +
新增 → gemini 建议 → 推钉钉。三个任务共用同一 runner、同一 `~/.config/tradeSystem.env`(钉钉/GEMINI)
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
tail -f /tmp/tradesystem-cognition-digest.log   # 看 [env] DINGTALK/GEMINI =set + 运行结果
```

卸载：

```bash
launchctl unload ~/Library/LaunchAgents/com.alyx.tradesystem.cognition-digest-recent3d.plist
launchctl unload ~/Library/LaunchAgents/com.alyx.tradesystem.cognition-digest-weekly.plist
launchctl unload ~/Library/LaunchAgents/com.alyx.tradesystem.cognition-digest-monthly.plist
rm ~/Library/LaunchAgents/com.alyx.tradesystem.cognition-digest-*.plist
```

**时段**：recent3d 18:30 在 today-post(20:00) 之前、空档无冲突；weekly 周日 20:00 与 recommend-weekly(周日 20:00) 同点但互不依赖、均短 I/O 任务可接受；monthly 每月 1 号 09:00 为非交易时段无争用。认知沉淀错过可接受(非交易决策),不配 pmset 唤醒。**调度唯一入口=launchd per-task plist**,不进 `main.py schedule`/APScheduler(避免双触发)。
