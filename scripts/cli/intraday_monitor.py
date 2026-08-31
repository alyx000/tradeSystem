"""CLI: 可扩展盘中实时阈值监控。"""
from __future__ import annotations

import argparse
import json
import logging

from services.intraday_monitor import (
    DEFAULT_MARKET_SCAN_RULES,
    DEFAULT_RULES,
    run_all_checks,
    run_e2e_test,
)
from services.intraday_monitor.guards import shanghai_now


logger = logging.getLogger(__name__)


def register_subparser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "intraday-monitor",
        help="盘中实时监控（单标的阈值 + 10点前百亿成交涨停板）",
        description=(
            "可扩展盘中实时阈值监控。长期监控上证指数从3955点下方站上"
            "3955点；2026年8月21日与24日监控科创50严格突破1700点及"
            "凯莱英严格突破172.26元；2026年8月31日监控国瓷材料严格跌破"
            "67.22元，8月31日至9月2日监控中科飞测严格跌破前5个已收盘"
            "交易日的前复权MA5；长期监控同花顺全A（沪深）单日跌幅严格超过"
            "4.00%；并在09:30至10:00（不含10:00）每5分钟扫描"
            "最新价达到正式涨停价且"
            "当日累计成交额不少于100亿元的A股，同股当日只提醒一次。"
        ),
    )
    commands = parser.add_subparsers(dest="intraday_monitor_command")
    check = commands.add_parser(
        "check",
        help="执行一次当前有效规则的监控检查",
        description=(
            "执行一次监控检查。上证指数从3955点下方站上3955点时推送钉钉；"
            "2026年8月21日与24日科创50严格高于1700点、凯莱英严格高于"
            "172.26元时推送；8月31日国瓷材料严格低于67.22元、8月31日至"
            "9月2日中科飞测严格低于前5个已收盘交易日MA5时推送；同花顺全A"
            "（沪深）单日涨跌幅严格低于-4.00%时推送。"
            "各规则持续命中去重，恢复后再次命中可重推；"
            "历史已退役规则保持下线；另扫描10点前百亿成交额涨停板。"
        ),
    )
    check.add_argument("--dry-run", action="store_true", help="只预览，不写状态、不推送")
    check.add_argument("--json", action="store_true", help="输出 JSON 结果")
    e2e = commands.add_parser(
        "e2e-test",
        help="对指定生产规则做真实链路测试",
        description=(
            "对指定生产规则做真实链路测试（默认上证指数3955规则）。只使用盘中新鲜"
            "真实行情和本次临时测试线，不读写正式监控状态。"
        ),
    )
    e2e.add_argument("--input-by", required=True, help="测试请求者，用于消息审计")
    e2e.add_argument(
        "--confirm-real-push",
        action="store_true",
        help="确认本次会使用真实行情并发送一条钉钉测试消息",
    )
    e2e.add_argument(
        "--rule-id",
        choices=[rule.rule_id for rule in DEFAULT_RULES],
        default=DEFAULT_RULES[0].rule_id if DEFAULT_RULES else None,
        help="选择要验证的生产规则；默认验证上证指数3955规则",
    )
    e2e.add_argument("--json", action="store_true", help="输出 JSON 结果")


def handle_command(config: dict, args: argparse.Namespace) -> int:
    command = getattr(args, "intraday_monitor_command", None)
    if command not in {"check", "e2e-test"}:
        logger.error(
            "用法: python3 scripts/main.py intraday-monitor "
            "check [--dry-run] [--json] | e2e-test [--rule-id RULE_ID] --input-by USER "
            "--confirm-real-push [--json]"
        )
        return 2

    from main import setup_providers

    if command == "e2e-test":
        selected_rule = next(
            (
                rule
                for rule in DEFAULT_RULES
                if rule.rule_id == getattr(args, "rule_id", None)
            ),
            DEFAULT_RULES[0] if DEFAULT_RULES else None,
        )
        confirm_real_push = getattr(args, "confirm_real_push", False)
        if confirm_real_push is not True:
            result = run_e2e_test(
                None,
                input_by=str(args.input_by),
                confirm_real_push=False,
                rule=selected_rule,
            )
        elif selected_rule is not None and not selected_rule.is_effective_on(
            shanghai_now().date()
        ):
            result = run_e2e_test(
                None,
                input_by=str(args.input_by),
                confirm_real_push=True,
                rule=selected_rule,
            )
        else:
            registry = setup_providers(config) if DEFAULT_RULES else None
            result = run_e2e_test(
                registry,
                input_by=str(args.input_by),
                confirm_real_push=True,
                rule=selected_rule,
            )
    else:
        registry = setup_providers(config) if (DEFAULT_RULES or DEFAULT_MARKET_SCAN_RULES) else None
        result = run_all_checks(registry, dry_run=bool(args.dry_run))
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
    if result["status"] in {
        "blocked_calendar",
        "source_failed",
        "partial",
        "push_failed",
        "state_error",
    }:
        return 1
    return 0
