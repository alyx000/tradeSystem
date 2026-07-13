#!/bin/bash
set -e

# 1. 设 PATH（launchd 不继承 shell PATH，agy/python 等都拿不到）
#    $HOME/.local/bin 必须在前：LLM CLI `agy` 装在此处，缺它 PK 每场启动失败→熔断（board-break runner 同款修复）
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

# 2. cd 到仓库根（python import 才能正确解析）
cd /Users/alyx/tradeSystem

# 3. source 项目专属 env（launchd 不读 ~/.zshrc，所以钉钉 token 等必须在这里加载）
if [ -f "$HOME/.config/tradeSystem.env" ]; then
    source "$HOME/.config/tradeSystem.env"
fi

# 4. 输出加时间戳前缀（排障时能区分多次触发）
echo "===== $(date '+%Y-%m-%d %H:%M:%S') tail-scan start ====="

# 5. 凭据存在性诊断（只判存在不打值，规避把 token/secret echo 进 /tmp/*.log）
echo "[env] DINGTALK_WEBHOOK_TOKEN=${DINGTALK_WEBHOOK_TOKEN:+set} DINGTALK_WEBHOOK_SECRET=${DINGTALK_WEBHOOK_SECRET:+set}"

exec /usr/bin/python3 scripts/main.py tail-scan daily "$@"
