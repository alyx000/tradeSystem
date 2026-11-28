#!/bin/bash
set -euo pipefail
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

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
# 同槽采样并行发起，各自记录实际收到行情的时间。
/usr/bin/python3 scripts/tools/intraday_events.py --interval-minutes 10 --push --input-by launchd --scheduled-at "$planned" &
market_pid=$!
/usr/bin/python3 scripts/tools/theme_catalysts.py --scheduled-at "$planned" --input-by launchd &
catalyst_pid=$!
/usr/bin/python3 scripts/tools/course_cross_market.py --scheduled-at "$planned" --input-by launchd &
cross_market_pid=$!
/usr/bin/python3 scripts/tools/broad_etf_minutes.py --scheduled-at "$planned" --input-by launchd &
etf_pid=$!
auction_pid=""
if [ "$hour" = "09" ] && [ "$slot_minute" -eq 32 ]; then
    /usr/bin/python3 scripts/tools/course_opening_auction.py --date "$day" --scheduled-at "$planned" --input-by launchd &
    auction_pid=$!
fi

market_exit=0
catalyst_exit=0
cross_market_exit=0
etf_exit=0
wait "$market_pid" || market_exit=$?
wait "$catalyst_pid" || catalyst_exit=$?
wait "$cross_market_pid" || cross_market_exit=$?
wait "$etf_pid" || etf_exit=$?
if [ -n "$auction_pid" ]; then
    auction_exit=0
    wait "$auction_pid" || auction_exit=$?
    echo "[intraday-events] opening_auction=$auction_exit"
fi
echo "[intraday-events] slot=$planned market=$market_exit catalysts=$catalyst_exit cross_market=$cross_market_exit broad_etf=$etf_exit"
for status in "$market_exit" "$catalyst_exit" "$cross_market_exit" "$etf_exit"; do
    if [ "$status" -ne 0 ]; then exit "$status"; fi
done
