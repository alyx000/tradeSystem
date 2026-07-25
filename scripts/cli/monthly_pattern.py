"""CLI：完成月技术确认 + 公告日 as-of 财务校验的月线观察池。"""
from __future__ import annotations

import argparse
import calendar
import json
import logging
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

from db.connection import get_connection
from db.migrate import migrate
from services.monthly_pattern import pool, renderer, service

logger = logging.getLogger(__name__)

DEFAULT_MONTHS = 48
REPORT_DIR = Path(__file__).resolve().parents[2] / "data" / "reports" / "monthly-pattern"


def _history_months(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("必须为整数") from exc
    if value < 35:
        raise argparse.ArgumentTypeError("至少需要 35 个完成月（MACD 12/26/9）")
    return value


def _year_month(raw: str) -> str:
    try:
        year, month = (int(part) for part in raw.split("-"))
        if len(raw) != 7:
            raise ValueError
        date(year, month, 1)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("必须为 YYYY-MM") from exc
    return raw


def _iso_date(raw: str) -> str:
    try:
        parsed = date.fromisoformat(raw)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("必须为 YYYY-MM-DD") from exc
    if parsed.isoformat() != raw:
        raise argparse.ArgumentTypeError("必须为 YYYY-MM-DD")
    return raw


def _input_by(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        raise argparse.ArgumentTypeError("不能为空")
    return value


def register_subparser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "monthly-pattern",
        help="月线模式观察池（完成月确认 / 公告日 as-of 财务校验）",
    )
    commands = parser.add_subparsers(dest="monthly_pattern_command")

    daily = commands.add_parser("daily", help="增量采集、扫描并维护观察池")
    daily.add_argument(
        "--date",
        type=_iso_date,
        default=None,
        help="扫描日 YYYY-MM-DD（默认今天）",
    )
    daily.add_argument(
        "--months",
        type=_history_months,
        default=DEFAULT_MONTHS,
        help=f"历史完成月数量（至少35，默认 {DEFAULT_MONTHS}）",
    )
    daily.add_argument(
        "--input-by",
        type=_input_by,
        required=True,
        help="本次扫描请求者（写入审计，必填）",
    )
    daily.add_argument(
        "--no-financial",
        action="store_true",
        help="只跑技术候选层，不拉财报（不会伪装为财务已验证）",
    )
    daily.add_argument("--dry-run", action="store_true", help="内存副本运行，不落库、不落报告、不推送")
    daily.add_argument("--no-push", action="store_true", help="落库并落报告，但不推送")

    read_pool = commands.add_parser("pool", help="只读查看观察池")
    read_pool.set_defaults(input_by=None)
    read_pool.add_argument(
        "--status",
        choices=sorted(pool.VALID_STATUSES),
        default=None,
    )
    read_pool.add_argument(
        "--strategy",
        choices=list(service.STRATEGIES),
        default=None,
    )
    read_pool.add_argument("--json", action="store_true")

    backfill = commands.add_parser("backfill", help="按完成月正序历史回放")
    backfill.add_argument("--start-month", type=_year_month, required=True)
    backfill.add_argument("--end-month", type=_year_month, required=True)
    backfill.add_argument(
        "--input-by",
        type=_input_by,
        required=True,
        help="本次回放请求者（写入审计，必填）",
    )
    backfill.add_argument("--months", type=_history_months, default=DEFAULT_MONTHS)
    backfill.add_argument("--no-financial", action="store_true")
    backfill.add_argument("--dry-run", action="store_true")


def handle_command(config: dict, args: argparse.Namespace) -> None:
    command = getattr(args, "monthly_pattern_command", None)
    if command == "daily":
        _run_daily(config, args)
    elif command == "pool":
        _run_pool(args)
    elif command == "backfill":
        _run_backfill(config, args)
    else:
        print("用法：python main.py monthly-pattern daily|pool|backfill [...]", file=sys.stderr)
        raise SystemExit(2)


def _initialized_registry(config: dict):
    from main import setup_providers

    registry = setup_providers(config)
    registry.initialize_all()
    return registry


def _working_connection(*, dry_run: bool) -> tuple[sqlite3.Connection, sqlite3.Connection | None]:
    real = get_connection()
    if not dry_run:
        migrate(real)
        return real, None
    memory = sqlite3.connect(":memory:")
    memory.row_factory = sqlite3.Row
    real.backup(memory)
    migrate(memory)
    return memory, real


def _close_working(conn: sqlite3.Connection, real: sqlite3.Connection | None) -> None:
    conn.close()
    if real is not None:
        real.close()


def _run_daily(config: dict, args: argparse.Namespace) -> None:
    scan_date = args.date or date.today().isoformat()
    registry = _initialized_registry(config)
    conn, real = _working_connection(dry_run=args.dry_run)
    try:
        try:
            summary = service.run_daily(
                conn,
                registry,
                scan_date,
                months=args.months,
                include_financial=not args.no_financial,
                input_by=args.input_by,
            )
        except service.MonthlyPatternTemporalOrderError as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(2) from exc
    finally:
        _close_working(conn, real)

    markdown = renderer.render_daily(summary)
    print(markdown)
    if args.dry_run:
        logger.info("[monthly-pattern] dry-run：内存副本完成，未落库/报告/推送")
    else:
        report_path = _write_report(scan_date, markdown)
        logger.info("[monthly-pattern] 报告: %s", report_path)
        if not args.no_push:
            _push(f"月线模式观察池 · {scan_date}", markdown)
    if summary["status"] == "failed":
        raise SystemExit(1)


def _run_pool(args: argparse.Namespace) -> None:
    conn = get_connection()
    try:
        rows = pool.list_pool(
            conn,
            status=args.status,
            strategy_type=args.strategy,
        )
    except sqlite3.OperationalError as exc:
        print(f"月线观察池尚未初始化：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        conn.close()
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print(renderer.render_pool(rows))


def _month_range(start: str, end: str) -> list[str]:
    start_key = tuple(int(part) for part in start.split("-"))
    end_key = tuple(int(part) for part in end.split("-"))
    if start_key > end_key:
        raise ValueError("start-month 不能晚于 end-month")
    year, month = start_key
    result: list[str] = []
    while (year, month) <= end_key:
        result.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year += 1
            month = 1
    return result


def _last_completed_month() -> str:
    first_of_this_month = date.today().replace(day=1)
    return (first_of_this_month - timedelta(days=1)).strftime("%Y-%m")


def _run_backfill(config: dict, args: argparse.Namespace) -> None:
    try:
        months = _month_range(args.start_month, args.end_month)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
    last_completed = _last_completed_month()
    if months and months[-1] > last_completed:
        print(
            "backfill 只接受已完成月份："
            f"end-month={months[-1]} 晚于最近完成月 {last_completed}",
            file=sys.stderr,
        )
        raise SystemExit(2)
    registry = _initialized_registry(config)
    conn, real = _working_connection(dry_run=args.dry_run)
    receipts = []
    try:
        for year_month in months:
            year, month = (int(part) for part in year_month.split("-"))
            scan_date = (
                f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"
            )
            try:
                summary = service.run_daily(
                    conn,
                    registry,
                    scan_date,
                    months=args.months,
                    include_financial=not args.no_financial,
                    input_by=args.input_by,
                )
            except service.MonthlyPatternTemporalOrderError as exc:
                print(str(exc), file=sys.stderr)
                raise SystemExit(2) from exc
            receipts.append(
                {
                    "scan_date": scan_date,
                    "signal_month": summary["signal_month"],
                    "status": summary["status"],
                    "counts": summary["counts"],
                }
            )
            if summary["status"] == "failed":
                break
    finally:
        _close_working(conn, real)
    print(json.dumps(receipts, ensure_ascii=False, indent=2))
    if receipts and receipts[-1]["status"] == "failed":
        raise SystemExit(1)


def _write_report(scan_date: str, markdown: str) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"{scan_date}.md"
    temporary = path.with_suffix(".md.tmp")
    temporary.write_text(markdown, encoding="utf-8")
    temporary.replace(path)
    return path


def _push(title: str, markdown: str) -> None:
    from pushers.dingtalk_pusher import DingTalkPusher

    pusher = DingTalkPusher(config={})
    if not pusher.initialize():
        logger.error("[monthly-pattern] DingTalk 未启用（缺 DINGTALK_*），跳过推送")
        return
    ok = pusher.send_markdown(title=title, content=markdown)
    logger.info("[monthly-pattern] 推送%s", "成功" if ok else "失败")
