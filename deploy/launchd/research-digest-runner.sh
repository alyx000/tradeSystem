#!/bin/bash
# 每日研报速读定时入口（launchd 调用）。
#
# 由 ~/Library/LaunchAgents/com.alyx.tradesystem.research-digest.plist 触发（工作日 06:42 盘前）。
# 跑 main.py research-digest daily：A股研报评级（巨潮）+ 美股 yfinance 评级 → Top3 → 落盘 + 推钉钉。
# 美股评级源时效稀疏/部分标的冻结时该段可能为空，任务内显式标注，不报错。
set -e

# 1. PATH（launchd 默认不含 /opt/homebrew/bin，python/agy/依赖找不到）
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

# 2. 仓库根（python import 解析）
REPO_ROOT="/Users/alyx/tradeSystem"
cd "$REPO_ROOT"

# 3. env：scripts/.env（TUSHARE_TOKEN）+ ~/.config/tradeSystem.env（DingTalk 凭据 / Antigravity 配置）
if [ -f "$REPO_ROOT/scripts/.env" ]; then
    # shellcheck disable=SC1091
    source "$REPO_ROOT/scripts/.env"
fi
if [ -f "$HOME/.config/tradeSystem.env" ]; then
    # shellcheck disable=SC1091
    source "$HOME/.config/tradeSystem.env"
fi

# 4. 时间戳前缀方便排障
echo "===== $(date '+%Y-%m-%d %H:%M:%S') research-digest daily start ====="

# 5. 凭据存在性诊断（${VAR:+set} 只判存在不打值，规避 /tmp/*.log 泄漏）
echo "[env] DINGTALK_WEBHOOK_TOKEN=${DINGTALK_WEBHOOK_TOKEN:+set} DINGTALK_WEBHOOK_SECRET=${DINGTALK_WEBHOOK_SECRET:+set} ANTIGRAVITY_BIN=${ANTIGRAVITY_BIN:+set} AGY_BIN=${AGY_BIN:+set} LLM_TIMEOUT_SECONDS=${LLM_TIMEOUT_SECONDS:+set}"

exec /usr/bin/python3 scripts/main.py research-digest daily
