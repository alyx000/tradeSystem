"""CLI：完成月技术确认 + 公告日 as-of 财务校验的月线观察池。"""
from __future__ import annotations

import argparse
import calendar
import json
import logging
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from db.connection import get_connection, get_readonly_connection
from db.migrate import ensure_monthly_pattern_derived_fact_schema, migrate
from services.monthly_pattern import (
    fact_backfill_service,
    indicator_watch_renderer,
    indicator_watch_service,
    monitor_daily,
    pool,
    renderer,
    service,
)

logger = logging.getLogger(__name__)

DEFAULT_MONTHS = 48
REPORT_DIR = Path(__file__).resolve().parents[2] / "data" / "reports" / "monthly-pattern"
MONITOR_REPORT_DIR = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "reports"
    / "monthly-pattern-monitor"
)
MONITOR_DAILY_STATE_DIR = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "runs"
    / "monthly-pattern-monitor"
)


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


def _positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("必须为整数") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("必须为正整数")
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

    monitor = commands.add_parser(
        "monitor",
        help="只读影子监控：五阳回踩月线种子 + 日/周 MACD 状态",
    )
    monitor.set_defaults(input_by=None)
    monitor.add_argument(
        "--date",
        type=_iso_date,
        default=None,
        help="目标交易日 YYYY-MM-DD（默认最近已完成交易日）",
    )
    monitor.add_argument(
        "--months",
        type=_history_months,
        default=DEFAULT_MONTHS,
        help=f"certified 完成月历史窗口（至少35，默认 {DEFAULT_MONTHS}）",
    )
    monitor.add_argument(
        "--max-seeds",
        type=_positive_int,
        default=None,
        help="仅用于校准的月线种子扫描上限；省略时扫描全部",
    )
    monitor.add_argument("--json", action="store_true", help="输出结构化 JSON")
    monitor.add_argument(
        "--save-report",
        action="store_true",
        help="显式保存本地 Markdown；手工 monitor 本身不推送",
    )

    monitor_daily_parser = commands.add_parser(
        "monitor-daily",
        help="日频自动监控：保存快照并只推送相对完整基线的状态变化",
    )
    monitor_daily_parser.set_defaults(input_by=None)
    monitor_daily_parser.add_argument(
        "--date",
        type=_iso_date,
        default=None,
        help=(
            "历史校准日 YYYY-MM-DD；默认仅预览且永不推送，"
            "同时带 --no-push 才保存并推进基线"
        ),
    )
    monitor_daily_parser.add_argument(
        "--months",
        type=_history_months,
        default=DEFAULT_MONTHS,
        help=f"certified 完成月历史窗口（至少35，默认 {DEFAULT_MONTHS}）",
    )
    monitor_daily_mode = monitor_daily_parser.add_mutually_exclusive_group()
    monitor_daily_mode.add_argument(
        "--dry-run",
        action="store_true",
        help="现算并预览差异，不写本地状态/报告、不推送",
    )
    monitor_daily_mode.add_argument(
        "--no-push",
        action="store_true",
        help=(
            "保存本地快照并推进基线，但明确抑制本轮新通知；"
            "也是显式 --date 的确认写入开关"
        ),
    )
    monitor_daily_parser.add_argument(
        "--json",
        action="store_true",
        help="输出结构化运行收据",
    )

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

    facts_backfill = commands.add_parser(
        "facts-backfill",
        help="审计式修复只读监控仍 blocked 的完成月派生事实",
    )
    facts_backfill.add_argument(
        "--date",
        type=_iso_date,
        required=True,
        help="绑定待修复监控快照的目标交易日 YYYY-MM-DD（必填）",
    )
    facts_backfill.add_argument(
        "--months",
        type=_history_months,
        default=DEFAULT_MONTHS,
        help=f"certified 完成月历史窗口（至少35，默认 {DEFAULT_MONTHS}）",
    )
    facts_backfill.add_argument(
        "--input-by",
        type=_input_by,
        required=True,
        help="本次回补请求者（写入审计，必填）",
    )
    facts_backfill.add_argument(
        "--dry-run",
        action="store_true",
        help="内存副本生成确认收据和监控预览，不写真实数据库",
    )
    facts_backfill.add_argument(
        "--expect-receipt-hash",
        default=None,
        help="实际写入必须提供上一轮完整 dry-run 的 receipt_hash",
    )
    facts_backfill.add_argument(
        "--max-stocks",
        type=_positive_int,
        default=None,
        help="仅 dry-run 校准可用的股票上限；实际写入拒绝截断",
    )


