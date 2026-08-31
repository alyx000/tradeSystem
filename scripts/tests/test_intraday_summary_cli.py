from __future__ import annotations

import argparse

import main
from cli import intraday_summary


def _args() -> argparse.Namespace:
    return argparse.Namespace(
        intraday_summary_command="run",
        dry_run=False,
        no_push=False,
        json=True,
    )


def test_cli_returns_nonzero_for_source_failure(monkeypatch, capsys):
    monkeypatch.setattr(main, "setup_providers", lambda config: object())
    monkeypatch.setattr(
        intraday_summary,
        "run",
        lambda registry, dry_run, no_push: {
            "status": "source_failed",
            "errors": ["行情失败"],
            "pushed": False,
        },
    )
    assert intraday_summary.handle_command({}, _args()) == 1
    assert '"status": "source_failed"' in capsys.readouterr().out


def test_cli_accepts_partial_delivery(monkeypatch, capsys):
    monkeypatch.setattr(main, "setup_providers", lambda config: object())
    monkeypatch.setattr(
        intraday_summary,
        "run",
        lambda registry, dry_run, no_push: {
            "status": "partial",
            "errors": ["行业映射缺失"],
            "pushed": True,
        },
    )
    assert intraday_summary.handle_command({}, _args()) == 0
    assert '"status": "partial"' in capsys.readouterr().out
