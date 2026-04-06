"""Schema 版本管理 + YAML 历史数据导入。"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

import yaml

from .dual_write import _extract_market_row
from .schema import holding_code_norm_sql, init_schema

logger = logging.getLogger(__name__)

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = SCRIPTS_DIR.parent


def get_schema_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def set_schema_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(f"PRAGMA user_version = {version}")


def _close_duplicate_active_holdings(conn: sqlite3.Connection) -> int:
    """清理历史重复 active 持仓：同归一化代码只保留最新一条。"""
    norm_expr = holding_code_norm_sql("stock_code")
    dup_norms = conn.execute(
        f"""
        SELECT {norm_expr} AS norm_code, COUNT(*) AS cnt
        FROM holdings
        WHERE status = 'active'
        GROUP BY {norm_expr}
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    closed = 0
    for row in dup_norms:
        rows = conn.execute(
            f"""
            SELECT id
            FROM holdings
            WHERE status = 'active' AND {norm_expr} = ?
            ORDER BY (updated_at IS NOT NULL) DESC, updated_at DESC, id DESC
            """,
            (row["norm_code"],),
        ).fetchall()
        keep_id = rows[0]["id"]
        for dup in rows[1:]:
            conn.execute("UPDATE holdings SET status = 'closed' WHERE id = ?", (dup["id"],))
            closed += 1
        logger.info(
            "Closed duplicate active holdings for %s: kept id=%s, closed=%d",
            row["norm_code"], keep_id, max(len(rows) - 1, 0),
        )
    return closed


def migrate(conn: sqlite3.Connection) -> None:
    """自动迁移到最新 schema 版本。"""
    version = get_schema_version(conn)

    if version < 1:
        logger.info("Applying schema v1: initial tables + FTS + triggers")
        init_schema(conn)
        set_schema_version(conn, 1)
        conn.commit()
        version = get_schema_version(conn)

    if version < 2:
        logger.info("Applying schema v2: daily_market.premium_30cm")
        cols = {row[1] for row in conn.execute("PRAGMA table_info(daily_market)").fetchall()}
        if "premium_30cm" not in cols:
            conn.execute("ALTER TABLE daily_market ADD COLUMN premium_30cm REAL")
        set_schema_version(conn, 2)
        conn.commit()

    if version < 3:
        logger.info("Applying schema v3: daily_market node_signals/top_volume_stocks/etf_flow/hk_indices")
        cols = {row[1] for row in conn.execute("PRAGMA table_info(daily_market)").fetchall()}
        for col, coltype in [
            ("node_signals", "TEXT"),
            ("top_volume_stocks", "TEXT"),
            ("etf_flow", "TEXT"),
            ("hk_indices", "TEXT"),
        ]:
            if col not in cols:
                conn.execute(f"ALTER TABLE daily_market ADD COLUMN {col} {coltype}")
        set_schema_version(conn, 3)
        conn.commit()
        version = get_schema_version(conn)

    if version < 4:
        logger.info("Applying schema v4: dedupe active holdings + unique normalized index")
        closed = _close_duplicate_active_holdings(conn)
        conn.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS idx_holdings_active_norm_unique "
            f"ON holdings ({holding_code_norm_sql()}) WHERE status = 'active'"
        )
        set_schema_version(conn, 4)
        conn.commit()
        logger.info("Schema v4 complete: closed duplicate active holdings=%d", closed)
        version = get_schema_version(conn)

    if version < 5:
        logger.info("Applying schema v5: raw fact layer + ingest audit tables")
        init_schema(conn)
        set_schema_version(conn, 5)
        conn.commit()
        version = get_schema_version(conn)

    if version < 6:
        logger.info("Applying schema v6: planning layer tables")
        init_schema(conn)
        set_schema_version(conn, 6)
        conn.commit()
        version = get_schema_version(conn)

    if version < 7:
        logger.info("Applying schema v7: knowledge assets table")
        init_schema(conn)
        set_schema_version(conn, 7)
        conn.commit()
        version = get_schema_version(conn)

    if version < 8:
        logger.info("Applying schema v8: mark permission-like ingest errors as non-retryable")
        conn.execute(
            """
            UPDATE ingest_errors
            SET retryable = 0
            WHERE retryable = 1
              AND (
                    error_message LIKE '%权限不足%'
                 OR error_message LIKE '%积分不足%'
                 OR error_message LIKE '%token不对%'
              )
            """
        )
        set_schema_version(conn, 8)
        conn.commit()

    if version < 9:
        logger.info("Applying schema v9: holding_tasks table")
        init_schema(conn)
        set_schema_version(conn, 9)
        conn.commit()
        version = get_schema_version(conn)

    if version < 10:
        logger.info("Applying schema v10: holding_quote_snapshots table")
        init_schema(conn)
        set_schema_version(conn, 10)
        conn.commit()


# ──────────────────────────────────────────────────────────────
# YAML 数据导入
# ──────────────────────────────────────────────────────────────

