#!/bin/bash
# 盘前/盘后定时入口（launchd 调用）
# 用法：today-runner.sh pre|post
#
# 由 ~/Library/LaunchAgents/com.alyx.tradesystem.today-{pre,post}.plist 触发。
# 加载 ~/.config/tradeSystem.env 取钉钉 webhook 凭据，调 main.py pre|post。
# cmd_pre / cmd_post 内置 weekday>=5 跳过 + 法定假期校验，周末/假日真触发会安全退出。

set -e

MODE="${1:?usage: today-runner.sh pre|post}"
case "$MODE" in
    pre|post) ;;
    *) echo "invalid mode: $MODE (expect pre|post)" >&2; exit 2 ;;
esac

# launchd 默认 PATH 不含 /opt/homebrew/bin
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

REPO_ROOT="/Users/alyx/tradeSystem"
cd "$REPO_ROOT"

# 加载钉钉 webhook 凭据
if [ -f "$HOME/.config/tradeSystem.env" ]; then
    # shellcheck disable=SC1091
    source "$HOME/.config/tradeSystem.env"
fi

echo "===== $(date '+%Y-%m-%d %H:%M:%S') today $MODE start ====="
# 只判存在不打值（规避凭据 echo 泄漏），用于排查 source/launchd env 注入失败
echo "[env] DINGTALK_WEBHOOK_TOKEN=${DINGTALK_WEBHOOK_TOKEN:+set} DINGTALK_WEBHOOK_SECRET=${DINGTALK_WEBHOOK_SECRET:+set}"
exec /usr/bin/python3 scripts/main.py "$MODE" --date "$(date +%F)"
