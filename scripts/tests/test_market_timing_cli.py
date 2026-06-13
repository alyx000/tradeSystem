"""market-timing CLI 单测：pivot 覆盖配对 + daily 三档推送行为（monkeypatch 隔离网络/DB）。"""
from __future__ import annotations

import argparse

import pytest

from cli import market_timing as mt


def _args(**kw):
    ns = argparse.Namespace(pivot_index=None, pivot_date=None, date=None,
                            no_push=False, dry_run=False)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def test_pivot_overrides_pair():
    assert mt._pivot_overrides(_args(pivot_index="000001.SH", pivot_date="2026-04-25")) == {"000001.SH": "2026-04-25"}


def test_pivot_overrides_single_side_ignored():
    assert mt._pivot_overrides(_args(pivot_index="000001.SH")) is None
    assert mt._pivot_overrides(_args(pivot_date="2026-04-25")) is None


_CANNED = {
    "date": "2026-06-13",
    "signals": [{"index_name": "上证综指", "fib_day_count": 21, "fib_hit": 21, "fib_near": None,
                 "swing_pivot_date": "2026-05-15", "swing_pivot_type": "high", "swing_pivot_price": 4258.0,
                 "fractal_status": "none", "fractal_low_date": None, "fractal_low_price": None,
                 "fractal_confirm_date": None}],
    "context": {"market_amount_yi": 25000.0, "amount_pctile_20d": 0.1, "advance": 4500,
                "decline": 900, "limit_down_count": 12},
    "resonance_count": 1, "skipped": [],
}


@pytest.fixture
def _patched(monkeypatch):
    """隔离网络/DB/推送：scanner 返回 canned，setup_providers/连接/推送替身。"""
    pushed = []
    monkeypatch.setattr(mt.scanner, "run_daily", lambda *a, **k: _CANNED)
    monkeypatch.setattr(mt, "get_connection", lambda: _DummyConn())
    monkeypatch.setattr(mt, "_push_to_dingtalk", lambda title, md: pushed.append(title))

    import main
    monkeypatch.setattr(main, "setup_providers", lambda config: _DummyRegistry())
    return pushed


class _DummyConn:
    def close(self):
        pass


class _DummyRegistry:
    def initialize_all(self):
        return {}


def test_daily_dry_run_does_not_push(_patched, capsys):
    mt._run_daily({}, _args(dry_run=True))
    assert _patched == []                       # 未推送
    assert "大盘择时观察" in capsys.readouterr().out


def test_daily_no_push_prints_only(_patched, capsys):
    mt._run_daily({}, _args(no_push=True))
    assert _patched == []                       # 未推送
    assert "命中斐波那契 21" in capsys.readouterr().out


def test_daily_bare_pushes(_patched):
    mt._run_daily({}, _args())
    assert _patched == ["大盘择时观察 · 2026-06-13"]   # 裸跑推送
