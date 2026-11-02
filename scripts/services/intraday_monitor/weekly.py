"""只读完整开放日历，锁定已完成交易周末价与当前昨收锚。"""
from __future__ import annotations

from datetime import date, timedelta

from db.connection import get_readonly_connection


def weekly_history_dates(day: str, week_count: int, *, db_path=None) -> tuple[list[str], list[str]] | None:
    if isinstance(week_count, bool) or not isinstance(week_count, int) or not 1 <= week_count <= 52:
        return None
    try:
        today = date.fromisoformat(day)
        monday = today - timedelta(days=today.weekday())
        start = monday - timedelta(weeks=week_count + 8)
        conn = get_readonly_connection(db_path)
    except Exception:
        return None
    try:
        rows = conn.execute("SELECT date,is_open FROM trade_calendar WHERE exchange='SSE' "
                            "AND date>=? AND date<? ORDER BY date", (str(start), day)).fetchall()
    except Exception:
        return None
    finally:
        conn.close()
    expected = [str(start + timedelta(days=i)) for i in range((today-start).days)]
    if [row["date"] for row in rows] != expected or any(row["is_open"] not in (0, 1) for row in rows):
        return None
    open_dates = [row["date"] for row in rows if row["is_open"] == 1]
    ends = {}
    for td in open_dates:
        d = date.fromisoformat(td)
        if d < monday:
            ends[d.isocalendar()[:2]] = td
    basis = list(ends.values())[-week_count:]
    if len(basis) != week_count or not open_dates:
        return None
    first = date.fromisoformat(basis[0])
    first_monday = str(first - timedelta(days=first.weekday()))
    # 全区间日线/因子必须齐全，末日始终为严格上一开放日，不能把周五当昨收锚。
    return [td for td in open_dates if td >= first_monday], basis
