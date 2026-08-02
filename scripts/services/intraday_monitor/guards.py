"""上海交易时段、交易日历与实时行情新鲜度守卫。"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from db.connection import get_readonly_connection
from db.queries import is_trade_day_from_db


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
MAX_QUOTE_AGE = timedelta(minutes=10)
MAX_FUTURE_SKEW = timedelta(minutes=2)
MORNING = (time(9, 30), time(11, 30))
AFTERNOON = (time(13, 0), time(15, 0))


def shanghai_now(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(SHANGHAI_TZ)
    if now.tzinfo is None:
        return now.replace(tzinfo=SHANGHAI_TZ)
    return now.astimezone(SHANGHAI_TZ)


def is_intraday_session(now: datetime) -> bool:
    local = shanghai_now(now)
    current = local.time().replace(tzinfo=None)
    return MORNING[0] <= current <= MORNING[1] or AFTERNOON[0] <= current <= AFTERNOON[1]


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


def _parse_quote_date(raw: object) -> date | None:
    text = str(raw or "").strip()
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def quote_datetime(quote: dict) -> datetime | None:
    quote_day = _parse_quote_date(quote.get("quote_date"))
    raw_time = str(quote.get("quote_time") or "").strip()
    if quote_day is None:
        return None
    try:
        quote_time = time.fromisoformat(raw_time)
    except ValueError:
        return None
    return datetime.combine(quote_day, quote_time, tzinfo=SHANGHAI_TZ)


def quote_is_fresh(quote: dict, now: datetime) -> tuple[bool, str | None, datetime | None]:
    local = shanghai_now(now)
    quoted_at = quote_datetime(quote)
    if quoted_at is None:
        return False, "行情时间字段缺失或非法", None
    if quoted_at.date() != local.date():
        return False, f"行情日期陈旧: {quoted_at.date().isoformat()}", quoted_at
    age = local - quoted_at
    if age > MAX_QUOTE_AGE:
        return False, f"行情已陈旧 {int(age.total_seconds())} 秒", quoted_at
    if age < -MAX_FUTURE_SKEW:
        return False, f"行情时间超前 {int(-age.total_seconds())} 秒", quoted_at
    return True, None, quoted_at
