"""WatchlistCollector.collect_watchlist_announcements：临时 watchlist.yaml + mock registry。"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from collectors.watchlist import WatchlistCollector
from providers.base import DataResult


@pytest.fixture
def sqlite_db(tmp_path: Path):
    from db.connection import get_db
    from db.migrate import migrate

    path = tmp_path / "watchlist.db"
    with get_db(path) as conn:
        migrate(conn)
    return path


def _insert_watchlist(conn, code: str, name: str, tier: str) -> None:
    from db import queries as Q

    Q.insert_watchlist(
        conn,
        stock_code=code,
        stock_name=name,
        tier=tier,
        input_by="test",
    )


def test_collect_watchlist_announcements_tier1_tier2_dedupe(sqlite_db):
    from db.connection import get_db

    with get_db(sqlite_db) as conn:
        _insert_watchlist(conn, "000001.SZ", "A", "tier1_core")
        _insert_watchlist(conn, " 000002.SZ ", "B", "tier1_core")
        _insert_watchlist(conn, "000002.SZ", "B_dup", "tier2_watch")
        _insert_watchlist(conn, "688000.SH", "C", "tier2_watch")

    registry = MagicMock()

    def announcements_for(code: str, *_args):
        if code == "000001.SZ":
            return DataResult(
                data=[{"title": "A公告", "ann_date": "20260329"}],
                source="mock",
            )
        if code == "000002.SZ":
            return DataResult(data=[], source="mock")
        if code == "688000.SH":
            return DataResult(data=[{"title": "C公告", "ann_date": "20260330"}], source="mock")
        return DataResult(data=None, source="mock", error="no")

    registry.call.side_effect = lambda method, *a, **kw: (
        announcements_for(a[0], a[1], a[2]) if method == "get_stock_announcements" else DataResult(data=[], source="mock")
    )

    col = WatchlistCollector(registry, db_path=sqlite_db)
    out = col.collect_watchlist_announcements("2026-03-27", "2026-03-30", db_path=sqlite_db)

    assert set(out.keys()) == {"000001.SZ", "000002.SZ", "688000.SH"}
    assert out["000001.SZ"]["announcements"][0]["title"] == "A公告"
    assert out["000002.SZ"]["announcements"] == []
    assert out["688000.SH"]["name"] == "C"


def test_collect_watchlist_announcements_no_registry(sqlite_db):
    col = WatchlistCollector(None, db_path=sqlite_db)
    assert col.collect_watchlist_announcements("2026-03-01", "2026-03-30", db_path=sqlite_db) == {}
