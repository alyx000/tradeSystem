#!/bin/bash
# 形态篇选股形态观察清单定时入口（launchd 调用）。
#
# 由 ~/Library/LaunchAgents/com.alyx.tradesystem.pattern-scan.plist 触发（工作日 22:45）。
# 跑 main.py pattern-scan daily：主线板块内筛「均线多头排列 + MACD 零上金叉/运行 +
# 阳放阴缩 + 未加速」四条件共振 → 渲染只读观察清单（标 [判断]）+ 推钉钉。
# 出处 teacher_notes#444（鞠磊《形态篇》），认知 cog_3b32e660。
set -e

# launchd 默认 PATH 不含 ~/.local/bin。本任务不调 LLM，但保持与兄弟任务同一份 PATH，
# 避免将来加依赖时再踩一次「命令找不到且静默降级」。
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

REPO_ROOT="/Users/alyx/tradeSystem"
cd "$REPO_ROOT"

# `set -a` 让 source 进来的变量自动导出给 `exec` 的子进程。两个 env 文件形式不同：
#   ~/.config/tradeSystem.env → 14 行 `export KEY=value`，本来就会传给子进程
#   scripts/.env              → 11 行**裸** `KEY=value`，不加 set -a 则子进程读不到
# 实测（env -i 干净环境模拟）：不加 set -a 时 wrapper 里 TUSHARE_TOKEN=set，
# 而 python 子进程 os.getenv 拿到的是 None。
# 功能上目前不坏——main.py:69 有 `load_dotenv(SCRIPT_DIR/".env")` 自己兜住了；
# 但下面那行 `[env] ... =set` 诊断反映的是 **wrapper 视角**，与子进程实际可见性不一致，
# 靠它排障会被误导（「显示 set 却仍报缺 token」）。set -a 消除这个语义裂缝，
# 并防止将来新增一个「只靠 shell 环境、Python 侧不 load_dotenv」的变量时静默失效。
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

echo "===== $(date '+%Y-%m-%d %H:%M:%S') pattern-scan daily start ====="
echo "[env] DINGTALK_WEBHOOK_TOKEN=${DINGTALK_WEBHOOK_TOKEN:+set} DINGTALK_WEBHOOK_SECRET=${DINGTALK_WEBHOOK_SECRET:+set} TUSHARE_TOKEN=${TUSHARE_TOKEN:+set}"

exec /usr/bin/python3 scripts/main.py pattern-scan daily
