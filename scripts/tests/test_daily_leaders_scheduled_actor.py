from pathlib import Path

import pytest
from services.daily_leaders import service


def test_cli_forwards_actor_from_scheduled_command(monkeypatch):
    import argparse
    import datetime as dt
    from functools import partial
    import shlex
    import sqlite3
    from cli import daily_leaders
    from services.daily_leaders import retry
    from services.daily_leaders.readiness import SHANGHAI
    import api.routes.review as review
    import api.deps as deps
    runner = Path(__file__).resolve().parents[2] / "deploy/launchd/daily-leaders-runner.sh"
    command = next(line for line in runner.read_text().splitlines() if line.startswith("exec "))
    tokens = shlex.split(command)
    parser = argparse.ArgumentParser()
    daily_leaders.register_subparser(parser.add_subparsers(dest="command"))
    args = parser.parse_args(tokens[tokens.index("daily-leaders"):])
    assert args.input_by == "launchd"
    assert args.push is True
    assert args.wait_ready is True
    # Exercise the real time/calendar gates with fixed inputs, not the host clock.
    now = dt.datetime(2026, 11, 10, 21, 15, tzinfo=SHANGHAI)
    monkeypatch.setattr(daily_leaders, "now_shanghai", lambda: now)
    def unexpected_sleep(seconds):
        pytest.fail("Ready scheduled input must not sleep or retry")
    monkeypatch.setattr(daily_leaders, "run_when_ready", partial(
        retry.run_when_ready, now=lambda: now, sleep=unexpected_sleep))
    monkeypatch.setattr(daily_leaders, "check_window", partial(retry.check_window, now=now))
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE trade_calendar(date TEXT, exchange TEXT, is_open INTEGER)")
    connection.execute("INSERT INTO trade_calendar VALUES ('2026-11-10','SSE',1)")
    monkeypatch.setattr(daily_leaders, "get_connection", lambda: connection)
    monkeypatch.setattr(review, "get_prefill", lambda *a, **kw: {})
    monkeypatch.setattr(deps, "get_provider_registry", lambda: object())
    captured = {}
    def propose(*a, **kwargs):
        assert a[1] == "2026-11-10"
        captured.update(kwargs)
        return {"date": "2026-11-10", "top_leaders": []}
    monkeypatch.setattr(service, "propose", propose)
    sends = []
    monkeypatch.setattr(daily_leaders, "_push_to_dingtalk", lambda *a: sends.append(a) or True)
    daily_leaders._handle_propose(args)
    assert captured["input_by"] == "launchd"
    assert captured["require_ready"] is True
    assert len(sends) == 1 and sends[0][0].endswith("2026-11-10")
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")
