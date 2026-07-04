#!/bin/bash
# 断板反包盘后扫描定时入口（launchd 调用）。
#
# 由 ~/Library/LaunchAgents/com.alyx.tradesystem.board-break.plist 触发（工作日 21:20）。
# 跑 main.py board-break daily：昨日连板>=2 断板（<=6%未跌停，10cm主板剔ST）→
# 八维度加权打分 + LLM 两两 PK 循环赛 → 双排序观察清单（全标 [判断]）+ 推钉钉。
#
# 依赖：排在 volume-watch（21:00）+ sector-correlation（21:15）之后、
#       trend-leader（21:30）之前——主线板块归属取 daily_volume_concentration 当日快照。
# 非交易日（节假日）任务内自动跳过（不落盘、不推送）。
# 核心源失败（source_failed）时不产出正常候选清单，落失败报告 + 推告警 + 非零退出。
# 时区前提：launchd 按**系统时区**（本机 /etc/localtime = Asia/Shanghai）解释 plist 的
# StartCalendarInterval，shell 会话的 TZ 环境变量不影响 launchd——21:20 即北京时间 21:20，
# 与仓库其余 launchd 任务同前提（反驳 codex 收尾轮时区 finding：其沙箱 TZ=LA 属环境误判）。
set -e

# 1. PATH（launchd 默认不含 /opt/homebrew/bin / ~/.local/bin，python/agy/依赖找不到；
#    ~/.local/bin 对齐 research-digest runner——agy 装在那里时缺它会让 PK 腿静默熔断,门2 收尾轮发现）
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

# 2. 仓库根（python import 解析）
REPO_ROOT="/Users/alyx/tradeSystem"
cd "$REPO_ROOT"

# 3. env：scripts/.env（TUSHARE_TOKEN，行情/公告/增减持/复权因子走 Tushare 镜像）
#       + ~/.config/tradeSystem.env（DingTalk webhook 凭据 + Antigravity PK LLM）
if [ -f "$REPO_ROOT/scripts/.env" ]; then
    # shellcheck disable=SC1091
    source "$REPO_ROOT/scripts/.env"
fi
if [ -f "$HOME/.config/tradeSystem.env" ]; then
    # shellcheck disable=SC1091
    source "$HOME/.config/tradeSystem.env"
fi

# 4. 时间戳前缀方便排障
echo "===== $(date '+%Y-%m-%d %H:%M:%S') board-break daily start ====="

# 5. 凭据存在性诊断（${VAR:+set} 只判存在不打值，规避 /tmp/*.log 泄漏）
#    看到 =set 表示注入成功；=（空）表示对应 env 未 source 到该变量。
echo "[env] DINGTALK_WEBHOOK_TOKEN=${DINGTALK_WEBHOOK_TOKEN:+set} DINGTALK_WEBHOOK_SECRET=${DINGTALK_WEBHOOK_SECRET:+set} TUSHARE_TOKEN=${TUSHARE_TOKEN:+set} ANTIGRAVITY_BIN=${ANTIGRAVITY_BIN:+set} AGY_BIN=${AGY_BIN:+set} LLM_TIMEOUT_SECONDS=${LLM_TIMEOUT_SECONDS:+set}"

exec /usr/bin/python3 scripts/main.py board-break daily "$@"
