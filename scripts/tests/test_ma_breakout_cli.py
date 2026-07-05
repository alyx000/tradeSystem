from __future__ import annotations

import argparse
import json
from pathlib import Path

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


def test_no_push_writes_markdown_and_json_reports(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main_module, "setup_providers", lambda _config: _Registry())
    monkeypatch.setattr(ma_breakout, "get_connection", lambda: _Conn())

    import utils.trade_date as trade_date
    monkeypatch.setattr(trade_date, "is_non_trading_day", lambda _conn, _registry, _date: False)
    monkeypatch.setattr(
        ma_breakout.scanner,
        "load_former_leader_universe",
        lambda _conn, _date, *, lookback_days, registry, stats: {"688041": {"name": "海光信息"}},
    )
    monkeypatch.setattr(
        ma_breakout.scanner,
        "run_daily",
        lambda *_args, **_kwargs: {
            "status": "ok",
            "date": "2026-06-12",
            "windows": [5, 10],
            "candidates": [
                {
                    "code": "688041",
                    "name": "海光信息",
                    "sw_l2": "半导体",
                    "pct_chg": 3.2,
                    "today_amount": 12345.0,
                    "amount_ma5": 10000.0,
                    "amount_ma10": 9000.0,
                    "ma4_today": 10.2,
                    "ma4_prev": 9.8,
                    "former_leader_first_seen": "2026-06-01",
                    "former_leader_role": "走势引领",
                }
            ],
            "source_errors": [],
            "leader_universe_count": 1,
            "scanned_count": 1,
            "matched_count": 1,
            "insufficient_count": 0,
            "truncated": False,
        },
    )

    ma_breakout._run_daily({}, _args(date="2026-06-12", json=False, no_push=True))

    report_dir = Path("data/reports/ma-breakout")
    md_path = report_dir / "2026-06-12.md"
    json_path = report_dir / "2026-06-12.json"
    assert md_path.exists()
    assert json_path.exists()
    assert "海光信息" in md_path.read_text(encoding="utf-8")
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["date"] == "2026-06-12"
    assert payload["candidates"][0]["code"] == "688041"
    assert str(md_path) in capsys.readouterr().out


def test_dry_run_does_not_write_reports(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main_module, "setup_providers", lambda _config: _Registry())
    monkeypatch.setattr(ma_breakout, "get_connection", lambda: _Conn())
    monkeypatch.setattr(
        ma_breakout.scanner,
        "load_former_leader_universe",
        lambda _conn, _date, *, lookback_days, registry, stats: {"688041": {"name": "海光信息"}},
    )
    monkeypatch.setattr(
        ma_breakout.scanner,
        "run_daily",
        lambda *_args, **_kwargs: {
            "status": "ok",
            "date": "2026-06-12",
            "windows": [5, 10],
            "candidates": [],
            "source_errors": [],
            "leader_universe_count": 1,
            "scanned_count": 1,
            "matched_count": 0,
            "insufficient_count": 0,
            "truncated": False,
        },
    )

    ma_breakout._run_daily({}, _args(date="2026-06-12", json=False, dry_run=True))

    assert not Path("data/reports/ma-breakout/2026-06-12.md").exists()
    assert not Path("data/reports/ma-breakout/2026-06-12.json").exists()
