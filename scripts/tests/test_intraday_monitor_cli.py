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


def _check_args() -> argparse.Namespace:
    return argparse.Namespace(
        intraday_monitor_command="check",
        dry_run=False,
        json=True,
    )


def _fail_provider_setup(config):
    raise AssertionError("无启用规则时不得初始化行情 provider")


def test_check_cli_returns_no_rules_without_provider_setup(monkeypatch, capsys):
    monkeypatch.setattr(main, "setup_providers", _fail_provider_setup)
    calls = []

    def fake_run_check(registry, *, dry_run):
        calls.append((registry, dry_run))
        return {
            "status": "no_rules",
            "events": [],
            "errors": [],
            "rules_checked": 0,
            "quotes_checked": 0,
            "pushed": False,
            "retired_pending_count": 0,
        }

    monkeypatch.setattr(intraday_monitor, "run_check", fake_run_check)

    assert intraday_monitor.handle_command({}, _check_args()) == 0
    output = capsys.readouterr().out
    assert '"status": "no_rules"' in output
    assert '"pushed": false' in output
    assert calls == [(None, False)]


def test_e2e_cli_returns_no_rules_without_provider_setup(monkeypatch, capsys):
    monkeypatch.setattr(main, "setup_providers", _fail_provider_setup)

    assert intraday_monitor.handle_command({}, _args()) == 1
    output = capsys.readouterr().out
    assert '"status": "no_rules"' in output
    assert '"pushed": false' in output


def test_help_describes_disabled_behavior_at_every_command_level():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    intraday_monitor.register_subparser(subparsers)
    root_choices = next(
        action.choices
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    intraday_parser = root_choices["intraday-monitor"]
    command_choices = next(
        action.choices
        for action in intraday_parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )

    assert "当前没有启用生产规则" in intraday_parser.format_help()
    assert "仅将既有 pending 移入 expired" in command_choices["check"].format_help()
    e2e_help = command_choices["e2e-test"].format_help()
    assert "no_rules 与非零退出" in e2e_help
    assert "不抓行情、不推送、不读写正式状态" in e2e_help


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
