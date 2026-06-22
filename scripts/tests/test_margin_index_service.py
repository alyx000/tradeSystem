"""margin_index_correlation.service：编排层（monkeypatch collector 隔离）。"""
from __future__ import annotations

import sqlite3

import pytest

from db.schema import init_schema
from services.margin_index_correlation import service, repo


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_schema(c)
    return c


def _canned_record(date="2026-06-19"):
    return {
        "date": date, "data_trade_date": date, "windows": [5, 20],
        "indices": [{"pair_key": "total:000001.SH", "margin_key": "total",
                     "index_code": "000001.SH", "index_name": "上证指数", "group": "broad"}],
        "base_index": "000001.SH",
        "lag": {"total:000001.SH": {"best_lag": 1, "best_corr": 0.5, "relation": "两融滞后", "by_lag": {}}},
        "sync_corr": {"total:000001.SH": {"5": {"corr": 0.7, "label": "强同向"}, "20": {"corr": 0.5, "label": "弱同向"}}},
        "divergence": {"total:000001.SH": {"5": {"index_cum": 1.0, "margin_cum": 0.5, "diverged": False, "type": "无背离", "magnitude": 0.5}}},
        "balance": {"total": {"latest_yi": 18000.0, "dod_pct": 0.3, "pctile_20d": 0.8,
                              "up_streak": 2, "down_streak": 0, "ma20": 17500.0, "vs_ma20": 2.0},
                    "sse": {"latest_yi": 9000.0, "dod_pct": 0.2, "pctile_20d": 0.7,
                            "up_streak": 1, "down_streak": 0, "ma20": 8900.0, "vs_ma20": 1.1},
                    "szse": {"latest_yi": 8900.0, "dod_pct": 0.4, "pctile_20d": 0.75,
                             "up_streak": 2, "down_streak": 0, "ma20": 8700.0, "vs_ma20": 2.3}},
        "sample_days": {"5": 5, "20": 20},
        "meta": {"source": "tushare:margin", "market_scope": "BSE+SSE+SZSE", "stale": False, "divergence_windows": [5]},
    }


class _FakeProvider:
    """带 .pro 的 provider 桩：run_daily 须解包 provider.pro 传给 collector（真机 bug 回归）。"""
    pro = object()


def test_run_daily_unwraps_provider_pro(conn, monkeypatch):
    """run_daily 必须把 provider.pro（原始 tushare pro_api）传给 build_record，而非 provider 本身。

    真机暴露：传 TushareProvider 包装类 → fetch_index_series 调 pro.index_daily 抛 AttributeError。
    """
    seen = {}
    monkeypatch.setattr(service.collector, "build_record",
                        lambda registry, pro, **k: seen.update(pro=pro) or _canned_record())
    prov = _FakeProvider()
    service.run_daily(conn, registry=object(), provider=prov, date="2026-06-19", persist=False)
    assert seen["pro"] is prov.pro  # 解包后的原始 pro，不是 provider 包装类
    assert seen["pro"] is not prov


def test_run_daily_persist_writes_and_returns_markdown(conn, monkeypatch):
    monkeypatch.setattr(service.collector, "build_record", lambda *a, **k: _canned_record())
    md = service.run_daily(conn, registry=object(), provider=_FakeProvider(), date="2026-06-19", persist=True)
    assert md is not None and "两融×指数联动" in md
    assert repo.get(conn, "2026-06-19") is not None  # 已落库


def test_run_daily_dry_run_does_not_persist(conn, monkeypatch):
    monkeypatch.setattr(service.collector, "build_record", lambda *a, **k: _canned_record())
    md = service.run_daily(conn, registry=object(), provider=_FakeProvider(), date="2026-06-19", persist=False)
    assert md is not None
    assert repo.get(conn, "2026-06-19") is None  # 未落库


def test_run_daily_none_when_insufficient(conn, monkeypatch):
    monkeypatch.setattr(service.collector, "build_record", lambda *a, **k: None)
    md = service.run_daily(conn, registry=object(), provider=_FakeProvider(), date="2026-06-19", persist=True)
    assert md is None
    assert repo.get(conn, "2026-06-19") is None


def test_run_signals_reads_recent(conn, monkeypatch):
    for d in ["2026-06-18", "2026-06-19"]:
        repo.save(conn, _canned_record(d))
    out = service.run_signals(conn, end_date="2026-06-19", days=5)
    assert "2026-06-19" in out
