#!/bin/bash
# 每日研报速读定时入口（launchd 调用）。
#
# 由 ~/Library/LaunchAgents/com.alyx.tradesystem.research-digest.plist 触发（每天 22:00）。
# 仅在 A 股交易日或 A 股交易日前一天继续执行；其它日期只记录 skip。
# 跑 JS workflow：基础研报段 + 慧博深读 Antigravity reader → 落盘 + 推钉钉。
# workflow 负责 state/events/run_report、断点续跑、preflight 与 Antigravity 全局失败显式标记。
set -e

# 1. PATH（launchd 默认不含 /opt/homebrew/bin / ~/.local/bin，python/agy/依赖找不到）
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

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
export HUIBO_REFRESH_URL_FROM_APP="${HUIBO_REFRESH_URL_FROM_APP:-1}"

# 4. 时间戳前缀方便排障
RUN_DATE="$(date '+%Y-%m-%d')"
echo "===== $(date '+%Y-%m-%d %H:%M:%S') research-digest daily start ====="

RUN_CHECK="$(python3 scripts/workflows/huibo_helper.py should-run --date "$RUN_DATE")"
echo "[schedule] $RUN_CHECK"
if ! python3 -c 'import json,sys; raise SystemExit(0 if json.loads(sys.argv[1]).get("should_run") else 1)' "$RUN_CHECK"; then
    echo "[schedule] skip research-digest: not A-share trade day or pre-trade day"
    exit 0
fi

# 5. 凭据存在性诊断（${VAR:+set} 只判存在不打值，规避 /tmp/*.log 泄漏）
echo "[env] DINGTALK_WEBHOOK_TOKEN=${DINGTALK_WEBHOOK_TOKEN:+set} DINGTALK_WEBHOOK_SECRET=${DINGTALK_WEBHOOK_SECRET:+set} ANTIGRAVITY_BIN=${ANTIGRAVITY_BIN:+set} AGY_BIN=${AGY_BIN:+set} LLM_TIMEOUT_SECONDS=${LLM_TIMEOUT_SECONDS:+set}"

exec node scripts/workflows/research-digest-workflow.mjs daily \
    --reader-cap "${HUIBO_READER_CAP:-20}" \
    --reader-concurrency "${HUIBO_READER_CONCURRENCY:-20}" \
    --reader-max-attempts "${HUIBO_READER_MAX_ATTEMPTS:-2}" \
    --recommend-cap "${HUIBO_RECOMMEND_CAP:-2}" \
    --preflight \
    --resume \
    --publish \
    --include-base-digest
