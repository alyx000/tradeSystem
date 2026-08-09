from __future__ import annotations

import argparse

import main
from cli import intraday_monitor


def _args(*, confirm_real_push: bool = True) -> argparse.Namespace:
    return argparse.Namespace(
        intraday_monitor_command="e2e-test",
        input_by="pytest",
        confirm_real_push=confirm_real_push,
        json=True,
    )


def _check_args() -> argparse.Namespace:
    return argparse.Namespace(
        intraday_monitor_command="check",
        dry_run=False,
        json=True,
    )


def test_check_cli_initializes_provider_for_active_rule(monkeypatch, capsys):
    registry = object()
    monkeypatch.setattr(main, "setup_providers", lambda config: registry)
    calls = []

    def fake_run_check(registry, *, dry_run):
        calls.append((registry, dry_run))
        return {
            "status": "complete",
            "events": [],
            "errors": [],
            "quotes_checked": 1,
            "pending_count": 0,
            "pushed": False,
        }

    monkeypatch.setattr(intraday_monitor, "run_check", fake_run_check)

    assert intraday_monitor.handle_command({}, _check_args()) == 0
    output = capsys.readouterr().out
    assert '"status": "complete"' in output
    assert '"pushed": false' in output
    assert calls == [(registry, False)]


def test_e2e_cli_initializes_provider_for_active_rule(monkeypatch, capsys):
    registry = object()
    monkeypatch.setattr(main, "setup_providers", lambda config: registry)
    calls = []
    monkeypatch.setattr(
        intraday_monitor,
        "run_e2e_test",
        lambda got, input_by, confirm_real_push: calls.append(
            (got, input_by, confirm_real_push)
        ) or {
            "status": "complete",
            "events": [{}],
            "errors": [],
            "pushed": True,
        },
    )

    assert intraday_monitor.handle_command({}, _args()) == 0
    output = capsys.readouterr().out
    assert '"status": "complete"' in output
    assert '"pushed": true' in output
    assert calls == [(registry, "pytest", True)]


def test_e2e_cli_requires_explicit_real_push_confirmation_before_provider_setup(
    monkeypatch,
    capsys,
):
    monkeypatch.setattr(
        main,
        "setup_providers",
        lambda config: (_ for _ in ()).throw(
            AssertionError("缺少显式确认时不得初始化行情 provider")
        ),
    )
    calls = []
    monkeypatch.setattr(
        intraday_monitor,
        "run_e2e_test",
        lambda registry, input_by, confirm_real_push: calls.append(
            (registry, input_by, confirm_real_push)
        ) or {
            "status": "authorization_required",
            "events": [],
            "errors": ["必须显式确认"],
            "pushed": False,
        },
    )

    denied_values = (False, None, 1, "false", "true")
    for denied_value in denied_values:
        assert (
            intraday_monitor.handle_command(
                {},
                _args(confirm_real_push=denied_value),
            )
            == 1
        )
    output = capsys.readouterr().out
    assert '"status": "authorization_required"' in output
    assert calls == [(None, "pytest", False)] * len(denied_values)


def test_help_describes_sse_3955_behavior_at_every_command_level():
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

    assert "上证指数从3955点下方" in intraday_parser.format_help()
    check_help = command_choices["check"].format_help()
    assert "跌回下方后再次站上可重推" in check_help
    e2e_help = command_choices["e2e-test"].format_help()
    assert "上证指数3955生产规则" in e2e_help
    assert "不读写正式监控状态" in e2e_help
    assert "--confirm-real-push" in e2e_help


def test_e2e_cli_returns_nonzero_when_verification_did_not_complete(monkeypatch, capsys):
    monkeypatch.setattr(main, "setup_providers", lambda config: object())
    monkeypatch.setattr(
        intraday_monitor,
        "run_e2e_test",
        lambda registry, input_by, confirm_real_push: {
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
        lambda registry, input_by, confirm_real_push: {
            "status": "complete",
            "events": [{}],
            "errors": [],
            "pushed": True,
        },
    )

    assert intraday_monitor.handle_command({}, _args()) == 0
    assert '"status": "complete"' in capsys.readouterr().out
