from __future__ import annotations

import argparse
import json

import main as main_module
from cli import ma_breakout


class _Registry:
    def initialize_all(self):
        return {"fake": True}


class _Conn:
    def close(self):
        pass


def _args(**overrides):
    values = {
        "date": "2026-06-13",
        "windows": (5, 10),
        "top_n": 50,
        "leader_lookback_days": 60,
        "dry_run": False,
        "no_push": False,
        "json": True,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_json_non_trading_day_emits_structured_skip(monkeypatch, capsys):
    monkeypatch.setattr(main_module, "setup_providers", lambda _config: _Registry())
    monkeypatch.setattr(ma_breakout, "get_connection", lambda: _Conn())

    import utils.trade_date as trade_date
    monkeypatch.setattr(trade_date, "is_non_trading_day", lambda _conn, _registry, _date: True)

    args = _args(dry_run=False)

    ma_breakout._run_daily({}, args)

    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "status": "skipped",
        "reason": "non_trading_day",
        "date": "2026-06-13",
        "candidates": [],
    }


def test_json_dry_run_non_trading_day_emits_structured_skip(monkeypatch, capsys):
    monkeypatch.setattr(main_module, "setup_providers", lambda _config: _Registry())
    monkeypatch.setattr(ma_breakout, "get_connection", lambda: _Conn())

    import utils.trade_date as trade_date
    monkeypatch.setattr(trade_date, "is_non_trading_day", lambda _conn, _registry, _date: True)

    args = _args(dry_run=True)

    ma_breakout._run_daily({}, args)

    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "status": "skipped",
        "reason": "non_trading_day",
        "date": "2026-06-13",
        "candidates": [],
    }


def test_json_stock_basic_resolution_failure_is_source_failed(monkeypatch, capsys):
    monkeypatch.setattr(main_module, "setup_providers", lambda _config: _Registry())
    monkeypatch.setattr(ma_breakout, "get_connection", lambda: _Conn())

    import utils.trade_date as trade_date
    monkeypatch.setattr(trade_date, "is_non_trading_day", lambda _conn, _registry, _date: False)

    def fake_load(_conn, _date, *, lookback_days, registry, stats):
        stats["leader_resolution_error"] = "stock_basic_failed:rate_limit"
        stats["unresolved_leader_tracking"] = 3
        return {}

    monkeypatch.setattr(ma_breakout.scanner, "load_former_leader_universe", fake_load)
    monkeypatch.setattr(
        ma_breakout.scanner,
        "run_daily",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("run_daily should not be called")),
    )

    ma_breakout._run_daily({}, _args(date="2026-06-12"))

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "source_failed"
    assert payload["source_errors"] == ["leader_resolution_error:stock_basic_failed:rate_limit"]
    assert payload["leader_unresolved_count"] == 3
