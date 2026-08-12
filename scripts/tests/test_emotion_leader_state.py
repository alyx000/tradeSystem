from __future__ import annotations

import json

from services.emotion_leader.state import load_previous_report, plan_incremental_refresh


def _item(code: str, **overrides) -> dict:
    base = {
        "code": code,
        "name": code,
        "promoted_date": "2026-07-20",
        "manual_confirmed": False,
        "last_limit_up_date": "2026-07-20",
    }
    return {**base, **overrides}


def test_incremental_refreshes_active_new_and_reactivated_but_caches_archived() -> None:
    previous = {
        "date": "2026-07-23",
        "active": [{"code": "000001.SZ", "max_gain_pct": 80.0}],
        "archived": [
            {"code": "000002.SZ", "max_gain_pct": 50.0, "archived": True},
            {"code": "000003.SZ", "max_gain_pct": 40.0, "archived": True},
        ],
    }
    promoted = [
        _item("000001.SZ"),
        _item("000002.SZ"),
        _item("000003.SZ"),
        _item("000004.SZ", promoted_date="2026-07-24"),
    ]

    refresh, cached, receipt = plan_incremental_refresh(
        promoted, previous, {"000003.SZ"}, "2026-07-24"
    )

    assert {row["code"] for row in refresh} == {"000001.SZ", "000003.SZ", "000004.SZ"}
    assert [row["code"] for row in cached] == ["000002.SZ"]
    assert cached[0]["metric_status"] == "cached_archived"
    assert cached[0]["metric_as_of"] == "2026-07-23"
    assert receipt == {
        "mode": "incremental",
        "previous_report_date": "2026-07-23",
        "discovered_count": 4,
        "metric_refresh_count": 3,
        "cached_archived_count": 1,
    }


def test_full_refresh_ignores_previous_cache() -> None:
    promoted = [_item("000001.SZ")]
    refresh, cached, receipt = plan_incremental_refresh(
        promoted,
        {"date": "2026-07-23", "active": [], "archived": [{"code": "000001.SZ"}]},
        set(),
        "2026-07-24",
        full_refresh=True,
    )

    assert refresh == promoted
    assert cached == []
    assert receipt["mode"] == "full_refresh"


def test_load_previous_report_skips_future_damaged_and_wrong_lookback(tmp_path) -> None:
    out_dir = tmp_path / "data/reports/emotion-leader"
    out_dir.mkdir(parents=True)
    (out_dir / "2026-07-25.json").write_text("{}", encoding="utf-8")
    (out_dir / "2026-07-23.json").write_text("not-json", encoding="utf-8")
    (out_dir / "2026-07-22.json").write_text(json.dumps({
        "date": "2026-07-22", "status": "ok", "lookback_days": 60,
        "active": [], "archived": [],
    }), encoding="utf-8")
    expected = {
        "date": "2026-07-21", "status": "partial", "lookback_days": 90,
        "active": [], "archived": [],
    }
    (out_dir / "2026-07-21.json").write_text(json.dumps(expected), encoding="utf-8")

    assert load_previous_report("2026-07-24", lookback_days=90, root=tmp_path) == expected
