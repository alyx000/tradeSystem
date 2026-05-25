#!/bin/bash
# 最近 4 个交易日交易复盘（launchd 调用）
#
# 由 ~/Library/LaunchAgents/com.alyx.tradesystem.four-trading-day-review.plist 触发。
# 加载 ~/.config/tradeSystem.env 取钉钉 webhook 凭据，生成报告并推送短摘要。
#
# 只读业务数据：脚本仅写入 /Users/alyx/tradeSystem/tmp/daily-trade-reviews/*.md

set -euo pipefail

# launchd 默认 PATH 不含 /opt/homebrew/bin
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

REPO_ROOT="/Users/alyx/tradeSystem"
cd "$REPO_ROOT"

# 加载钉钉 webhook 凭据
if [ -f "$HOME/.config/tradeSystem.env" ]; then
    # shellcheck disable=SC1091
    source "$HOME/.config/tradeSystem.env"
fi

echo "===== $(date '+%Y-%m-%d %H:%M:%S') four-trading-day-review start ====="
# 只判存在不打值（规避凭据 echo 泄漏），用于排查 source/launchd env 注入失败
echo "[env] DINGTALK_WEBHOOK_TOKEN=${DINGTALK_WEBHOOK_TOKEN:+set} DINGTALK_WEBHOOK_SECRET=${DINGTALK_WEBHOOK_SECRET:+set}"

exec /usr/bin/python3 scripts/automations/four_trading_day_review.py \
    --date "$(date +%F)" \
    --account default \
    --limit 10000 \
    --push

