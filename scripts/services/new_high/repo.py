from __future__ import annotations

import json
import sqlite3


def save_daily_stats(conn: sqlite3.Connection, record: dict) -> None:
    conn.execute(
        """
        INSERT INTO daily_new_high_stats (
            date, market_count, new_high_count, sector_summary_json,
            stocks_json, source_json, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(date) DO UPDATE SET
            market_count = excluded.market_count,
            new_high_count = excluded.new_high_count,
            sector_summary_json = excluded.sector_summary_json,
            stocks_json = excluded.stocks_json,
            source_json = excluded.source_json,
            updated_at = excluded.updated_at
        """,
        (
            record["date"],
            int(record["market_count"]),
            int(record["new_high_count"]),
            json.dumps(record.get("sector_summary", []), ensure_ascii=False),
            json.dumps(record.get("stocks", []), ensure_ascii=False),
            json.dumps(record.get("source"), ensure_ascii=False)
            if record.get("source") is not None
            else None,
        ),
    )
    conn.commit()


def _daily_row_to_record(row: sqlite3.Row) -> dict:
    return {
        "date": row["date"],
        "market_count": row["market_count"],
        "new_high_count": row["new_high_count"],
        "sector_summary": json.loads(row["sector_summary_json"] or "[]"),
        "stocks": json.loads(row["stocks_json"] or "[]"),
        "source": json.loads(row["source_json"]) if row["source_json"] else None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get_daily_stats(conn: sqlite3.Connection, date: str) -> dict | None:
    row = conn.execute("SELECT * FROM daily_new_high_stats WHERE date = ?", (date,)).fetchone()
    return _daily_row_to_record(row) if row else None


def get_recent_stats(conn: sqlite3.Connection, end_date: str, days: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM daily_new_high_stats WHERE date <= ? ORDER BY date DESC LIMIT ?",
        (end_date, max(1, int(days))),
    ).fetchall()
    return [_daily_row_to_record(r) for r in reversed(rows)]


def get_watermarks(conn: sqlite3.Connection, codes: list[str]) -> dict[str, dict]:
    if not codes:
        return {}
    placeholders = ",".join("?" for _ in codes)
    rows = conn.execute(
        f"SELECT * FROM stock_adjusted_high_watermark WHERE code IN ({placeholders})",
        tuple(codes),
    ).fetchall()
    return {r["code"]: dict(r) for r in rows}


def upsert_watermark(conn: sqlite3.Connection, item: dict) -> None:
    upsert_watermarks(conn, [item])


def upsert_watermarks(conn: sqlite3.Connection, items: list[dict]) -> None:
    if not items:
        return
    conn.executemany(
        """
        INSERT INTO stock_adjusted_high_watermark (
            code, name, max_adj_high, max_high_date, max_raw_high,
            last_seen_date, industry, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(code) DO UPDATE SET
            name = excluded.name,
            max_adj_high = excluded.max_adj_high,
            max_high_date = excluded.max_high_date,
            max_raw_high = excluded.max_raw_high,
            last_seen_date = excluded.last_seen_date,
            industry = excluded.industry,
            updated_at = excluded.updated_at
        """,
        [
            (
                item["code"],
                item.get("name"),
                float(item["max_adj_high"]),
                item["max_high_date"],
                item.get("max_raw_high"),
                item["last_seen_date"],
                item.get("industry"),
            )
            for item in items
        ],
    )
    conn.commit()
