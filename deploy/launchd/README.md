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
