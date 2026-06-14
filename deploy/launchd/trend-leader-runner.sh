#!/bin/bash
# 趋势主升漏斗扫描定时入口（launchd 调用）。
#
# 由 ~/Library/LaunchAgents/com.alyx.tradesystem.trend-leader.plist 触发（交易日 21:30）。
# 跑 main.py trend-leader daily：当日涨停 ∩ 主线板块 → 首次涨停加速+缓涨入池 →
# 缩量回踩/贴MA5/乖离信号 → 趋势破坏退池 → 渲染只读观察清单（标 [判断]）+ 推钉钉。
#
# 依赖：必须排在 volume-watch（21:00）之后——主线池取 daily_volume_concentration Top-K，
#       volume-watch 当日未落库则回退最近一日（报告会标「主线回退」）。
# 非交易日（节假日）当日无涨停/无集中度 → 候选为空，报告仍生成但无新入池。
set -e

# 1. PATH（launchd 默认不含 /opt/homebrew/bin，python/依赖找不到）
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

# 2. 仓库根（python import 解析）
REPO_ROOT="/Users/alyx/tradeSystem"
cd "$REPO_ROOT"

# 3. env：scripts/.env（TUSHARE_TOKEN，行情区间 OHLCV 走 Tushare 镜像）
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
echo "===== $(date '+%Y-%m-%d %H:%M:%S') trend-leader daily start ====="

# 5. 凭据存在性诊断（${VAR:+set} 只判存在不打值，规避 /tmp/*.log 泄漏）
#    看到 =set 表示注入成功；=（空）表示对应 env 未 source 到该变量。
echo "[env] DINGTALK_WEBHOOK_TOKEN=${DINGTALK_WEBHOOK_TOKEN:+set} DINGTALK_WEBHOOK_SECRET=${DINGTALK_WEBHOOK_SECRET:+set} TUSHARE_TOKEN=${TUSHARE_TOKEN:+set}"

exec /usr/bin/python3 scripts/main.py trend-leader daily
