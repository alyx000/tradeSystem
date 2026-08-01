"""宏观事件日历从预取收据幂等同步到 SQLite。

写入只由显式 CLI 入口调用；不直接服务报告生成，避免读路径隐式改库。
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from collectors.market import CalendarPrefetchResult


def _optional(value) -> str | None:
    text = str(value or "").strip()
    return text or None


def _db_values(event: dict, input_by: str) -> dict:
    return {
        "time": _optional(event.get("time")),
        "impact": _optional(event.get("impact") or event.get("importance")),
        "category": _optional(event.get("category")),
        "source": "auto_prefetch",
        "country": _optional(event.get("country") or event.get("region")),
        "prior": _optional(event.get("prior")),
        "expected": _optional(event.get("expected")),
        "actual": _optional(event.get("actual")),
        "note": f"input_by={input_by}",
    }


def sync_calendar_events(
    conn: sqlite3.Connection,
    events: tuple[dict, ...] | list[dict],
    *,
    input_by: str,
) -> dict:
    """按 ``(date,event)`` 幂等插入/刷新自动事件；人工同名事件永不覆盖。"""
    actor = str(input_by or "").strip()
    if not actor:
        raise ValueError("input_by 不能为空")

    inserted = updated = unchanged = manual_preserved = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        for event in events:
            event_date = str(event.get("date") or "").strip()
            event_text = str(event.get("event") or "").strip()
            if not event_date or not event_text:
                continue
            date.fromisoformat(event_date)
            existing = conn.execute(
                "SELECT id, time, impact, category, source, country, prior, expected, actual, note "
                "FROM calendar_events WHERE date = ? AND event = ? "
                "ORDER BY CASE WHEN source IN ('auto', 'auto_prefetch', 'akshare') "
                "THEN 1 ELSE 0 END, id LIMIT 1",
                (event_date, event_text),
            ).fetchone()
            values = _db_values(event, actor)
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO calendar_events (
                        date, time, event, impact, category, source,
                        country, prior, expected, actual, note
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_date,
                        values["time"],
                        event_text,
                        values["impact"],
                        values["category"],
                        values["source"],
                        values["country"],
                        values["prior"],
                        values["expected"],
                        values["actual"],
                        values["note"],
                    ),
                )
                inserted += 1
                continue

            source = str(existing["source"] or "") if isinstance(existing, sqlite3.Row) else str(existing[4] or "")
            if source not in {"auto", "auto_prefetch", "akshare"}:
                manual_preserved += 1
                continue
            current = {
                "time": existing["time"],
                "impact": existing["impact"],
                "category": existing["category"],
                "source": existing["source"],
                "country": existing["country"],
                "prior": existing["prior"],
                "expected": existing["expected"],
                "actual": existing["actual"],
                "note": existing["note"],
            }
            if current == values:
                unchanged += 1
                continue
            conn.execute(
                """
                UPDATE calendar_events
                SET time = ?, impact = ?, category = ?, source = ?,
                    country = ?, prior = ?, expected = ?, actual = ?, note = ?
                WHERE id = ?
                """,
                (
                    values["time"],
                    values["impact"],
                    values["category"],
                    values["source"],
                    values["country"],
                    values["prior"],
                    values["expected"],
                    values["actual"],
                    values["note"],
                    existing["id"],
                ),
            )
            updated += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    max_row = conn.execute("SELECT MAX(date) FROM calendar_events").fetchone()
    return {
        "inserted": inserted,
        "updated": updated,
        "unchanged": unchanged,
        "manual_preserved": manual_preserved,
        "db_max_date": max_row[0] if max_row else None,
    }


def coverage_receipt(result: CalendarPrefetchResult, *, required_days: int = 7) -> dict:
    """用 provider 返回事件的最大日期判断未来窗口是否真的被覆盖。"""
    if required_days < 1:
        raise ValueError("required_days 必须 >= 1")
    start = date.fromisoformat(result.from_date)
    requested_days = (date.fromisoformat(result.to_date) - start).days + 1
    window_days = min(required_days, requested_days)
    required_through = (start + timedelta(days=window_days - 1)).isoformat()
    complete = bool(
        result.fetched_count
        and result.coverage_end
        and result.coverage_end >= required_through
    )
    return {
        "status": "complete" if complete else "partial",
        "required_through": required_through,
        "coverage_end": result.coverage_end,
        "fetched_count": result.fetched_count,
    }
