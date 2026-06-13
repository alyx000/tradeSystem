#!/bin/bash
# 业绩预告/快报速报定时入口（launchd 调用）。
#
# 由 ~/Library/LaunchAgents/com.alyx.tradesystem.earnings-digest.plist 触发
# （工作日 22:00 + 周日 22:00；周六不跑——周六公告由周日回看窗口覆盖）。
# 全市场 forecast_vip/express_vip 采集存档 → 水位线增量 → 次日缺口验证（市场投票 2×2）
# → 五段 markdown → MD 落盘 + 推钉钉（空窗口日不推送）。
set -e

# 1. PATH（launchd 默认不含 /opt/homebrew/bin，python 依赖找不到）
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

# 1.1 TZ 钉死 A 股市场时区：只保证子进程 date.today() 算出的**目标日**不随系统时区
#     漂移；launchd StartCalendarInterval 的**触发时刻**仍随系统时区（当前即上海）——
#     改系统时区时触发点漂移为已知接受（plist sleep policy：错过可接受，回看窗口次日补齐）
export TZ="Asia/Shanghai"

# 2. 仓库根（python import 解析；对齐其余 runner 的 cd 根目录惯例）
REPO_ROOT="/Users/alyx/tradeSystem"
cd "$REPO_ROOT"

# 3. env：scripts/.env（TUSHARE_TOKEN）+ ~/.config/tradeSystem.env（DingTalk 凭据）
if [ -f "$REPO_ROOT/scripts/.env" ]; then
    # shellcheck disable=SC1091
    set -a; source "$REPO_ROOT/scripts/.env"; set +a
fi
if [ -f "$HOME/.config/tradeSystem.env" ]; then
    # shellcheck disable=SC1091
    set -a; source "$HOME/.config/tradeSystem.env"; set +a
fi

# 4. 时间戳前缀（排障区分多次触发）
echo "===== $(date '+%Y-%m-%d %H:%M:%S') earnings-digest start ====="

# 5. 凭据存在性诊断（${VAR:+set} 只判存在不打值，禁止 echo 真凭据进 /tmp/*.log）
echo "[env] TUSHARE_TOKEN=${TUSHARE_TOKEN:+set} DINGTALK_WEBHOOK_TOKEN=${DINGTALK_WEBHOOK_TOKEN:+set} DINGTALK_WEBHOOK_SECRET=${DINGTALK_WEBHOOK_SECRET:+set}"

exec /usr/bin/python3 scripts/main.py earnings-digest daily "$@"
