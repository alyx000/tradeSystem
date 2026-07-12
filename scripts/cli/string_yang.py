"""CLI: 主线板块串阳首阴股票池。

  string-yang daily [--date YYYY-MM-DD] [--top-k N] [--top-concepts N]
                    [--teacher-lookback-days N] [--no-llm] [--dry-run] [--no-push]

只推已经出现第一根阴线的票；连续五阳但尚未出阴线的预备票不输出。
"""
from __future__ import annotations

import argparse
import datetime
import logging
import sys

from db.connection import get_connection
from services.string_yang import constants as C
from services.string_yang import renderer, scanner

logger = logging.getLogger(__name__)


def _positive_int(raw: str) -> int:
    try:
        v = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("必须为正整数") from exc
    if v <= 0:
        raise argparse.ArgumentTypeError("必须为正整数")
    return v


def register_subparser(subparsers: argparse._SubParsersAction) -> None:
    sy = subparsers.add_parser("string-yang", help="主线板块串阳首阴股票池")
    sub = sy.add_subparsers(dest="string_yang_command")

    daily = sub.add_parser("daily", help="扫描主线板块连续五阳后的第一根阴线")
    daily.add_argument("--date", default=None, help="交易日 YYYY-MM-DD（默认今天）")
    daily.add_argument("--top-k", type=_positive_int, default=C.DEFAULT_TOP_K_SECTORS,
                       help=f"主线取成交额集中度 Top-K 申万二级（默认 {C.DEFAULT_TOP_K_SECTORS}）")
    daily.add_argument("--top-concepts", type=_positive_int, default=C.DEFAULT_TOP_CONCEPTS,
                       help=f"提供给 LLM 的同花顺概念资金分支 Top-N（默认 {C.DEFAULT_TOP_CONCEPTS}）")
    daily.add_argument("--teacher-lookback-days", type=_positive_int, default=C.TEACHER_LOOKBACK_DAYS,
                       help=f"提供给 LLM 的老师观点回看自然日（默认 {C.TEACHER_LOOKBACK_DAYS}）")
    daily.add_argument("--no-llm", action="store_true",
                       help="不调用 LLM，降级为成交额集中度 Top-K 申万二级主线")
    daily.add_argument("--dry-run", action="store_true", help="仅打印，不落报告、不推送")
    daily.add_argument("--no-push", action="store_true", help="落报告但不推送")


def handle_command(config: dict, args: argparse.Namespace) -> None:
    sub = getattr(args, "string_yang_command", None)
    if sub == "daily":
        _run_daily(config, args)
    else:
        print("用法：python main.py string-yang daily [...]", file=sys.stderr)
        sys.exit(2)


def _today() -> str:
    return datetime.date.today().isoformat()


def _run_daily(config: dict, args: argparse.Namespace) -> None:
    from main import setup_providers
    from utils.network_env import without_standard_http_proxy
    from utils.trade_date import is_non_trading_day

    date = args.date or _today()
    conn = get_connection()
    try:
        with without_standard_http_proxy():
            registry = setup_providers(config)
            registry.initialize_all()
            if not args.dry_run and is_non_trading_day(conn, registry, date):
                logger.warning("⚠️ %s 为非交易日，跳过串阳首阴扫描（不落报告、不推送）", date)
                return
            result = scanner.run_daily(
                conn,
                registry,
                date,
                top_k=args.top_k,
                top_concepts=args.top_concepts,
                teacher_lookback_days=args.teacher_lookback_days,
                use_llm=not args.no_llm,
            )
    finally:
        conn.close()

    md = renderer.render_daily(result)
    print(md)
    source_failed = result.get("status") == "source_failed"
    if args.dry_run:
        logger.info("[string-yang daily] dry-run 完成，未落报告/未推送")
        if source_failed:
            raise SystemExit(1)
        return

    try:
        path = renderer.write_report(md, date)
        logger.info("[string-yang daily] 报告已写入 %s", path)
    except Exception:  # noqa: BLE001 - source_failed needs alert even if disk write fails.
        logger.exception("[string-yang daily] 报告落盘失败")
        if source_failed and not args.no_push:
            _push_to_dingtalk(f"串阳首阴数据源失败 · {date}", md)
        raise
    if source_failed:
        logger.error("[string-yang daily] %s 数据源失败，未完成扫描", date)
        if args.no_push:
            logger.info("[string-yang daily] --no-push：失败报告已落盘，未推送")
            raise SystemExit(1)
        _push_to_dingtalk(f"串阳首阴数据源失败 · {date}", md)
        raise SystemExit(1)
    if args.no_push:
        logger.info("[string-yang daily] --no-push：已落报告，未推送")
        return
    ok = _push_to_dingtalk(f"串阳首阴股票池 · {date}", md)
    if not ok:
        logger.error("[string-yang daily] %s 推送失败", date)
        raise SystemExit(1)


def _push_to_dingtalk(title: str, markdown: str) -> bool:
    from pushers.dingtalk_pusher import DingTalkPusher

    pusher = DingTalkPusher(config={})
    if not pusher.initialize():
        logger.error("[string-yang] DingTalk pusher 未启用（缺 env DINGTALK_WEBHOOK_TOKEN/SECRET），跳过推送")
        return False
    ok = pusher.send_markdown(title=title, content=markdown)
    logger.info("[string-yang] 推送 %s", "成功" if ok else "失败")
    return bool(ok)
