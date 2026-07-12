"""CLI: 前复权历史新高统计。"""
from __future__ import annotations

import argparse
import datetime
import json
from pathlib import Path
import sys

from db.connection import get_connection
from db.migrate import migrate
from services.new_high import constants as C
from services.new_high import renderer, service


def _positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("必须为正整数") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("必须为正整数")
    return value


def register_subparser(subparsers: argparse._SubParsersAction) -> None:
    nh = subparsers.add_parser("new-high", help="前复权历史新高统计（日报 / 趋势 / 回填）")
    sub = nh.add_subparsers(dest="new_high_command")

    daily = sub.add_parser("daily", help="统计当日创前复权历史新高个股并落库")
    daily.add_argument("--date", default=None, help="交易日 YYYY-MM-DD（默认今天）")
    daily.add_argument("--top-n", type=_positive_int, default=C.DEFAULT_TOP_N, help="每行业报告展示数量（默认 10）")
    daily.add_argument("--dry-run", action="store_true", help="只打印，不落库、不落报告、不推送")
    daily.add_argument("--push", action="store_true", help="显式推送钉钉；默认不推送")
    daily.add_argument("--json", action="store_true", help="输出 JSON，不推送")

    trend = sub.add_parser("trend", help="只读查看最近 N 个已统计交易日趋势")
    trend.add_argument("--date", default=None, help="截止交易日 YYYY-MM-DD（默认今天）")
    trend.add_argument("--days", type=_positive_int, default=30, help="趋势窗口天数（默认 30）")
    trend.add_argument("--json", action="store_true", help="输出 JSON")

    backfill = sub.add_parser("backfill", help="按交易日正序回填近 5 年历史水位和每日统计")
    backfill.add_argument("--start-date", default=None, help="起始交易日 YYYY-MM-DD（默认近 5 年）")
    backfill.add_argument("--end-date", default=None, help="结束交易日 YYYY-MM-DD（默认今天）")
    backfill.add_argument("--top-n", type=_positive_int, default=C.DEFAULT_TOP_N, help="每行业报告展示数量（默认 10）")
    backfill.add_argument("--dry-run", action="store_true", help="只打印计划处理日期，不落库")


def handle_command(config: dict, args: argparse.Namespace) -> None:
    sub = getattr(args, "new_high_command", None)
    if sub == "daily":
        _run_daily(config, args)
        return
    if sub == "trend":
        _run_trend(args)
        return
    if sub == "backfill":
        _run_backfill(config, args)
        return
    print("用法：python main.py new-high daily|trend|backfill [...]", file=sys.stderr)
    sys.exit(2)


def _today() -> str:
    return datetime.date.today().isoformat()


def _write_reports(date: str, markdown: str, record: dict) -> dict[str, str]:
    report_dir = Path(C.REPORT_DIR)
    report_dir.mkdir(parents=True, exist_ok=True)
    md_path = report_dir / f"{date}.md"
    json_path = report_dir / f"{date}.json"
    md_path.write_text(markdown, encoding="utf-8")
    json_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"md": str(md_path), "json": str(json_path)}


def _push_to_dingtalk(title: str, markdown: str) -> None:
    from pushers.dingtalk_pusher import DingTalkPusher

    pusher = DingTalkPusher(config={})
    if not pusher.initialize():
        print("[new-high] DingTalk pusher 未启用，跳过推送", file=sys.stderr)
        return
    pusher.send_markdown(title=title, content=markdown)


def _run_daily(config: dict, args: argparse.Namespace) -> None:
    from main import setup_providers

    date = args.date or _today()
    registry = setup_providers(config)
    registry.initialize_all()
    conn = get_connection()
    try:
        migrate(conn)
        record = service.run_daily(conn, registry, date, persist=not args.dry_run, top_n=args.top_n)
    finally:
        conn.close()

    if args.json:
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return
    md = renderer.render_daily(record, top_n=args.top_n)
    print(md)
    if args.dry_run:
        return
    paths = _write_reports(date, md, record)
    print(f"report_md: {paths['md']}")
    print(f"report_json: {paths['json']}")
    if args.push and record.get("status") == "ok":
        _push_to_dingtalk(f"前复权历史新高统计 · {date}", md)


def _run_trend(args: argparse.Namespace) -> None:
    date = args.date or _today()
    conn = get_connection()
    try:
        migrate(conn)
        rows = service.run_trend(conn, date, days=args.days)
    finally:
        conn.close()
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if not rows:
        print("暂无前复权历史新高统计数据，需先运行 new-high daily 或 backfill。")
        return
    for row in rows:
        print(f"{row['date']} 新高 {row['new_high_count']} / 有效行情 {row['market_count']}")


def _date_range(start: str, end: str) -> list[str]:
    sd = datetime.date.fromisoformat(start)
    ed = datetime.date.fromisoformat(end)
    if sd > ed:
        raise SystemExit("--start-date 不能晚于 --end-date")
    out = []
    cur = sd
    while cur <= ed:
        out.append(cur.isoformat())
        cur += datetime.timedelta(days=1)
    return out


def _run_backfill(config: dict, args: argparse.Namespace) -> None:
    from main import setup_providers

    end = args.end_date or _today()
    if args.start_date:
        start = args.start_date
    else:
        start = (
            datetime.date.fromisoformat(end)
            - datetime.timedelta(days=365 * C.DEFAULT_BACKFILL_YEARS)
        ).isoformat()
    dates = _date_range(start, end)
    if args.dry_run:
        print(json.dumps({"start_date": start, "end_date": end, "candidate_days": len(dates)}, ensure_ascii=False, indent=2))
        return

    registry = setup_providers(config)
    registry.initialize_all()
    conn = get_connection()
    try:
        migrate(conn)
        summary = service.run_backfill(conn, registry, dates, persist=True, top_n=args.top_n)
    finally:
        conn.close()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
