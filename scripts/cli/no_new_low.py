"""CLI：七天横盘不创新低全市场盘后扫描 + LLM 两两 PK。"""
from __future__ import annotations

import argparse
import datetime
import logging
import sys

from db.connection import get_readonly_connection
from services.no_new_low import pk, renderer, scanner

logger = logging.getLogger(__name__)


def _iso_date(raw: str) -> str:
    try:
        parsed = datetime.date.fromisoformat(raw)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("日期必须为 YYYY-MM-DD") from exc
    if parsed.isoformat() != raw:
        raise argparse.ArgumentTypeError("日期必须为 YYYY-MM-DD")
    return raw


def register_subparser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("no-new-low", help="七天横盘不创新低个股扫描与两两 PK")
    commands = parser.add_subparsers(dest="no_new_low_command")
    daily = commands.add_parser("daily", help="全市场扫描→两两 PK→落报告→推钉钉")
    daily.add_argument("--date", type=_iso_date, default=None, help="目标交易日 YYYY-MM-DD（默认今天）")
    daily.add_argument("--dry-run", action="store_true", help="仅打印，不落盘、不推送")
    daily.add_argument("--no-push", action="store_true", help="落盘但不推送")
    daily.add_argument("--no-llm", action="store_true", help="跳过 LLM 两两 PK，保留全量事实名单与极窄横盘附表")
    daily.add_argument("--input-by", default="launchd", help="请求者（手工补跑显式传 USER）")


def handle_command(config: dict, args: argparse.Namespace) -> int:
    if getattr(args, "no_new_low_command", None) != "daily":
        print("用法：python main.py no-new-low daily [...]", file=sys.stderr)
        return 2
    return _run_daily(config, args)


def _run_daily(config: dict, args: argparse.Namespace) -> int:
    from main import setup_providers
    from utils.network_env import without_standard_http_proxy

    date = args.date or datetime.date.today().isoformat()
    conn = get_readonly_connection()
    try:
        with without_standard_http_proxy():
            registry = setup_providers(config)
            registry.initialize_all()
            cached_trade_day = _trade_day_from_db(conn, date) if not args.dry_run else True
            if cached_trade_day is False:
                logger.warning("%s 为已确认非交易日，跳过七天横盘不创新低扫描（不落盘、不推送）", date)
                return 0
            if cached_trade_day is None:
                result = {
                    "status": "source_failed", "date": date,
                    "failed_sources": {"trade_calendar": "目标日交易日历缺失，拒绝按工作日近似执行"},
                }
            else:
                result = scanner.run_daily(conn, registry, date)
    finally:
        conn.close()
    result["input_by"] = getattr(args, "input_by", "launchd")

    if result.get("status") == "source_failed":
        markdown = renderer.render_source_failed(result)
        print(markdown)
        if not args.dry_run:
            try:
                renderer.save_artifacts(markdown, result, None, date)
            except OSError:
                logger.error("[no-new-low] 失败报告落盘异常，继续尝试告警", exc_info=True)
            if not args.no_push:
                _push_to_dingtalk(f"⚠️ 七天横盘不创新低·数据失败 · {date}", markdown)
        return 1

    pk_result = None
    if not args.no_llm:
        pool = result.get("pk_pool") or []
        llm_runner = None
        if len(pool) >= 2:
            try:
                llm_runner = pk.build_llm_runner()
            except Exception:
                logger.error("[no-new-low] PK runner 初始化失败，将按熔断状态落报告", exc_info=True)
        pk_result = pk.run_pk(pool, llm_runner)
    markdown = renderer.render_daily(result, pk_result)
    print(markdown)
    if args.dry_run:
        return 0

    md_path, json_path = renderer.save_artifacts(markdown, result, pk_result, date)
    logger.info("[no-new-low] 报告已落盘 %s；%s", md_path, json_path)
    if args.no_push:
        return 0
    pushed = _push_to_dingtalk(
        f"七天横盘不创新低·名单摘要 · {date}", renderer.render_push(result, pk_result))
    return 0 if pushed else 2


def _trade_day_from_db(conn, date: str) -> bool | None:
    """只读本地交易日历；定时任务不得为守卫隐式写 SQLite。"""
    from db import queries as Q

    return Q.is_trade_day_from_db(conn, date)


def _push_to_dingtalk(title: str, markdown: str) -> bool:
    from pushers.dingtalk_pusher import DingTalkPusher

    pusher = DingTalkPusher(config={})
    if not pusher.initialize():
        logger.error("[no-new-low] DingTalk pusher 未启用（缺 env）")
        return False
    ok = bool(pusher.send_markdown(title=title, content=markdown))
    logger.info("[no-new-low] 推送 %s", "成功" if ok else "失败")
    return ok
