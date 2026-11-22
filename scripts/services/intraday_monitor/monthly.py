"""只读完整上交所日历，定位已完成月份的最后开放日。"""
from __future__ import annotations

from datetime import date, timedelta

from db.connection import get_readonly_connection


def monthly_history_dates(day: str, month_count: int, *, db_path=None) -> tuple[list[str], list[str]] | None:
    if isinstance(month_count, bool) or not isinstance(month_count, int) or not 1 <= month_count <= 60:
        return None
    try:
        today = date.fromisoformat(day)
        current_month = today.year * 12 + today.month - 1
        year, month0 = divmod(current_month - month_count, 12)
        start = date(year, month0 + 1, 1)
    except (ValueError, TypeError):
        return None
    try:
        conn = get_readonly_connection(db_path)
    except Exception:
        return None
    try:
        rows = conn.execute(
            "SELECT date,is_open FROM trade_calendar WHERE exchange='SSE' "
            "AND date>=? AND date<? ORDER BY date", (start.isoformat(), today.isoformat()),
        ).fetchall()
    except Exception:
        return None
    finally:
        conn.close()
    expected = [(start + timedelta(days=i)).isoformat() for i in range((today - start).days)]
    if [row["date"] for row in rows] != expected or any(row["is_open"] not in (0, 1) for row in rows):
        return None
    history = [row["date"] for row in rows if row["is_open"] == 1]
    current_ym = today.isoformat()[:7]
    month_ends = {td[:7]: td for td in history if td[:7] < current_ym}
    if len(month_ends) != month_count:
        return None
    # 取完整开放日序列以校验日线、因子和严格上一开放日的昨收锚；
    # 均线样本只取历史月末点，不把本月已发生的日收盘作为额外样本。
    return history, list(month_ends.values())
