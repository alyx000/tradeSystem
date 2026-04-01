"""Schema 版本管理 + YAML 历史数据导入。"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

import yaml

from .schema import init_schema

logger = logging.getLogger(__name__)

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = SCRIPTS_DIR.parent


def get_schema_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def set_schema_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(f"PRAGMA user_version = {version}")


def migrate(conn: sqlite3.Connection) -> None:
    """自动迁移到最新 schema 版本。"""
    version = get_schema_version(conn)

    if version < 1:
        logger.info("Applying schema v1: initial tables + FTS + triggers")
        init_schema(conn)
        set_schema_version(conn, 1)
        conn.commit()

    # 未来新版本在此追加
    # if version < 2:
    #     _apply_v2(conn)
    #     set_schema_version(conn, 2)
    #     conn.commit()


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
        indices = data.get("indices", {})
        emotion = data.get("emotion", {})
        style = data.get("style_analysis", data.get("style", {}))
        capital = data.get("capital_flow", data.get("capital", {}))
        breadth = data.get("market_breadth", {})

        row = {
            "date": trade_date,
            "sh_index_close": _nested_get(indices, "sh_close", "shanghai", "close"),
            "sh_index_change_pct": _nested_get(indices, "sh_change_pct", "shanghai", "change_pct"),
            "sz_index_close": _nested_get(indices, "sz_close", "shenzhen", "close"),
            "sz_index_change_pct": _nested_get(indices, "sz_change_pct", "shenzhen", "change_pct"),
            "total_amount": data.get("total_amount") or data.get("total_volume"),
            "advance_count": breadth.get("advance_count") or breadth.get("up_count"),
            "decline_count": breadth.get("decline_count") or breadth.get("down_count"),
            "limit_up_count": emotion.get("limit_up_count"),
            "limit_down_count": emotion.get("limit_down_count"),
            "seal_rate": emotion.get("seal_rate"),
            "broken_rate": emotion.get("broken_rate"),
            "highest_board": emotion.get("highest_board"),
            "continuous_board_counts": emotion.get("continuous_board_counts"),
            "premium_10cm": _nested_get(style, "premium_10cm"),
            "premium_20cm": _nested_get(style, "premium_20cm"),
            "premium_second_board": _nested_get(style, "premium_second_board"),
            "northbound_net": _nested_get(capital, "northbound_net"),
            "margin_balance": _nested_get(capital, "margin_balance"),
            "market_breadth": breadth if breadth else None,
            "raw_data": data,
        }

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


def _nested_get(d: dict, *keys: str) -> Any:
    """从嵌套字典中尝试多种 key 路径获取值。"""
    for key in keys:
        if key in d:
            return d[key]
        parts = key.split(".")
        current = d
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                current = None
                break
        if current is not None:
            return current
    return None


def import_all(conn: sqlite3.Connection) -> dict[str, int]:
    """一键导入全部 YAML 历史数据。"""
    results = {
        "teacher_notes": import_teacher_notes(conn),
        "calendar": import_calendar(conn),
        "daily_market": import_daily_market(conn),
    }
    logger.info("Import complete: %s", results)
    return results
