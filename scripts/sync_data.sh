#!/bin/bash
# VPS 专用：拉取最新代码 → 运行盘后采集 → 提交数据文件 → 推送
set -e
cd "$(dirname "$0")/.."

git pull origin main
python3 scripts/main.py post
git add daily/ tracking/
git diff --cached --quiet || git commit -m "数据更新: $(date +%Y-%m-%d)"
git push origin main
