"""CLI: 大盘择时观察（斐波那契变盘点 + 底分型，盘后 EOD 只读派生信号）。

  market-timing daily    [--date] [--pivot-index CODE --pivot-date DATE] [--no-push] [--dry-run]
  market-timing signals  [--date] [--index CODE] [--limit N] [--json]

daily 三档：裸[落库+渲染+推钉钉] / --no-push[落库+仅打印] / --dry-run[内存副本不落库不推,历史校准]。
signals 只读看池：--date 看当日全部指数 / 无 date 看最近 N 行。

守红线：全标 [判断]，不预判方向、不出价位、不给买卖建议。
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import re

from db.connection import get_connection
from services.market_timing import constants as C
from services.market_timing import formatter, repo, scanner

logger = logging.getLogger(__name__)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def register_subparser(subparsers: argparse._SubParsersAction) -> None:
    mt = subparsers.add_parser("market-timing", help="大盘择时观察(变盘点+底分型)")
    sub = mt.add_subparsers(dest="market_timing_command")

    daily = sub.add_parser("daily", help="扫描+落库+渲染+推钉钉")
    daily.add_argument("--date", default=None, help="交易日 YYYY-MM-DD(默认今天)")
    daily.add_argument("--pivot-index", default=None, help="手工指定某指数的 swing 起算 code(配合 --pivot-date)")
    daily.add_argument("--pivot-date", default=None, help="手工 swing 起算日 YYYY-MM-DD(配合 --pivot-index)")
    daily.add_argument("--no-push", action="store_true", help="落库+仅打印,不推送")
    daily.add_argument("--dry-run", action="store_true", help="内存副本,不落库不推送(历史校准)")

    sig = sub.add_parser("signals", help="只读看最近信号")
    sig.add_argument("--date", default=None, help="只看某交易日全部指数")
    sig.add_argument("--index", default=None, help="只看某指数 code")
    sig.add_argument("--limit", type=int, default=30, help="无 --date 时返回最近 N 行(默认 30)")
    sig.add_argument("--json", action="store_true", help="输出 JSON 而非表格")


def handle_command(config: dict, args: argparse.Namespace) -> None:
    sub = getattr(args, "market_timing_command", None)
    if sub == "daily":
        _run_daily(config, args)
    elif sub == "signals":
        _run_signals(args)
    else:
        print("用法: market-timing {daily|signals} ...")


def _today() -> str:
    return datetime.date.today().strftime("%Y-%m-%d")


def _validate_pivot_args(args: argparse.Namespace) -> None:
    """pivot 参数 fail-fast：成对缺失 / 未知指数 / 日期格式非法都在采集落库前报错，
    避免用户以为已手工校准、实际跑了自动口径还落库/推送（date 是否在 K 线窗口由 scanner 兜底）。"""
    pi, pd = getattr(args, "pivot_index", None), getattr(args, "pivot_date", None)
    if bool(pi) != bool(pd):
        logger.error("[market-timing] --pivot-index 与 --pivot-date 必须成对提供")
        raise SystemExit(2)
    if not (pi and pd):
        return
    valid_codes = {idx["code"] for idx in C.INDEX_LIST}
    if pi not in valid_codes:
        logger.error("[market-timing] --pivot-index %s 不在扫描指数清单 %s", pi, sorted(valid_codes))
        raise SystemExit(2)
    if not _DATE_RE.match(pd):
        logger.error("[market-timing] --pivot-date 须为 YYYY-MM-DD: %s", pd)
        raise SystemExit(2)


def _pivot_overrides(args: argparse.Namespace) -> dict | None:
    pi, pd = getattr(args, "pivot_index", None), getattr(args, "pivot_date", None)
    return {pi: pd} if pi and pd else None


def _run_daily(config: dict, args: argparse.Namespace) -> None:
    from main import setup_providers
    from utils.network_env import without_standard_http_proxy

    _validate_pivot_args(args)  # 在采集/落库/推送之前 fail-fast
    date = args.date or _today()
    conn = get_connection()
    try:
        with without_standard_http_proxy():
            registry = setup_providers(config)
            registry.initialize_all()
            result = scanner.run_daily(conn, registry, date,
                                       dry_run=args.dry_run, pivot_overrides=_pivot_overrides(args))
    except ValueError as e:
        # 手工 pivot 覆盖未命中（日期不在窗口/未知指数）→ 提交/推送前硬失败
        logger.error("[market-timing] pivot 覆盖无效: %s", e)
        raise SystemExit(2)
    finally:
        conn.close()

    md = formatter.render_daily(result)
    if not result.get("signals"):
        logger.info("[market-timing daily] %s 无可用指数数据,跳过(不推送)", date)
        print(md)
        return
    if args.dry_run or args.no_push:
        print(md)
        logger.info("[market-timing daily] %s 完成(%s,未推送)", date, "dry-run" if args.dry_run else "no-push")
        return
    ok = _push_to_dingtalk(f"大盘择时观察 · {date}", md)
    print(md)
    if not ok:
        logger.error("[market-timing daily] %s 推送失败,以非零退出供定时任务/监控发现", date)
        raise SystemExit(1)


def _run_signals(args: argparse.Namespace) -> None:
    conn = get_connection()
    try:
        rows = repo.list_signals(conn, date=args.date, index_code=args.index, limit=args.limit)
    finally:
        conn.close()
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    print(formatter.render_signals(rows))


def _push_to_dingtalk(title: str, markdown: str) -> bool:
    """推送钉钉。返回是否成功（未初始化/发送失败均 False，供裸跑分支非零退出）。"""
    from pushers.dingtalk_pusher import DingTalkPusher

    pusher = DingTalkPusher(config={})
    if not pusher.initialize():
        logger.error("[market-timing] DingTalk pusher 未启用(缺 env DINGTALK_WEBHOOK_TOKEN/SECRET),跳过推送")
        return False
    ok = pusher.send_markdown(title=title, content=markdown)
    logger.info("[market-timing] 推送 %s", "成功" if ok else "失败")
    return bool(ok)
