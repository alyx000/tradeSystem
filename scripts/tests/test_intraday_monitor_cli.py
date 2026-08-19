from __future__ import annotations

import argparse

import main
from cli import intraday_monitor


def _args(*, confirm_real_push: bool = True, rule_id: str | None = None) -> argparse.Namespace:
    return argparse.Namespace(
        intraday_monitor_command="e2e-test",
        input_by="pytest",
        confirm_real_push=confirm_real_push,
        rule_id=rule_id or intraday_monitor.DEFAULT_RULES[0].rule_id,
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
        lambda got, input_by, confirm_real_push, rule: calls.append(
            (got, input_by, confirm_real_push, rule)
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
    assert calls == [(registry, "pytest", True, intraday_monitor.DEFAULT_RULES[0])]


def test_e2e_cli_selects_requested_dynamic_rule(monkeypatch, capsys):
    registry = object()
    monkeypatch.setattr(main, "setup_providers", lambda config: registry)
    calls = []
    monkeypatch.setattr(
        intraday_monitor,
        "run_e2e_test",
        lambda got, input_by, confirm_real_push, rule: calls.append(rule) or {
            "status": "complete",
            "events": [{}],
            "errors": [],
            "pushed": True,
        },
    )
    selected = next(
        rule for rule in intraday_monitor.DEFAULT_RULES if rule.rule_id.startswith("jinjian-rice")
    )

    assert intraday_monitor.handle_command({}, _args(rule_id=selected.rule_id)) == 0
    assert calls == [selected]
    assert '"status": "complete"' in capsys.readouterr().out


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
        lambda registry, input_by, confirm_real_push, rule: calls.append(
            (registry, input_by, confirm_real_push, rule)
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
    assert calls == [
        (None, "pytest", False, intraday_monitor.DEFAULT_RULES[0])
    ] * len(denied_values)


def test_help_describes_current_rules_at_every_command_level():
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

    root_help = intraday_parser.format_help()
    assert "上证指数从3955点下方" in root_help
    assert "2026年8月19日至20日监控金健米业、红四方、京粮控股" in root_help
    check_help = "".join(command_choices["check"].format_help().split())
    assert "价格严格低于当日涨停价时提醒当前未封涨停" in check_help
    assert "最终是否断板以收盘为准" in check_help
    assert "持续命中去重" in check_help
    assert "恢复后再次命中可重推" in check_help
    e2e_help = command_choices["e2e-test"].format_help()
    assert "默认上证指数3955规则" in e2e_help
    assert "不读写正式监控状态" in e2e_help
    assert "--confirm-real-push" in e2e_help
    assert "--rule-id" in e2e_help


def test_e2e_cli_returns_nonzero_when_verification_did_not_complete(monkeypatch, capsys):
    monkeypatch.setattr(main, "setup_providers", lambda config: object())
    monkeypatch.setattr(
        intraday_monitor,
        "run_e2e_test",
        lambda registry, input_by, confirm_real_push, rule: {
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
        lambda registry, input_by, confirm_real_push, rule: {
            "status": "complete",
            "events": [{}],
            "errors": [],
            "pushed": True,
        },
    )

    assert intraday_monitor.handle_command({}, _args()) == 0
    assert '"status": "complete"' in capsys.readouterr().out


def test_check_cli_returns_nonzero_for_partial_monitoring(monkeypatch, capsys):
    monkeypatch.setattr(main, "setup_providers", lambda config: object())
    monkeypatch.setattr(
        intraday_monitor,
        "run_check",
        lambda registry, dry_run: {
            "status": "partial",
            "events": [],
            "errors": ["目标行情缺失"],
            "pushed": False,
        },
    )

    assert intraday_monitor.handle_command({}, _check_args()) == 1
    assert '"status": "partial"' in capsys.readouterr().out
