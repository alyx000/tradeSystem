"""CLI: 盘中尾盘强势股扫描（实时筛选→四维事实卡+产业逻辑→PK→渲染）。

  tail-scan daily [--date] [--min-pct 7] [--min-amount 20] [--dry-run] [--no-push] [--no-llm]

红线：筛选依据为 [事实]，排序为 [判断]，主营/催化保留行内边界标签；非买卖建议、不出价位。
"""
from __future__ import annotations

import argparse
import datetime
import logging
import sys

from db.connection import get_connection
from services.tail_scan import constants as C
from services.tail_scan import pk, renderer, scanner, scorer

logger = logging.getLogger(__name__)


def register_subparser(subparsers: argparse._SubParsersAction) -> None:
    ts = subparsers.add_parser(
        "tail-scan",
        help=f"盘中尾盘强势股扫描（涨幅>{C.DEFAULT_MIN_PCT:g}%%/非ST/成交额>{C.DEFAULT_MIN_AMOUNT_YI:g}亿 + 四维事实卡+产业逻辑+PK）",
    )
    sub = ts.add_subparsers(dest="tail_scan_command")
    daily = sub.add_parser("daily", help="实时筛选→四维事实卡+产业逻辑→PK→渲染")
    daily.add_argument("--date", default=None, help="交易日 YYYY-MM-DD（默认今天）")
    daily.add_argument("--min-pct", type=float, default=C.DEFAULT_MIN_PCT,
                        help=f"涨幅下限%%（默认{C.DEFAULT_MIN_PCT:g}）")
    daily.add_argument("--min-amount", type=float, default=C.DEFAULT_MIN_AMOUNT_YI,
                        help=f"成交额下限（亿，默认{C.DEFAULT_MIN_AMOUNT_YI:g}）")
    daily.add_argument("--dry-run", action="store_true", help="只打印，不落盘不推送")
    daily.add_argument("--no-push", action="store_true", help="落盘但不推送")
    daily.add_argument("--no-llm", action="store_true", help="跳过 LLM 两两 PK")


def handle_command(config: dict, args: argparse.Namespace) -> None:
    if getattr(args, "tail_scan_command", None) == "daily":
        _run_daily(config, args)
    else:
        print("用法：python main.py tail-scan daily [...]", file=sys.stderr)
        sys.exit(2)


def _run_daily(config: dict, args: argparse.Namespace) -> None:
    from main import setup_providers
    from utils.trade_date import is_non_trading_day

    date = args.date or datetime.date.today().isoformat()
    registry = setup_providers(config)
    registry.initialize_all()
    conn = get_connection()
    try:
        if not args.dry_run and is_non_trading_day(conn, registry, date):
            logger.warning("⚠️ %s 为非交易日，跳过尾盘扫描（不落盘、不推送）", date)
            return
        scan_result = scanner.scan(registry, date, min_pct=args.min_pct,
                                   min_amount_yi=args.min_amount)
        if scan_result["status"] == "source_failed":
            md = renderer.render_source_failed(scan_result)
            print(md)
            logger.error("[tail-scan] 数据源失败：%s", scan_result.get("error"))
            if not args.dry_run:
                # 失败报告必须落盘可观测（镜像 board_break 门2 S4 R1/R2）：
                # launchd 场景 stdout 不可见，只打印会退化成"静默失败"。
                try:
                    path = renderer.save_report(md, date)
                    logger.info("[tail-scan daily] 失败报告已落盘 %s", path)
                except OSError:
                    # 落盘失败不得挡掉告警——告警是本分支的核心可观测性
                    logger.error("[tail-scan daily] 失败报告落盘异常，继续尝试推送告警", exc_info=True)
                if not args.no_push:
                    _push(f"⚠️ 尾盘扫描·数据失败 · {date}", md)
            sys.exit(1)
        cards = scorer.build_fact_cards(conn, registry, scan_result, params={"date": date})
        scored = scorer.score_all(cards)
        # 不用 `and len(scored) >= 2` 短路——短路会让 pk_result 恒为 None，
        # 与 --no-llm 走的 None 分支撞车，导致 0/1 候选文案（"PK 未运行" vs
        # "候选不足 2 只"）不一致（镜像 board_break.py 同一处已修复的坑）。
        # run_pk 内部 len(pool)<2 时自走 status="skipped" 状态机。
        pk_result = None
        if not args.no_llm:
            llm_runner = pk.build_llm_runner() if len(scored) >= 2 else None
            pk_result = pk.run_pk(cards, scored, llm_runner)
        md = renderer.render_daily(scan_result, scored, pk_result)
    finally:
        conn.close()

    print(md)
    if args.dry_run:
        logger.info("[tail-scan daily] dry-run 完成（未落盘/未推送）")
        return
    path = renderer.save_report(md, date)
    logger.info("[tail-scan daily] 报告已落盘 %s", path)
    if not args.no_push:
        push_md = renderer.render_push_summary(
            scan_result,
            scored,
            pk_result,
            full_md=md,
            report_path=str(path),
        )
        _push(f"尾盘强势股观察清单 · {date}", push_md)


def _push(title: str, markdown: str) -> None:
    from pushers.dingtalk_pusher import DingTalkPusher

    pusher = DingTalkPusher(config={})
    if not pusher.initialize():
        logger.error("[tail-scan] DingTalk pusher 未启用（缺 env），跳过推送")
        return
    ok = pusher.send_markdown(title=title, content=markdown)
    logger.info("[tail-scan] 推送 %s", "成功" if ok else "失败")
