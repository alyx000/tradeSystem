#!/bin/bash
# 月线指标日频监控定时入口（工作日 19:10）。
set -euo pipefail

# launchd 不继承交互式 shell PATH。
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

# plist 用 15 分钟无时区 tick；只有上海时间工作日 19:10（含）至
# 19:25（不含）的唯一一次 tick 才进入重任务，避免 Mac 切换时区后静默错过。
SHANGHAI_WEEKDAY="$(TZ=Asia/Shanghai /bin/date '+%u')"
SHANGHAI_HHMM="$(TZ=Asia/Shanghai /bin/date '+%H%M')"
if [ "$SHANGHAI_WEEKDAY" -gt 5 ] \
    || [ "$((10#$SHANGHAI_HHMM))" -lt 1910 ] \
    || [ "$((10#$SHANGHAI_HHMM))" -ge 1925 ]; then
    echo "[monthly-pattern monitor-daily] skip outside Shanghai workday 19:10-19:25; weekday=$SHANGHAI_WEEKDAY hhmm=$SHANGHAI_HHMM"
    exit 0
fi

REPO_ROOT="/Users/alyx/tradeSystem"
cd "$REPO_ROOT"

# scripts/.env 提供行情凭据，tradeSystem.env 提供钉钉凭据。
# set -a 保证裸 KEY=value 也会导出给 exec 后的 Python 子进程。
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

echo "===== $(TZ=Asia/Shanghai date '+%Y-%m-%d %H:%M:%S %Z') monthly-pattern monitor-daily start ====="
echo "[env] DINGTALK_WEBHOOK_TOKEN=${DINGTALK_WEBHOOK_TOKEN:+set} DINGTALK_WEBHOOK_SECRET=${DINGTALK_WEBHOOK_SECRET:+set} TUSHARE_TOKEN=${TUSHARE_TOKEN:+set}"

exec /usr/bin/python3 scripts/main.py monthly-pattern monitor-daily
