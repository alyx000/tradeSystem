#!/usr/bin/env python3
"""Generate or check docs/commands.md from Makefile help entries."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MAKEFILE = REPO_ROOT / "Makefile"
OUTPUT = REPO_ROOT / "docs" / "commands.md"
JSON_OUTPUT = REPO_ROOT / "docs" / "commands.json"

HELP_LINE_RE = re.compile(r'^\s*@echo\s+"  make ([^ ]+)\s+-\s+(.+)"$')


CATEGORY_ORDER = [
    ("环境与检查", [
        "bootstrap",
        "doctor",
        "check",
        "check-web",
        "check-scripts",
        "hooks-install",
    ]),
    ("开发与页面", [
        "dev",
        "dev-api",
        "dev-web",
        "dashboard-open",
        "search-open",
        "market-open",
        "review-open",
        "plan-open",
        "knowledge-open",
        "ingest-open",
        "teachers-open",
        "holdings-open",
        "watchlist-open",
        "calendar-open",
        "industry-open",
    ]),
    ("数据与查询", [
        "db-init",
        "db-sync",
        "db-reconcile",
        "holdings",
        "watchlist",
        "notes-search",
        "db-search",
        "market-json",
        "market-envelope",
        "review-prefill",
    ]),
    ("采集与计划", [
        "ingest-list",
        "ingest-run-post",
        "ingest-run-interface",
        "ingest-inspect",
        "ingest-health",
        "plan-draft",
        "plan-show-draft",
        "plan-confirm",
        "plan-diagnose",
        "plan-review",
        "knowledge-list",
        "knowledge-add-note",
        "knowledge-draft-from-asset",
    ]),
    ("日常流程", [
        "pre",
        "post",
        "today-open",
        "today-close",
        "today-pre",
        "today-post",
        "today-evening",
        "today-watchlist",
        "today-obsidian",
        "today-ingest-inspect",
        "today-ingest-health",
    ]),
]

DAILY_QUICKSTART = [
    ("make bootstrap", "首次安装依赖并启用本地 hooks"),
    ("make doctor", "检查 Python / Node / .env / hooksPath"),
    ("make check", "执行命令索引校验 + 前后端完整检查"),
    ("make dev", "同时启动 FastAPI 与 Vite 开发服务"),
    ("make today-open", "执行今日盘前流程"),
    ("make today-close", "执行今日盘后流程"),
    ("make market-open DATE=YYYY-MM-DD", "打开指定日期市场看板"),
    ("make review-open DATE=YYYY-MM-DD", "打开指定日期复盘工作台"),
    ("make plan-open DATE=YYYY-MM-DD", "打开指定日期计划工作台"),
    ("make today-ingest-inspect", "查看今日采集审计"),
    ("make today-ingest-health", "查看今日采集健康摘要"),
]


def parse_help_entries() -> dict[str, str]:
    entries: dict[str, str] = {}
    for line in MAKEFILE.read_text(encoding="utf-8").splitlines():
        match = HELP_LINE_RE.match(line)
        if match:
            target, desc = match.groups()
            entries[target] = desc.strip()
    return entries


def render_section(title: str, targets: list[str], entries: dict[str, str]) -> str:
    lines = [f"## {title}", "", "| 命令 | 说明 |", "|------|------|"]
    for target in targets:
        desc = entries.get(target)
        if desc:
            lines.append(f"| `make {target}` | {desc} |")
    lines.append("")
    return "\n".join(lines)


def build_output(entries: dict[str, str]) -> str:
    categorized = {target for _, targets in CATEGORY_ORDER for target in targets}

    parts = [
        "# 命令索引",
        "",
        "> 本文件由 `python3 scripts/generate_command_index.py` 自动生成，请勿手改。",
        "",
        "统一入口优先使用仓库根目录的 `make` 目标；更底层的 `python3 main.py ...` 与 API/URL 入口保留给调试和细粒度控制。",
        "",
        "## 每日高频",
        "",
        "| 命令 | 用途 |",
        "|------|------|",
    ]

    for command, desc in DAILY_QUICKSTART:
        parts.append(f"| `{command}` | {desc} |")
    parts.append("")

    for title, targets in CATEGORY_ORDER:
        parts.append(render_section(title, targets, entries))

    uncategorized = sorted(target for target in entries if target not in categorized)
    if uncategorized:
        parts.append(render_section("未分类", uncategorized, entries))

    return "\n".join(parts).rstrip() + "\n"


def build_json_payload(entries: dict[str, str]) -> dict[str, object]:
    categorized = {target for _, targets in CATEGORY_ORDER for target in targets}
    sections: list[dict[str, object]] = []
    for title, targets in CATEGORY_ORDER:
        items = [
            {"target": target, "command": f"make {target}", "description": entries[target]}
            for target in targets
            if target in entries
        ]
        sections.append({"title": title, "items": items})

    uncategorized = [
        {"target": target, "command": f"make {target}", "description": entries[target]}
        for target in sorted(entries)
        if target not in categorized
    ]
    if uncategorized:
        sections.append({"title": "未分类", "items": uncategorized})

    return {
        "generated_by": "python3 scripts/generate_command_index.py",
        "summary": "统一入口优先使用仓库根目录的 make 目标；更底层的 python3 main.py ... 与 API/URL 入口保留给调试和细粒度控制。",
        "daily_quickstart": [
            {"command": command, "description": desc}
            for command, desc in DAILY_QUICKSTART
        ],
        "sections": sections,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate docs/commands.md from Makefile help entries.")
    parser.add_argument("--check", action="store_true", help="fail if docs/commands.md is out of date")
    args = parser.parse_args()

    entries = parse_help_entries()
    output = build_output(entries)
    json_output = json.dumps(build_json_payload(entries), ensure_ascii=False, indent=2) + "\n"

    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        current_json = JSON_OUTPUT.read_text(encoding="utf-8") if JSON_OUTPUT.exists() else ""
        if current != output or current_json != json_output:
            print("docs/commands.md is out of date. Run: make commands-doc", file=sys.stderr)
            raise SystemExit(1)
        return

    OUTPUT.write_text(output, encoding="utf-8")
    JSON_OUTPUT.write_text(json_output, encoding="utf-8")


if __name__ == "__main__":
    main()
