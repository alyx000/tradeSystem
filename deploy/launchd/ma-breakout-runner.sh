#!/bin/bash
# 4日均线二波观察池定时入口（launchd 调用）。
#
# 工作日 + 周日 21:35 触发（plist 按系统时区 Asia/Shanghai，单触发，与兄弟任务同范式）；
# 近端历史龙头池内 MA4 拐头 + 成交额突破 5/10 日均额线 → 只读观察清单 + 钉钉。
# 2026-07-11 修复：移除原「Pacific 折算触发 + 21:20-22:05 时间窗守卫」——休眠错过晚间档后
# launchd 把补跑合并到晨间折算触发、再被守卫杀掉，曾致 0708/0709 连续两日无产出（详见 plist 注释）。
set -e

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
# TZ 钉死 A 股市场时区：保证子进程 date.today()（cli/ma_breakout.py:_today）算出的目标日
# 不随系统时区漂移（与 market-timing / earnings-digest runner 同派）。此 TZ 与已删除的
# Pacific 折算触发/时间窗守卫无关——那才是 0708/0709 缺报的根因，TZ 本身无害且必要。
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
