from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from db import queries
from services.broker_executions import import_executions, normalize_rows, parse_file

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def register_subparser(subparsers: argparse._SubParsersAction) -> None:
    executions_parser = subparsers.add_parser("executions", help="券商成交记录导入与审计")
    executions_subparsers = executions_parser.add_subparsers(dest="executions_command")

    import_parser = executions_subparsers.add_parser("import", help="导入券商成交导出文件")
    import_parser.add_argument("--file", required=True, help="券商导出文件路径")
    import_parser.add_argument("--input-by", required=True, help="输入来源")
    import_parser.add_argument("--account", default="default", help="账户标识")
    import_parser.add_argument("--dry-run", action="store_true", help="只解析和校验，不提交入库")
    import_parser.add_argument("--json", action="store_true", help="输出 JSON")
    # plan I 系列(trade_thesis 中间层)
    import_parser.add_argument(
        "--allow-orphan-buy", action="store_true",
        help="降级:允许无 open thesis 的 buy 写入(thesis_id=NULL);默认严格 reject",
    )
    import_parser.add_argument(
        "--no-auto-close", dest="auto_close", action="store_false", default=True,
        help="禁用同批 sell 归零时的 thesis auto-close",
    )

    list_parser = executions_subparsers.add_parser("list", help="列出已入库券商成交记录")
    list_parser.add_argument("--from", dest="date_from", help="起始日期 YYYY-MM-DD")
    list_parser.add_argument("--to", dest="date_to", help="结束日期 YYYY-MM-DD")
    list_parser.add_argument("--account", help="账户标识")
    list_parser.add_argument("--limit", type=int, default=50, help="返回条数上限")
    list_parser.add_argument("--json", action="store_true", help="输出 JSON")

    audit_parser = executions_subparsers.add_parser("audit-export", help="导出成交记录审计报告")
    audit_parser.add_argument("--from", required=True, dest="date_from", help="起始日期 YYYY-MM-DD")
    audit_parser.add_argument("--to", required=True, dest="date_to", help="结束日期 YYYY-MM-DD")
    audit_parser.add_argument("--account", help="账户标识")
    audit_parser.add_argument("--out", help="输出 Markdown 文件路径")


def handle_executions_command(config: dict, args: argparse.Namespace) -> None:
    command = getattr(args, "executions_command", None)
    if command == "import":
        _cmd_import(config, args)
    elif command == "list":
        _cmd_list(config, args)
    elif command == "audit-export":
        _cmd_audit_export(config, args)
    else:
        print("用法: main.py executions {import|list|audit-export}")


def _open_conn(config: dict) -> sqlite3.Connection:
    database = config.get("database") or {}
    db_path = database.get("path") or str(PROJECT_ROOT / "data" / "trade.db")
    conn = sqlite3.connect(db_path)
    return conn


def _cmd_import(config: dict, args: argparse.Namespace) -> None:
    source_path = Path(args.file).expanduser()
    raw_rows, meta = parse_file(source_path)
    source_format = str(meta.get("source_format", ""))
    normalized_rows, failed_rows = normalize_rows(
        raw_rows,
        account_id=args.account,
        input_by=args.input_by,
        source_file=str(source_path),
        source_format=source_format,
        import_run_id="",
    )

    conn = _open_conn(config)
    try:
        report = import_executions(
            conn,
            normalized_rows,
            source_file=str(source_path),
            source_format=source_format,
            input_by=args.input_by,
            account_id=args.account,
            dry_run=args.dry_run,
            pre_errors=failed_rows,
            enforce_strict_thesis=True,
            allow_orphan_buy=getattr(args, "allow_orphan_buy", False),
            auto_close=getattr(args, "auto_close", True),
        )
    finally:
        conn.close()

    payload = {
        "parsed": len(raw_rows),
        "normalized": len(normalized_rows),
        "inserted": len(report.inserted),
        "skipped": len(report.skipped),
        "conflicts": len(report.conflicts),
        "conflict_rows": report.conflicts,
        "degraded": len(report.degraded),
        "failed": len(report.errors),
        "error_rows": report.errors,
        "dry_run": report.dry_run,
        "source_format": source_format,
        "import_run_id": report.import_run_id,
        "archive_path": report.archive_path,
        "report_path": report.report_path,
        "thesis_triggers": report.thesis_triggers,
        "auto_closed_thesis_ids": report.auto_closed_thesis_ids,
    }
    _emit_import_result(payload, source_path, args.json)


