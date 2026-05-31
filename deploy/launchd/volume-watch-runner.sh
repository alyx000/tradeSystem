#!/bin/bash
# 成交额 Top20 板块集中度日报定时入口（launchd 调用）。
#
# 由 ~/Library/LaunchAgents/com.alyx.tradesystem.volume-watch.plist 触发（交易日 21:00）。
# 跑 main.py volume-watch daily：read-through 采集 top20 + 申万二级打标 + 落库 + 渲染 + 推钉钉。
# 非交易日（节假日）当日无成交额数据 → 任务内返「无数据跳过」，不写库不推送。
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
echo "===== $(date '+%Y-%m-%d %H:%M:%S') volume-watch daily start ====="

# 5. 凭据存在性诊断（${VAR:+set} 只判存在不打值，规避 /tmp/*.log 泄漏）
#    看到 =set 表示注入成功；=（空）表示对应 env 未 source 到该变量。
echo "[env] DINGTALK_WEBHOOK_TOKEN=${DINGTALK_WEBHOOK_TOKEN:+set} DINGTALK_WEBHOOK_SECRET=${DINGTALK_WEBHOOK_SECRET:+set} TUSHARE_TOKEN=${TUSHARE_TOKEN:+set}"

exec /usr/bin/python3 scripts/main.py volume-watch daily
