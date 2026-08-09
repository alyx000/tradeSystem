#!/bin/bash
# 4日均线二波尾盘观察定时入口（launchd 调用）。
# 工作日 14:50 触发；CLI 只允许 14:45-15:00 的上海当日实时快照，休眠补触发安全跳过。
# 近端历史龙头池内：实时价合成今日收盘 + 累计成交额 → MA4 拐头与 5/10 日均额线观察。
set -e

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
# TZ 钉死 A 股市场时区，保证日期与尾盘时间窗均按上海解释。
export TZ="Asia/Shanghai"

REPO_ROOT="/Users/alyx/tradeSystem"
cd "$REPO_ROOT"

if [ -f "$REPO_ROOT/scripts/.env" ]; then
    # shellcheck disable=SC1091
    set -a; source "$REPO_ROOT/scripts/.env"; set +a
fi
if [ -f "$HOME/.config/tradeSystem.env" ]; then
    # shellcheck disable=SC1091
    set -a; source "$HOME/.config/tradeSystem.env"; set +a
fi

echo "===== $(date '+%Y-%m-%d %H:%M:%S') ma-breakout daily start ====="
echo "[env] TUSHARE_TOKEN=${TUSHARE_TOKEN:+set} DINGTALK_WEBHOOK_TOKEN=${DINGTALK_WEBHOOK_TOKEN:+set} DINGTALK_WEBHOOK_SECRET=${DINGTALK_WEBHOOK_SECRET:+set}"

exec /usr/bin/python3 scripts/main.py ma-breakout daily "$@"
