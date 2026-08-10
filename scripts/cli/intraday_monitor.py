"""CLI: 可扩展盘中实时阈值监控。"""
from __future__ import annotations

import argparse
import json
import logging

from services.intraday_monitor import DEFAULT_RULES, run_check, run_e2e_test


logger = logging.getLogger(__name__)


def register_subparser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "intraday-monitor",
        help="盘中实时阈值监控（上证3955长期规则 + 利通电子8月11日临时规则）",
        description=(
            "可扩展盘中实时阈值监控。长期规则监控上证指数从3955点下方站上"
            "3955点；2026年8月11日临时监控利通电子严格跌破123.92元。"
        ),
    )
    commands = parser.add_subparsers(dest="intraday_monitor_command")
    check = commands.add_parser(
        "check",
        help="执行一次当前有效规则的监控检查",
        description=(
            "执行一次监控检查。上证指数从3955点下方站上3955点时推送钉钉；"
            "利通电子规则仅在2026年8月11日有效，价格严格低于123.92元时推送。"
            "各规则持续命中去重，恢复后再次命中可重推。"
        ),
    )
    check.add_argument("--dry-run", action="store_true", help="只预览，不写状态、不推送")
    check.add_argument("--json", action="store_true", help="输出 JSON 结果")
    e2e = commands.add_parser(
        "e2e-test",
        help="对上证指数3955规则做真实链路测试",
        description=(
            "对当前启用的上证指数3955生产规则做真实链路测试。只使用盘中新鲜"
            "真实行情和本次临时测试线，不读写正式监控状态。"
        ),
    )
    e2e.add_argument("--input-by", required=True, help="测试请求者，用于消息审计")
    e2e.add_argument(
        "--confirm-real-push",
        action="store_true",
        help="确认本次会使用真实行情并发送一条钉钉测试消息",
    )
    e2e.add_argument("--json", action="store_true", help="输出 JSON 结果")


def handle_command(config: dict, args: argparse.Namespace) -> int:
    command = getattr(args, "intraday_monitor_command", None)
    if command not in {"check", "e2e-test"}:
        logger.error(
            "用法: python3 scripts/main.py intraday-monitor "
            "check [--dry-run] [--json] | e2e-test --input-by USER "
            "--confirm-real-push [--json]"
        )
        return 2

    from main import setup_providers

    if command == "e2e-test":
        confirm_real_push = getattr(args, "confirm_real_push", False)
        if confirm_real_push is not True:
            result = run_e2e_test(
                None,
                input_by=str(args.input_by),
                confirm_real_push=False,
            )
        else:
            registry = setup_providers(config) if DEFAULT_RULES else None
            result = run_e2e_test(
                registry,
                input_by=str(args.input_by),
                confirm_real_push=True,
            )
    else:
        registry = setup_providers(config) if DEFAULT_RULES else None
        result = run_check(registry, dry_run=bool(args.dry_run))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(
            f"[intraday-monitor] status={result['status']} "
            f"events={len(result.get('events') or [])} "
            f"pending={result.get('pending_count', 0)}"
        )
        for error in result.get("errors") or []:
            print(f"- {error}")

    if command == "e2e-test" and result["status"] != "complete":
        return 1
    if result["status"] in {"blocked_calendar", "source_failed", "push_failed", "state_error"}:
        return 1
    return 0
