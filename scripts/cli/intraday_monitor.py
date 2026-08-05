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
        help="盘中实时阈值监控（能力保留，当前无启用规则）",
        description=(
            "可扩展盘中实时阈值监控。当前没有启用生产规则；"
            "正式 check 仅将既有待发送事件标记过期，不抓行情、不推送。"
        ),
    )
    commands = parser.add_subparsers(dest="intraday_monitor_command")
    check = commands.add_parser(
        "check",
        help="执行一次监控检查；当前无规则时仅过期既有 pending",
        description=(
            "执行一次监控检查。当前没有启用生产规则：正式运行仅将既有 pending "
            "移入 expired；状态文件不存在时不创建，不访问行情源或钉钉。"
        ),
    )
    check.add_argument("--dry-run", action="store_true", help="只预览，不写状态、不推送")
    check.add_argument("--json", action="store_true", help="输出 JSON 结果")
    e2e = commands.add_parser(
        "e2e-test",
        help="对当前启用规则做真实链路测试；无启用规则时拒绝执行并返回非零",
        description=(
            "对当前启用的生产规则做真实链路测试。当前无启用规则时返回 "
            "no_rules 与非零退出，不抓行情、不推送、不读写正式状态。"
        ),
    )
    e2e.add_argument("--input-by", required=True, help="测试请求者，用于消息审计")
    e2e.add_argument("--json", action="store_true", help="输出 JSON 结果")


def handle_command(config: dict, args: argparse.Namespace) -> int:
    command = getattr(args, "intraday_monitor_command", None)
    if command not in {"check", "e2e-test"}:
        logger.error(
            "用法: python3 scripts/main.py intraday-monitor "
            "check [--dry-run] [--json] | e2e-test --input-by USER [--json]"
        )
        return 2

    from main import setup_providers

    registry = setup_providers(config) if DEFAULT_RULES else None
    if command == "e2e-test":
        result = run_e2e_test(registry, input_by=str(args.input_by))
    else:
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
