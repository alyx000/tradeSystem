"""executions CLI 输出与审计报告单元测试。"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from cli.executions import _build_audit_report, _emit_import_result
from services.broker_executions.models import ConflictRow, ErrorRow, RowSummary


def test_audit_report_appends_per_stock_and_import_batches() -> None:
    rows = [
        {
            "biz_date": "2026-04-01",
            "exec_time": "09:30:00",
            "account_id": "default",
            "stock_code": "002594",
            "stock_name": "比亚迪",
            "direction": "buy",
            "shares": 100,
            "price": 10,
            "amount": 1000,
            "total_fees": 1,
            "import_run_id": "run_a",
            "imported_at": "2026-05-01 10:00:00",
            "source_file": "trade_a.tsv",
            "source_archive_path": "tmp/imports/trade_a.tsv",
        },
        {
            "biz_date": "2026-04-02",
            "exec_time": "10:30:00",
            "account_id": "default",
            "stock_code": "002594",
            "stock_name": "比亚迪",
            "direction": "sell",
            "shares": 40,
            "price": 11,
            "amount": 440,
            "total_fees": 0.5,
            "import_run_id": "run_a",
            "imported_at": "2026-05-01 10:05:00",
            "source_file": "trade_a.tsv",
            "source_archive_path": "tmp/imports/trade_a.tsv",
        },
        {
            "biz_date": "2026-04-03",
            "exec_time": "11:30:00",
            "account_id": "default",
            "stock_code": "600519",
            "stock_name": "贵州茅台",
            "direction": "buy",
            "shares": 50,
            "price": 20,
            "amount": 1000,
            "total_fees": 2,
            "import_run_id": "run_b",
            "imported_at": "2026-05-02 10:00:00",
            "source_file": "trade_b.tsv",
            "source_archive_path": "",
        },
    ]
    args = SimpleNamespace(date_from="2026-04-01", date_to="2026-05-31", account=None)

    report = _build_audit_report(rows, args)

    assert "## Summary" in report
    assert "## Per Stock" in report
    assert "## Import Batches" in report
    assert "## Rows" in report
    assert "| 002594 | 2 | 60 | 10.286 | 1.500 |" in report
    assert "| run_a | 2026-05-01 10:00:00~2026-05-01 10:05:00 | trade_a.tsv | 2 | tmp/imports/trade_a.tsv |" in report


def test_emit_import_result_prints_full_conflicts_errors_and_paths(capsys) -> None:
    summary = RowSummary(
        row_index=7,
        biz_date="2026-04-01",
        exec_time="09:30:00",
        stock_code="002594",
        stock_name="比亚迪",
        direction="buy",
        shares=100,
        price=10,
        broker_trade_no="T001",
    )
    payload = {
        "parsed": 2,
        "normalized": 1,
        "inserted": 0,
        "skipped": 3,
        "conflicts": 1,
        "conflict_rows": [
            ConflictRow(summary=summary, diffs={"commission": (1.0, 2.0)}),
        ],
        "degraded": 0,
        "failed": 1,
        "error_rows": [
            ErrorRow(row_index=8, reason="缺少证券代码", raw={"证券名称": "空"}),
        ],
        "dry_run": True,
        "source_format": "tsv-gbk",
        "import_run_id": "run_x",
        "archive_path": None,
        "report_path": "tmp/import-reports/report.md",
    }

    _emit_import_result(payload, Path("trade.tsv"), as_json=False)

    output = capsys.readouterr().out
    assert "skipped: 3（明细见 markdown 报告）" in output
    assert "archive_path:" in output
    assert "report_path: tmp/import-reports/report.md" in output
    # conflict 行需包含完整 RowSummary 字段：row_index/biz_date/exec_time/
    # stock_code/stock_name/direction/shares/price/broker_trade_no
    assert (
        "row_index: 7 biz_date: 2026-04-01 exec_time: 09:30:00 "
        "stock_code: 002594 stock_name: 比亚迪 direction: buy "
        "shares: 100 @ 10.000 broker_trade_no: T001"
    ) in output
    assert "commission: 1.0 -> 2.0" in output
    # error 行需包含 row_index/reason + 关键 raw 字段
    assert "row_index: 8 reason: 缺少证券代码" in output
    assert "raw: {'证券名称': '空'}" in output
