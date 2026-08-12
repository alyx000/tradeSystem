"""CLI：盘后情绪核心生命周期监控。"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import sys

from db.connection import get_readonly_connection
from services.emotion_leader import constants as C
from services.emotion_leader import renderer, service
from services.emotion_leader.state import load_previous_report

logger = logging.getLogger(__name__)


def _positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("必须为正整数") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("必须为正整数")
    return value


def register_subparser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("emotion-leader", help="盘后情绪核心生命周期监控")
    modes = parser.add_subparsers(dest="emotion_leader_command")
    daily = modes.add_parser("daily", help="连板启动识别→生命周期统计→报告/推送")
    daily.add_argument("--date", default=None, help="目标交易日 YYYY-MM-DD（默认今天）")
    daily.add_argument("--lookback-days", type=_positive_int, default=C.DEFAULT_LOOKBACK_DAYS,
                       help=f"连板事实回看自然日（默认 {C.DEFAULT_LOOKBACK_DAYS}）")
    daily.add_argument("--max-rows", type=_positive_int, default=C.DEFAULT_MAX_ROWS,
                       help=f"Markdown 最多展示活跃核心数（默认 {C.DEFAULT_MAX_ROWS}，JSON 保留全量）")
    daily.add_argument("--dry-run", action="store_true", help="仅打印，不落报告、不推送")
    daily.add_argument("--no-push", action="store_true", help="落 Markdown/JSON，但不推送")
    daily.add_argument("--json", action="store_true", help="输出 JSON；不落报告、不推送")
    daily.add_argument("--full-refresh", action="store_true",
                       help="忽略上一份日报缓存，全量刷新历史晋级核心（首次校准/口径变更使用）")


def handle_command(config: dict, args: argparse.Namespace) -> int:
    if getattr(args, "emotion_leader_command", None) != "daily":
        print("用法：python main.py emotion-leader daily [...]", file=sys.stderr)
        return 2
    return _run_daily(config, args)


def _run_daily(config: dict, args: argparse.Namespace) -> int:
    from main import setup_providers
    from utils.network_env import without_standard_http_proxy

    target_date = args.date or datetime.date.today().isoformat()
    try:
        datetime.date.fromisoformat(target_date)
    except ValueError:
        print("--date 必须为 YYYY-MM-DD", file=sys.stderr)
        return 2

    conn = get_readonly_connection()
    try:
        previous_report = None if args.full_refresh else load_previous_report(
            target_date,
            lookback_days=args.lookback_days,
        )
        with without_standard_http_proxy():
            registry = setup_providers(config)
            registry.initialize_all()
            result = service.run_daily(
                conn,
                registry,
                target_date,
                lookback_days=args.lookback_days,
                previous_report=previous_report,
                full_refresh=args.full_refresh,
            )
    finally:
        conn.close()

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 1 if result.get("status") == "source_failed" else 0

    markdown = renderer.render_daily(result, max_rows=args.max_rows)
    print(markdown)
    source_failed = result.get("status") == "source_failed"
    if args.dry_run:
        logger.info("[emotion-leader] dry-run 完成，未落报告/未推送")
        return 1 if source_failed else 0

    md_path, json_path = renderer.write_reports(result, markdown)
    logger.info("[emotion-leader] 报告已落盘 %s / %s", md_path, json_path)
    if args.no_push:
        return 1 if source_failed else 0
    title = f"{'⚠️ ' if source_failed else ''}情绪核心生命周期 · {target_date}"
    if not _push(title, markdown):
        return 1
    return 1 if source_failed else 0


def _push(title: str, markdown: str) -> bool:
    from pushers.dingtalk_pusher import DingTalkPusher

    pusher = DingTalkPusher(config={})
    if not pusher.initialize():
        logger.error("[emotion-leader] DingTalk pusher 未启用，跳过推送")
        return False
    ok = bool(pusher.send_markdown(title=title, content=markdown))
    logger.info("[emotion-leader] 推送%s", "成功" if ok else "失败")
    return ok
