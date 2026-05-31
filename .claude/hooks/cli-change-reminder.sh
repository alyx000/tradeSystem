#!/usr/bin/env bash
# 改动 CLI/API 入口后，提醒走 .agents/rules/skills-sync.md 同步清单。
# PostToolUse hook：匹配 Edit|Write|MultiEdit。通过 additionalContext 把提醒喂回 Claude。
input=$(cat)
fp=$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty' 2>/dev/null)
case "$fp" in
  */scripts/db/cli.py|*/scripts/main.py|*/scripts/api/routes/*.py|scripts/db/cli.py|scripts/main.py|scripts/api/routes/*.py)
    msg="🔄 skills-sync 提醒（${fp}）：按 .agents/rules/skills-sync.md 检查 ① INDEX.md 依赖表 ② pytest scripts/tests/test_cli_smoke.py ③ 新增 main.py 顶层子命令须在 test_cli_smoke 的 ARCHITECTURE_COMMANDS 加用例 ④ 受影响 SKILL.md。"
    jq -n --arg c "$msg" '{hookSpecificOutput:{hookEventName:"PostToolUse",additionalContext:$c}}'
    ;;
esac
exit 0