def _emit_import_result(payload: dict[str, Any], source_path: Path, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default))
        return

    print("[parse]")
    print(f"source_file: {source_path}")
    print(f"source_format: {payload['source_format']}")
    print(f"parsed: {payload['parsed']}")
    print("[normalize]")
    print(f"normalized: {payload['normalized']}")
    print(f"failed: {payload['failed']}")
    print("[import]")
    print(f"inserted: {payload['inserted']}")
    print(f"skipped: {payload['skipped']}（明细见 markdown 报告）")
    print(f"conflicts: {payload['conflicts']}")
    print(f"degraded: {payload['degraded']}")
    print(f"errors: {payload['failed']}")
    print(f"dry_run: {payload['dry_run']}")
    _print_conflict_details(payload.get("conflict_rows") or [])
    _print_error_details(payload.get("error_rows") or [])
    print(f"archive_path: {payload.get('archive_path') or ''}")
    print(f"report_path: {payload['report_path']}")
    _print_thesis_triggers(payload.get("thesis_triggers") or [])
    auto_closed = payload.get("auto_closed_thesis_ids") or []
    if auto_closed:
        print(f"[thesis] auto-closed thesis_ids: {auto_closed}")


def _print_thesis_triggers(triggers: list) -> None:
    """plan I1:dry-run / 实写时打印「思路触发段」+ 可执行 thesis-open 命令模板."""
    if not triggers:
        return
    opens = [t for t in triggers if t.get("action") == "open"]
    attaches = [t for t in triggers if t.get("action") == "attach"]
    print("\n[thesis triggers]")
    if opens:
        print(f"  待开新 thesis ({len(opens)} 只):")
        for t in opens:
            print(f"    - {t['stock_code']} @ {t['account_id']} ({t['biz_date']})")
            print(f"      {t['command_template']}")
    if attaches:
        print(f"  归入已有 open thesis ({len(attaches)} 笔加仓):")
        for t in attaches:
            print(
                f"    - {t['stock_code']} @ {t['account_id']} → thesis #{t['thesis_id']}"
            )


def _cmd_list(config: dict, args: argparse.Namespace) -> None:
    conn = _open_conn(config)
    try:
        rows = queries.list_broker_executions(
            conn,
            date_from=args.date_from,
            date_to=args.date_to,
            account_id=args.account,
        )
    finally:
        conn.close()

    limit = max(args.limit, 0)
    rows = rows[:limit]
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2, default=_json_default))
        return

    _print_rows_table(rows)


def _cmd_audit_export(config: dict, args: argparse.Namespace) -> None:
    conn = _open_conn(config)
    try:
        rows = queries.list_broker_executions(
            conn,
            date_from=args.date_from,
            date_to=args.date_to,
            account_id=args.account,
        )
    finally:
        conn.close()

    out_path = Path(args.out).expanduser() if args.out else _default_audit_path(args)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_build_audit_report(rows, args), encoding="utf-8")
    print(f"审计报告已导出: {out_path}")


