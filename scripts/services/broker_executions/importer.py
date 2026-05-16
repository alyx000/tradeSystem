from __future__ import annotations

import logging
import shutil
import sqlite3
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from db import queries

from .models import ConflictRow, ErrorRow, ImportReport, NormalizedRow, RowSummary

logger = logging.getLogger(__name__)

# 非键字段比对集合（仅用于键命中后再比对内容）。
# broker_contract_no / broker_trade_no 不在此集合：它们已属于 UNIQUE 键的一部分
# （含 COALESCE NULL → '' 处理），命中 UNIQUE 已表示编号一致；如果一方有编号一方
# 为空，UNIQUE 不命中、走 INSERT 分支，不会进入此比对路径。
_COMPARE_FIELDS = [
    "stock_name",
    "market",
    "net_amount",
    "balance_after",
    "commission",
    "stamp_duty",
    "transfer_fee",
    "exchange_fee",
    "regulatory_fee",
    "other_fees",
    "total_fees",
]

_FLOAT_FIELDS = {
    "net_amount",
    "commission",
    "stamp_duty",
    "transfer_fee",
    "exchange_fee",
    "regulatory_fee",
    "other_fees",
    "total_fees",
}


def import_executions(
    conn: sqlite3.Connection,
    normalized_rows: list[NormalizedRow],
    *,
    source_file: str,
    source_format: str,
    input_by: str,
    account_id: str = "default",
    dry_run: bool = False,
    report_root: Path = Path("tmp/import-reports"),
    archive_root: Path = Path("tmp/imports"),
    pre_errors: list[ErrorRow] | None = None,
) -> ImportReport:
    import_run_id = uuid.uuid4().hex[:12]
    for row in normalized_rows:
        row.import_run_id = import_run_id
        row.input_by = input_by
        row.account_id = account_id

    started_at = datetime.utcnow().isoformat()
    ts = datetime.utcnow().strftime("%Y-%m-%d_%H%M%S")
    basename = Path(source_file).name
    errors = list(pre_errors or [])
    report = ImportReport(
        source_file=source_file,
        source_format=source_format,
        import_run_id=import_run_id,
        parsed=len(normalized_rows) + len(errors),
        errors=errors,
        started_at=started_at,
        dry_run=dry_run,
    )

    try:
        conn.execute("BEGIN")
        for row in normalized_rows:
            summary = _row_summary(row)
            existing = queries.find_broker_execution_by_dedupe(
                conn,
                row.account_id,
                row.biz_date,
                row.stock_code,
                row.direction,
                row.shares,
                row.price,
                row.broker_contract_no,
                row.broker_trade_no,
            )
            if existing is None:
                queries.insert_broker_execution(conn, **_row_to_insert_dict(row))
                report.inserted.append(summary)
            else:
                diffs = _diff_existing(existing, row)
                if diffs:
                    report.conflicts.append(ConflictRow(summary=summary, diffs=diffs))
                else:
                    report.skipped.append(summary)
            if row._dedupe_mode == "degraded":
                report.degraded.append(summary)

        if dry_run:
            conn.execute("ROLLBACK")
        else:
            conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    if dry_run:
        report.archive_path = None
    else:
        archive_path = _archive_source_file(source_file, archive_root, ts, basename)
        if archive_path is not None:
            report.archive_path = str(archive_path)
            queries.update_broker_execution_archive_path(conn, import_run_id, str(archive_path))
            conn.commit()

    report_path = report_root / f"{ts}_{basename}.md"
    report.report_path = str(report_path)
    report.finished_at = datetime.utcnow().isoformat()
    report_root.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report.to_markdown(), encoding="utf-8")
    return report


def _row_summary(row: NormalizedRow) -> RowSummary:
    return RowSummary(
        row_index=row.row_index,
        biz_date=row.biz_date,
        exec_time=row.exec_time,
        stock_code=row.stock_code,
        stock_name=row.stock_name,
        direction=row.direction,
        shares=row.shares,
        price=row.price,
        broker_trade_no=row.broker_trade_no,
    )


def _row_to_insert_dict(row: NormalizedRow) -> dict[str, Any]:
    data = asdict(row)
    data.pop("row_index", None)
    data.pop("_dedupe_mode", None)
    return data


def _diff_existing(existing: dict[str, Any], row: NormalizedRow) -> dict[str, tuple[Any, Any]]:
    diffs: dict[str, tuple[Any, Any]] = {}
    incoming = _row_to_insert_dict(row)
    for field_name in _COMPARE_FIELDS:
        old = existing.get(field_name)
        new = incoming.get(field_name)
        if field_name in _FLOAT_FIELDS:
            if _float_equal(old, new):
                continue
        elif old == new:
            continue
        diffs[field_name] = (old, new)
    return diffs


def _float_equal(left: Any, right: Any) -> bool:
    if left is None and right is None:
        return True
    if left is None or right is None:
        return False
    try:
        return abs(float(left) - float(right)) < 0.01
    except (TypeError, ValueError):
        return False


def _archive_source_file(
    source_file: str,
    archive_root: Path,
    ts: str,
    basename: str,
) -> Path | None:
    archive_root.mkdir(parents=True, exist_ok=True)
    archive_path = archive_root / f"{ts}_{basename}"
    try:
        shutil.copy2(source_file, archive_path)
    except OSError as exc:
        logger.warning("failed to archive broker execution source file: %s", exc)
        return None
    return archive_path
