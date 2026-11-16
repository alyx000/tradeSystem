from __future__ import annotations

import argparse

import pytest

from cli import daily_leaders
from cli.daily_leaders import register_subparser


def _parser():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    register_subparser(sub)
    return parser


def test_parse_propose():
    args = _parser().parse_args([
        "daily-leaders",
        "propose",
        "--date",
        "2026-07-03",
        "--push",
        "--no-llm",
        "--max-candidates",
        "12",
    ])
    assert args.command == "daily-leaders"
    assert args.daily_leaders_command == "propose"
    assert args.date == "2026-07-03"
    assert args.push is True
    assert args.no_llm is True
    assert args.max_candidates == 12


def test_parse_propose_defaults_to_hard_cap():
    args = _parser().parse_args(["daily-leaders", "propose"])

    assert args.max_candidates == 15


@pytest.mark.parametrize("value", ["1", "15"])
def test_parse_propose_accepts_candidate_limit_boundaries(value):
    args = _parser().parse_args([
        "daily-leaders",
        "propose",
        "--max-candidates",
        value,
    ])

    assert args.max_candidates == int(value)


@pytest.mark.parametrize("value", ["0", "16"])
def test_parse_propose_rejects_candidate_limit_outside_hard_cap(value):
    with pytest.raises(SystemExit) as exc_info:
        _parser().parse_args([
            "daily-leaders",
            "propose",
            "--max-candidates",
            value,
        ])

    assert exc_info.value.code == 2


def test_parse_propose_help_describes_candidate_limit_range():
    help_text = _parser().format_help()

    assert "每日最票候选确认稿" in help_text
    root_subparsers = _parser()._subparsers._group_actions[0]
    daily_leaders_parser = root_subparsers.choices["daily-leaders"]
    daily_leaders_subparsers = daily_leaders_parser._subparsers._group_actions[0]
    propose_help = daily_leaders_subparsers.choices["propose"].format_help()
    assert "1 到 15" in propose_help


