"""CLI: 板块相关性分析（板块×板块 联动/跷跷板 + 板块×指数 同向/逆向）。

  sector-correlation daily   [--date] [--windows 20,60] [--top-industries 15] [--top-concepts 10]
                             [--activity-days 10] [--indices a,b,..] [--no-concept] [--dry-run]
  sector-correlation matrix  [--date] [--windows] [--top-industries] [--top-concepts] [--no-concept] [--refetch]
  sector-correlation trend   [--date] [--days 30]

daily: Tushare 采集 + 落库 + 渲染 + 推钉钉(--dry-run 仅打印不推不落)。
matrix: 读当天(无/--refetch 现采,不落库)→ 打印完整矩阵,不推送。
trend: 只读最近 N 日漂移,不采集不推送。
"""
from __future__ import annotations

import argparse
import datetime
import logging
import sys

from db.connection import get_connection
from services.sector_correlation import formatter, repo, service

logger = logging.getLogger(__name__)

DEFAULT_INDICES = "000001.SH,399006.SZ,000300.SH,000688.SH"


def register_subparser(subparsers: argparse._SubParsersAction) -> None:
    sc = subparsers.add_parser("sector-correlation", help="板块相关性(同向/逆向/跷跷板)")
    sub = sc.add_subparsers(dest="sector_correlation_command")

    def _common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--date", default=None, help="交易日 YYYY-MM-DD(默认今天)")
        p.add_argument("--windows", default=None, help="相关窗口,逗号分隔(默认 5,20,60:近5日共振/中期/结构)")
        p.add_argument("--top-industries", type=int, default=service.TOP_INDUSTRIES,
                       help=f"申万二级行业数(按多日均成交额,默认 {service.TOP_INDUSTRIES})")
        p.add_argument("--top-concepts", type=int, default=service.TOP_CONCEPTS,
                       help=f"同花顺概念数(按多日均换手率,默认 {service.TOP_CONCEPTS})")
        p.add_argument("--activity-days", type=int, default=service.ACTIVITY_DAYS,
                       help=f"多日活跃度窗口(默认 {service.ACTIVITY_DAYS})")
        p.add_argument("--indices", default=None, help=f"对标指数逗号分隔(默认 {DEFAULT_INDICES})")
        p.add_argument("--no-concept", action="store_true", help="只看申万行业,不纳概念")

    daily = sub.add_parser("daily", help="采集 + 落库 + 渲染 + 推钉钉")
    _common(daily)
    daily.add_argument("--dry-run", action="store_true", help="仅打印 markdown,不落库不推送")

    matrix = sub.add_parser("matrix", help="打印完整相关矩阵(不推送)")
    _common(matrix)
    matrix.add_argument("--refetch", action="store_true", help="忽略库内缓存强制现算(不落库)")

    trend = sub.add_parser("trend", help="只读最近 N 日漂移(不采集不推送)")
    trend.add_argument("--date", default=None, help="截止交易日 YYYY-MM-DD(默认今天)")
    trend.add_argument("--days", type=int, default=30, help="趋势窗口天数(默认 30)")


def handle_command(config: dict, args: argparse.Namespace) -> None:
    sub = getattr(args, "sector_correlation_command", None)
    if sub == "daily":
        _run_daily(config, args)
    elif sub == "matrix":
        _run_matrix(config, args)
    elif sub == "trend":
        _run_trend(args)
    else:
        print("用法:python main.py sector-correlation daily|matrix|trend [...]", file=sys.stderr)
        sys.exit(2)


def _today() -> str:
    return datetime.date.today().isoformat()


def _windows(args) -> list | None:
    if not args.windows:
        return None
    try:
        return [int(x.strip()) for x in args.windows.split(",")]
    except ValueError:
        logger.error("[sector-correlation] --windows 非法（应为逗号分隔整数，如 20,60）：%s", args.windows)
        sys.exit(2)


def _indices(args) -> list | None:
    src = args.indices if args.indices else None
    return [x.strip() for x in src.split(",")] if src else None


