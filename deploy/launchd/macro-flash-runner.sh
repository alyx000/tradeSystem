#!/bin/bash
# 宏观快讯速读定时入口(launchd 调用)。
#
# 由 ~/Library/LaunchAgents/com.alyx.tradesystem.macro-flash.plist 触发
# (工作日 16:30 回溯 24h + 周日 22:00 回溯 54h 覆盖周末,进周日复盘)。
# 金十快讯采集 → 关键词筛宏观政策类 → 归档 data/runs/macro-flash/ + 推钉钉。
set -e

# 1. PATH(launchd 默认不含 /opt/homebrew/bin)
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

# 1.1 TZ 钉死 A 股市场时区:窗口计算与归档日期不随系统时区漂移
export TZ="Asia/Shanghai"

# 2. 仓库根(python import 解析)
REPO_ROOT="/Users/alyx/tradeSystem"
cd "$REPO_ROOT"

# 3. env:~/.config/tradeSystem.env(DingTalk 凭据)
if [ -f "$HOME/.config/tradeSystem.env" ]; then
    # shellcheck disable=SC1091
    set -a; source "$HOME/.config/tradeSystem.env"; set +a
fi

# 4. 时间戳前缀(排障区分多次触发)
echo "===== $(date '+%Y-%m-%d %H:%M:%S') macro-flash start ====="

# 5. 凭据存在性诊断(${VAR:+set} 只判存在不打值,禁止真凭据进 /tmp/*.log)
echo "[env] DINGTALK_WEBHOOK_TOKEN=${DINGTALK_WEBHOOK_TOKEN:+set} DINGTALK_WEBHOOK_SECRET=${DINGTALK_WEBHOOK_SECRET:+set}"

# 周日档回溯 54h(周五 16:00 起,覆盖周五晚+周末);工作日档 24h
if [ "$(date +%u)" = "7" ]; then
    LOOKBACK=54
else
    LOOKBACK=24
fi

exec /usr/bin/python3 scripts/main.py macro-flash run --lookback-hours "$LOOKBACK" "$@"
