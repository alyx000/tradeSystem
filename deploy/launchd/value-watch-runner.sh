#!/bin/bash
set -e

# 1. 设 PATH（launchd 不继承 shell PATH）
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

# 2. cd 到仓库根（python import 才能正确解析）
cd /Users/alyx/tradeSystem

# 3. source 项目专属 env（launchd 不读 ~/.zshrc；钉钉 token 等在此加载）
if [ -f "$HOME/.config/tradeSystem.env" ]; then
    source "$HOME/.config/tradeSystem.env"
fi

# 4. 时间戳前缀（排障区分多次触发）
echo "===== $(date '+%Y-%m-%d %H:%M:%S') value-watch daily start ====="

# 5. 凭据存在性诊断（${VAR:+set} 只判存在不打值，防凭据泄漏进 /tmp/*.log）
echo "[env] DINGTALK_WEBHOOK_TOKEN=${DINGTALK_WEBHOOK_TOKEN:+set} DINGTALK_WEBHOOK_SECRET=${DINGTALK_WEBHOOK_SECRET:+set}"

exec /usr/bin/python3 scripts/main.py value-watch daily "$@"
