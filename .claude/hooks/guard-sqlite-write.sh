#!/usr/bin/env bash
# 红线守卫：拦截对 SQLite 的直接写入（CLAUDE.md 第一红线）。只读查询不受限。
# PreToolUse hook：匹配 Bash|Edit|Write|MultiEdit。exit 2 = 阻断，stderr 反馈给 Claude。
# 失败开放：任何解析异常都默认放行（exit 0），不阻塞正常工作。
input=$(cat)
tool=$(printf '%s' "$input" | jq -r '.tool_name // empty' 2>/dev/null)
deny() { echo "$1" >&2; exit 2; }

WRITE_KW='insert|update|delete|replace|drop|alter|truncate|create'
case "$tool" in
  Bash)
    cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // empty' 2>/dev/null)
    # (a) sqlite3 CLI 写库：必须出现 sqlite3 可执行 + 写关键字/写点命令
    if printf '%s' "$cmd" | grep -qE '(^|[^._[:alnum:]])sqlite3([[:space:]]|$)'; then
      if printf '%s' "$cmd" | grep -qiwE "$WRITE_KW" \
         || printf '%s' "$cmd" | grep -qE '\.(import|read)([[:space:]]|$)'; then
        deny "⛔ 红线拦截：sqlite3 直接写库。Agent 写入必须走 python3 main.py db ... / make 入口。只读（SELECT/.schema/.tables）不受限。"
      fi
    fi
    # (b) 内联 python 写库：python -c + .connect( + 写关键字
    if printf '%s' "$cmd" | grep -qE 'python3?[^|]*-c' \
       && printf '%s' "$cmd" | grep -qE '\.connect\(' \
       && printf '%s' "$cmd" | grep -qiwE "$WRITE_KW"; then
      deny "⛔ 红线拦截：内联 Python 直接写 SQLite。请改用 python3 main.py db ... 标准入口。"
    fi
    ;;
  Edit|Write|MultiEdit)
    fp=$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty' 2>/dev/null)
    printf '%s' "$fp" | grep -qiE '\.db$' \
      && deny "⛔ 红线拦截：禁止用 Edit/Write 改写 SQLite 文件（${fp}）。写入走 python3 main.py db ... 标准入口。"
    ;;
esac
exit 0