def handle_command(config: dict, args: argparse.Namespace) -> None:
    command = getattr(args, "monthly_pattern_command", None)
    if command == "daily":
        _run_daily(config, args)
    elif command == "pool":
        _run_pool(args)
    elif command == "monitor":
        _run_monitor(config, args)
    elif command == "monitor-daily":
        _run_monitor_daily(config, args)
    elif command == "backfill":
        _run_backfill(config, args)
    elif command == "facts-backfill":
        _run_facts_backfill(config, args)
    else:
        print(
            "用法：python main.py monthly-pattern "
            "daily|pool|monitor|monitor-daily|backfill|facts-backfill [...]",
            file=sys.stderr,
        )
        raise SystemExit(2)


def _initialized_registry(config: dict):
    from main import setup_providers

    registry = setup_providers(config)
    registry.initialize_all()
    return registry


def _working_connection(*, dry_run: bool) -> tuple[sqlite3.Connection, sqlite3.Connection | None]:
    if not dry_run:
        real = get_connection()
        migrate(real)
        return real, None
    real = get_readonly_connection()
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
    push_focus_rows: list[dict] | None = None
    report_path: Path | None = None
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

        markdown = renderer.render_daily(summary)
        print(markdown)
        if args.dry_run:
            logger.info("[monthly-pattern] dry-run：内存副本完成，未落库/报告/推送")
        else:
            # 全量归档先于可选的专池摘要查询；后者异常时也不能丢失扫描报告。
            report_path = _write_report(scan_date, markdown)
            logger.info("[monthly-pattern] 报告: %s", report_path)

        if not args.dry_run and not args.no_push and summary["status"] != "failed":
            push_focus_rows = list(pool.list_pool(conn, status="active"))
            push_focus_rows.extend(
                pool.list_pool(conn, status="fundamental_verified")
            )
    finally:
        _close_working(conn, real)

    if not args.dry_run and not args.no_push:
        if report_path is None:  # pragma: no cover - 防御不变量
            raise RuntimeError("monthly-pattern 报告未落盘，拒绝推送")
        push_markdown = renderer.render_push_summary(
            summary,
            full_markdown=markdown,
            report_path=str(report_path),
            focus_candidates=push_focus_rows,
        )
        _push(f"月线模式观察池 · {scan_date}", push_markdown)
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


