"""CLI：低价股赚钱效应历史补采与趋势图。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from db.connection import get_readonly_connection
from services.low_price_effect_history import (
    DEFAULT_REPORT_DIR,
    backfill_history,
    build_trend_rows,
    select_trade_dates,
    write_trend_artifacts,
)


def register_subparser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "low-price-effect",
        help="低价股赚钱效应历史补采与趋势图（无推送）",
    )
    commands = parser.add_subparsers(dest="low_price_effect_command")

    backfill = commands.add_parser(
        "backfill",
        help="补采最近 N 个交易日并更新既有盘后 YAML/SQLite、生成趋势图",
    )
    backfill.add_argument("--end-date", required=True, help="截止交易日 YYYY-MM-DD")
    backfill.add_argument("--days", type=int, default=10, help="交易日数（默认 10）")
    backfill.add_argument("--input-by", required=True, help="写入请求者（审计必填）")
    backfill.add_argument(
        "--refetch",
        action="store_true",
        help="完整历史块也重新抓取；新结果降级时保留旧事实",
    )
    backfill.add_argument(
        "--output-dir",
        default=str(DEFAULT_REPORT_DIR),
        help="CSV/JSON/PNG 输出目录",
    )
    backfill.add_argument("--json", action="store_true", help="输出 JSON 收据")

    trend = commands.add_parser(
        "trend",
        help="只读既有盘后归档并重新生成最近 N 个交易日趋势图",
    )
    trend.add_argument("--end-date", required=True, help="截止交易日 YYYY-MM-DD")
    trend.add_argument("--days", type=int, default=10, help="交易日数（默认 10）")
    trend.add_argument(
        "--output-dir",
        default=str(DEFAULT_REPORT_DIR),
        help="CSV/JSON/PNG 输出目录",
    )
    trend.add_argument("--json", action="store_true", help="输出 JSON 收据")


def _trade_dates(end_date: str, days: int) -> list[str]:
    conn = get_readonly_connection()
    try:
        return select_trade_dates(conn, end_date, days)
    finally:
        conn.close()


def _emit(payload: dict, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print(
        f"[low-price-effect] status={payload['status']} "
        f"complete={payload['complete_count']}/{payload['days']}"
    )
    if payload.get("receipts"):
        for receipt in payload["receipts"]:
            line = f"- {receipt.get('trade_date')}: {receipt.get('status')}"
            if receipt.get("source_status"):
                line += f" ({receipt['source_status']})"
            if receipt.get("error"):
                line += f" — {receipt['error']}"
            print(line)
    for key in ("png_path", "csv_path", "json_path"):
        if payload.get(key):
            print(f"{key}: {payload[key]}")


def handle_command(config: dict, args: argparse.Namespace) -> int:
    command = getattr(args, "low_price_effect_command", None)
    if command not in {"backfill", "trend"}:
        print(
            "用法: python3 scripts/main.py low-price-effect "
            "backfill|trend --end-date YYYY-MM-DD [--days N]",
            file=sys.stderr,
        )
        return 2

    try:
        trade_dates = _trade_dates(args.end_date, args.days)
    except (OSError, RuntimeError, ValueError) as exc:
        payload = {
            "status": "calendar_failed",
            "message": str(exc),
            "days": args.days,
            "complete_count": 0,
        }
        _emit(payload, as_json=bool(args.json))
        return 2

    receipts: list[dict] = []
    if command == "backfill":
        from main import setup_providers
        from utils.network_env import without_standard_http_proxy

        with without_standard_http_proxy():
            registry = setup_providers(config)
            registry.initialize_all()
            receipts = backfill_history(
                registry,
                trade_dates,
                input_by=args.input_by,
                refetch=bool(args.refetch),
            )

    rows = build_trend_rows(trade_dates)
    artifacts = write_trend_artifacts(
        rows,
        report_dir=Path(args.output_dir).expanduser().resolve(),
    )
    complete_count = sum(row.get("status") == "complete" for row in rows)
    receipt_ok = all(
        receipt.get("status") in {"persisted", "already_complete"}
        for receipt in receipts
    )
    is_complete = complete_count == len(trade_dates) and receipt_ok
    payload = {
        "status": "complete" if is_complete else "partial",
        "mode": command,
        "end_date": args.end_date,
        "days": len(trade_dates),
        "trade_dates": trade_dates,
        "complete_count": complete_count,
        "receipts": receipts,
        "rows": rows,
        **artifacts,
        "push_status": "not_requested",
    }
    _emit(payload, as_json=bool(args.json))
    return 0 if is_complete else 2
