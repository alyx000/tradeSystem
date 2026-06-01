"""CLI 行为单测（阶段4）：matrix 缓存命中走纯只读，不初始化 Tushare（review L2）。"""
from __future__ import annotations

import argparse
import sqlite3

from cli import sector_correlation as cli
from db.schema import init_schema
from services.sector_correlation import repo


def _record(date="2026-05-29") -> dict:
    return {
        "date": date, "windows": [20, 60], "top_n": 6, "activity_days": 10,
        "sample_days": {"20": 20, "60": 58}, "base_index": "000001.SH",
        "indices": ["000001.SH"], "sectors": [],
        "sector_index": {"20": {}, "60": {}},
        "pair_raw": {"20": [], "60": []}, "pair_excess": {"20": [], "60": []},
        "meta": {},
    }


def _matrix_args(**over):
    kw = dict(date="2026-05-29", refetch=False, windows=None, top_industries=15,
              top_concepts=10, indices=None, activity_days=10, no_concept=False)
    kw.update(over)
    return argparse.Namespace(**kw)


def test_run_matrix_cache_hit_skips_provider(monkeypatch, capsys):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    repo.save_correlation(conn, _record())

    monkeypatch.setattr(cli, "get_connection", lambda: conn)
    # 若 _setup_tushare 被调用即判失败（缓存命中不该初始化 provider）
    def _boom(_config):
        raise AssertionError("缓存命中不应初始化 Tushare provider")
    monkeypatch.setattr(cli, "_setup_tushare", _boom)

    cli._run_matrix({}, _matrix_args())  # 不应抛 AssertionError
    out = capsys.readouterr().out
    assert "矩阵" in out  # 渲染了缓存矩阵
