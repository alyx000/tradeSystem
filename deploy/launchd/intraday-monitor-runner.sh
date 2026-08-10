#!/bin/bash
# 盘中实时阈值监控：每 5 分钟 tick，runner 仅在上海交易时段进入 Python。
set -euo pipefail

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

SHANGHAI_WEEKDAY="$(TZ=Asia/Shanghai /bin/date '+%u')"
SHANGHAI_HHMM="$(TZ=Asia/Shanghai /bin/date '+%H%M')"
HHMM="$((10#$SHANGHAI_HHMM))"
if [ "$SHANGHAI_WEEKDAY" -gt 5 ]; then
    echo "[intraday-monitor] skip outside Shanghai sessions; weekday=$SHANGHAI_WEEKDAY hhmm=$SHANGHAI_HHMM"
    exit 0
fi
if { [ "$HHMM" -lt 930 ] || [ "$HHMM" -ge 1131 ]; } \
    && { [ "$HHMM" -lt 1300 ] || [ "$HHMM" -ge 1501 ]; }; then
    echo "[intraday-monitor] skip outside Shanghai sessions; weekday=$SHANGHAI_WEEKDAY hhmm=$SHANGHAI_HHMM"
    exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

set -a
if [ -f "$REPO_ROOT/scripts/.env" ]; then
    # shellcheck disable=SC1091
    source "$REPO_ROOT/scripts/.env"
fi
if [ -f "$HOME/.config/tradeSystem.env" ]; then
    # shellcheck disable=SC1091
    source "$HOME/.config/tradeSystem.env"
fi
set +a

echo "===== $(TZ=Asia/Shanghai date '+%Y-%m-%d %H:%M:%S %Z') intraday-monitor start ====="
echo "[env] DINGTALK_WEBHOOK_TOKEN=${DINGTALK_WEBHOOK_TOKEN:+set} DINGTALK_WEBHOOK_SECRET=${DINGTALK_WEBHOOK_SECRET:+set}"

exec /usr/bin/python3 scripts/main.py intraday-monitor check --json
