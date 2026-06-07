#!/bin/bash
# 交易认知沉淀定时汇总入口（launchd 调用）。window 作为 $1 传入（recent3d|weekly|monthly）。
# 由 com.alyx.tradesystem.cognition-digest-{recent3d,weekly,monthly}.plist 触发。
# 跑 main.py cognition-digest <window>：只读认知三表 → 热度+共识+新增 → Antigravity 建议 → 推钉钉。
set -e

# 1. PATH（launchd 默认不含 /opt/homebrew/bin，python/agy 找不到）
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

# 2. 仓库根（python import 解析）
REPO_ROOT="/Users/alyx/tradeSystem"
cd "$REPO_ROOT"

# 3. env：scripts/.env + ~/.config/tradeSystem.env（DingTalk 凭据 / Antigravity 配置）
if [ -f "$REPO_ROOT/scripts/.env" ]; then
    # shellcheck disable=SC1091
    source "$REPO_ROOT/scripts/.env"
fi
if [ -f "$HOME/.config/tradeSystem.env" ]; then
    # shellcheck disable=SC1091
    source "$HOME/.config/tradeSystem.env"
fi

# 4. 时间戳前缀方便排障
echo "===== $(date '+%Y-%m-%d %H:%M:%S') cognition-digest ${1} start ====="

# 5. 凭据存在性诊断（${VAR:+set} 只判存在不打值，规避 /tmp/*.log 泄漏）
echo "[env] DINGTALK_WEBHOOK_TOKEN=${DINGTALK_WEBHOOK_TOKEN:+set} DINGTALK_WEBHOOK_SECRET=${DINGTALK_WEBHOOK_SECRET:+set} ANTIGRAVITY_BIN=${ANTIGRAVITY_BIN:+set} AGY_BIN=${AGY_BIN:+set} LLM_TIMEOUT_SECONDS=${LLM_TIMEOUT_SECONDS:+set}"

exec /usr/bin/python3 scripts/main.py cognition-digest "$@"