def _print_rows_table(rows: list[dict]) -> None:
    headers = ["biz_date", "exec_time", "account", "code", "name", "dir", "shares", "price", "amount"]
    table_rows = [
        [
            str(row.get("biz_date") or ""),
            str(row.get("exec_time") or ""),
            str(row.get("account_id") or ""),
            str(row.get("stock_code") or ""),
            str(row.get("stock_name") or ""),
            str(row.get("direction") or ""),
            str(row.get("shares") or ""),
            _fmt_number(row.get("price")),
            _fmt_number(row.get("amount")),
        ]
        for row in rows
    ]
    if not table_rows:
        print("无成交记录")
        return

    widths = [
        max(len(headers[index]), *(len(row[index]) for row in table_rows))
        for index in range(len(headers))
    ]
    print(" | ".join(headers[index].ljust(widths[index]) for index in range(len(headers))))
    print("-+-".join("-" * width for width in widths))
    for row in table_rows:
        print(" | ".join(row[index].ljust(widths[index]) for index in range(len(row))))


def _default_audit_path(args: argparse.Namespace) -> Path:
    account = args.account or "all"
    filename = f"broker-executions_{args.date_from}_{args.date_to}_{account}.md"
    return PROJECT_ROOT / "tmp" / "audit-reports" / filename


def _build_audit_report(rows: list[dict], args: argparse.Namespace) -> str:
    buy_rows = [row for row in rows if row.get("direction") == "buy"]
    sell_rows = [row for row in rows if row.get("direction") == "sell"]
    total_amount = sum(_to_float(row.get("amount")) for row in rows)
    total_fees = sum(_to_float(row.get("total_fees")) for row in rows)

    lines = [
        "# Broker Executions Audit Report",
        "",
        "## Scope",
        "",
        f"- date_from: {args.date_from}",
        f"- date_to: {args.date_to}",
        f"- account: {args.account or 'all'}",
        f"- generated_at: {datetime.utcnow().isoformat()}Z",
        "",
        "## Summary",
        "",
        f"- total_rows: {len(rows)}",
        f"- buy_rows: {len(buy_rows)}",
        f"- sell_rows: {len(sell_rows)}",
        f"- total_amount: {_fmt_number(total_amount)}",
        f"- total_fees: {_fmt_number(total_fees)}",
        "",
        "## Rows",
        "",
    ]
    if not rows:
        lines.extend(["None.", ""])
        lines.extend(_build_per_stock_section(rows))
        lines.extend(_build_import_batches_section(rows))
        return "\n".join(lines).rstrip() + "\n"

    lines.extend([
        "| biz_date | exec_time | account | stock_code | stock_name | direction | shares | price | amount | fees | import_run_id |",
        "|---|---|---|---|---|---|---:|---:|---:|---:|---|",
    ])
    for row in rows:
        lines.append(
            "| "
            f"{row.get('biz_date') or ''} | {row.get('exec_time') or ''} | "
            f"{row.get('account_id') or ''} | {row.get('stock_code') or ''} | "
            f"{row.get('stock_name') or ''} | {row.get('direction') or ''} | "
            f"{row.get('shares') or ''} | {_fmt_number(row.get('price'))} | "
            f"{_fmt_number(row.get('amount'))} | {_fmt_number(row.get('total_fees'))} | "
            f"{row.get('import_run_id') or ''} |"
        )
    lines.append("")
    lines.extend(_build_per_stock_section(rows))
    lines.extend(_build_import_batches_section(rows))
    return "\n".join(lines).rstrip() + "\n"


def _build_per_stock_section(rows: list[dict]) -> list[str]:
    lines = ["## Per Stock", ""]
    if not rows:
        lines.extend(["None.", ""])
        return lines

    grouped: dict[str, dict[str, float | int | str]] = {}
    for row in rows:
        stock_code = str(row.get("stock_code") or "")
        item = grouped.setdefault(
            stock_code,
            {"count": 0, "net_shares": 0, "weighted_value": 0.0, "weighted_shares": 0.0, "fees": 0.0},
        )
        shares = int(_to_float(row.get("shares")))
        direction = row.get("direction")
        item["count"] = int(item["count"]) + 1
        item["net_shares"] = int(item["net_shares"]) + (shares if direction == "buy" else -shares)
        item["weighted_value"] = float(item["weighted_value"]) + shares * _to_float(row.get("price"))
        item["weighted_shares"] = float(item["weighted_shares"]) + shares
        item["fees"] = float(item["fees"]) + _to_float(row.get("total_fees"))

    lines.extend([
        "| stock_code | 成交笔数 | 净持仓变动 | 加权均价 | 费用合计 |",
        "|---|---:|---:|---:|---:|",
    ])
    for stock_code in sorted(grouped):
        item = grouped[stock_code]
        weighted_shares = float(item["weighted_shares"])
        avg_price = float(item["weighted_value"]) / weighted_shares if weighted_shares else 0.0
        lines.append(
            "| "
            f"{stock_code} | {int(item['count'])} | {int(item['net_shares'])} | "
            f"{_fmt_number(avg_price)} | {_fmt_number(item['fees'])} |"
        )
    lines.append("")
    return lines