def _load_yaml(path: Path) -> Any:
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def import_teacher_notes(conn: sqlite3.Connection,
                         yaml_path: Path | None = None) -> int:
    """从 tracking/teacher-notes.yaml 导入老师笔记。返回导入数量。"""
    path = yaml_path or PROJECT_ROOT / "tracking" / "teacher-notes.yaml"
    data = _load_yaml(path)
    if not data or "teachers" not in data:
        logger.warning("No teacher notes found at %s", path)
        return 0

    count = 0
    for teacher_data in data["teachers"]:
        name = teacher_data.get("name", "")
        if not name:
            continue
        row = conn.execute("SELECT id FROM teachers WHERE name = ?", (name,)).fetchone()
        if row:
            teacher_id = row[0]
        else:
            cur = conn.execute(
                "INSERT INTO teachers (name, platform, schedule) VALUES (?, ?, ?)",
                (name, teacher_data.get("platform"), teacher_data.get("schedule")),
            )
            teacher_id = cur.lastrowid

        for note in teacher_data.get("notes", []):
            title = note.get("title", "")
            note_date = str(note.get("date", ""))
            if not title or not note_date:
                continue

            existing = conn.execute(
                "SELECT id FROM teacher_notes WHERE teacher_id = ? AND date = ? AND title = ?",
                (teacher_id, note_date, title),
            ).fetchone()
            if existing:
                continue

            def _json_or_none(v: Any) -> str | None:
                if v is None:
                    return None
                if isinstance(v, (list, dict)):
                    return json.dumps(v, ensure_ascii=False)
                return str(v)

            conn.execute(
                """INSERT INTO teacher_notes
                   (teacher_id, date, title, source_type, input_by, core_view,
                    position_advice, tags, key_points, sectors, avoid, raw_content)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    teacher_id, note_date, title,
                    note.get("source_type", "text"),
                    note.get("input_by", "manual"),
                    note.get("core_view"),
                    note.get("position_advice"),
                    _json_or_none(note.get("tags")),
                    _json_or_none(note.get("key_points")),
                    _json_or_none(note.get("sectors")),
                    _json_or_none(note.get("avoid")),
                    note.get("raw_content"),
                ),
            )
            count += 1

    conn.commit()
    logger.info("Imported %d teacher notes", count)
    return count


def import_calendar(conn: sqlite3.Connection,
                    manual_path: Path | None = None,
                    auto_path: Path | None = None) -> int:
    """从 tracking/calendar.yaml + calendar_auto.yaml 导入日历事件。"""
    count = 0
    for path, default_source in [
        (manual_path or PROJECT_ROOT / "tracking" / "calendar.yaml", "manual"),
        (auto_path or PROJECT_ROOT / "tracking" / "calendar_auto.yaml", "auto_prefetch"),
    ]:
        data = _load_yaml(path)
        if not data:
            continue
        events = data if isinstance(data, list) else data.get("events", [])
        if not events:
            continue
        for evt in events:
            evt_date = str(evt.get("date", ""))
            event_text = evt.get("event", "")
            if not evt_date or not event_text:
                continue

            existing = conn.execute(
                "SELECT id FROM calendar_events WHERE date = ? AND event = ?",
                (evt_date, event_text),
            ).fetchone()
            if existing:
                continue

            conn.execute(
                """INSERT INTO calendar_events
                   (date, time, event, impact, category, source, country, prior, expected, actual, note)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    evt_date, evt.get("time"), event_text,
                    evt.get("impact"), evt.get("category"),
                    evt.get("source", default_source),
                    evt.get("country"),
                    evt.get("prior"), evt.get("expected"),
                    evt.get("actual"), evt.get("note"),
                ),
            )
            count += 1

    conn.commit()
    logger.info("Imported %d calendar events", count)
    return count


def import_daily_market(conn: sqlite3.Connection,
                        daily_dir: Path | None = None) -> int:
    """从 daily/*/post-market.yaml 导入行情数据。"""
    base = daily_dir or PROJECT_ROOT / "daily"
    if not base.exists():
        return 0

    count = 0
    for day_dir in sorted(base.iterdir()):
        if not day_dir.is_dir():
            continue
        pm_path = day_dir / "post-market.yaml"
        data = _load_yaml(pm_path)
        if not data:
            continue

        trade_date = day_dir.name
        row = _extract_market_row(trade_date, data)

        existing = conn.execute(
            "SELECT date FROM daily_market WHERE date = ?", (trade_date,)
        ).fetchone()
        if existing:
            continue

        from .queries import upsert_daily_market
        upsert_daily_market(conn, row)
        count += 1

    conn.commit()
    logger.info("Imported %d daily market records", count)
    return count


def import_all(conn: sqlite3.Connection) -> dict[str, int]:
    """一键导入全部 YAML 历史数据。"""
    results = {
        "teacher_notes": import_teacher_notes(conn),
        "calendar": import_calendar(conn),
        "daily_market": import_daily_market(conn),
    }
    logger.info("Import complete: %s", results)
    return results
