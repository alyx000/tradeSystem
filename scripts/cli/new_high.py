"""CLI: 前复权历史新高统计。"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import os
from pathlib import Path
import sys
import tempfile

from db.connection import get_connection
from db.migrate import migrate
from services.new_high import constants as C
from services.new_high import renderer, service
from utils.network_env import without_standard_http_proxy
from utils.trade_date import ensure_trade_calendar


logger = logging.getLogger(__name__)
REPO_ROOT = Path(__file__).resolve().parents[2]

_REPORT_RECORD_KEYS = (
    "date",
    "market_count",
    "new_high_count",
    "sector_summary",
    "stocks",
    "source",
)


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


def _write_reports(
    date: str,
    markdown: str,
    record: dict,
    *,
    preserve_matching: bool = False,
) -> dict:
    json_text = json.dumps(record, ensure_ascii=False, indent=2) + "\n"
    report_dir = Path(C.REPORT_DIR)
    if not report_dir.is_absolute():
        report_dir = REPO_ROOT / report_dir
    report_dir.mkdir(parents=True, exist_ok=True)
    md_path = report_dir / f"{date}.md"
    json_path = report_dir / f"{date}.json"
    actions = {"md": "written", "json": "written"}

    write_json = True
    write_markdown = True
    if preserve_matching:
        if _markdown_report_matches(md_path, markdown):
            write_markdown = False
            actions["md"] = "preserved"
        elif md_path.exists():
            actions["md"] = "repaired"

        if _json_report_matches(json_path, record):
            write_json = False
            actions["json"] = "preserved"
        elif json_path.exists():
            actions["json"] = "repaired"

    if write_json:
        _atomic_write_text(json_path, json_text)
    if write_markdown:
        _atomic_write_text(md_path, markdown)
    return {
        "md": str(md_path),
        "json": str(json_path),
        "actions": actions,
    }


def _atomic_write_text(path: Path, content: str) -> None:
    fd, temp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def _markdown_report_matches(path: Path, expected: str) -> bool:
    try:
        return path.is_file() and path.read_text(encoding="utf-8") == expected
    except (OSError, UnicodeError):
        return False


def _json_report_matches(path: Path, expected: dict) -> bool:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(loaded, dict):
        return False
    return all(loaded.get(key) == expected.get(key) for key in _REPORT_RECORD_KEYS)


def run_for_post(
    config: dict,
    target_date: str,
    registry=None,
) -> dict:
    """由 ``cmd_post`` 连续推进生产尾日，不写 schema 或交易日历。"""
    conn = get_connection()
    try:
        with without_standard_http_proxy():
            active_registry = registry
            if active_registry is None:
                from main import setup_providers

                active_registry = setup_providers(config)
                active_registry.initialize_all()
            summary = service.run_due_dates(
                conn,
                active_registry,
                target_date,
                top_n=C.DEFAULT_TOP_N,
            )
    finally:
        conn.close()

    status = summary.get("status")
    if status in {"ok", "already_complete"}:
        target_record = summary.get("target_record")
        if target_record is None:
            logger.warning(
                "[new-high post] status=%s failed_date=%s target_record 缺失",
                status,
                target_date,
            )
            return summary
        markdown = renderer.render_daily(target_record, top_n=C.DEFAULT_TOP_N)
        paths = _write_reports(
            target_date,
            markdown,
            target_record,
            preserve_matching=status == "already_complete",
        )
        summary = {
            **summary,
            "report_paths": {"md": paths["md"], "json": paths["json"]},
            "report_actions": paths["actions"],
        }
        logger.info(
            "[new-high post] status=%s target_date=%s processed_dates=%s "
            "report_md=%s report_json=%s report_actions=%s",
            status,
            target_date,
            summary.get("processed_dates"),
            paths["md"],
            paths["json"],
            paths["actions"],
        )
    else:
        failure_detail = summary.get("failure_detail") or {}
        error = str(failure_detail.get("error") or "")[:500]
        log = logger.info if status == "non_trading_day" else logger.warning
        log(
            "[new-high post] status=%s target_date=%s processed_dates=%s "
            "failed_date=%s failed_source=%s error=%s",
            status,
            target_date,
            summary.get("processed_dates"),
            summary.get("failed_date"),
            failure_detail.get("failed_source"),
            error,
        )
    return summary


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
        if args.dry_run:
            record = service.run_daily(
                conn,
                registry,
                date,
                persist=False,
                top_n=args.top_n,
            )
            run_status = record.get("status")
        else:
            summary = service.run_due_dates(
                conn,
                registry,
                date,
                top_n=args.top_n,
            )
            run_status = summary.get("status")
            record = summary.get("target_record")
    finally:
        conn.close()

    if record is None:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return
    if args.json:
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return
    md = renderer.render_daily(record, top_n=args.top_n)
    print(md)
    if args.dry_run:
        return
    paths = _write_reports(
        date,
        md,
        record,
        preserve_matching=run_status == "already_complete",
    )
    print(f"report_md: {paths['md']}")
    print(f"report_json: {paths['json']}")
    if args.push and run_status in {"ok", "already_complete"}:
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
        for year in range(int(start[:4]), int(end[:4]) + 1):
            ensure_trade_calendar(conn, registry, year=year, force=True)
        summary = service.run_backfill(conn, registry, dates, top_n=args.top_n)
    finally:
        conn.close()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
