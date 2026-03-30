"""WatchlistCollector.collect_watchlist_announcements：临时 watchlist.yaml + mock registry。"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from collectors.watchlist import WatchlistCollector
from providers.base import DataResult


def test_collect_watchlist_announcements_tier1_tier2_dedupe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    wl = tmp_path / "watchlist.yaml"
    wl.write_text(
        yaml.dump(
            {
                "tier1_core": [
                    {"stock_code": "000001.SZ", "stock_name": "A"},
                    {"stock_code": " 000002.SZ ", "stock_name": "B"},
                ],
                "tier2_watch": [
                    {"stock_code": "000002.SZ", "stock_name": "B_dup"},
                    {"stock_code": "688000.SH", "stock_name": "C"},
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr("collectors.watchlist.WATCHLIST_FILE", wl)

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
        announcements_for(a[0], a[1], a[2]) if method == "get_stock_announcements" else DataResult(data=None, error="x")
    )

    col = WatchlistCollector(registry)
    out = col.collect_watchlist_announcements("2026-03-27", "2026-03-30")

    assert set(out.keys()) == {"000001.SZ", "000002.SZ", "688000.SH"}
    assert out["000001.SZ"]["announcements"][0]["title"] == "A公告"
    assert out["000002.SZ"]["announcements"] == []
    assert out["688000.SH"]["name"] == "C"


def test_collect_watchlist_announcements_no_registry():
    col = WatchlistCollector(None)
    assert col.collect_watchlist_announcements("2026-03-01", "2026-03-30") == {}