def _build_import_batches_section(rows: list[dict]) -> list[str]:
    lines = ["## Import Batches", ""]
    if not rows:
        lines.extend(["None.", ""])
        return lines

    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        run_id = str(row.get("import_run_id") or "")
        item = grouped.setdefault(
            run_id,
            {"imported_at": [], "source_files": set(), "count": 0, "archive_paths": set()},
        )
        imported_at = row.get("imported_at")
        if imported_at:
            item["imported_at"].append(str(imported_at))
        if row.get("source_file"):
            item["source_files"].add(str(row.get("source_file")))
        if row.get("source_archive_path"):
            item["archive_paths"].add(str(row.get("source_archive_path")))
        item["count"] += 1

    lines.extend([
        "| run_id | imported_at 范围 | source_file | 行数 | source_archive_path |",
        "|---|---|---|---:|---|",
    ])
    for run_id in sorted(grouped):
        item = grouped[run_id]
        imported_at_values = sorted(item["imported_at"])
        imported_range = (
            f"{imported_at_values[0]}~{imported_at_values[-1]}"
            if imported_at_values else ""
        )
        source_files = ", ".join(sorted(item["source_files"]))
        archive_paths = ", ".join(sorted(item["archive_paths"]))
        lines.append(
            "| "
            f"{run_id} | {imported_range} | {source_files} | "
            f"{item['count']} | {archive_paths} |"
        )
    lines.append("")
    return lines


def _fmt_number(value: Any) -> str:
    if value is None or value == "":
        return ""
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return str(value)


def _to_float(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _json_default(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    return str(value)


def _print_conflict_details(conflicts: list[Any]) -> None:
    if not conflicts:
        return
    print("[conflicts]")
    for conflict in conflicts:
        summary = _get_value(conflict, "summary")
        print(
            "row_index: "
            f"{_get_value(summary, 'row_index')} "
            f"biz_date: {_get_value(summary, 'biz_date')} "
            f"exec_time: {_get_value(summary, 'exec_time') or ''} "
            f"stock_code: {_get_value(summary, 'stock_code')} "
            f"stock_name: {_get_value(summary, 'stock_name')} "
            f"direction: {_get_value(summary, 'direction')} "
            f"shares: {_get_value(summary, 'shares')} @ "
            f"{_fmt_number(_get_value(summary, 'price'))} "
            f"broker_trade_no: {_get_value(summary, 'broker_trade_no') or ''}"
        )
        diffs = _get_value(conflict, "diffs") or {}
        for field_name, values in diffs.items():
            existing, incoming = values
            print(f"  {field_name}: {existing} -> {incoming}")


def _print_error_details(errors: list[Any]) -> None:
    if not errors:
        return
    print("[errors]")
    for error in errors:
        raw = _get_value(error, "raw") or {}
        print(
            f"row_index: {_get_value(error, 'row_index')} "
            f"reason: {_get_value(error, 'reason')}"
        )
        if isinstance(raw, dict) and raw:
            keys = ("成交日期", "成交时间", "证券代码", "证券名称",
                    "操作", "成交数量", "成交均价", "成交金额")
            preview = {k: raw[k] for k in keys if k in raw}
            if preview:
                print(f"  raw: {preview}")


def _get_value(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)
