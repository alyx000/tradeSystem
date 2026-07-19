"""CLI: 板块拥挤度（交易拥挤度/斜率拥挤度/资金流代理）。

  sector-crowding daily    [--date] [--dry-run] [--push]
  sector-crowding report   [--date]
  sector-crowding trend    --sector CODE [--date] [--days 60]
  sector-crowding backfill --start 2019-01-01 [--end]

daily: 采集+落库,默认不推送(--push 才推钉钉;--dry-run 仅打印不落库)。
report/trend: 只读现算分位。backfill: 一次性历史回填(两阶段,fail-closed)。
"""
from __future__ import annotations

import argparse
import datetime
import logging
import sys

from db.connection import get_connection
from services.sector_crowding import service

logger = logging.getLogger(__name__)


def register_subparser(subparsers: argparse._SubParsersAction) -> None:
    sc = subparsers.add_parser("sector-crowding", help="板块拥挤度(交易/斜率/资金流代理)")
    sub = sc.add_subparsers(dest="sector_crowding_command")

    daily = sub.add_parser("daily", help="采集+落库(默认不推送)")
    daily.add_argument("--date", default=None, help="交易日 YYYY-MM-DD(默认今天)")
    daily.add_argument("--dry-run", action="store_true", help="仅打印,不落库不推送")
    daily.add_argument("--push", action="store_true", help="落库后推钉钉(默认不推)")

    report = sub.add_parser("report", help="只读:当日三部分全景+分位+双高")
    report.add_argument("--date", default=None, help="交易日 YYYY-MM-DD(默认今天)")

    trend = sub.add_parser("trend", help="只读:单板块拥挤度时间序列")
    trend.add_argument("--sector", required=True,
                       help="申万行业代码(如 801080.SI,建议用代码;回填历史行无中文名,按名查会缺段)")
    trend.add_argument("--date", default=None, help="截止交易日(默认今天)")
    trend.add_argument("--days", type=int, default=60, help="窗口天数(默认 60)")

    backfill = sub.add_parser("backfill", help="一次性历史回填(fail-closed,重跑即重试)")
    backfill.add_argument("--start", default=service.DEFAULT_BACKFILL_START,
                          help=f"起始日 YYYY-MM-DD(默认 {service.DEFAULT_BACKFILL_START})")
    backfill.add_argument("--end", default=None, help="截止日 YYYY-MM-DD(默认今天)")


def handle_command(config: dict, args: argparse.Namespace) -> None:
    sub = getattr(args, "sector_crowding_command", None)
    if sub == "daily":
        _run_daily(config, args)
    elif sub == "report":
        _run_readonly(lambda conn: service.run_report(conn, args.date or _today()))
    elif sub == "trend":
        _run_readonly(lambda conn: service.run_trend(
            conn, args.date or _today(), args.sector, days=args.days))
    elif sub == "backfill":
        _run_backfill(config, args)
    else:
        print("用法:python main.py sector-crowding daily|report|trend|backfill [...]",
              file=sys.stderr)
        sys.exit(2)


def _today() -> str:
    return datetime.date.today().isoformat()


def _run_readonly(fn) -> None:
    conn = get_connection()
    try:
        print(fn(conn))
    finally:
        conn.close()


def _setup(config):
    """复刻 sector_correlation._setup_tushare:setup_providers+initialize_all 取 tushare。

    返回 (registry, provider);失败 (None, None)。调用方须在 without_standard_http_proxy() 内。"""
    from main import setup_providers

    registry = setup_providers(config)
    registry.initialize_all()
    provider = registry.get_provider("tushare")
    if provider is None or not getattr(provider, "_initialized", False):
        logger.error("[sector-crowding] Tushare provider 未初始化(检查 TUSHARE_TOKEN / tushare.xyz 可达)")
        return None, None
    return registry, provider


def _run_daily(config: dict, args: argparse.Namespace) -> None:
    from utils.network_env import without_standard_http_proxy

    date = args.date or _today()
    conn = get_connection()
    try:
        with without_standard_http_proxy():
            registry, provider = _setup(config)
            if provider is None:
                return
            # 非交易日守卫在 service.run_daily 内(persist 时生效,dry-run 豁免)
            md = service.run_daily(conn, registry, provider, date,
                                   persist=not args.dry_run)
    finally:
        conn.close()
    if md is None:
        print(f"{date} 无拥挤度数据或非交易日,跳过。")
        return
    print(md)
    if args.push and not args.dry_run:
        _push_to_dingtalk(f"板块拥挤度 · {date}", md)


def _run_backfill(config: dict, args: argparse.Namespace) -> None:
    from utils.network_env import without_standard_http_proxy

    end = args.end or _today()
    conn = get_connection()
    try:
        with without_standard_http_proxy():
            registry, provider = _setup(config)
            if provider is None:
                return
            stats = service.run_backfill(conn, registry, provider, args.start, end)
    finally:
        conn.close()
    print(f"回填完成: 写入 {stats['dates_written']} 日 / 跳过已有 {stats['dates_skipped']} 日"
          f" / 两市总额缺失 {stats['dates_null_total']} 日(建议抽查)")


def _push_to_dingtalk(title: str, markdown: str) -> None:
    from pushers.dingtalk_pusher import DingTalkPusher

    pusher = DingTalkPusher(config={})
    if not pusher.initialize():
        logger.error("[sector-crowding] DingTalk pusher 未启用(缺 env DINGTALK_WEBHOOK_TOKEN/SECRET),跳过推送")
        return
    ok = pusher.send_markdown(title=title, content=markdown)
    logger.info("[sector-crowding] 推送 %s", "成功" if ok else "失败")
