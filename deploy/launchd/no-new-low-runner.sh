#!/bin/bash
# 七天不创新低盘后扫描：工作日 23:20 触发，程序内再以交易日历 fail-closed。
# 全市场最近 8 根有效 K 线机械筛选 → 成交额强池 → LLM 两两 PK → 报告先落盘再推钉钉。
set -e

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

REPO_ROOT="/Users/alyx/tradeSystem"
cd "$REPO_ROOT"

# source 的变量需要 export 给 Python/Antigravity 子进程；只打印存在性，不打印凭据值。
set -a
if [ -f "$REPO_ROOT/scripts/.env" ]; then
    # shellcheck disable=SC1091
    source "$REPO_ROOT/scripts/.env"
fi
if [ -f "$HOME/.config/tradeSystem.env" ]; then
    # shellcheck disable=SC1091
    source "$HOME/.config/tradeSystem.env"
fi
set +a

echo "===== $(date '+%Y-%m-%d %H:%M:%S') no-new-low daily start ====="
echo "[env] DINGTALK_WEBHOOK_TOKEN=${DINGTALK_WEBHOOK_TOKEN:+set} DINGTALK_WEBHOOK_SECRET=${DINGTALK_WEBHOOK_SECRET:+set} TUSHARE_TOKEN=${TUSHARE_TOKEN:+set} ANTIGRAVITY_BIN=${ANTIGRAVITY_BIN:+set} AGY_BIN=${AGY_BIN:+set} LLM_TIMEOUT_SECONDS=${LLM_TIMEOUT_SECONDS:+set}"

exec /usr/bin/python3 scripts/main.py no-new-low daily "$@"
