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
            "4.00%；2026年9月21日至10月21日监控方盛制药达到或高于11.11元；"
            "2026年9月7日至10月6日监控美迪西严格跌破87.65元；"
            "2026年9月9日至22日监控好想你严格突破11.24元、品渥食品严格"
            "突破25.89元、良品铺子严格突破10.17元；"
            "2026年9月17日至28日（7个交易日）监控大金重工严格突破41.96元；"
            "2026年9月14日至22日（7个交易日）监控长春燃气进入动态前复权MA20±1%范围；"
            "MA20使用前19个已收盘交易日与当日最新价；同期另监控长春燃气重新站上动态前复权MA5，"
            "MA5使用前4个已收盘交易日与当日最新价，每日首次采样只建立基线。并在09:30至10:00"
            "（不含10:00）每3分钟扫描"
            "最新价达到正式涨停价且"
            "当日累计成交额不少于100亿元的A股，同股当日只提醒一次。"
            "2026年9月16日至24日（7个交易日）另监控福龙马严格突破14.36元。"
            "2026年9月17日至10月16日（一个月）监控双星新材严格突破12.98元。"
            "2026年9月17日至28日（7个交易日）监控欣旺达严格突破19.94元。"
            "2026年9月17日至10月8日（10个交易日）监控飞龙股份严格突破57.16元。"
            "2026年9月21日至23日（3个交易日）另监控飞龙股份重新站上动态前复权MA5。"
            "科翔股份108.30元监控已下线，其他监控能力保留。"
            "2026年9月22日至10月8日（7个交易日）监控有研硅严格突破57.96元；旧46.14元规则保持下线。"
            "2026年9月23日仅一天监控南华生物断板风险；盘中未封涨停提醒，收盘低于当日涨停价才确认断板。"
            "2026年9月24日至29日（3个交易日）监控中材科技严格跌破59.00元。"
            "气派科技37.11元监控已下线，历史规则与记录保留。"
            "2026年9月29日至10月8日（3个交易日）监控瑞芯微严格跌破203.90元或严格超过219.02元。"
            "2026年9月29日至10月8日（3个交易日）监控先导基电严格突破49.72元。"
            "2026年10月9日至22日（10个交易日）监控圣邦股份严格突破103.41元。"
            "2026年10月12日至20日（7个交易日）监控上证指数严格突破3870/3930点、"
            "创业板指3140点、科创200 1400点及中证1000 7340点。"
            "方盛制药原规则10月12日结束后，10月13日至21日接续7个交易日。"
            "2026年10月13日至26日（10个交易日）监控伊利股份严格跌破27.31元。"
            "2026年10月14日至16日（3个交易日）监控旋极信息严格突破3.76元、"
            "有研新材严格跌破42.40元。"
            "2026年10月14日至22日（7个交易日）监控襄阳轴承严格突破11.97元。"
            "2026年10月27日至11月9日伊利股份追加10个交易日，仍严格跌破27.31元提醒；"
            "10月26日至11月6日方盛制药恢复10个交易日，仍达到或高于11.11元提醒，不追补过期空档。"
            "2026年10月26日至30日（5个交易日）监控云汉芯城严格跌破105.49元，等于不触发。"
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
            "2026年9月21日至10月21日方盛制药达到或高于11.11元时推送。"
            "2026年9月7日至10月6日美迪西严格低于87.65元时推送。"
            "2026年9月9日至22日好想你严格高于11.24元、品渥食品严格高于"
            "25.89元、良品铺子严格高于10.17元时推送。"
            "2026年9月17日至28日大金重工严格高于41.96元时推送；旧35.95元规则已停用。"
            "2026年9月14日至22日长春燃气进入动态前复权MA20±1%范围（含边界）时推送。"
            "同期另监控长春燃气从不高于动态前复权MA5变为严格高于；每日首次采样已在线上不补报。"
            "各规则持续命中去重，恢复后再次命中可重推；"
            "历史已退役规则保持下线；另扫描10点前百亿成交额涨停板。"
            "2026年9月16日至24日福龙马严格高于14.36元时推送；等于不触发，首次已突破提醒。"
            "2026年9月17日至10月16日双星新材严格高于12.98元时推送；等于不触发，首次已突破提醒。"
            "2026年9月17日至28日欣旺达严格高于19.94元时推送；等于不触发，首次已突破提醒。"
            "2026年9月17日至10月8日飞龙股份严格高于57.16元时推送；等于不触发，首次已突破提醒。"
            "2026年9月21日至23日飞龙股份从不高于动态前复权MA5变为严格高于时推送；"
            "MA5使用前4个已收盘交易日与当日最新价，每日首次采样已在线上不补报，57.16元规则保留。"
            "科翔股份108.30元监控已下线，不再为该规则取数或推送。"
            "2026年9月22日至10月8日有研硅严格高于57.96元时推送；等于不触发，首次已突破提醒；旧46.14元规则保持下线。"
            "2026年9月23日南华生物严格低于当日动态涨停价时推送；首次未封板提醒，"
            "回封后再开板可重推，收盘仍未封板另发断板确认，9月24日起停用。"
            "2026年9月24、28、29日中材科技严格低于59.00元时推送；等于不触发，首次已跌破提醒。"
            "气派科技37.11元监控已下线，不再为该规则取数或推送。"
            "2026年9月29、30日和10月8日瑞芯微严格低于203.90元或严格高于219.02元时分别推送；等于不触发，首次已命中提醒。"
            "2026年9月29、30日和10月8日先导基电严格高于49.72元时推送；等于不触发，首次已突破提醒。"
            "2026年10月9日至22日圣邦股份严格高于103.41元时推送；等于不触发，首次已突破提醒。"
            "2026年10月12日至20日上证指数3870/3930点分档独立提醒；创业板指3140点、"
            "科创200 1400点、中证1000 7340点均严格高于压力位时推送，等于不触发，"
            "首次已突破提醒，持续突破去重，回落后再次突破可重推。"
            "方盛制药10月13日至21日接续7个交易日，等于11.11元仍触发，旧窗口及历史状态保留。"
            "2026年10月13日至26日伊利股份严格低于27.31元时推送；等于不触发，"
            "首次已跌破提醒，持续跌破去重，恢复到线或线上后再跌破可重推；原窗口10月26日结束，后续按续期窗口运行。"
            "2026年10月14日至16日旋极信息严格高于3.76元、有研新材严格低于42.40元时分别推送；"
            "等于不触发，首次已命中提醒，持续命中去重，恢复后再次命中可重推，10月17日起停用。"
            "2026年10月14日至22日襄阳轴承严格高于11.97元时推送；等于不触发，"
            "首次已突破提醒，持续突破去重，回落至阈值或以下后再次突破可重推，10月23日起停用。"
            "伊利股份原规则保留至10月26日，10月27日至11月9日追加10个交易日，严格低于27.31元，11月10日起停用。"
            "方盛制药10月26日至11月6日恢复10个交易日，达到或高于11.11元，11月7日起停用；不追补此前空档。"
            "两条续期独立身份、不重叠旧窗口，首次新鲜行情已命中提醒，持续命中去重，恢复后再次命中可重推，历史状态保留。"
            "云汉芯城301563.SZ于2026年10月26日至30日（5个交易日）严格低于105.49元时推送；"
            "等于不触发，首次新鲜行情已跌破提醒、持续跌破去重、恢复后再跌破可重推，10月31日起停用。"
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
