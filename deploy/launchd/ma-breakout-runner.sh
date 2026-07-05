#!/bin/bash
# 4日均线二波观察池定时入口（launchd 调用）。
#
# 中国时间工作日 + 周日 21:35 触发：近端历史龙头池内 MA4 拐头 + 成交额突破 5/10 日均额线 → 只读观察清单 + 钉钉。
set -e

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
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

if [ "${MA_BREAKOUT_FORCE:-0}" != "1" ] && [ "$#" -eq 0 ]; then
    china_hhmm="$(date '+%H%M')"
    china_hhmm_num=$((10#$china_hhmm))
    if [ "$china_hhmm_num" -lt 2120 ] || [ "$china_hhmm_num" -gt 2205 ]; then
        echo "[skip] 当前中国时间 $china_hhmm 不在 ma-breakout 允许窗口 21:20-22:05；用于过滤 Pacific 夏令时/冬令时双触发"
        exit 0
    fi
fi

exec /usr/bin/python3 scripts/main.py ma-breakout daily "$@"
