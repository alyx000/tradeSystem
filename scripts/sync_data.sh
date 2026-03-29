#!/bin/bash
# VPS 专用：拉取 main → python main.py post（post 会先跑晚间任务：溢价回填、关注池、复盘 Obsidian，再生成全日盘后）→ 提交 daily/、tracking/ → push
set -e
cd "$(dirname "$0")/.."

git pull origin main
python3 scripts/main.py post
git add daily/ tracking/
git diff --cached --quiet || git commit -m "数据更新: $(date +%Y-%m-%d)"
git push origin main
