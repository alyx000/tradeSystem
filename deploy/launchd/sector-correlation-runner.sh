#!/bin/bash
# 板块相关性日报定时入口（launchd 调用）。
#
# 由 ~/Library/LaunchAgents/com.alyx.tradesystem.sector-correlation.plist 触发（交易日 21:15，
# 错开 volume-watch 21:00，降低 Tushare 镜像并发压力）。
# 跑 main.py sector-correlation daily：Tushare 采集多日活跃板块 + 指数 → 双窗相关/超额/β
# → 落库 sector_correlation_daily + 渲染 + 推钉钉。
# 非交易日 / 数据不足 → 任务内返「无足够数据跳过」，不写库不推送。
set -e

# 1. PATH（launchd 默认不含 /opt/homebrew/bin，python/依赖找不到）
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

# 2. 仓库根（python import 解析）
REPO_ROOT="/Users/alyx/tradeSystem"
cd "$REPO_ROOT"

# 3. env：scripts/.env（TUSHARE_TOKEN，main.py 也会 load_dotenv 它）
#       + ~/.config/tradeSystem.env（DingTalk webhook 凭据）
if [ -f "$REPO_ROOT/scripts/.env" ]; then
    # shellcheck disable=SC1091
    source "$REPO_ROOT/scripts/.env"
fi
if [ -f "$HOME/.config/tradeSystem.env" ]; then
    # shellcheck disable=SC1091
    source "$HOME/.config/tradeSystem.env"
fi

# 4. 时间戳前缀方便排障
echo "===== $(date '+%Y-%m-%d %H:%M:%S') sector-correlation daily start ====="

# 5. 凭据存在性诊断（${VAR:+set} 只判存在不打值，规避 /tmp/*.log 泄漏）
echo "[env] DINGTALK_WEBHOOK_TOKEN=${DINGTALK_WEBHOOK_TOKEN:+set} DINGTALK_WEBHOOK_SECRET=${DINGTALK_WEBHOOK_SECRET:+set} TUSHARE_TOKEN=${TUSHARE_TOKEN:+set}"

# /usr/bin/python3 绝对路径：按 launchd-deploy.md 规范保证版本可预测；依赖装在
# system python user-site（与 volume-watch runner 同款，dry-run 实证 tushare/pandas 可用）。
exec /usr/bin/python3 scripts/main.py sector-correlation daily
