from __future__ import annotations

import json
import sqlite3

from services.monthly_pattern import valuation


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE raw_interface_payloads (
            id INTEGER PRIMARY KEY,
            interface_name TEXT NOT NULL,
            provider TEXT NOT NULL,
            target_date TEXT,
            payload_json TEXT NOT NULL,
            row_count INTEGER NOT NULL,
            status TEXT NOT NULL
        )
        """
    )
    return conn


def _save_snapshot(
    conn: sqlite3.Connection,
    rows: list[dict],
    *,
    target_date: str = "2026-07-31",
    receipt_count: int | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO raw_interface_payloads (
            interface_name, provider, target_date, payload_json, row_count, status
        ) VALUES ('daily_basic', 'tushare:daily_basic', ?, ?, ?, 'success')
        """,
        (
            target_date,
            json.dumps({"rows": rows}),
            len(rows) if receipt_count is None else receipt_count,
        ),
    )


def test_valuation_uses_pb_for_financial_industry_and_industry_percentile() -> None:
    conn = _connection()
    rows = [
        {
            "ts_code": f"{index:06d}.SZ",
            "pb": float(index),
            "pe_ttm": float(index + 20),
            "ps_ttm": float(index + 10),
        }
        for index in range(1, 11)
    ]
    _save_snapshot(conn, rows)
    industry_map = {
        f"{index:06d}": {"sw_l2": "股份制银行Ⅱ"}
        for index in range(1, 11)
    }

    views, meta = valuation.load_industry_valuation_views(
        conn,
        "2026-08-01",
        industry_map=industry_map,
        market_codes=set(industry_map),
    )

    assert meta["status"] == "success"
    assert meta["market_coverage"] == 1.0
    assert views["000001"]["metric"] == "pb"
    assert views["000001"]["industry_percentile"] == 10.0
    assert views["000010"]["industry_percentile"] == 100.0


def test_valuation_uses_positive_pe_ttm_then_ps_ttm_fallback() -> None:
    conn = _connection()
    rows = []
    industry_map = {}
    for index in range(1, 7):
        code = f"300{index:03d}"
        rows.append(
            {
                "ts_code": f"{code}.SZ",
                "pe_ttm": float(index * 10),
                "ps_ttm": float(index),
                "pb": 2.0,
            }
        )
        industry_map[code] = {"sw_l2": "通信设备"}
    loss_code = "300007"
    rows.append(
        {
            "ts_code": f"{loss_code}.SZ",
            "pe_ttm": -5.0,
            "ps_ttm": 1.5,
            "pb": 2.0,
        }
    )
    industry_map[loss_code] = {"sw_l2": "通信设备"}
    for index in range(8, 13):
        code = f"300{index:03d}"
        rows.append(
            {
                "ts_code": f"{code}.SZ",
                "pe_ttm": -float(index),
                "ps_ttm": float(index),
                "pb": 2.0,
            }
        )
        industry_map[code] = {"sw_l2": "通信设备"}
    _save_snapshot(conn, rows)

    views, _meta = valuation.load_industry_valuation_views(
        conn,
        "2026-08-01",
        industry_map=industry_map,
        market_codes=set(industry_map),
    )

    assert views["300001"]["metric"] == "pe_ttm"
    assert views[loss_code]["metric"] == "ps_ttm"
    assert views[loss_code]["status"] == "success"


def test_valuation_fails_closed_for_stale_or_receipt_mismatch() -> None:
    stale = _connection()
    _save_snapshot(stale, [{"ts_code": "000001.SZ", "pb": 1.0}], target_date="2026-07-01")
    views, meta = valuation.load_industry_valuation_views(
        stale,
        "2026-08-01",
        industry_map={"000001": {"sw_l2": "银行"}},
        market_codes={"000001"},
    )
    assert views == {}
    assert meta["status"] == "stale"

    broken = _connection()
    _save_snapshot(
        broken,
        [{"ts_code": "000001.SZ", "pb": 1.0}],
        receipt_count=2,
    )
    views, meta = valuation.load_industry_valuation_views(
        broken,
        "2026-08-01",
        industry_map={"000001": {"sw_l2": "银行"}},
        market_codes={"000001"},
    )
    assert views == {}
    assert meta["status"] == "source_failed"
    assert "行数" in meta["reason"]


def test_valuation_rejects_incomplete_market_snapshot() -> None:
    conn = _connection()
    _save_snapshot(conn, [{"ts_code": "000001.SZ", "pb": 1.0}])
    market_codes = {f"{index:06d}" for index in range(1, 11)}

    views, meta = valuation.load_industry_valuation_views(
        conn,
        "2026-08-01",
        industry_map={code: {"sw_l2": "银行"} for code in market_codes},
        market_codes=market_codes,
    )

    assert views == {}
    assert meta["status"] == "coverage_failed"