def _run_monitor(config: dict, args: argparse.Namespace) -> None:
    """执行数据库只读、默认不落文件、不推送的日频影子监控。"""
    registry = _initialized_registry(config)
    conn = get_readonly_connection()
    try:
        summary = indicator_watch_service.run_monitor(
            conn,
            registry,
            args.date,
            months=args.months,
            max_seeds=args.max_seeds,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
    finally:
        conn.close()

    markdown = indicator_watch_renderer.render_monitor(summary)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(markdown)
    if args.save_report:
        target_date = str(summary.get("target_date") or args.date or "blocked")
        report_path = _write_monitor_report(target_date, markdown)
        logger.info("[monthly-pattern monitor] 报告: %s", report_path)
    if summary.get("status") == "blocked":
        raise SystemExit(1)


def _run_monitor_daily(config: dict, args: argparse.Namespace) -> None:
    """执行严格交易日闸门、本地状态账本和变化推送。"""
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    target_date = args.date
    push_allowed = False
    persist_gate_blocked = False
    summary: dict | None = None
    try:
        registry = _initialized_registry(config)
    except Exception as exc:
        persist_gate_blocked = target_date is None
        summary = indicator_watch_service.build_blocked_summary(
            requested_date=target_date,
            target_date=target_date or now.date().isoformat(),
            error=exc,
        )

    if summary is None and target_date is None:
        try:
            target_date, _latest_open = indicator_watch_service.resolve_target_date(
                registry,
                None,
                now=now,
            )
        except (ValueError, indicator_watch_service.IndicatorWatchSourceError) as exc:
            target_date = now.date().isoformat()
            persist_gate_blocked = True
            summary = indicator_watch_service.build_blocked_summary(
                requested_date=None,
                target_date=target_date,
                error=exc,
            )
        else:
            if target_date != now.date().isoformat():
                receipt = {
                    "status": "skipped_non_trading_day",
                    "today": now.date().isoformat(),
                    "latest_closed_trade_date": target_date,
                    "writes": False,
                    "push": False,
                }
                print(
                    json.dumps(receipt, ensure_ascii=False, indent=2)
                    if args.json
                    else (
                        "[monthly-pattern monitor-daily] "
                        f"{receipt['today']} 非已收盘交易日，安全跳过；"
                        f"最近已收盘交易日={target_date}"
                    )
                )
                return
            push_allowed = True

    if summary is None:
        try:
            conn = get_readonly_connection()
        except Exception as exc:
            summary = indicator_watch_service.build_blocked_summary(
                requested_date=args.date,
                target_date=target_date or now.date().isoformat(),
                error=exc,
            )
        else:
            try:
                try:
                    summary = indicator_watch_service.run_monitor(
                        conn,
                        registry,
                        target_date,
                        months=args.months,
                        max_seeds=None,
                    )
                except ValueError as exc:
                    print(str(exc), file=sys.stderr)
                    raise SystemExit(2) from exc
                except Exception as exc:
                    summary = indicator_watch_service.build_blocked_summary(
                        requested_date=args.date,
                        target_date=target_date or now.date().isoformat(),
                        error=exc,
                    )
            finally:
                conn.close()

    if not summary.get("target_date"):
        summary = {
            **summary,
            "target_date": target_date or now.date().isoformat(),
        }
    markdown = indicator_watch_renderer.render_monitor(
        summary,
        automated_snapshot=True,
    )
    try:
        result = monitor_daily.process_summary(
            summary,
            state_dir=MONITOR_DAILY_STATE_DIR,
            report_dir=MONITOR_REPORT_DIR,
            monitor_markdown=markdown,
            dry_run=args.dry_run,
            push=not (args.dry_run or args.no_push),
            push_allowed=push_allowed,
            persist_gate_blocked=persist_gate_blocked,
            now=now,
        )
    except monitor_daily.MonitorDailyError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(
            "[monthly-pattern monitor-daily] "
            f"date={result['target_date']} status={result['run_status']} "
            f"events={len(result['events'])} "
            f"new={result['new_notification_count']} "
            f"pending={result['pending_count']} "
            f"sent={result['sent_count']} "
            f"push={result['push_status']} "
            f"report={result.get('report_path') or 'dry-run'}"
        )
    if result["run_status"] in {"partial", "blocked"}:
        raise SystemExit(1)
    if result["push_status"] in {
        "credentials_missing",
        "failed",
        "partial",
    }:
        raise SystemExit(1)


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


def _run_facts_backfill(config: dict, args: argparse.Namespace) -> None:
    """先生成可复核收据；只有完整且哈希匹配时才迁移并原子写派生层。"""
    if not args.dry_run and args.max_stocks is not None:
        print("--max-stocks 只允许与 --dry-run 同用", file=sys.stderr)
        raise SystemExit(2)
    if not args.dry_run and not str(args.expect_receipt_hash or "").strip():
        print(
            "实际写入必须提供 --expect-receipt-hash",
            file=sys.stderr,
        )
        raise SystemExit(2)

    registry = _initialized_registry(config)
    if args.dry_run:
        conn, real = _working_connection(dry_run=True)
        ensure_schema_before_persist = None
    else:
        # receipt mismatch 必须零写；确认后也只允许在写事务内确保派生两表，
        # 禁止从该入口调用全库 migrate() 触碰 raw 月线结构。
        conn, real = get_connection(), None
        ensure_schema_before_persist = (
            lambda: ensure_monthly_pattern_derived_fact_schema(conn)
        )
    try:
        try:
            summary = fact_backfill_service.run_backfill(
                conn,
                registry,
                target_date=args.date,
                months=args.months,
                input_by=args.input_by,
                dry_run=args.dry_run,
                expected_receipt_hash=args.expect_receipt_hash,
                max_stocks=args.max_stocks,
                ensure_schema_before_persist=ensure_schema_before_persist,
            )
        except fact_backfill_service.FactBackfillValidationError as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(2) from exc
        except fact_backfill_service.FactBackfillError as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(1) from exc
    finally:
        _close_working(conn, real)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary.get("status") in {"receipt_mismatch", "state_drift"}:
        raise SystemExit(2)
    if summary.get("status") == "partial":
        raise SystemExit(1)


def _write_report(scan_date: str, markdown: str) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"{scan_date}.md"
    temporary = path.with_suffix(".md.tmp")
    temporary.write_text(markdown, encoding="utf-8")
    temporary.replace(path)
    return path


def _write_monitor_report(scan_date: str, markdown: str) -> Path:
    MONITOR_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = MONITOR_REPORT_DIR / f"{scan_date}.md"
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
