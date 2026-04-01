#!/usr/bin/env bash
# 安装 tradesystem 调度器为 macOS launchd 用户 Agent
# 用法：bash scripts/launchd/install.sh [--uninstall]
set -euo pipefail

LABEL="com.tradesystem.schedule"
PLIST_SRC="$(cd "$(dirname "$0")" && pwd)/com.tradesystem.schedule.plist"
PLIST_DST="$HOME/Library/LaunchAgents/$LABEL.plist"

SCRIPTS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON3_PATH="$(command -v python3)"

if [[ "${1:-}" == "--uninstall" ]]; then
    echo "卸载 $LABEL ..."
    launchctl unload "$PLIST_DST" 2>/dev/null || true
    rm -f "$PLIST_DST"
    echo "✅ 已卸载"
    exit 0
fi

echo "安装 $LABEL"
echo "  scripts 目录: $SCRIPTS_DIR"
echo "  python3 路径: $PYTHON3_PATH"

mkdir -p "$HOME/Library/LaunchAgents"

sed \
    -e "s|__SCRIPTS_DIR__|$SCRIPTS_DIR|g" \
    -e "s|__PYTHON3_PATH__|$PYTHON3_PATH|g" \
    "$PLIST_SRC" > "$PLIST_DST"

# 若已加载则先卸载再重新加载
launchctl unload "$PLIST_DST" 2>/dev/null || true
launchctl load "$PLIST_DST"

echo "✅ 已安装并启动"
echo "   日志: /tmp/tradesystem-schedule.log"
echo "   状态: launchctl list | grep tradesystem"
