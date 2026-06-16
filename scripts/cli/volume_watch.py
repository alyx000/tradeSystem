"""CLI: 成交额 Top20 板块集中度监控。

  volume-watch daily  [--date YYYY-MM-DD] [--dry-run] [--refetch]
  volume-watch trend  [--date YYYY-MM-DD] [--days N]

daily:read-through 采集 + 申万打标 + 落库 + 渲染 + 推钉钉(--dry-run 仅打印不推送;
      --refetch 强制重拉绕过 daily_market 陈旧缓存,回填历史用)。
trend:只读最近 N 日打印,不采集、不落库、不推送。
"""
from __future__ import annotations

import argparse
import datetime
import logging
import sys

from db.connection import get_connection
from db.migrate import migrate
from services.volume_concentration import service

logger = logging.getLogger(__name__)

TREND_DAYS = 30


def register_subparser(subparsers: argparse._SubParsersAction) -> None:
    vw = subparsers.add_parser("volume-watch", help="成交额 Top20 板块集中度(日报 / 趋势)")
    sub = vw.add_subparsers(dest="volume_watch_command")

    daily = sub.add_parser("daily", help="采集 + 落库 + 渲染 + 推钉钉")
    daily.add_argument("--date", default=None, help="交易日 YYYY-MM-DD(默认今天)")
    daily.add_argument("--dry-run", action="store_true", help="仅打印 markdown,不推送")
    daily.add_argument("--refetch", action="store_true",
                       help="强制重拉 top20,绕过 daily_market 陈旧缓存(回填历史用)")

    trend = sub.add_parser("trend", help="只读打印最近 N 日趋势(不采集不推送)")
    trend.add_argument("--date", default=None, help="截止交易日 YYYY-MM-DD(默认今天)")
    trend.add_argument("--days", type=int, default=TREND_DAYS,
                       help=f"趋势窗口天数(默认 {TREND_DAYS})")


def handle_command(config: dict, args: argparse.Namespace) -> None:
    sub = getattr(args, "volume_watch_command", None)
    if sub == "daily":
        _run_daily(config, args)
    elif sub == "trend":
        _run_trend(config, args)
    else:
        print("用法:python main.py volume-watch daily|trend [--date YYYY-MM-DD] [--dry-run|--days N]",
              file=sys.stderr)
        sys.exit(2)


def _today() -> str:
    return datetime.date.today().isoformat()


def _run_daily(config: dict, args: argparse.Namespace) -> None:
    from main import setup_providers

    date = args.date or _today()
    registry = setup_providers(config)
    registry.initialize_all()  # 必须:register 不自动初始化,漏了则所有 provider 调用返 provider_not_initialized
    conn = get_connection()
    try:
        migrate(conn)  # 必须:对齐全仓库约定(每个写库命令连库后即 migrate),保证 v34 gain_universe_json 列存在,否则老库 save 崩
        md = service.run_daily(conn, registry, date,
                               persist=not args.dry_run, refetch=args.refetch)
    finally:
        conn.close()

    if md is None:
        logger.info("[volume-watch daily] %s 无成交额数据,跳过(不写库不推送)", date)
        print(f"{date} 无成交额数据,跳过。")
        return
    if args.dry_run:
        print(md)
        logger.info("[volume-watch daily] dry-run 完成,未推送")
        return
    _push_to_dingtalk(f"成交额板块集中度 · {date}", md)


def _run_trend(config: dict, args: argparse.Namespace) -> None:
    date = args.date or _today()
    conn = get_connection()
    try:
        migrate(conn)  # 对齐全仓库约定:连库后即 migrate(只读路径亦保证 schema 齐全)
        md = service.run_trend(conn, date, days=args.days)
    finally:
        conn.close()
    print(md)  # 只读,不推送


def _push_to_dingtalk(title: str, markdown: str) -> None:
    """走 DingTalkPusher 推送;token/secret 由 pusher.initialize() 从 env 读。"""
    from pushers.dingtalk_pusher import DingTalkPusher

    pusher = DingTalkPusher(config={})
    if not pusher.initialize():
        logger.error("[volume-watch] DingTalk pusher 未启用(缺 env DINGTALK_WEBHOOK_TOKEN/SECRET),跳过推送")
        return
    ok = pusher.send_markdown(title=title, content=markdown)
    logger.info("[volume-watch] 推送 %s", "成功" if ok else "失败")
