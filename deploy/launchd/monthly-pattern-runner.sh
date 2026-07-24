#!/bin/bash
# 完成月月线模式观察池定时入口（launchd 调用，每月 2 日 23:10）。
set -e

# launchd 默认 PATH 很短；显式加入本机常用 Python/工具路径。
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

REPO_ROOT="/Users/alyx/tradeSystem"
cd "$REPO_ROOT"

# 行情/财务凭据与钉钉凭据分开加载。
if [ -f "$REPO_ROOT/scripts/.env" ]; then
    # shellcheck disable=SC1091
    set -a; source "$REPO_ROOT/scripts/.env"; set +a
fi
if [ -f "$HOME/.config/tradeSystem.env" ]; then
    # shellcheck disable=SC1091
    set -a; source "$HOME/.config/tradeSystem.env"; set +a
fi

echo "===== $(date '+%Y-%m-%d %H:%M:%S') monthly-pattern daily start ====="
echo "[env] TUSHARE_TOKEN=${TUSHARE_TOKEN:+set} DINGTALK_WEBHOOK_TOKEN=${DINGTALK_WEBHOOK_TOKEN:+set} DINGTALK_WEBHOOK_SECRET=${DINGTALK_WEBHOOK_SECRET:+set}"

exec /usr/bin/python3 scripts/main.py monthly-pattern daily --input-by launchd