def _setup_tushare(config):
    """复刻生产 init：setup_providers + initialize_all，取 tushare 实例。调用方须在
    without_standard_http_proxy() 上下文内调用（provider HTTP 调用时读 os.environ）。

    返回 (registry, provider)：registry 供非交易日守卫复用（is_non_trading_day 走 registry.call），
    provider 供 service 调用。失败返回 (None, None)。"""
    from main import setup_providers

    registry = setup_providers(config)
    registry.initialize_all()
    provider = registry.get_provider("tushare")
    if provider is None or not getattr(provider, "_initialized", False):
        logger.error("[sector-correlation] Tushare provider 未初始化(检查 TUSHARE_TOKEN / tushare.xyz 可达)")
        return None, None
    return registry, provider


def _run_daily(config: dict, args: argparse.Namespace) -> None:
    from utils.network_env import without_standard_http_proxy

    date = args.date or _today()
    conn = get_connection()
    try:
        with without_standard_http_proxy():
            registry, provider = _setup_tushare(config)
            if provider is None:
                return
            # 非交易日守卫(--dry-run persist=False 不落库,豁免以免守卫的日历预取写真实库)：
            # 节假日 tushare 镜像常返上一交易日陈旧数据按当日落库+误推送。守卫天然在 no-proxy 内。
            from utils.trade_date import is_non_trading_day
            if not args.dry_run and is_non_trading_day(conn, registry, date):
                logger.warning("⚠️ %s 为非交易日(周末/法定假日),跳过板块相关性采集(不落库、不推送)", date)
                return
            md = service.run_daily(
                conn, provider, date,
                windows=_windows(args),
                top_industries=args.top_industries, top_concepts=args.top_concepts,
                indices=_indices(args), activity_days=args.activity_days,
                include_concept=not args.no_concept, persist=not args.dry_run,
            )
    finally:
        conn.close()

    if md is None:
        logger.info("[sector-correlation daily] %s 无足够数据,跳过(不落库不推送)", date)
        print(f"{date} 无足够板块相关性数据,跳过。")
        return
    if args.dry_run:
        print(md)
        logger.info("[sector-correlation daily] dry-run 完成,未推送")
        return
    _push_to_dingtalk(f"板块相关性 · {date}", md)


def _run_matrix(config: dict, args: argparse.Namespace) -> None:
    from utils.network_env import without_standard_http_proxy

    date = args.date or _today()
    conn = get_connection()
    try:
        # 缓存命中且非 refetch → 纯只读渲染，免初始化 Tushare：matrix 是只读巡检入口，
        # 不该被 token/网络/provider 初始化失败拦住（review L2）。
        cached = None if args.refetch else repo.get_correlation(conn, date)
        if cached is not None:
            print(formatter.format_matrix(cached))
            return
        with without_standard_http_proxy():
            _registry, provider = _setup_tushare(config)  # matrix 只读不需守卫，registry 忽略
            if provider is None:
                return
            md = service.run_matrix(
                conn, provider, date,
                windows=_windows(args),
                top_industries=args.top_industries, top_concepts=args.top_concepts,
                indices=_indices(args), activity_days=args.activity_days,
                include_concept=not args.no_concept, refetch=True,  # 已知缓存未命中
            )
    finally:
        conn.close()
    print(md)  # 只读,不推送


def _run_trend(args: argparse.Namespace) -> None:
    date = args.date or _today()
    conn = get_connection()
    try:
        md = service.run_trend(conn, date, days=args.days)
    finally:
        conn.close()
    print(md)


def _push_to_dingtalk(title: str, markdown: str) -> None:
    """走 DingTalkPusher 推送；token/secret 由 pusher.initialize() 从 env 读。

    钉钉是国内端点(oapi.dingtalk.com)，直连即可，pusher 默认 trust_env=False 正确；
    "推送走代理"原则只针对 Discord 等海外渠道（review M2）。
    """
    from pushers.dingtalk_pusher import DingTalkPusher

    pusher = DingTalkPusher(config={})
    if not pusher.initialize():
        logger.error("[sector-correlation] DingTalk pusher 未启用(缺 env DINGTALK_WEBHOOK_TOKEN/SECRET),跳过推送")
        return
    ok = pusher.send_markdown(title=title, content=markdown)
    logger.info("[sector-correlation] 推送 %s", "成功" if ok else "失败")
