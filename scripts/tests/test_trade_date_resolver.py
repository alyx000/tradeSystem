"""resolve_latest_closed_trade_date：strict calendar path 的"最新已收盘交易日"闸门。

spec v8：日历须显式覆盖目标年；≥15:30 交易日返当日、否则上一 open 日；不可确认返 None
（blocked，调用方必须不推送）；禁止 weekday/昨天 fallback；now 统一 Asia/Shanghai。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest
from zoneinfo import ZoneInfo

from db import queries as Q
from db.connection import get_connection
from db.schema import init_schema
from utils.trade_date import resolve_latest_closed_trade_date

SH = ZoneInfo("Asia/Shanghai")


def _seed_2026_calendar(c):
    """seed 全年 365 行(工作日=开市、周末=休市)。trade_calendar_year_covered 判定是
    "该年 ≥200 行",只 seed 关键几天会恒 not covered → 闸门全返 None。工作日近似对
    本测试关键日期成立:7/17 五开、7/18-19 休、7/20 一开、7/21 二开。"""
    import datetime as _dt
    rows = []
    d = _dt.date(2026, 1, 1)
    while d.year == 2026:
        rows.append({"date": d.isoformat(), "is_open": 1 if d.weekday() < 5 else 0})
        d += _dt.timedelta(days=1)
    Q.upsert_trade_calendar(c, rows)


@pytest.fixture
def conn(tmp_path):
    c = get_connection(tmp_path / "cal.db")
    init_schema(c)
    _seed_2026_calendar(c)   # ≥200 行 → year covered → strict path 不调 provider
    yield c
    c.close()


class _NoCallRegistry:
    def call(self, *a, **k):
        raise AssertionError("strict path 禁止在日历已覆盖时调 provider")


def test_after_close_returns_today(conn):
    now = datetime(2026, 7, 21, 21, 45, tzinfo=SH)
    assert resolve_latest_closed_trade_date(conn, _NoCallRegistry(), now=now) == "2026-07-21"


def test_intraday_returns_prev_open_day(conn):
    now = datetime(2026, 7, 21, 10, 0, tzinfo=SH)
    assert resolve_latest_closed_trade_date(conn, _NoCallRegistry(), now=now) == "2026-07-20"


def test_weekend_returns_friday(conn):
    now = datetime(2026, 7, 19, 12, 0, tzinfo=SH)
    assert resolve_latest_closed_trade_date(conn, _NoCallRegistry(), now=now) == "2026-07-17"


def test_naive_now_treated_as_shanghai(conn):
    now = datetime(2026, 7, 21, 21, 45)  # naive → 视为上海时间
    assert resolve_latest_closed_trade_date(conn, _NoCallRegistry(), now=now) == "2026-07-21"


def test_aware_utc_converted(conn):
    now = datetime(2026, 7, 21, 13, 45, tzinfo=timezone.utc)  # = 上海 21:45
    assert resolve_latest_closed_trade_date(conn, _NoCallRegistry(), now=now) == "2026-07-21"


def test_exactly_cutoff_counts_as_closed(conn):
    now = datetime(2026, 7, 21, 15, 30, tzinfo=SH)
    assert resolve_latest_closed_trade_date(conn, _NoCallRegistry(), now=now) == "2026-07-21"


def test_calendar_unavailable_returns_none(tmp_path):
    """空日历 + provider 失败 → blocked(None)：禁止 weekday/昨天 fallback。"""
    c = get_connection(tmp_path / "empty.db")
    init_schema(c)

    class _FailRegistry:
        def call(self, *a, **k):
            class R:
                success, data, error = False, None, "down"
            return R()

    assert resolve_latest_closed_trade_date(
        c, _FailRegistry(), now=datetime(2026, 7, 21, 21, 45, tzinfo=SH)) is None
    c.close()
