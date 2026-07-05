#!/bin/bash
# 每日最票候选确认稿定时入口（launchd 调用）。
#
# 由 ~/Library/LaunchAgents/com.alyx.tradesystem.daily-leaders.plist 触发（工作日 22:30）。
# 生成复盘第 5 步「龙头 / 最票」候选确认稿，落本地报告并推送钉钉 Markdown；
# v1 仅支持 Codex/CLI 确认后写回，不实现钉钉按钮回调。
set -e

# 1. PATH（launchd 默认不含 ~/.local/bin /opt/homebrew/bin，python/agy/依赖找不到）
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

# 2. 仓库根（python import 解析）
REPO_ROOT="/Users/alyx/tradeSystem"
cd "$REPO_ROOT"

# 3. env：launchd 不读交互 shell；这里只 source 项目专属 env。
if [ -f "$HOME/.config/tradeSystem.env" ]; then
    # shellcheck disable=SC1090
    set -a
    source "$HOME/.config/tradeSystem.env"
    set +a
fi

# 4. 时间戳前缀方便排障
echo "===== $(date '+%Y-%m-%d %H:%M:%S') daily-leaders propose start ====="

# 5. 凭据存在性诊断（${VAR:+set} 只判存在不打值，规避 /tmp/*.log 泄漏）
echo "[env] DINGTALK_WEBHOOK_TOKEN=${DINGTALK_WEBHOOK_TOKEN:+set} DINGTALK_WEBHOOK_SECRET=${DINGTALK_WEBHOOK_SECRET:+set} ANTIGRAVITY_BIN=${ANTIGRAVITY_BIN:+set} AGY_BIN=${AGY_BIN:+set} LLM_MODEL=${LLM_MODEL:+set}"

exec /usr/bin/python3 scripts/main.py daily-leaders propose --push