def test_parse_confirm_requires_input_by():
    parser = _parser()
    try:
        parser.parse_args(["daily-leaders", "confirm", "--date", "2026-07-03"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("confirm without --input-by should fail")


def test_parse_show_json():
    args = _parser().parse_args(["daily-leaders", "show", "--date", "2026-07-03", "--json"])
    assert args.daily_leaders_command == "show"
    assert args.json is True


def test_push_to_dingtalk_reports_success(monkeypatch, capsys):
    class Pusher:
        def __init__(self, config):
            self.config = config

        def initialize(self):
            return True

        def send_markdown(self, title, content):
            return True

    monkeypatch.setattr("pushers.dingtalk_pusher.DingTalkPusher", Pusher)

    ok = daily_leaders._push_to_dingtalk("T", "M")

    assert ok is True
    assert "[daily-leaders] DingTalk 推送成功" in capsys.readouterr().err


def test_push_to_dingtalk_reports_failure(monkeypatch, capsys):
    class Pusher:
        def __init__(self, config):
            self.config = config

        def initialize(self):
            return True

        def send_markdown(self, title, content):
            return False

    monkeypatch.setattr("pushers.dingtalk_pusher.DingTalkPusher", Pusher)

    ok = daily_leaders._push_to_dingtalk("T", "M")

    assert ok is False
    assert "[daily-leaders] DingTalk 推送失败" in capsys.readouterr().err


def test_push_to_dingtalk_skip_when_not_configured(monkeypatch, capsys):
    class Pusher:
        def __init__(self, config):
            self.config = config

        def initialize(self):
            return False

    monkeypatch.setattr("pushers.dingtalk_pusher.DingTalkPusher", Pusher)

    ok = daily_leaders._push_to_dingtalk("T", "M")

    assert ok is False
    assert "DingTalk pusher 未启用，跳过推送" in capsys.readouterr().err


def _mock_attempt_dependencies(monkeypatch):
    from unittest.mock import Mock
    import api.routes.review as review
    import api.deps as deps
    connection = Mock()
    monkeypatch.setattr(daily_leaders, 'get_connection', lambda: connection)
    # The deployed checkout has a market-only prefill entry; support both while
    # exercising the actual CLI on each checkout.
    prefill = Mock(return_value={'market': {'date': '2026-11-16'}})
    monkeypatch.setattr(review, 'get_prefill', prefill)
    if hasattr(review, 'build_daily_leaders_prefill'):
        monkeypatch.setattr(review, 'build_daily_leaders_prefill', prefill)
    monkeypatch.setattr(deps, 'get_provider_registry', lambda: object())
    monkeypatch.setattr(daily_leaders, 'is_open_day', lambda conn,date: True)
    monkeypatch.setattr(daily_leaders, 'check_window', lambda date: None)
    monkeypatch.setattr(daily_leaders.renderer, 'render_markdown', lambda proposal,compact: 'compact')
    push = Mock(return_value=True)
    monkeypatch.setattr(daily_leaders, '_push_to_dingtalk', push)
    return connection, prefill, push


def test_cli_push_missing_inputs_does_not_send(monkeypatch):
    from unittest.mock import Mock
    from services.daily_leaders.readiness import DataNotReady
    conn, prefill, push = _mock_attempt_dependencies(monkeypatch)
    propose = Mock(side_effect=DataNotReady('missing'))
    monkeypatch.setattr(daily_leaders.service, 'propose', propose)
    args = _parser().parse_args(['daily-leaders','propose','--date','2026-11-16','--push'])
    with pytest.raises(SystemExit) as exc:
        daily_leaders._handle_propose(args)
    assert exc.value.code == 75
    assert propose.call_args.kwargs['require_ready'] is True
    push.assert_not_called(); conn.close.assert_called_once()


def test_cli_retry_refreshes_inputs_and_sends_once(monkeypatch):
    import datetime as dt
    from unittest.mock import Mock
    from services.daily_leaders import retry
    from services.daily_leaders.readiness import DataNotReady, SHANGHAI
    conn, prefill, push = _mock_attempt_dependencies(monkeypatch)
    propose = Mock(side_effect=[DataNotReady('missing'), {'date':'2026-11-16'}])
    monkeypatch.setattr(daily_leaders.service, 'propose', propose)
    monkeypatch.setattr(daily_leaders, 'run_when_ready', lambda attempt,date,wait: retry.run_when_ready(
        attempt,date,wait=wait,now=lambda:dt.datetime(2026,11,16,21,tzinfo=SHANGHAI),sleep=lambda _:None))
    args = _parser().parse_args(['daily-leaders','propose','--date','2026-11-16','--push','--wait-ready','--input-by','launchd'])
    daily_leaders._handle_propose(args)
    assert prefill.call_count == 2 and conn.close.call_count == 2
    push.assert_called_once_with('每日最票候选确认稿 · 2026-11-16', 'compact')


def test_cli_closed_day_never_generates_or_pushes(monkeypatch):
    from unittest.mock import Mock
    conn, prefill, push = _mock_attempt_dependencies(monkeypatch)
    monkeypatch.setattr(daily_leaders, 'is_open_day', lambda conn,date: False)
    propose = Mock(); monkeypatch.setattr(daily_leaders.service, 'propose', propose)
    args = _parser().parse_args(['daily-leaders','propose','--date','2026-11-16','--push'])
    daily_leaders._handle_propose(args)
    propose.assert_not_called(); push.assert_not_called(); prefill.assert_not_called()


def test_cli_deadline_after_generation_never_pushes(monkeypatch):
    from unittest.mock import Mock
    conn, prefill, push = _mock_attempt_dependencies(monkeypatch)
    monkeypatch.setattr(daily_leaders.service, 'propose', Mock(return_value={'date':'2026-11-16'}))
    monkeypatch.setattr(daily_leaders, 'run_when_ready', lambda attempt,date,wait:attempt())
    monkeypatch.setattr(daily_leaders, 'check_window', Mock(side_effect=TimeoutError('deadline')))
    args = _parser().parse_args(['daily-leaders','propose','--date','2026-11-16','--push','--wait-ready'])
    with pytest.raises(SystemExit) as exc: daily_leaders._handle_propose(args)
    assert exc.value.code == 75; push.assert_not_called()


def test_cli_send_failure_is_not_retried(monkeypatch):
    from unittest.mock import Mock
    conn, prefill, push = _mock_attempt_dependencies(monkeypatch)
    monkeypatch.setattr(daily_leaders.service, 'propose', Mock(return_value={'date':'2026-11-16'}))
    push.return_value = False
    args = _parser().parse_args(['daily-leaders','propose','--date','2026-11-16','--push'])
    with pytest.raises(SystemExit) as exc: daily_leaders._handle_propose(args)
    assert exc.value.code == 1; assert push.call_count == 1


def test_blocked_attempt_logs_requester_and_date(monkeypatch, caplog):
    import logging
    from unittest.mock import Mock
    from services.daily_leaders.readiness import DataNotReady
    _mock_attempt_dependencies(monkeypatch)
    monkeypatch.setattr(daily_leaders.service, 'propose', Mock(side_effect=DataNotReady('missing')))
    args = _parser().parse_args(['daily-leaders','propose','--date','2026-11-16','--push','--input-by','launchd'])
    with caplog.at_level(logging.INFO), pytest.raises(SystemExit):
        daily_leaders._handle_propose(args)
    assert 'date=2026-11-16 input_by=launchd' in caplog.text
