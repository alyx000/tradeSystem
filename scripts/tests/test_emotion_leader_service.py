from __future__ import annotations

from services.emotion_leader import service


def test_active_rank_keeps_high_recognition_non_limit_core_in_front() -> None:
    low_recognition_limit_up = {
        "code": "000001.SZ",
        "manual_confirmed": False,
        "max_height": 3,
        "max_gain_pct": 32.0,
        "today_pct_chg": 10.0,
        "new_peak_today": True,
    }
    high_recognition_non_limit = {
        "code": "000002.SZ",
        "manual_confirmed": False,
        "max_height": 7,
        "max_gain_pct": 118.0,
        "today_pct_chg": 7.2,
        "new_peak_today": False,
    }

    ranked = sorted(
        [low_recognition_limit_up, high_recognition_non_limit],
        key=lambda row: service._active_rank_key(row, {"000001.SZ"}),
        reverse=True,
    )

    assert ranked[0]["code"] == "000002.SZ"


def test_active_rank_keeps_negative_feedback_core_visible_without_five_pct_gate() -> None:
    negative_feedback_core = {
        "code": "000002.SZ",
        "manual_confirmed": True,
        "max_height": 8,
        "max_gain_pct": 150.0,
        "today_pct_chg": -4.99,
    }
    current_limit_up = {
        "code": "000001.SZ",
        "manual_confirmed": False,
        "max_height": 3,
        "max_gain_pct": 30.0,
        "today_pct_chg": 10.0,
    }

    ranked = sorted(
        [negative_feedback_core, current_limit_up],
        key=lambda row: service._active_rank_key(row, {"000001.SZ"}),
        reverse=True,
    )

    assert ranked[0]["code"] == "000002.SZ"


def test_active_rank_puts_source_failed_metrics_after_comparable_core() -> None:
    missing_metric = {
        "code": "000001.SZ",
        "metric_status": "source_failed",
        "manual_confirmed": True,
        "max_height": 10,
        "launch_date": "2026-08-01",
        "max_gain_pct": None,
        "today_pct_chg": None,
    }
    comparable_core = {
        "code": "000002.SZ",
        "metric_status": "ok",
        "manual_confirmed": False,
        "max_height": 5,
        "launch_date": "2026-07-20",
        "max_gain_pct": 80.0,
        "today_pct_chg": -3.0,
    }

    ranked = sorted(
        [missing_metric, comparable_core],
        key=lambda row: service._active_rank_key(row, set()),
        reverse=True,
    )

    assert ranked[0]["code"] == "000002.SZ"


def test_feedback_type_labels_both_positive_and_negative_feedback() -> None:
    assert service._feedback_type({"code": "000001.SZ", "today_pct_chg": -2.95}, set(), set()) == "负反馈"
    assert service._feedback_type({"code": "000002.SZ", "today_pct_chg": 3.2}, set(), set()) == "弱正反馈"
    assert service._feedback_type({"code": "000003.SZ", "today_pct_chg": 7.2}, set(), set()) == "强正反馈"
    assert service._feedback_type({"code": "000004.SZ", "today_pct_chg": -9.8}, set(), {"000004.SZ"}) == "跌停负反馈"


def test_active_rank_prefers_newer_launch_within_same_height() -> None:
    older_with_larger_lifecycle_gain = {
        "code": "000001.SZ",
        "manual_confirmed": False,
        "max_height": 4,
        "launch_date": "2026-05-15",
        "max_gain_pct": 110.96,
        "today_pct_chg": 9.91,
    }
    newer_with_smaller_lifecycle_gain = {
        "code": "000002.SZ",
        "manual_confirmed": False,
        "max_height": 4,
        "launch_date": "2026-08-06",
        "max_gain_pct": 61.10,
        "today_pct_chg": 10.0,
    }
    missing_launch_date = {
        "code": "000003.SZ",
        "manual_confirmed": False,
        "max_height": 4,
        "launch_date": None,
        "max_gain_pct": 200.0,
        "today_pct_chg": 10.0,
    }

    ranked = sorted(
        [older_with_larger_lifecycle_gain, newer_with_smaller_lifecycle_gain, missing_launch_date],
        key=lambda row: service._active_rank_key(row, set()),
        reverse=True,
    )

    assert [row["code"] for row in ranked] == ["000002.SZ", "000001.SZ", "000003.SZ"]


def test_missing_height_window_marks_daily_result_partial(monkeypatch) -> None:
    monkeypatch.setattr(
        service,
        "load_history",
        lambda *_args, **_kwargs: {
            "coverage": {"expected_open_days": 10, "loaded_limit_days": 10},
            "missing_dates": [],
            "errors": [],
            "target_ok": True,
        },
    )
    monkeypatch.setattr(
        service,
        "discover_lifecycles",
        lambda *_args, **_kwargs: {
            "promoted": [],
            "candidates": [],
            "trade_dates": ["2026-07-21"],
            "current_limit_up_codes": set(),
            "current_down_codes": set(),
            "height_breakthrough": {
                "status": "missing_data",
                "source_status": "partial",
                "reason": "此前开放日不足20个",
                "leaders": [],
            },
        },
    )
    monkeypatch.setattr(service, "_industry_map", lambda _registry: ({}, None, ""))

    result = service.run_daily(None, None, "2026-07-21")

    assert result["status"] == "partial"
    assert result["height_breakthrough"]["status"] == "missing_data"
    assert "情绪高度节点:此前开放日不足20个" in result["source_errors"]
