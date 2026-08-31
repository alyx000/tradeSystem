#!/bin/bash
# 全市场盘中半小时扫描：每分钟轻量 tick，仅在目标槽位后 5 分钟内进入 Python。
set -euo pipefail

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

SHANGHAI_WEEKDAY="$(TZ=Asia/Shanghai /bin/date '+%u')"
SHANGHAI_HHMM="$(TZ=Asia/Shanghai /bin/date '+%H%M')"
if [ "$SHANGHAI_WEEKDAY" -gt 5 ]; then
    echo "[intraday-summary] skip non-weekday; weekday=$SHANGHAI_WEEKDAY hhmm=$SHANGHAI_HHMM"
    exit 0
fi

case "$SHANGHAI_HHMM" in
    093[0-5]|100[0-5]|103[0-5]|110[0-5]|113[0-5]|130[0-5]|133[0-5]|140[0-5]|143[0-5]|150[0-5]) ;;
    *)
        echo "[intraday-summary] skip outside scan slots; hhmm=$SHANGHAI_HHMM"
        exit 0
        ;;
esac

REPO_ROOT="/Users/alyx/tradeSystem"
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

echo "===== $(TZ=Asia/Shanghai date '+%Y-%m-%d %H:%M:%S %Z') intraday-summary start ====="
echo "[env] DINGTALK_WEBHOOK_TOKEN=${DINGTALK_WEBHOOK_TOKEN:+set} DINGTALK_WEBHOOK_SECRET=${DINGTALK_WEBHOOK_SECRET:+set}"

exec /usr/bin/python3 scripts/main.py intraday-summary run --json
