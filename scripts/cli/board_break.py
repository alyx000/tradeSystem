"""CLI: 断板反包盘后扫描（无状态观察清单 + 双打分）。

  board-break daily [--date YYYY-MM-DD] [--dry-run] [--no-push] [--no-llm]

  · 裸 daily：MD 落盘 data/reports/board-break/ + 推钉钉。
  · --no-push：落盘 + 打印，不推。
  · --dry-run：只打印，不落盘不推（历史校准）。
  · --no-llm：跳过两两 PK，只出加权分排序。

红线：筛选 [事实] / 打分 [判断]，非买卖建议；6% 参考位为用户既定规则机械换算。
"""
from __future__ import annotations

import argparse
import datetime
import logging
import sys

from db.connection import get_connection
from services.board_break import pk, renderer, scanner, scorer

logger = logging.getLogger(__name__)


def register_subparser(subparsers: argparse._SubParsersAction) -> None:
    bb = subparsers.add_parser("board-break", help="断板反包观察清单（断板筛选 + 双打分）")
    sub = bb.add_subparsers(dest="board_break_command")
    daily = sub.add_parser("daily", help="断板筛选→双打分→渲染→落盘→推钉钉")
    daily.add_argument("--date", default=None, help="交易日 YYYY-MM-DD（默认今天）")
    daily.add_argument("--dry-run", action="store_true", help="只打印，不落盘不推送")
    daily.add_argument("--no-push", action="store_true", help="落盘但不推送")
    daily.add_argument("--no-llm", action="store_true", help="跳过 LLM 两两 PK")


def handle_command(config: dict, args: argparse.Namespace) -> None:
    if getattr(args, "board_break_command", None) == "daily":
        _run_daily(config, args)
    else:
        print("用法：python main.py board-break daily [...]", file=sys.stderr)
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
            logger.warning("⚠️ %s 为非交易日，跳过断板反包扫描（不落盘、不推送）", date)
            return
        result = scanner.run_daily(conn, registry, date)
        if result["status"] == "source_failed":
            md = renderer.render_source_failed(result)
            print(md)
            logger.error("[board-break] 核心源失败，不产出候选清单：%s", result.get("failed_sources"))
            return  # source_failed 不落盘不推正常清单
        fact_cards = scorer.build_fact_cards(conn, registry, result)
        scored = scorer.score_all(fact_cards)
        # 空候选也交给 pk.run_pk（内部 len(pool)<2 时自走 skipped 状态机），
        # 不再用 `and scored` 短路——短路会让 pk_result 恒为 None，与 --no-llm
        # 走的 None 分支撞车，导致 0/1 候选文案（"PK 未运行" vs "候选不足 2 只"）不一致。
        # 门1 效率复查：候选 <2 只时 run_pk 必短路 skipped、根本不碰 llm_runner，
        # 此时不再构造 runner（build_llm_runner 内部 resolve_config 会做一次
        # shutil.which("agy") 的 PATH 扫描，空候选日没必要白扫）；只按 len(scored)
        # 门槛跳过构造，不改 run_pk 签名/契约，run_pk 自身的池截断逻辑不变。
        pk_result = None
        if not args.no_llm:
            llm_runner = pk.build_llm_runner() if len(scored) >= 2 else None
            pk_result = pk.run_pk(fact_cards, scored, llm_runner)
        md = renderer.render_daily(result, scored, pk_result)
    finally:
        conn.close()

    print(md)
    if args.dry_run:
        logger.info("[board-break daily] dry-run 完成（未落盘/未推送）")
        return
    path = renderer.save_report(md, date)
    logger.info("[board-break daily] 报告已落盘 %s", path)
    if args.no_push:
        return
    _push_to_dingtalk(f"断板反包观察清单 · {date}", md)


def _push_to_dingtalk(title: str, markdown: str) -> None:
    from pushers.dingtalk_pusher import DingTalkPusher

    pusher = DingTalkPusher(config={})
    if not pusher.initialize():
        logger.error("[board-break] DingTalk pusher 未启用（缺 env），跳过推送")
        return
    ok = pusher.send_markdown(title=title, content=markdown)
    logger.info("[board-break] 推送 %s", "成功" if ok else "失败")
