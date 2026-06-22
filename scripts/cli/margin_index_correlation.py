"""CLI: 两融余额与指数联动性监控（盘后只读观察，守红线）。

  margin-index-correlation daily   [--date] [--windows 5,20,60] [--divergence-windows 5,20]
                                   [--divergence-gap 0.5] [--max-lag 3] [--no-push] [--dry-run]
  margin-index-correlation signals [--date] [--days 30] [--json]

daily 三档：裸[采集+落库+推钉钉] / --no-push[落库+仅打印] / --dry-run[内存跑不落不推]。
signals：只读最近 N 日联动快照（不采集不推送），供周日复盘。
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import sys

from db.connection import get_connection
from services.margin_index_correlation import repo, service

logger = logging.getLogger(__name__)


def register_subparser(subparsers: argparse._SubParsersAction) -> None:
    mc = subparsers.add_parser("margin-index-correlation", help="两融余额与指数联动性(背离/领先滞后/相关)")
    sub = mc.add_subparsers(dest="margin_index_correlation_command")

    daily = sub.add_parser("daily", help="采集 + 落库 + 渲染 + 推钉钉")
    daily.add_argument("--date", default=None, help="交易日 YYYY-MM-DD(默认今天)")
    daily.add_argument("--windows", default=None, help="相关窗口,逗号分隔(默认 5,20,60)")
    daily.add_argument("--divergence-windows", default=None, help="背离累计窗口,逗号分隔(默认 5,20)")
    daily.add_argument("--divergence-gap", type=float, default=None,
                       help=f"背离噪声阈值%%,累计差≥此值才告警(默认 {service.MIN_GAP})")
    daily.add_argument("--max-lag", type=int, default=None,
                       help=f"领先/滞后最大 lag 天数(默认 {service.MAX_LAG})")
    daily.add_argument("--no-push", action="store_true", help="落库但仅打印,不推送")
    daily.add_argument("--dry-run", action="store_true", help="内存跑,不落库不推送")

    signals = sub.add_parser("signals", help="只读最近 N 日联动快照(不采集不推送)")
    signals.add_argument("--date", default=None, help="截止交易日 YYYY-MM-DD(默认今天)")
    signals.add_argument("--days", type=int, default=30, help="回看天数(默认 30)")
    signals.add_argument("--json", action="store_true", help="输出原始 JSON(供脚本消费)")


def handle_command(config: dict, args: argparse.Namespace) -> None:
    sub = getattr(args, "margin_index_correlation_command", None)
    if sub == "daily":
        _run_daily(config, args)
    elif sub == "signals":
        _run_signals(args)
    else:
        print("用法:python main.py margin-index-correlation daily|signals [...]", file=sys.stderr)
        sys.exit(2)


def _today() -> str:
    return datetime.date.today().isoformat()


def _int_list(raw: str | None, label: str) -> list | None:
    if not raw:
        return None
    try:
        return [int(x.strip()) for x in raw.split(",")]
    except ValueError:
        logger.error("[margin-index-correlation] %s 非法(应为逗号分隔整数):%s", label, raw)
        sys.exit(2)


def _setup_tushare(config):
    """setup_providers + initialize_all。返回 (registry, provider)：
    registry 供 get_margin_series 降级链与非交易日守卫；provider 供指数序列。失败返 (None, None)。"""
    from main import setup_providers

    registry = setup_providers(config)
    registry.initialize_all()
    provider = registry.get_provider("tushare")
    if provider is None or not getattr(provider, "_initialized", False):
        logger.error("[margin-index-correlation] Tushare provider 未初始化(检查 TUSHARE_TOKEN / tushare.xyz 可达)")
        return None, None
    return registry, provider


def _execute(
    config: dict,
    date: str,
    *,
    persist: bool,
    push: bool,
    windows: list | None = None,
    divergence_windows: list | None = None,
    min_gap: float | None = None,
    max_lag: int | None = None,
) -> str | None:
    """采集→（落库）→（推送）的核心流程，CLI daily 与盘后折入 (run_for_post) 共用。

    返回渲染好的 markdown（无足够数据返 None）。`persist` 时才挂非交易日守卫（dry-run 豁免，
    免守卫日历预取写真实库）；`push` 控制是否推钉钉。
    """
    from utils.network_env import without_standard_http_proxy

    conn = get_connection()
    md = None
    try:
        with without_standard_http_proxy():
            registry, provider = _setup_tushare(config)
            if provider is None:
                return None
            from utils.trade_date import is_non_trading_day
            if persist and is_non_trading_day(conn, registry, date):
                logger.warning("⚠️ %s 为非交易日(周末/法定假日),跳过两融联动采集(不落库、不推送)", date)
                return None
            md = service.run_daily(
                conn, registry, provider, date,
                persist=persist, windows=windows, divergence_windows=divergence_windows,
                min_gap=min_gap, max_lag=max_lag,
            )
    finally:
        conn.close()

    if md is None:
        logger.info("[margin-index-correlation] %s 无足够数据,跳过", date)
        return None
    if push:
        _push_to_dingtalk(f"两融×指数联动 · {date}", md)
    return md


def run_for_post(config: dict, target_date: str) -> str | None:
    """供 main.py cmd_post 折进盘后采集调用（落库 + 推送）。

    交易日守卫由 cmd_post 上游保证（已过周末/法定假日检查）；此处 persist 守卫为冗余兜底。
    注：cmd_post 工作日 20:00 触发，两融多为 T-1（交易所盘后发布滞后），报告会标注 stale。
    """
    return _execute(config, target_date, persist=True, push=True)


def _run_daily(config: dict, args: argparse.Namespace) -> None:
    date = args.date or _today()
    dry_or_nopush = args.dry_run or args.no_push
    md = _execute(
        config, date,
        persist=not args.dry_run,
        push=not dry_or_nopush,
        windows=_int_list(args.windows, "--windows"),
        divergence_windows=_int_list(args.divergence_windows, "--divergence-windows"),
        min_gap=args.divergence_gap,
        max_lag=args.max_lag,
    )
    if md is None:
        print(f"{date} 无足够两融联动数据,跳过。")
    elif dry_or_nopush:
        print(md)
        logger.info("[margin-index-correlation daily] %s 完成(%s)", date,
                    "dry-run 未落库未推送" if args.dry_run else "已落库未推送")


def _run_signals(args: argparse.Namespace) -> None:
    date = args.date or _today()
    conn = get_connection()
    try:
        if args.json:
            print(json.dumps(repo.get_recent(conn, date, args.days), ensure_ascii=False, indent=2))
        else:
            print(service.run_signals(conn, end_date=date, days=args.days))
    finally:
        conn.close()


def _push_to_dingtalk(title: str, markdown: str) -> None:
    """走 DingTalkPusher 推送；token/secret 由 pusher.initialize() 从 env 读。"""
    from pushers.dingtalk_pusher import DingTalkPusher

    pusher = DingTalkPusher(config={})
    if not pusher.initialize():
        logger.error("[margin-index-correlation] DingTalk pusher 未启用(缺 env DINGTALK_WEBHOOK_TOKEN/SECRET),跳过推送")
        return
    ok = pusher.send_markdown(title=title, content=markdown)
    logger.info("[margin-index-correlation] 推送 %s", "成功" if ok else "失败")
