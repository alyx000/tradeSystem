from __future__ import annotations

import argparse

import main
from cli import intraday_monitor


def _args() -> argparse.Namespace:
    return argparse.Namespace(
        intraday_monitor_command="e2e-test",
        input_by="pytest",
        json=True,
    )


def test_e2e_cli_returns_nonzero_when_verification_did_not_complete(monkeypatch, capsys):
    monkeypatch.setattr(main, "setup_providers", lambda config: object())
    monkeypatch.setattr(
        intraday_monitor,
        "run_e2e_test",
        lambda registry, input_by: {
            "status": "outside_session",
            "events": [],
            "errors": [],
        },
    )

    assert intraday_monitor.handle_command({}, _args()) == 1
    assert '"status": "outside_session"' in capsys.readouterr().out


def test_e2e_cli_returns_zero_only_for_complete_verification(monkeypatch, capsys):
    monkeypatch.setattr(main, "setup_providers", lambda config: object())
    monkeypatch.setattr(
        intraday_monitor,
        "run_e2e_test",
        lambda registry, input_by: {
            "status": "complete",
            "events": [{}],
            "errors": [],
            "pushed": True,
        },
    )

    assert intraday_monitor.handle_command({}, _args()) == 0
    assert '"status": "complete"' in capsys.readouterr().out
