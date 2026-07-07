from __future__ import annotations

import argparse

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
