# macOS launchd 部署规范（行业推荐 / 定时任务）

## 适用范围

所有需要在本地 macOS 上跑的定时任务，包括但不限于：

- 行业推荐日报 / 周报（`recommend daily/weekly`）
- 盘前 / 盘后 / 复盘自动化（如未来迁出 OpenClaw）
- 任何需要 `~/.config/tradeSystem.env` 凭据的定时入口

VPS / Linux 走 systemd（见 `deploy/systemd/`），不适用本规则。

## 文件位置

| 类型 | 仓库内（模板） | 系统安装目标 |
|---|---|---|
| 包装脚本 | `deploy/launchd/<task>-runner.sh` | 不复制，由 plist 直接引用仓库路径 |
| LaunchAgent plist | `deploy/launchd/com.alyx.tradesystem.<task>.plist` | `~/Library/LaunchAgents/` |
| 部署说明 | `deploy/launchd/README.md` | — |

## 包装脚本必须项（4 条）

任何 launchd 调用的 `*-runner.sh` 必须包含以下 4 段，**缺一就会运行时炸**：

```bash
#!/bin/bash
set -e

# 1. 设 PATH（launchd 不继承 shell PATH，gemini/python 等都拿不到）
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

# 2. cd 到仓库根（python import 才能正确解析）
cd /Users/alyx/tradeSystem

# 3. source 项目专属 env（launchd 不读 ~/.zshrc，所以钉钉 token 等必须在这里加载）
if [ -f "$HOME/.config/tradeSystem.env" ]; then
    source "$HOME/.config/tradeSystem.env"
fi

# 4. 输出加时间戳前缀（排障时能区分多次触发）
echo "===== $(date '+%Y-%m-%d %H:%M:%S') <task name> start ====="
exec /usr/bin/python3 scripts/main.py <command> "$@"
```

`/usr/bin/python3` 用绝对路径（不是 `python3`），保证版本可预测。

## plist 必须项

- `Label` 用 `com.alyx.tradesystem.<task>` 命名空间，与仓库内文件名匹配
- `StartCalendarInterval`：
  - 工作日多个时间点 → 用 `<array>` 包多个 `<dict>`，**不能用 `Weekday=1-5` 范围**（launchd 不支持范围语法）
  - Weekday: 0/7=周日, 1=周一, ..., 6=周六
- `StandardOutPath` + `StandardErrorPath` → `/tmp/tradesystem-<task>.log`（合并同一文件方便 tail）
- `RunAtLoad`：默认 `<false/>`，避免每次 launchctl load 就触发推送

## 安装与验证流程（强制）

```bash
# 1. 包装脚本可执行
chmod +x deploy/launchd/<task>-runner.sh

# 2. 拷 plist 到系统位置（不要 symlink，符号链接被 launchd 拒绝）
cp deploy/launchd/com.alyx.tradesystem.<task>.plist ~/Library/LaunchAgents/

# 3. load
launchctl load ~/Library/LaunchAgents/com.alyx.tradesystem.<task>.plist

# 4. 必须手动触发一次验证（不能只看 launchctl list 显示加载就算完）
rm -f /tmp/tradesystem-<task>.log
launchctl start com.alyx.tradesystem.<task>

# 5. 看完整日志确认推送 / 退出码
tail -f /tmp/tradesystem-<task>.log
```

**只完成 1-3 步而不做 4-5 步 = 没验证**。launchd 静默失败模式很多（PATH 缺失、env 缺失、脚本权限），不真触发一次绝不能宣称部署完成。

## LLM 任务的超时调整

launchd 子进程下 LLM CLI（gemini / claude / codex）启动比交互式 shell **慢 2-3 倍**（无 TTY，初始化路径不同）。

- 默认 `LLM_TIMEOUT_SECONDS=90` 在 launchd 下经常超时
- **launchd 触发的 LLM 任务**：建议 `~/.config/tradeSystem.env` 配 `LLM_TIMEOUT_SECONDS=180` 或更高
- launchd 不像 APScheduler BlockingScheduler 单线程，长超时不会卡后续 job（每次新进程）—— 上限可以放宽到 300+

## macOS 休眠对策

**休眠期间 launchd 不触发**。如果任务关键性高：

- 选项 A：`pmset -g sched` 安排定时唤醒（耗电）
- 选项 B：迁到 VPS（最稳）
- 选项 C：接受错过（行业推荐这种非交易决策任务可接受）

任务是否「关键」由业务定义；plist 文件头部加注释说明：

```xml
<!-- Sleep policy: 行业推荐错过可接受 / 风控告警必须 pmset wake / 交易触发必须 VPS -->
```

## 排障速查

| 现象 | 大概率原因 |
|---|---|
| `launchctl list` 不显示 task | plist 语法错（用 `plutil -lint ~/Library/LaunchAgents/*.plist` 验） |
| log 文件不生成 | StandardOutPath/StandardErrorPath 路径不存在或权限不够 |
| log 显示 `command not found: gemini` | PATH 没 set，回看包装脚本第 1 步 |
| log 显示 `[task] DingTalk pusher 未启用，跳过推送` | env 没 source，回看包装脚本第 3 步 |
| log 启动后无下文 | LLM 卡 timeout 或 subprocess 卡 stdin，看 `LLM_TIMEOUT_SECONDS` 是否够 |
