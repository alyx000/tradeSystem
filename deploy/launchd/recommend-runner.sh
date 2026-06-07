#!/bin/bash
# 行业推荐定时入口（launchd 调用）
# 用法：recommend-runner.sh daily|weekly
#
# 由 ~/Library/LaunchAgents/com.alyx.tradesystem.recommend-*.plist 触发。
# 加载 ~/.config/tradeSystem.env 取钉钉 webhook 凭据，调 main.py recommend。

set -e

MODE="${1:-daily}"

# 设 PATH 让 agy CLI 可被找到（launchd 默认 PATH 不含 /opt/homebrew/bin）
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

# 仓库根
REPO_ROOT="/Users/alyx/tradeSystem"
cd "$REPO_ROOT"

# 加载项目专属 env（DingTalk webhook、可选 Antigravity 覆盖）
if [ -f "$HOME/.config/tradeSystem.env" ]; then
    # shellcheck disable=SC1091
    source "$HOME/.config/tradeSystem.env"
fi

# 时间戳前缀方便排障
echo "===== $(date '+%Y-%m-%d %H:%M:%S') recommend $MODE start ====="
exec /usr/bin/python3 scripts/main.py recommend "$MODE"
