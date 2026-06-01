"""service 编排单测（阶段3）：monkeypatch collector 隔离，测 None 透传 / persist 开关 / trend。"""
from __future__ import annotations

import sqlite3

from db.schema import init_schema
from services.sector_correlation import repo, service


class _DummyProvider:
    pro = object()

    def _ensure_sw_l2_codes(self):
        return {"801081.SI"}

    def _ensure_ths_concept_map(self):
        return {"885001.TI": "算力租赁"}


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def _record(date="2026-05-29"):
    return {
        "date": date, "windows": [20, 60], "top_n": 6, "activity_days": 10,
        "sample_days": {"20": 20, "60": 58}, "base_index": "000001.SH",
        "indices": ["000001.SH"], "sectors": [],
        "sector_index": {"20": {}, "60": {}},
        "pair_raw": {"20": [], "60": []}, "pair_excess": {"20": [], "60": []},
        "meta": {},
    }


def test_run_daily_none_when_collector_returns_none(monkeypatch):
    monkeypatch.setattr(service.collector, "build_record", lambda *a, **k: None)
    assert service.run_daily(_conn(), _DummyProvider(), "2026-05-29") is None


def test_run_daily_persists_and_renders(monkeypatch):
    monkeypatch.setattr(service.collector, "build_record", lambda *a, **k: _record())
    conn = _conn()
    md = service.run_daily(conn, _DummyProvider(), "2026-05-29", persist=True)
    assert md is not None and "板块相关性" in md
    assert repo.get_correlation(conn, "2026-05-29") is not None  # 已落库


def test_run_daily_no_persist(monkeypatch):
    monkeypatch.setattr(service.collector, "build_record", lambda *a, **k: _record())
    conn = _conn()
    service.run_daily(conn, _DummyProvider(), "2026-05-29", persist=False)
    assert repo.get_correlation(conn, "2026-05-29") is None  # 未落库


def test_run_matrix_reads_db_first(monkeypatch):
    conn = _conn()
    repo.save_correlation(conn, _record())
    # build_record 若被调用则抛错，验证 matrix 优先读库
    monkeypatch.setattr(service.collector, "build_record",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应现采")))
    out = service.run_matrix(conn, _DummyProvider(), "2026-05-29")
    assert "矩阵" in out


def test_run_trend_reads_recent(monkeypatch):
    conn = _conn()
    for d in ["2026-05-28", "2026-05-29"]:
        repo.save_correlation(conn, _record(d))
    out = service.run_trend(conn, "2026-05-29", days=5)
    assert "2026-05-28" in out and "2026-05-29" in out
