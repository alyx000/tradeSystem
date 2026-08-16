"""CLI: 盘前早报(隔夜行情 + 海外/国内要闻 + 上市公司公告)。

  morning-brief daily [--date YYYY-MM-DD] [--dry-run|--no-push]

三档:裸(落报告+推钉钉)/--no-push(落报告不推)/--dry-run(仅打印,不落不推,豁免非交易日守卫)。
金十源失败 = source_failed:落失败报告+推告警+非零退出;公告/行情单源失败只降级对应段(partial)。
不写 SQLite 业务表/计划层/关注池,不构成买卖建议。
"""
from __future__ import annotations

import argparse
import datetime
import logging
import re
import sys

from db.connection import get_connection
from services.morning_brief import formatter, service

logger = logging.getLogger(__name__)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _iso_date(value: str) -> str:
    if not _DATE_RE.match(value):
        raise argparse.ArgumentTypeError(f"日期须为 YYYY-MM-DD 格式: {value!r}")
    try:
        return datetime.date.fromisoformat(value).isoformat()
    except ValueError:
        raise argparse.ArgumentTypeError(f"非法日期: {value!r}")


def register_subparser(subparsers: argparse._SubParsersAction) -> None:
    mb = subparsers.add_parser("morning-brief", help="盘前早报(隔夜行情+要闻+公告:报告+钉钉)")
    sub = mb.add_subparsers(dest="morning_brief_command")

    daily = sub.add_parser("daily", help="采集→分类→落报告→推送")
    daily.add_argument("--date", default=None, type=_iso_date,
                       help="补跑日 YYYY-MM-DD(窗口终点固定取该日 08:00;金十仅近期可回翻)")
    mode = daily.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="仅打印,不落报告不推送")
    mode.add_argument("--no-push", action="store_true", help="落报告但不推送")


def handle_command(config: dict, args: argparse.Namespace) -> None:
    sub = getattr(args, "morning_brief_command", None)
    if sub == "daily":
        _run_daily(config, args)
    else:
        print("用法:python main.py morning-brief daily [--date ...] [--dry-run|--no-push]",
              file=sys.stderr)
        sys.exit(2)


def _run_daily(config: dict, args: argparse.Namespace) -> None:
    from db.migrate import migrate
    from main import setup_providers
    from utils.network_env import without_standard_http_proxy
    from utils.trade_date import is_non_trading_day

    target_date = args.date or datetime.datetime.now(service.TZ).date().isoformat()
    conn = get_connection()
    try:
        migrate(conn)  # 连库即迁移(全仓库约定):fresh 库缺 trade_calendar 时守卫才不会静默 fail-open
        # 金十采集也在 without_standard_http_proxy 内跑,与 macro-flash 独立 CLI(沿用系统
        # 代理)行为分叉——这是有意的:早报还要经 akshare/巨潮取行情与公告,须统一直连;
        # 金十国内直连可达。若 08:00 档金十连续 source_failed 而 20:00 macro-flash 档
        # 正常,先查本机代理 env 差异再怀疑源。
        with without_standard_http_proxy():
            registry = setup_providers(config)
            registry.initialize_all()
            if not args.dry_run and is_non_trading_day(conn, registry, target_date):
                logger.warning("⚠️ %s 为非交易日,跳过盘前早报(不落报告、不推送)", target_date)
                return
            brief = service.build_brief(config, registry, date_str=args.date, conn=conn)
    finally:
        conn.close()

    md = formatter.render(brief.payload)
    print(md)
    source_failed = brief.status == service.STATUS_FAILED

    if args.dry_run:
        logger.info("[morning-brief] dry-run 完成,未落报告/未推送")
        if source_failed:
            raise SystemExit(1)
        return

    path = formatter.write_report(md, brief.date)
    logger.info("[morning-brief] 报告已写入 %s", path)

    if source_failed:
        if not args.no_push:
            _push(f"盘前早报数据源失败 · {brief.date}",
                  formatter.build_failure_push(brief.date, brief.error or "未知错误"))
        raise SystemExit(1)

    if args.no_push:
        logger.info("[morning-brief] --no-push:已落报告,未推送")
        return

    ok = _push(f"盘前早报 · {brief.date}", formatter.build_push_body(md, brief.date))
    if not ok:
        logger.error("[morning-brief] %s 推送失败", brief.date)
        raise SystemExit(1)


def _push(title: str, markdown: str) -> bool:
    from pushers.dingtalk_pusher import DingTalkPusher

    pusher = DingTalkPusher(config={})
    if not pusher.initialize():
        logger.error("[morning-brief] DingTalk pusher 未启用(缺 env DINGTALK_WEBHOOK_TOKEN/SECRET),跳过推送")
        return False
    ok = pusher.send_markdown(title=title, content=markdown)
    logger.info("[morning-brief] 推送 %s", "成功" if ok else "失败")
    return bool(ok)
