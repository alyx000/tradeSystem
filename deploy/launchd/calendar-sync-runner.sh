#!/bin/bash
# 宏观日历同步定时入口：每天 06:30 拉取未来 14 个自然日，
# 原子更新 tracking/calendar_auto.yaml，并经 CLI 幂等同步 calendar_events。
set -e

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
export TZ="Asia/Shanghai"

REPO_ROOT="/Users/alyx/tradeSystem"
cd "$REPO_ROOT"

if [ -f "$REPO_ROOT/scripts/.env" ]; then
    # shellcheck disable=SC1091
    set -a
    source "$REPO_ROOT/scripts/.env"
    set +a
fi
if [ -f "$HOME/.config/tradeSystem.env" ]; then
    # shellcheck disable=SC1091
    set -a
    source "$HOME/.config/tradeSystem.env"
    set +a
fi

echo "===== $(date '+%Y-%m-%d %H:%M:%S') calendar-sync start ====="
echo "[env] TUSHARE_TOKEN=${TUSHARE_TOKEN:+set}"

exec /usr/bin/python3 scripts/main.py prefetch-calendar \
    --days 14 \
    --input-by launchd_calendar_sync \
    --json \
    "$@"
