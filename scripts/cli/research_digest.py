"""CLI: 每日研报速读（A股研报评级 + 美股 yfinance 评级 → Top3 → MD + 钉钉）。

  research-digest daily [--date YYYY-MM-DD] [--dry-run] [--no-llm]

- 默认推送钉钉（对齐 volume-watch / recommend 语义，无 --push）。
- --dry-run：仅打印 markdown 到 stdout，不调 gemini、不落盘、不推送。
- --no-llm：关闭 LLM 叙事，纯结构化（A股本就默认关，此项额外关美股叙事）。
"""
from __future__ import annotations

import argparse
import datetime
import logging
import sys

logger = logging.getLogger(__name__)

US_LOOKBACK_DAYS = 5


def register_subparser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("research-digest", help="每日研报速读（A股+美股机构评级 Top3）")
    sub = p.add_subparsers(dest="research_digest_command")
    daily = sub.add_parser("daily", help="采集 + 渲染 + 落盘 + 推钉钉")
    daily.add_argument("--date", default=None, help="A股交易日 YYYY-MM-DD（默认最近交易日，跨周末回溯）")
    daily.add_argument("--dry-run", action="store_true", help="仅打印 markdown，不调 gemini、不落盘、不推送")
    daily.add_argument("--no-llm", action="store_true", help="关闭 LLM 叙事，纯结构化")


def handle_command(config: dict, args: argparse.Namespace) -> None:
    sub = getattr(args, "research_digest_command", None)
    if sub == "daily":
        _run_daily(config, args)
    else:
        print("用法：python main.py research-digest daily [--date YYYY-MM-DD] [--dry-run] [--no-llm]",
              file=sys.stderr)
        sys.exit(2)


def _run_daily(config: dict, args: argparse.Namespace) -> None:
    from main import setup_providers
    from services.research_digest import narrator, run_daily_digest
    from utils.trade_date import get_prev_trade_date

    registry = setup_providers(config)
    registry.initialize_all()  # 必须：register 不自动初始化，漏了 provider 调用全返 not_initialized

    # A股交易日：显式 --date 优先；否则取最近交易日（周一/节后自动回溯）
    a_date = args.date or get_prev_trade_date(registry, datetime.date.today().isoformat())

    # dry-run 也不调 gemini（对齐 recommend/volume-watch）
    no_llm = bool(args.no_llm or args.dry_run)
    llm_runner = None if no_llm else narrator.build_gemini_runner()

    digest = run_daily_digest(
        registry, a_date,
        no_llm=no_llm,
        llm_runner=llm_runner,
        us_lookback_days=US_LOOKBACK_DAYS,
    )

    if args.dry_run:
        print(digest.markdown)
        logger.info("[research-digest] dry-run 完成（未落盘 / 未推送）")
        return

    from services.research_digest.renderer import write_md
    path = write_md(digest.markdown, a_date)
    logger.info("[research-digest] MD 落盘: %s", path)
    _push_to_dingtalk(digest.title, digest.markdown)


def _push_to_dingtalk(title: str, markdown: str) -> None:
    """走 DingTalkPusher 推送；token/secret 由 pusher.initialize() 从 env 读（CLI 直接实例化，不碰 multi.py）。"""
    from pushers.dingtalk_pusher import DingTalkPusher

    pusher = DingTalkPusher(config={})
    if not pusher.initialize():
        logger.error("[research-digest] DingTalk pusher 未启用（缺 env DINGTALK_WEBHOOK_TOKEN/SECRET），跳过推送")
        return
    ok = pusher.send_markdown(title=title, content=markdown)
    logger.info("[research-digest] 推送 %s", "成功" if ok else "失败")
