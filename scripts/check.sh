#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root/scripts"

check_db_dir="$(mktemp -d "${TMPDIR:-/tmp}/tradesystem-check.XXXXXX")"
trap 'rm -rf "$check_db_dir"' EXIT
export TRADE_DB_PATH="$check_db_dir/trade.db"
# 桌面环境可能把 Python 缓存指到不可写的用户缓存区；与测试 DB 一起隔离并清理。
export PYTHONPYCACHEPREFIX="$check_db_dir/pycache"

echo "[scripts-check] compileall"
python3 -m compileall .

echo "[scripts-check] pytest"
python3 -m pytest -q
