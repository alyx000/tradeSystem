#!/bin/bash
# 大盘择时观察定时入口（launchd 调用）。
#
# 由 ~/Library/LaunchAgents/com.alyx.tradesystem.market-timing.plist 触发
# （工作日 21:40 + 周日 21:40；接 volume-watch 21:00 / trend-leader 21:30 之后，盘后数据已就绪）。
# 逐指数斐波那契变盘点 + 底分型生命周期 + 市场级客观上下文 → MD 观察清单 + 推钉钉。
set -e

# 1. PATH（launchd 默认不含 /opt/homebrew/bin，python 依赖找不到）
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

# 1.1 TZ 钉死 A 股市场时区：保证子进程 date.today() 算出的目标日不随系统时区漂移。
export TZ="Asia/Shanghai"

# 2. 仓库根（python import 解析）
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
echo "===== $(date '+%Y-%m-%d %H:%M:%S') market-timing start ====="

# 5. 凭据存在性诊断（${VAR:+set} 只判存在不打值，禁止 echo 真凭据进 /tmp/*.log）
echo "[env] TUSHARE_TOKEN=${TUSHARE_TOKEN:+set} DINGTALK_WEBHOOK_TOKEN=${DINGTALK_WEBHOOK_TOKEN:+set} DINGTALK_WEBHOOK_SECRET=${DINGTALK_WEBHOOK_SECRET:+set}"

exec /usr/bin/python3 scripts/main.py market-timing daily "$@"
