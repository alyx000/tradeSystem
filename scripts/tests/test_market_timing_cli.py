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


def test_validate_pivot_args_single_side_fails_fast():
    with pytest.raises(SystemExit) as e1:
        mt._validate_pivot_args(_args(pivot_index="000001.SH"))
    assert e1.value.code == 2
    with pytest.raises(SystemExit) as e2:
        mt._validate_pivot_args(_args(pivot_date="2026-04-25"))
    assert e2.value.code == 2
    # 成对/均空均不报错
    mt._validate_pivot_args(_args(pivot_index="000001.SH", pivot_date="2026-04-25"))
    mt._validate_pivot_args(_args())


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
    """隔离网络/DB/推送：scanner 返回 canned，setup_providers/连接/推送替身（默认推送成功）。"""
    pushed = []

    def _fake_push(title, md):
        pushed.append(title)
        return True

    monkeypatch.setattr(mt.scanner, "run_daily", lambda *a, **k: _CANNED)
    monkeypatch.setattr(mt, "get_connection", lambda: _DummyConn())
    monkeypatch.setattr(mt, "_push_to_dingtalk", _fake_push)

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


def test_daily_bare_push_failure_exits_nonzero(monkeypatch):
    """裸跑推送失败 → 非零退出，供定时任务/监控发现日报缺失。"""
    monkeypatch.setattr(mt.scanner, "run_daily", lambda *a, **k: _CANNED)
    monkeypatch.setattr(mt, "get_connection", lambda: _DummyConn())
    monkeypatch.setattr(mt, "_push_to_dingtalk", lambda title, md: False)  # 推送失败
    import main
    monkeypatch.setattr(main, "setup_providers", lambda config: _DummyRegistry())
    with pytest.raises(SystemExit) as e:
        mt._run_daily({}, _args())
    assert e.value.code == 1


def test_push_returns_false_when_pusher_uninitialized(monkeypatch):
    """pusher 未初始化(缺 env) → _push_to_dingtalk 返回 False（不静默成功）。"""
    class _StubPusher:
        def __init__(self, config=None):
            pass

        def initialize(self):
            return False

    import pushers.dingtalk_pusher as dp
    monkeypatch.setattr(dp, "DingTalkPusher", _StubPusher)
    assert mt._push_to_dingtalk("t", "md") is False
