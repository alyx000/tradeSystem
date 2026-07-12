"""string-yang CLI：source_failed 必须可观测并非零退出。"""
from __future__ import annotations

import argparse

import main as main_module
from cli import string_yang as sy


class _Conn:
    def close(self):
        pass


class _Registry:
    def initialize_all(self):
        pass


def _args(**over):
    base = dict(
        date="2026-07-06",
        dry_run=False,
        no_push=False,
        top_k=5,
        top_concepts=8,
        teacher_lookback_days=7,
        no_llm=True,
    )
    base.update(over)
    return argparse.Namespace(**base)


def _wire(monkeypatch, *, result):
    monkeypatch.setattr(main_module, "setup_providers", lambda _config: _Registry())
    monkeypatch.setattr(sy, "get_connection", lambda: _Conn())

    import utils.trade_date as trade_date

    monkeypatch.setattr(trade_date, "is_non_trading_day", lambda conn, registry, date: False)
    monkeypatch.setattr(sy.scanner, "run_daily", lambda *args, **kwargs: result)

    saves = []
    monkeypatch.setattr(sy.renderer, "write_report", lambda md, date: saves.append((md, date)))

    pushes = []
    monkeypatch.setattr(sy, "_push_to_dingtalk", lambda title, md: pushes.append((title, md)) or True)

    return {"saves": saves, "pushes": pushes}


def test_source_failed_saves_failure_report_pushes_alert_and_exits(monkeypatch, capsys):
    import pytest

    failed = {
        "status": "source_failed",
        "date": "2026-07-06",
        "main_sectors": ["半导体"],
        "mainline": {"status": "disabled"},
        "source_errors": ["sw_map"],
        "candidates": [],
    }
    wired = _wire(monkeypatch, result=failed)

    with pytest.raises(SystemExit) as exc:
        sy._run_daily({}, _args())

    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "数据源失败" in out
    assert len(wired["saves"]) == 1
    assert len(wired["pushes"]) == 1
    assert "数据源失败" in wired["pushes"][0][0]


def test_source_failed_dry_run_does_not_save_or_push_but_exits(monkeypatch, capsys):
    import pytest

    failed = {
        "status": "source_failed",
        "date": "2026-07-06",
        "main_sectors": ["半导体"],
        "mainline": {"status": "disabled"},
        "source_errors": ["sw_map"],
        "candidates": [],
    }
    wired = _wire(monkeypatch, result=failed)

    with pytest.raises(SystemExit) as exc:
        sy._run_daily({}, _args(dry_run=True))

    assert exc.value.code == 1
    assert "数据源失败" in capsys.readouterr().out
    assert wired["saves"] == []
    assert wired["pushes"] == []
