#!/bin/bash
set -euo pipefail
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
cd /Users/alyx/tradeSystem

read -r day weekday hour minute <<< "$(TZ=Asia/Shanghai /bin/date '+%F %u %H %M')"
slot_minute=$((10#$minute / 10 * 10 + 2))
# 唤醒合并时仅接受本槽前三分钟，不把旧任务补成历史采样。
case "$weekday:$hour:$minute" in
    [1-5]:09:[3-5][2-4]|[1-5]:10:[0-5][2-4]|[1-5]:11:[0-2][2-4]|[1-5]:1[34]:[0-5][2-4]) ;;
    *) echo "[intraday-events] $day $hour:$minute 上海时间不在采样槽，跳过"; exit 0 ;;
esac
printf -v planned '%sT%s:%02d:00+08:00' "$day" "$hour" "$slot_minute"
set -a
if [ -f "$HOME/.config/tradeSystem.env" ]; then
    source "$HOME/.config/tradeSystem.env"
fi
set +a
echo "[intraday-events] 计划 $planned；DingTalk token=${DINGTALK_WEBHOOK_TOKEN:+set} secret=${DINGTALK_WEBHOOK_SECRET:+set}"
exec /usr/bin/python3 scripts/tools/intraday_events.py --interval-minutes 10 --push --input-by launchd --scheduled-at "$planned"
