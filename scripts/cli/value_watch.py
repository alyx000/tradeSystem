"""CLI: 价值投资条件监控（鞠磊红利/稀缺价值框架，认知出处 teacher_notes#391）。

  value-watch daily  [--date YYYY-MM-DD] [--dry-run | --no-push]
  value-watch report [--date YYYY-MM-DD]

daily:采集(锚定起点)+三引擎重放+落库+事件推送(仅目标日=最新已收盘交易日且事件未发)。
      三档:裸=落库+推送;--no-push=落库+打印候选;--dry-run=内存计算不落库不推送不写账本
      (豁免非交易日守卫)。
report:只读渲染已落库快照(不采集不现算);要看"现在的最新状态"用 daily --dry-run。
"""
from __future__ import annotations

import argparse
import datetime
import logging
import sys

from db.connection import get_connection
from db.migrate import migrate
from services.value_watch import service

logger = logging.getLogger(__name__)


def register_subparser(subparsers: argparse._SubParsersAction) -> None:
    vw = subparsers.add_parser("value-watch", help="价值投资条件监控(红利回撤/卖出阶梯/稀缺周线)")
    sub = vw.add_subparsers(dest="value_watch_command")

    daily = sub.add_parser("daily", help="采集 + 重放 + 落库 + 事件推送")
    daily.add_argument("--date", default=None, help="目标交易日 YYYY-MM-DD(默认今天)")
    daily.add_argument("--dry-run", action="store_true",
                       help="内存计算,不落库不推送不写账本(历史校准/预览)")
    daily.add_argument("--no-push", action="store_true",
                       help="落库+打印候选事件,不推送钉钉")

    report = sub.add_parser("report", help="只读渲染已落库快照")
    report.add_argument("--date", default=None, help="快照日期 YYYY-MM-DD(默认最新)")


def handle_command(config: dict, args: argparse.Namespace) -> None:
    sub = getattr(args, "value_watch_command", None)
    if sub == "daily":
        _run_daily(config, args)
    elif sub == "report":
        _run_report(args)
    else:
        print("用法:python main.py value-watch daily|report [--date YYYY-MM-DD] "
              "[--dry-run|--no-push]", file=sys.stderr)
        sys.exit(2)


def _today() -> str:
    return datetime.date.today().isoformat()


def _run_daily(config: dict, args: argparse.Namespace) -> None:
    from main import setup_providers

    date = args.date or _today()
    registry = setup_providers(config)
    registry.initialize_all()  # register 不自动初始化,漏了则 provider 调用全部失败
    provider = registry.get_provider("tushare")
    if provider is None:
        logger.error("[value-watch] tushare provider 不可用(sw_daily 直连依赖),中止")
        sys.exit(1)
    conn = get_connection()
    try:
        migrate(conn)  # 连库即迁移(全仓库约定,保证 value_watch_daily 表存在)
        # 非交易日守卫(仅 persist):节假日源常返上一交易日陈旧数据按当日落库。
        # dry-run 豁免(也避免守卫日历预取写真实库,破坏无副作用语义)。
        from utils.trade_date import is_non_trading_day
        if not args.dry_run and is_non_trading_day(conn, registry, date):
            logger.warning("⚠️ %s 为非交易日,跳过 value-watch 采集(不落库不推送)", date)
            return
        md = service.run_daily(conn, registry, provider, date,
                               persist=not args.dry_run,
                               push=not (args.dry_run or args.no_push))
    finally:
        conn.close()

    if md is None:
        print(f"{date} 无可用数据,跳过。")
        return
    print(md)
    mode = "dry-run" if args.dry_run else ("no-push" if args.no_push else "daily")
    logger.info("[value-watch] %s 完成(%s)", date, mode)


def _run_report(args: argparse.Namespace) -> None:
    conn = get_connection()
    try:
        migrate(conn)
        md = service.run_report(conn, args.date)
    finally:
        conn.close()
    print(md)  # 只读,不推送
