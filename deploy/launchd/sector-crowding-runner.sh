#!/bin/bash
# 板块拥挤度定时入口(launchd 调用)。交易日 21:30 采集 L1/L2 拥挤度快照落库,默认不推送
# (复盘时 sector-crowding report 查看;要推送手动跑 --push)。非交易日任务内守卫跳过。
#
# 由 ~/Library/LaunchAgents/com.alyx.tradesystem.sector-crowding.plist 触发
# (工作日 21:30,错开 volume-watch 21:00 / sector-correlation 21:15)。
set -e

# 1. PATH（launchd 默认不含 /opt/homebrew/bin，python/依赖找不到）
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

# 2. 仓库根（python import 解析）
REPO_ROOT="/Users/alyx/tradeSystem"
cd "$REPO_ROOT"

# 3. env：scripts/.env（TUSHARE_TOKEN，main.py 也会 load_dotenv 它）
#       + ~/.config/tradeSystem.env（DingTalk webhook 凭据）
if [ -f "$REPO_ROOT/scripts/.env" ]; then
    # shellcheck disable=SC1091
    source "$REPO_ROOT/scripts/.env"
fi
if [ -f "$HOME/.config/tradeSystem.env" ]; then
    # shellcheck disable=SC1091
    source "$HOME/.config/tradeSystem.env"
fi

# 4. 时间戳前缀方便排障
echo "===== $(date '+%Y-%m-%d %H:%M:%S') sector-crowding daily start ====="

# 5. 凭据存在性诊断(${VAR:+set} 只判存在不打值,规避 /tmp/*.log 泄漏;
#    默认不推送,钉钉凭据仅 --push 场景需要)
echo "[env] TUSHARE_TOKEN=${TUSHARE_TOKEN:+set} DINGTALK_WEBHOOK_TOKEN=${DINGTALK_WEBHOOK_TOKEN:+set} DINGTALK_WEBHOOK_SECRET=${DINGTALK_WEBHOOK_SECRET:+set}"

# /usr/bin/python3 绝对路径：按 launchd-deploy.md 规范保证版本可预测。
exec /usr/bin/python3 scripts/main.py sector-crowding daily
