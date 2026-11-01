"""上海交易时段、交易日历与实时行情新鲜度守卫。"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from db.connection import get_readonly_connection
from db.queries import is_trade_day_from_db
from utils.realtime_quote import (
    SHANGHAI_TZ, MAX_QUOTE_AGE, MAX_FUTURE_SKEW,
    shanghai_now, _parse_quote_date, quote_datetime, quote_is_fresh,
)


# launchd 按分钟唤起，进程通常会晚数秒进入 Python。结束边界保留到下一
# 分钟（不含），确保 11:30 / 15:00 这一分钟的 tick 能真正执行。
MORNING = (time(9, 30), time(11, 31))
AFTERNOON = (time(13, 0), time(15, 1))
CLOSE_FINALIZATION = (time(15, 0), time(15, 6))


def is_intraday_session(now: datetime) -> bool:
    local = shanghai_now(now)
    current = local.time().replace(tzinfo=None)
    return MORNING[0] <= current < MORNING[1] or AFTERNOON[0] <= current < AFTERNOON[1]


def is_close_finalization_window(now: datetime) -> bool:
    """允许相对 5 分钟节拍在收盘后补取一次 15:00 终态行情。"""
    local = shanghai_now(now)
    current = local.time().replace(tzinfo=None)
    return CLOSE_FINALIZATION[0] <= current < CLOSE_FINALIZATION[1]


def confirmed_trade_day(day: str, db_path=None) -> bool | None:
    """只信本地交易日历；缺库、缺表或缺日期时返回 None（fail-closed）。"""
    try:
        conn = get_readonly_connection(db_path)
    except Exception:
        return None
    try:
        return is_trade_day_from_db(conn, day)
    except Exception:
        return None
    finally:
        conn.close()


def previous_open_dates(day: str, limit: int, db_path=None) -> list[str] | None:
    """读取 day 之前最近的开放日；日历不足或不可读时 fail-closed。"""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        return None
    try:
        conn = get_readonly_connection(db_path)
    except Exception:
        return None
    try:
        rows = conn.execute(
            "SELECT date FROM trade_calendar "
            "WHERE date < ? AND is_open = 1 ORDER BY date DESC LIMIT ?",
            (day, limit),
        ).fetchall()
        if len(rows) != limit:
            return None
        descending_dates = [
            str(row["date"] if hasattr(row, "keys") else row[0]) for row in rows
        ]
        try:
            start_day = date.fromisoformat(descending_dates[-1])
            end_day = date.fromisoformat(day)
        except ValueError:
            return None
        if start_day >= end_day:
            return None
        coverage_rows = conn.execute(
            "SELECT date FROM trade_calendar WHERE date >= ? AND date < ? ORDER BY date",
            (start_day.isoformat(), end_day.isoformat()),
        ).fetchall()
    except Exception:
        return None
    finally:
        conn.close()
    expected_natural_dates: list[str] = []
    cursor = start_day
    while cursor < end_day:
        expected_natural_dates.append(cursor.isoformat())
        cursor += timedelta(days=1)
    actual_natural_dates = [
        str(row["date"] if hasattr(row, "keys") else row[0]) for row in coverage_rows
    ]
    if actual_natural_dates != expected_natural_dates:
        return None
    dates = descending_dates
    dates.reverse()
    return dates
