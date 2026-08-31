"""CLI：全市场盘中半小时扫描与钉钉摘要。"""
from __future__ import annotations

import argparse
import json
import logging

from services.intraday_summary import run


logger = logging.getLogger(__name__)


def register_subparser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("intraday-summary", help="全市场盘中半小时扫描与钉钉摘要")
    commands = parser.add_subparsers(dest="intraday_summary_command")
    daily = commands.add_parser("run", help="执行当前半小时槽位扫描")
    daily.add_argument("--dry-run", action="store_true", help="只计算和打印，不写文件、不推送")
    daily.add_argument("--no-push", action="store_true", help="保存快照和报告，但不推送")
    daily.add_argument("--json", action="store_true", help="输出 JSON 结果")


def handle_command(config: dict, args: argparse.Namespace) -> int:
    if getattr(args, "intraday_summary_command", None) != "run":
        logger.error("用法: python3 scripts/main.py intraday-summary run [--dry-run|--no-push] [--json]")
        return 2
    from main import setup_providers

    registry = setup_providers(config)
    result = run(
        registry,
        dry_run=bool(args.dry_run),
        no_push=bool(args.no_push),
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result.get("markdown"):
        print(result["markdown"])
    else:
        print(
            f"[intraday-summary] status={result.get('status')} "
            f"slot={result.get('slot_id', '—')} scanned={result.get('scanned', 0)} "
            f"pushed={result.get('pushed', False)}"
        )
        for error in result.get("errors") or []:
            print(f"- {error}")
    if result.get("status") in {"blocked_calendar", "source_failed", "push_failed", "state_error"}:
        return 1
    return 0
