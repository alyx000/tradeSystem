#!/bin/bash
# 主线板块串阳首阴股票池定时入口（launchd 调用）。
#
# 由 ~/Library/LaunchAgents/com.alyx.tradesystem.string-yang.plist 触发（工作日 21:50）。
# 跑 main.py string-yang daily：LLM 融合主线/概念分支后扫描连续五阳后第一根阴线
# → 渲染只读观察清单（标 [判断]）+ 推钉钉。
set -e

# launchd 默认 PATH 不含 ~/.local/bin；agy 安装在该目录时会启动失败并静默降级。
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

REPO_ROOT="/Users/alyx/tradeSystem"
cd "$REPO_ROOT"

if [ -f "$REPO_ROOT/scripts/.env" ]; then
    # shellcheck disable=SC1091
    source "$REPO_ROOT/scripts/.env"
fi
if [ -f "$HOME/.config/tradeSystem.env" ]; then
    # shellcheck disable=SC1091
    source "$HOME/.config/tradeSystem.env"
fi

echo "===== $(date '+%Y-%m-%d %H:%M:%S') string-yang daily start ====="
echo "[env] DINGTALK_WEBHOOK_TOKEN=${DINGTALK_WEBHOOK_TOKEN:+set} DINGTALK_WEBHOOK_SECRET=${DINGTALK_WEBHOOK_SECRET:+set} TUSHARE_TOKEN=${TUSHARE_TOKEN:+set} ANTIGRAVITY_BIN=${ANTIGRAVITY_BIN:+set} AGY_BIN=${AGY_BIN:+set}"

exec /usr/bin/python3 scripts/main.py string-yang daily
