"""CLI: 4日均线拐头 + 成交额突破双均量线观察池。"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import sys

from db.connection import get_connection
from services.ma_breakout import constants as C
from services.ma_breakout import renderer, scanner

logger = logging.getLogger(__name__)


def _positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("必须为正整数") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("必须为正整数")
    return value


def _windows(raw: str) -> tuple[int, ...]:
    try:
        values = tuple(int(x.strip()) for x in raw.split(",") if x.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("必须为逗号分隔正整数") from exc
    if len(values) < 2 or any(v <= 0 for v in values):
        raise argparse.ArgumentTypeError("必须至少提供两条正整数均量线,如 5,10")
    return values


def register_subparser(subparsers: argparse._SubParsersAction) -> None:
    mb = subparsers.add_parser("ma-breakout", help="4日均线拐头 + 成交额突破双均量线观察池")
    sub = mb.add_subparsers(dest="ma_breakout_command")

    daily = sub.add_parser("daily", help="盘后扫描并输出观察清单")
    daily.add_argument("--date", default=None, help="交易日 YYYY-MM-DD（默认今天）")
    daily.add_argument("--windows", type=_windows, default=C.DEFAULT_AMOUNT_WINDOWS,
                       help="成交额均线周期，逗号分隔（默认 5,10）")
    daily.add_argument("--top-n", type=_positive_int, default=C.DEFAULT_TOP_N,
                       help=f"最多展示数量（默认 {C.DEFAULT_TOP_N}）")
    daily.add_argument("--leader-lookback-days", type=_positive_int, default=C.DEFAULT_LEADER_LOOKBACK_DAYS,
                       help=f"历史龙头回看自然日数（默认 {C.DEFAULT_LEADER_LOOKBACK_DAYS}，用于二波）")
    daily.add_argument("--dry-run", action="store_true", help="仅打印，不推送")
    daily.add_argument("--no-push", action="store_true", help="仅打印，不推送")
    daily.add_argument("--json", action="store_true", help="输出 JSON（不推送）")


def handle_command(config: dict, args: argparse.Namespace) -> None:
    sub = getattr(args, "ma_breakout_command", None)
    if sub == "daily":
        _run_daily(config, args)
        return
    print("用法：python main.py ma-breakout daily [...]", file=sys.stderr)
    sys.exit(2)


def _today() -> str:
    return datetime.date.today().isoformat()


def _run_daily(config: dict, args: argparse.Namespace) -> None:
    from main import setup_providers

    date = args.date or _today()
    registry = setup_providers(config)
    registry.initialize_all()

    former_leaders = {}
    leader_stats = {}
    conn = get_connection()
    try:
        if args.json or not args.dry_run:
            from utils.trade_date import is_non_trading_day
            if is_non_trading_day(conn, registry, date):
                if args.json:
                    print(json.dumps({
                        "status": "skipped",
                        "reason": "non_trading_day",
                        "date": date,
                        "candidates": [],
                    }, ensure_ascii=False, indent=2))
                    return
                logger.warning("⚠️ %s 为非交易日，跳过 4日均线模式扫描（不推送）", date)
                return
        former_leaders = scanner.load_former_leader_universe(
            conn,
            date,
            lookback_days=args.leader_lookback_days,
            registry=registry,
            stats=leader_stats,
        )
    finally:
        conn.close()

    if leader_stats.get("leader_resolution_error"):
        summary = {
            "status": "source_failed",
            "date": date,
            "windows": list(args.windows),
            "candidates": [],
            "source_errors": [f"leader_resolution_error:{leader_stats['leader_resolution_error']}"],
            "leader_universe_count": len(former_leaders),
            "leader_lookback_days": args.leader_lookback_days,
            "leader_unresolved_count": leader_stats.get("unresolved_leader_tracking", 0),
            "scanned_count": 0,
            "matched_count": 0,
            "insufficient_count": 0,
            "truncated": False,
        }
    else:
        summary = scanner.run_daily(
            registry,
            date,
            windows=args.windows,
            top_n=args.top_n,
            former_leaders=former_leaders,
        )
        summary["leader_lookback_days"] = args.leader_lookback_days
        summary["leader_unresolved_count"] = leader_stats.get("unresolved_leader_tracking", 0)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return
    md = renderer.render_daily(summary)
    print(md)
    if args.dry_run or args.no_push:
        logger.info("[ma-breakout daily] 仅打印，未推送")
        return
    if summary.get("status") == "source_failed":
        logger.error("[ma-breakout daily] 行情源失败，跳过推送")
        return
    _push_to_dingtalk(f"4日均线模式观察池 · {date}", md)


def _push_to_dingtalk(title: str, markdown: str) -> None:
    from pushers.dingtalk_pusher import DingTalkPusher

    pusher = DingTalkPusher(config={})
    if not pusher.initialize():
        logger.error("[ma-breakout] DingTalk pusher 未启用（缺 env DINGTALK_WEBHOOK_TOKEN/SECRET），跳过推送")
        return
    ok = pusher.send_markdown(title=title, content=markdown)
    logger.info("[ma-breakout] 推送 %s", "成功" if ok else "失败")
