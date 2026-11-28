#!/bin/bash
# 盘前/盘后定时入口（launchd 调用）
# 用法：today-runner.sh pre|post
#
# 由 ~/Library/LaunchAgents/com.alyx.tradesystem.today-{pre,post}.plist 触发。
# 加载 ~/.config/tradeSystem.env 取钉钉 webhook 凭据，调 main.py pre|post。
# cmd_pre / cmd_post 内置 weekday>=5 跳过 + 法定假期校验，周末/假日真触发会安全退出。

set -e

MODE="${1:?usage: today-runner.sh pre|post}"
case "$MODE" in
    pre|post) ;;
    *) echo "invalid mode: $MODE (expect pre|post)" >&2; exit 2 ;;
esac

# launchd 默认 PATH 不含 /opt/homebrew/bin
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

# 加载钉钉 webhook 凭据
if [ -f "$HOME/.config/tradeSystem.env" ]; then
    # shellcheck disable=SC1091
    source "$HOME/.config/tradeSystem.env"
fi

echo "===== $(date '+%Y-%m-%d %H:%M:%S') today $MODE start ====="
# 只判存在不打值（规避凭据 echo 泄漏），用于排查 source/launchd env 注入失败
echo "[env] DINGTALK_WEBHOOK_TOKEN=${DINGTALK_WEBHOOK_TOKEN:+set} DINGTALK_WEBHOOK_SECRET=${DINGTALK_WEBHOOK_SECRET:+set}"
trade_date="$(TZ=Asia/Shanghai date +%F)"
if [ "$MODE" = pre ]; then
    exec /usr/bin/python3 scripts/main.py pre --date "$trade_date"
fi

# 常规盘后先落成分与行情。后续各项独立保存证据，返回首个非零退出码。
post_status=0
run_post_step() {
    local step="$1" status=0
    shift
    /usr/bin/python3 "$@" || status=$?
    echo "[post] date=$trade_date step=$step exit=$status"
    if [ "$post_status" -eq 0 ]; then
        post_status=$status
    fi
}

run_post_step market scripts/main.py post --date "$trade_date"
run_post_step core_minutes scripts/tools/course_core_minutes.py --date "$trade_date" --input-by launchd --retry-previous
run_post_step opening_auction scripts/tools/course_opening_auction.py --date "$trade_date" --input-by launchd
run_post_step theme_breadth scripts/tools/theme_intraday_breadth.py --date "$trade_date" --input-by launchd --save
run_post_step cross_market scripts/tools/course_cross_market.py --replay-day --date "$trade_date" --input-by launchd
run_post_step broad_etf scripts/tools/broad_etf_minutes.py --replay-day --date "$trade_date" --input-by launchd
run_post_step domestic_futures scripts/tools/domestic_index_futures.py --date "$trade_date" --input-by launchd
run_post_step bank_broker scripts/tools/bank_broker_intraday.py --date "$trade_date" --input-by launchd --save
exit "$post_status"
