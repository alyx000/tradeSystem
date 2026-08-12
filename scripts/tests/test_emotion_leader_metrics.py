from __future__ import annotations

from services.emotion_leader.metrics import calculate_metrics, infer_wave_evidence


def _bar(date: str, open_: float, close: float, high: float, low: float, pct: float = 0.0) -> dict:
    return {
        "trade_date": date,
        "open": open_,
        "close": close,
        "high": high,
        "low": low,
        "pct_chg": pct,
    }


def test_qfq_lifecycle_gain_and_second_wave_evidence() -> None:
    bars = [
        _bar("2026-07-17", 9.8, 10.0, 10.1, 9.7),
        _bar("2026-07-20", 10.2, 10.8, 11.0, 10.1),
        _bar("2026-07-21", 13.5, 14.0, 15.0, 13.0),
        _bar("2026-07-22", 13.6, 13.0, 13.8, 12.8),
        _bar("2026-07-23", 13.2, 15.1, 15.2, 13.1, 16.15),
    ]
    factors = [{"trade_date": row["trade_date"], "adj_factor": 1.0} for row in bars]

    result = calculate_metrics(bars, factors, "2026-07-20", "2026-07-23")

    assert result["metric_status"] == "ok"
    assert result["max_gain_pct"] == 52.0
    assert result["interval_gain_pct"] == 51.0
    assert result["distance_from_peak_pct"] == -0.66
    assert result["wave_label"] == "二波"
    assert result["new_peak_today"] is True


def test_missing_target_bar_fails_closed() -> None:
    result = calculate_metrics(
        [_bar("2026-07-22", 10, 10, 10, 10)],
        [{"trade_date": "2026-07-22", "adj_factor": 1.0}],
        "2026-07-22",
        "2026-07-23",
    )
    assert result == {"metric_status": "source_failed", "metric_error": "目标日行情缺失或陈旧"}


def test_wave_candidate_needs_recovery_but_not_previous_peak() -> None:
    evidence = infer_wave_evidence([
        _bar("2026-07-20", 10, 10, 10, 9.8),
        _bar("2026-07-21", 11, 12, 12, 10.8),
        _bar("2026-07-22", 10.8, 10.5, 11, 10.4),
        _bar("2026-07-23", 10.6, 11.6, 11.8, 10.5),
    ])
    assert evidence["wave_label"] == "二波候选"
    assert evidence["confirmed_restarts"] == 0
