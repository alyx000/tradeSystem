"""盘后两市成交额相对滚动三个月峰值的事实指标。"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import yaml

from analyzers.node_signals import NodeSignalAnalyzer
from collectors.market import MarketCollector
from generators.report import ReportGenerator
from providers.base import DataResult


TARGET_DATE = "2026-04-01"


def _open_dates(count: int = 60) -> list[str]:
    target = date.fromisoformat(TARGET_DATE)
    return [
        (target - timedelta(days=offset)).isoformat()
        for offset in range(count, 0, -1)
    ]


def _write_calendar(base_dir: Path, dates: list[str]) -> None:
    db_dir = base_dir / "data"
    db_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_dir / "trade.db")
    try:
        conn.execute("CREATE TABLE trade_calendar (date TEXT PRIMARY KEY, is_open INTEGER)")
        conn.execute("CREATE TABLE daily_market (date TEXT PRIMARY KEY, total_amount REAL)")
        conn.executemany(
            "INSERT INTO trade_calendar(date, is_open) VALUES (?, 1)",
            [(trade_date,) for trade_date in [*dates, TARGET_DATE]],
        )
        conn.commit()
    finally:
        conn.close()


def _write_market_rows(base_dir: Path, rows: list[tuple[str, float]]) -> None:
    conn = sqlite3.connect(base_dir / "data" / "trade.db")
    try:
        conn.executemany(
            "INSERT INTO daily_market(date, total_amount) VALUES (?, ?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def _write_volume(base_dir: Path, trade_date: str, amount: float) -> None:
    day_dir = base_dir / "daily" / trade_date
    day_dir.mkdir(parents=True, exist_ok=True)
    payload = {"raw_data": {"total_volume": {"total_billion": amount}}}
    (day_dir / "post-market.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True),
        encoding="utf-8",
    )


def _prepare_complete_window(base_dir: Path, *, peak: float = 100.0) -> list[str]:
    dates = _open_dates()
    _write_calendar(base_dir, dates)
    rows: list[tuple[str, float]] = []
    for index, trade_date in enumerate(dates):
        amount = peak if index == 20 else 80.0
        _write_volume(base_dir, trade_date, amount)
        rows.append((trade_date, amount))
    _write_market_rows(base_dir, rows)
    return dates


def test_rolling_3m_peak_triggers_at_inclusive_half_boundary(tmp_path, monkeypatch):
    dates = _prepare_complete_window(tmp_path)
    monkeypatch.setattr("collectors.market.BASE_DIR", tmp_path)

    volume = {"total_billion": 50.0}
    MarketCollector(registry=None)._enrich_volume_comparison(volume, TARGET_DATE)

    result = volume["rolling_3m_peak_comparison"]
    assert result["status"] == "complete"
    assert result["window_start"] == dates[0]
    assert result["window_end"] == dates[-1]
    assert result["peak_date"] == dates[20]
    assert result["today_to_peak_pct"] == 50.0
    assert result["triggered"] is True


def test_rolling_3m_peak_does_not_trigger_above_half(tmp_path, monkeypatch):
    _prepare_complete_window(tmp_path)
    monkeypatch.setattr("collectors.market.BASE_DIR", tmp_path)

    volume = {"total_billion": 50.01}
    MarketCollector(registry=None)._enrich_volume_comparison(volume, TARGET_DATE)

    result = volume["rolling_3m_peak_comparison"]
    assert result["status"] == "complete"
    assert result["triggered"] is False


def test_rolling_3m_peak_is_partial_when_one_open_day_is_missing(tmp_path, monkeypatch):
    dates = _prepare_complete_window(tmp_path)
    missing_path = tmp_path / "daily" / dates[17] / "post-market.yaml"
    missing_path.unlink()
    conn = sqlite3.connect(tmp_path / "data" / "trade.db")
    try:
        conn.execute("DELETE FROM daily_market WHERE date = ?", (dates[17],))
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr("collectors.market.BASE_DIR", tmp_path)

    volume = {"total_billion": 1.0}
    MarketCollector(registry=None)._enrich_volume_comparison(volume, TARGET_DATE)

    result = volume["rolling_3m_peak_comparison"]
    assert result["status"] == "partial"
    assert result["observed_days"] == 59
    assert result["missing_dates"] == [dates[17]]
    assert "triggered" not in result


def test_rolling_3m_peak_recovers_a_small_db_gap_from_yaml(tmp_path, monkeypatch):
    dates = _prepare_complete_window(tmp_path)
    conn = sqlite3.connect(tmp_path / "data" / "trade.db")
    try:
        conn.execute("DELETE FROM daily_market WHERE date = ?", (dates[17],))
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr("collectors.market.BASE_DIR", tmp_path)

    volume = {"total_billion": 50.0}
    MarketCollector(registry=None)._enrich_volume_comparison(volume, TARGET_DATE)

    result = volume["rolling_3m_peak_comparison"]
    assert result["status"] == "complete"
    assert result["db_days"] == 59
    assert result["yaml_fallback_dates"] == [dates[17]]


def test_rolling_3m_peak_recovers_a_small_archive_gap_from_provider(tmp_path, monkeypatch):
    dates = _prepare_complete_window(tmp_path)
    missing_date = dates[17]
    (tmp_path / "daily" / missing_date / "post-market.yaml").unlink()
    conn = sqlite3.connect(tmp_path / "data" / "trade.db")
    try:
        conn.execute("DELETE FROM daily_market WHERE date = ?", (missing_date,))
        conn.commit()
    finally:
        conn.close()

    registry = MagicMock()
    registry.call.return_value = DataResult(
        data={"total_billion": 75.0},
        source="mock:historical_market_volume",
    )
    monkeypatch.setattr("collectors.market.BASE_DIR", tmp_path)

    volume = {"total_billion": 50.0}
    MarketCollector(registry=registry)._enrich_volume_comparison(volume, TARGET_DATE)

    result = volume["rolling_3m_peak_comparison"]
    assert result["status"] == "complete"
    assert result["db_days"] == 59
    assert result["provider_fallback"] == [{
        "date": missing_date,
        "source": "mock:historical_market_volume",
    }]
    registry.call.assert_called_once_with("get_market_volume", missing_date)


def test_rolling_3m_peak_fails_closed_when_calendar_window_is_short(tmp_path, monkeypatch):
    dates = _open_dates(59)
    _write_calendar(tmp_path, dates)
    monkeypatch.setattr("collectors.market.BASE_DIR", tmp_path)

    volume = {"total_billion": 1.0}
    MarketCollector(registry=None)._enrich_volume_comparison(volume, TARGET_DATE)

    result = volume["rolling_3m_peak_comparison"]
    assert result["status"] == "source_failed"
    assert "59/60" in result["reason"]
    assert "triggered" not in result


def test_rolling_3m_peak_rejects_internal_natural_calendar_gap(tmp_path, monkeypatch):
    dates = _open_dates(61)
    _write_calendar(tmp_path, dates)
    rows: list[tuple[str, float]] = []
    for index, trade_date in enumerate(dates):
        amount = 100.0 if index == 10 else 80.0
        _write_volume(tmp_path, trade_date, amount)
        rows.append((trade_date, amount))
    _write_market_rows(tmp_path, rows)

    missing_date = dates[-20]
    conn = sqlite3.connect(tmp_path / "data" / "trade.db")
    try:
        conn.execute("DELETE FROM trade_calendar WHERE date = ?", (missing_date,))
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr("collectors.market.BASE_DIR", tmp_path)

    volume = {"total_billion": 40.0}
    MarketCollector(registry=None)._enrich_volume_comparison(volume, TARGET_DATE)

    result = volume["rolling_3m_peak_comparison"]
    assert result["status"] == "source_failed"
    assert result["missing_calendar_dates"] == [missing_date]
    assert "triggered" not in result


def test_triggered_comparison_emits_node_signal_without_short_window_history(tmp_path):
    analyzer = NodeSignalAnalyzer(tmp_path)
    today = {
        "total_volume": {
            "total_billion": 50.0,
            "rolling_3m_peak_comparison": {
                "status": "complete",
                "triggered": True,
                "today_to_peak_pct": 50.0,
                "peak_billion": 100.0,
                "peak_date": "2026-02-03",
                "window_start": "2026-01-01",
                "window_end": "2026-03-31",
            },
        }
    }

    signals = analyzer._check_volume_extreme(today, history=[])

    assert len(signals) == 1
    assert signals[0]["signal"] == "成交额缩至滚动三个月峰值一半"
    assert signals[0]["value"] == 50.0


def test_post_market_report_renders_complete_partial_and_skipped_states(tmp_path):
    generator = ReportGenerator()
    generator.daily_dir = tmp_path / "daily"
    complete = {
        "indices": {},
        "total_volume": {
            "total_billion": 50.0,
            "rolling_3m_peak_comparison": {
                "status": "complete",
                "triggered": True,
                "today_to_peak_pct": 50.0,
                "peak_billion": 100.0,
                "peak_date": "2026-02-03",
                "half_peak_billion": 50.0,
            },
        },
    }
    partial = {
        "indices": {},
        "total_volume": {
            "total_billion": 50.0,
            "rolling_3m_peak_comparison": {
                "status": "partial",
                "observed_days": 59,
                "expected_days": 60,
                "reason": "连续开放日成交额存在缺口，未计算峰值比例",
            },
        },
    }
    skipped = {
        "indices": {},
        "total_volume": {
            "total_billion": 50.0,
            "rolling_3m_peak_comparison": {
                "status": "skipped",
                "reason": "目标日不是 SSE 开放日",
            },
        },
    }

    complete_md, _ = generator.generate_post_market(TARGET_DATE, complete)
    partial_md, _ = generator.generate_post_market(TARGET_DATE, partial)
    skipped_md, _ = generator.generate_post_market(TARGET_DATE, skipped)

    assert "今日为峰值的 **50.00%**（已缩至一半及以下）" in complete_md
    assert "50% 阈值 50.00 亿" in complete_md
    assert "滚动三个月量能：**未计算**（59/60 个开放日" in partial_md
    assert "滚动三个月量能：**已跳过**（目标日不是 SSE 开放日）" in skipped_md
