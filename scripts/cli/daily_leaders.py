from __future__ import annotations

import argparse
import json
import logging
import sys

from db.connection import get_connection
from services.daily_leaders import renderer, service
from services.daily_leaders.models import MAX_CONFIRMATION_CANDIDATES
from services.daily_leaders.readiness import DataNotReady, is_open_day, now_shanghai
from services.daily_leaders.retry import check_window, run_when_ready


def _candidate_limit(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--max-candidates 必须是整数") from exc
    if not 1 <= parsed <= MAX_CONFIRMATION_CANDIDATES:
        raise argparse.ArgumentTypeError(
            f"--max-candidates 必须在 1 到 {MAX_CONFIRMATION_CANDIDATES} 之间"
        )
    return parsed


def register_subparser(subparsers: argparse._SubParsersAction) -> None:
    dl = subparsers.add_parser("daily-leaders", help="每日最票候选确认稿")
    sub = dl.add_subparsers(dest="daily_leaders_command")

    propose = sub.add_parser("propose", help="生成每日最票候选确认稿")
    propose.add_argument("--date", default=None, help="交易日 YYYY-MM-DD（默认今天）")
    propose.add_argument("--push", action="store_true", help="生成后推送钉钉")
    propose.add_argument("--wait-ready", action="store_true", help="当日21:00至23:00每5分钟重试数据就绪检查，须配合--push")
    propose.add_argument("--input-by", default="USER", help="生成请求者（定时任务为launchd）")
    propose.add_argument("--no-llm", action="store_true", help="跳过 LLM 辅助理由")
    propose.add_argument(
        "--max-candidates",
        type=_candidate_limit,
        default=MAX_CONFIRMATION_CANDIDATES,
        help=f"确认稿最多保留候选数（1 到 {MAX_CONFIRMATION_CANDIDATES}，默认 {MAX_CONFIRMATION_CANDIDATES}）",
    )

    confirm = sub.add_parser("confirm", help="确认写入复盘第5步并同步最票跟踪")
    confirm.add_argument("--date", required=True, help="交易日 YYYY-MM-DD")
    confirm.add_argument("--input-by", required=True, help="确认操作者")
    confirm.add_argument("--leaders-file", default=None, help="候选 JSON 文件路径（默认读取已落盘 proposal）")

    show = sub.add_parser("show", help="查看已生成的每日最票候选稿")
    show.add_argument("--date", required=True, help="交易日 YYYY-MM-DD")
    show.add_argument("--json", action="store_true", help="输出 JSON")


def handle_command(config: dict, args: argparse.Namespace) -> None:
    sub = getattr(args, "daily_leaders_command", None)
    if sub == "propose":
        _handle_propose(args)
    elif sub == "confirm":
        _handle_confirm(args)
    elif sub == "show":
        _handle_show(args)
    else:
        print("用法：python main.py daily-leaders propose|confirm|show [...]", file=sys.stderr)
        sys.exit(2)


def _handle_propose(args: argparse.Namespace) -> None:
    from api.routes.review import get_prefill
    from api.deps import get_provider_registry

    date = args.date or now_shanghai().date().isoformat()
    logging.getLogger(__name__).info(
        "[daily-leaders propose] date=%s input_by=%s", date, getattr(args, "input_by", "USER")
    )
    wait = getattr(args, "wait_ready", False)
    if wait and not args.push:
        raise SystemExit("--wait-ready 必须与 --push 一起使用")

    def attempt():
        # A fresh connection/prefill/registry per attempt observes late arrivals;
        # do not hold a transaction or reuse failed provider caches while waiting.
        conn = get_connection()
        try:
            if args.push and not is_open_day(conn, date):
                print(f"[daily-leaders] {date}非开放日，跳过生成与推送")
                return None
            prefill = get_prefill(date, conn=conn)
            return service.propose(
                conn, date, prefill, no_llm=args.no_llm,
                registry=get_provider_registry(), max_candidates=args.max_candidates,
                require_ready=args.push, input_by=getattr(args, "input_by", "USER"),
            )
        finally:
            conn.close()

    try:
        proposal = run_when_ready(attempt, date, wait=wait)
        if proposal is None:
            return
        # Generation itself may take us beyond the same-day send window.
        if wait:
            check_window(date)
    except (DataNotReady, TimeoutError) as exc:
        print(f"[daily-leaders] 停止：{exc}；未推送", file=sys.stderr)
        raise SystemExit(75) from exc
    markdown = renderer.render_markdown(proposal, compact=True)
    print(markdown)
    if args.push and not _push_to_dingtalk(f"每日最票候选确认稿 · {date}", markdown):
        # Delivery is not a readiness failure; avoid retrying an ambiguous send.
        raise SystemExit(1)


def _handle_confirm(args: argparse.Namespace) -> None:
    conn = get_connection()
    try:
        result = service.confirm(
            conn,
            args.date,
            args.input_by,
            leaders_file=args.leaders_file,
        )
    finally:
        conn.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _handle_show(args: argparse.Namespace) -> None:
    proposal = service.show(args.date)
    if args.json:
        print(json.dumps(proposal, ensure_ascii=False, indent=2))
    else:
        print(renderer.render_markdown(proposal, compact=True))


def _push_to_dingtalk(title: str, markdown: str) -> bool:
    from pushers.dingtalk_pusher import DingTalkPusher

    pusher = DingTalkPusher(config={})
    if not pusher.initialize():
        print("[daily-leaders] DingTalk pusher 未启用，跳过推送", file=sys.stderr)
        return False
    ok = pusher.send_markdown(title=title, content=markdown)
    print(
        "[daily-leaders] DingTalk 推送成功" if ok else "[daily-leaders] DingTalk 推送失败",
        file=sys.stderr,
    )
    return bool(ok)
