# macOS launchd 部署（行业推荐定时推送）

适用：用户本机长期开机的 macOS。如果在 VPS 上跑，看仓库 `deploy/systemd/`。

## 文件

- `recommend-runner.sh` — 包装脚本：cd 仓库根 → source 项目 env → 调 `python3 main.py recommend`
- `com.alyx.tradesystem.recommend-daily.plist` — 工作日 07:10 触发
- `com.alyx.tradesystem.recommend-weekly.plist` — 周日 20:00 触发

## 前置条件

- `~/.config/tradeSystem.env` 已存在且含 `DINGTALK_WEBHOOK_TOKEN` + `DINGTALK_WEBHOOK_SECRET`
- `python3` 在 `/usr/bin/python3`（或修改 `recommend-runner.sh` 第 27 行）
- `gemini` 在 `/opt/homebrew/bin/gemini`（或修改 PATH）

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

## 已知限制

- **macOS 休眠时 launchd 不触发**。若 07:10 Mac 在睡眠，错过本次推送，下次启动不补跑（plist 未配 `RunAtLoad`，避免每次开机骚扰）。
- 若希望休眠也能触发，需 `pmset -g sched` 安排唤醒，或迁到 VPS。

## 排障

- 日志：`/tmp/tradesystem-recommend-*.log`
- 立即重载：`launchctl unload ... && launchctl load ...`
- 看 launchd 自身是否报错：`log show --predicate 'process == "launchd"' --info --last 1h | grep tradesystem`
