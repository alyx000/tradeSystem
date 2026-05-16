"""CLI: 行业推荐定时推送 (G3 入口)。

子命令：
  recommend daily   [--lookback-days N] [--dry-run]
  recommend weekly  [--lookback-days N] [--dry-run]

--dry-run：只生成 markdown 打印到 stdout，不调 gemini、不真推钉钉。
"""
from __future__ import annotations

import argparse
import logging
import sys

from db.connection import get_connection
from services.recommend.service import run_recommend

logger = logging.getLogger(__name__)

DAILY_DEFAULTS = {"lookback_days": 3, "top_k": 5}
WEEKLY_DEFAULTS = {"lookback_days": 7, "top_k": 8}


def register_subparser(subparsers: argparse._SubParsersAction) -> None:
    rec_parser = subparsers.add_parser("recommend", help="行业推荐定时推送（日报 / 周报）")
    rec_sub = rec_parser.add_subparsers(dest="recommend_command")

    daily = rec_sub.add_parser("daily", help="生成日报（近 N 日 Top K 行业 + AI 点评）")
    daily.add_argument("--lookback-days", type=int, default=DAILY_DEFAULTS["lookback_days"],
                       help=f"回溯天数（默认 {DAILY_DEFAULTS['lookback_days']}）")
    daily.add_argument("--top-k", type=int, default=DAILY_DEFAULTS["top_k"],
                       help=f"Top K 截断（默认 {DAILY_DEFAULTS['top_k']}）")
    daily.add_argument("--dry-run", action="store_true",
                       help="仅打印 markdown 到 stdout，不调 gemini、不真推送")

    weekly = rec_sub.add_parser("weekly", help="生成周报（近 N 日深度版）")
    weekly.add_argument("--lookback-days", type=int, default=WEEKLY_DEFAULTS["lookback_days"],
                        help=f"回溯天数（默认 {WEEKLY_DEFAULTS['lookback_days']}）")
    weekly.add_argument("--top-k", type=int, default=WEEKLY_DEFAULTS["top_k"],
                        help=f"Top K 截断（默认 {WEEKLY_DEFAULTS['top_k']}）")
    weekly.add_argument("--dry-run", action="store_true",
                        help="仅打印 markdown 到 stdout，不调 gemini、不真推送")


def handle_command(config: dict, args: argparse.Namespace) -> None:
    """分发 recommend daily / weekly。"""
    sub = getattr(args, "recommend_command", None)
    if sub == "daily":
        _run("daily", args)
    elif sub == "weekly":
        _run("weekly", args)
    else:
        print("用法：python main.py recommend daily|weekly [--lookback-days N] [--dry-run]",
              file=sys.stderr)
        sys.exit(2)


def _run(mode: str, args: argparse.Namespace) -> None:
    conn = get_connection()
    try:
        rec = run_recommend(
            conn,
            lookback_days=args.lookback_days,
            top_k=args.top_k,
            skip_llm=args.dry_run,
        )
    finally:
        conn.close()

    if args.dry_run:
        # 仅打印，不推送
        print(rec.markdown)
        logger.info("[%s] dry-run 完成，未推送", mode)
        return

    # 真推送
    _push_to_dingtalk(rec.title, rec.markdown, mode=mode)


def _push_to_dingtalk(title: str, markdown: str, *, mode: str) -> None:
    """走 DingTalkPusher 推送。token/secret 由 pusher.initialize() 从 env 读。"""
    from pushers.dingtalk_pusher import DingTalkPusher

    pusher = DingTalkPusher(config={})
    if not pusher.initialize():
        logger.error("[%s] DingTalk pusher 未启用（缺 env DINGTALK_WEBHOOK_TOKEN/SECRET），跳过推送", mode)
        return

    ok = pusher.send_markdown(title=title, content=markdown)
    logger.info("[%s] 推送 %s", mode, "成功" if ok else "失败")
