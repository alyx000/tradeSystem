"""CLI: 交易认知沉淀定时汇总（近3日/近1周/近1月 → 钉钉）。

  cognition-digest recent3d|weekly|monthly [--date YYYY-MM-DD] [--dry-run] [--no-llm]

- 默认推送钉钉（对齐 research-digest 语义，无 --push）。
- --dry-run：仅打印 markdown，不调 gemini、不推送。
- --no-llm：关闭 LLM 叙事，纯结构化建议。
"""
from __future__ import annotations

import argparse
import datetime
import logging
import sys

logger = logging.getLogger(__name__)

WINDOW_CHOICES = ("recent3d", "weekly", "monthly")


def _iso_date(s: str) -> str:
    """argparse type 校验：--date 必须 YYYY-MM-DD，否则给 argparse 风格报错 + exit 2（codex 中项）。"""
    try:
        datetime.date.fromisoformat(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"--date 需为 YYYY-MM-DD 格式，收到: {s!r}")
    return s


def register_subparser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("cognition-digest", help="交易认知沉淀定时汇总（近3日/周/月 → 钉钉）")
    sub = p.add_subparsers(dest="cognition_digest_window")
    for win in WINDOW_CHOICES:
        sp = sub.add_parser(win, help=f"{win} 窗口认知沉淀汇总")
        sp.add_argument("--date", default=None, type=_iso_date, help="anchor 日期 YYYY-MM-DD（默认今天）")
        sp.add_argument("--dry-run", action="store_true", help="仅打印 markdown，不推送")
        sp.add_argument("--no-llm", action="store_true", help="关闭 LLM，纯结构化建议")


def handle_command(config: dict, args: argparse.Namespace) -> None:
    win = getattr(args, "cognition_digest_window", None)
    if win not in WINDOW_CHOICES:
        print("用法：python main.py cognition-digest recent3d|weekly|monthly "
              "[--date YYYY-MM-DD] [--dry-run] [--no-llm]", file=sys.stderr)
        sys.exit(2)
    _run(config, args, win)


def _run(config: dict, args: argparse.Namespace, window: str) -> None:
    from services.cognition_digest import run_window_digest
    from services.research_digest.narrator import build_gemini_runner

    anchor = args.date or datetime.date.today().isoformat()
    no_llm = bool(args.no_llm or args.dry_run)
    llm_runner = None if no_llm else build_gemini_runner()

    digest = run_window_digest(None, window, anchor, no_llm=no_llm, llm_runner=llm_runner)

    if args.dry_run:
        print(digest.markdown)
        logger.info("[cognition-digest] dry-run 完成（未推送）")
        return
    # 空窗口不推送：避免安静日（尤其每日 recent3d）发"无新增认知"噪音通知（钉钉减负；消费 is_empty）
    if digest.is_empty:
        logger.info("[cognition-digest] %s 本窗口无新增认知沉淀，跳过推送", window)
        return
    _push_to_dingtalk(digest.title, digest.markdown)


def _push_to_dingtalk(title: str, markdown: str) -> None:
    from pushers.dingtalk_pusher import DingTalkPusher

    pusher = DingTalkPusher(config={})
    if not pusher.initialize():
        logger.error("[cognition-digest] DingTalk pusher 未启用（缺 env DINGTALK_*），跳过推送")
        return
    ok = pusher.send_markdown(title=title, content=markdown)
    logger.info("[cognition-digest] 推送 %s", "成功" if ok else "失败")
