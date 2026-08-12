from __future__ import annotations

from services.emotion_leader import service


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
