from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

import yaml

from services.low_price_effect_history import (
    backfill_history,
    build_trend_rows,
    persist_date_snapshot,
    select_trade_dates,
    write_trend_artifacts,
)


def _complete_snapshot(trade_date: str, *, median: float = 1.2) -> dict:
    return {
        "status": "complete",
        "trade_date": trade_date,
        "gaps": [],
        "coverage": {"unique_quote_count": 5300},
        "low_price": {
            "sample_count": 1600,
            "pct_chg_median": median,
            "median_excess_vs_market_pp": 0.7,
            "advance_rate": 0.62,
            "amount_share_pct": 18.5,
            "strong_gain_rate": 0.05,
            "strong_loss_rate": 0.01,
            "limit_up_rate": 0.02,
            "limit_down_rate": 0.003,
        },
        "market_benchmark": {"pct_chg_median": 0.5},
    }


def _write_envelope(daily_dir: Path, trade_date: str, block: dict | None = None) -> Path:
    path = daily_dir / trade_date / "post-market.yaml"
    path.parent.mkdir(parents=True)
    raw_data = {"indices": {"shanghai": {"close": 3000}}}
    if block is not None:
        raw_data["low_price_effect"] = block
    path.write_text(
        yaml.safe_dump(
            {"date": trade_date, "generated_at": "test", "raw_data": raw_data},
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def test_select_trade_dates_uses_open_calendar_and_returns_ascending():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE trade_calendar(date TEXT PRIMARY KEY, is_open INTEGER)")
    conn.executemany(
        "INSERT INTO trade_calendar(date, is_open) VALUES (?, ?)",
        [
            ("2026-08-28", 1),
            ("2026-08-29", 0),
            ("2026-08-31", 1),
            ("2026-09-01", 1),
        ],
    )

    assert select_trade_dates(conn, "2026-09-01", 3) == [
        "2026-08-28",
        "2026-08-31",
        "2026-09-01",
    ]


def test_persist_date_snapshot_atomically_updates_yaml_and_syncs_db(tmp_path):
    daily_dir = tmp_path / "daily"
    trade_date = "2026-09-01"
    path = _write_envelope(daily_dir, trade_date)
    synced: list[tuple[str, dict]] = []

    receipt = persist_date_snapshot(
        trade_date,
        _complete_snapshot(trade_date),
        input_by="pytest",
        daily_dir=daily_dir,
        sync_fn=lambda date, envelope: synced.append((date, envelope)) or True,
    )

    stored = yaml.safe_load(path.read_text(encoding="utf-8"))
    block = stored["raw_data"]["low_price_effect"]
    assert receipt["status"] == "persisted"
    assert receipt["db_synced"] is True
    assert block["status"] == "complete"
    assert block["collection_receipt"]["input_by"] == "pytest"
    assert synced[0][0] == trade_date
    assert synced[0][1]["raw_data"]["low_price_effect"] == block


def test_backfill_refetch_failure_never_downgrades_existing_complete(tmp_path):
    daily_dir = tmp_path / "daily"
    trade_date = "2026-09-01"
    original = _complete_snapshot(trade_date)
    path = _write_envelope(daily_dir, trade_date, original)
    sync_calls: list[tuple] = []

    receipts = backfill_history(
        object(),
        [trade_date],
        input_by="pytest",
        refetch=True,
        daily_dir=daily_dir,
        collect_fn=lambda _registry, _date: {
            "status": "source_failed",
            "trade_date": trade_date,
            "error": "network down",
        },
        sync_fn=lambda *args: sync_calls.append(args) or True,
    )

    stored = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert receipts == [
        {
            "status": "fallback_preserved",
            "trade_date": trade_date,
            "source_status": "source_failed",
            "preserved_status": "complete",
            "error": "network down",
            "db_synced": True,
        }
    ]
    assert stored["raw_data"]["low_price_effect"] == original
    assert sync_calls[0][0] == trade_date


def test_backfill_skips_existing_complete_without_refetch(tmp_path):
    daily_dir = tmp_path / "daily"
    trade_date = "2026-09-01"
    _write_envelope(daily_dir, trade_date, _complete_snapshot(trade_date))
    called = False

    def _collect(_registry, _date):
        nonlocal called
        called = True
        return {}

    receipts = backfill_history(
        object(),
        [trade_date],
        input_by="pytest",
        daily_dir=daily_dir,
        collect_fn=_collect,
        sync_fn=lambda *_args: True,
    )

    assert receipts[0]["status"] == "already_complete"
    assert receipts[0]["db_synced"] is True
    assert called is False


def test_trend_rejects_malformed_complete_archive(tmp_path):
    daily_dir = tmp_path / "daily"
    trade_date = "2026-09-01"
    malformed = _complete_snapshot(trade_date)
    malformed["low_price"].pop("amount_share_pct")
    _write_envelope(daily_dir, trade_date, malformed)

    row = build_trend_rows([trade_date], daily_dir=daily_dir)[0]

    assert row["status"] == "archive_invalid"
    assert row["chart_eligible"] is False
    assert "amount_share_pct" in row["gaps"]


def test_trend_artifacts_keep_partial_as_gap_and_write_png_csv_json(tmp_path):
    daily_dir = tmp_path / "daily"
    dates = ["2026-08-31", "2026-09-01"]
    _write_envelope(daily_dir, dates[0], _complete_snapshot(dates[0], median=-0.4))
    partial = _complete_snapshot(dates[1], median=1.2)
    partial["status"] = "partial"
    partial["gaps"] = ["涨停来源失败"]
    _write_envelope(daily_dir, dates[1], partial)

    rows = build_trend_rows(dates, daily_dir=daily_dir)
    paths = write_trend_artifacts(rows, report_dir=tmp_path / "reports")

    assert rows[0]["chart_eligible"] is True
    assert rows[1]["chart_eligible"] is False
    assert rows[1]["gaps"] == "涨停来源失败"
    png = Path(paths["png_path"])
    assert png.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    with Path(paths["csv_path"]).open(encoding="utf-8") as handle:
        csv_rows = list(csv.DictReader(handle))
    assert [row["status"] for row in csv_rows] == ["complete", "partial"]
    payload = json.loads(Path(paths["json_path"]).read_text(encoding="utf-8"))
    assert payload["definition"]["chart_rule"].startswith("仅 status=complete")
