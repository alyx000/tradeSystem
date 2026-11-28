from datetime import datetime, timedelta

import pytest

from automations import board_break_tail_wait as runner


def shanghai(hour, minute, second=0):
    return datetime(2026, 11, 20, hour, minute, second, tzinfo=runner.SHANGHAI)


@pytest.mark.parametrize("stage, arrival, cutoff", [
    ("tail", shanghai(14, 38), shanghai(14, 50)),
    ("tail", shanghai(14, 48, 22), shanghai(14, 50)),
    ("open-review", shanghai(9, 28), shanghai(9, 40)),
    ("open-review", shanghai(9, 37, 15), shanghai(9, 40)),
])
def test_early_dispatch_waits_until_cutoff(stage, arrival, cutoff):
    current = arrival

    def pause(seconds):
        nonlocal current
        current += timedelta(seconds=seconds)

    assert runner.wait_for_cutoff(lambda: current, pause, stage) == cutoff


@pytest.mark.parametrize("stage, arrival, earliest", [
    ("tail", shanghai(14, 37, 59), "14:38"),
    ("open-review", shanghai(9, 27, 59), "09:28"),
])
def test_outside_early_window_does_not_wait(stage, arrival, earliest):
    with pytest.raises(RuntimeError, match=f"早于北京时间{earliest}"):
        runner.wait_for_cutoff(
            lambda: arrival, lambda _: pytest.fail("must not sleep before the early window"), stage,
        )


def test_waking_on_another_date_does_not_dispatch():
    times = iter([shanghai(14, 49), shanghai(14, 49) + timedelta(days=1)])
    with pytest.raises(RuntimeError, match="已跨日"):
        runner.wait_for_cutoff(lambda: next(times), lambda _: None)


@pytest.mark.parametrize("argv, stage, push_flag, late", [
    ([], "tail", "--no-push", shanghai(14, 55, 15)),
    (["--push"], "tail", "--push", shanghai(14, 55, 15)),
    (["--stage", "open-review", "--push"], "open-review", "--push", shanghai(9, 44, 20)),
])
def test_late_arrival_reaches_original_cli_immediately_for_skip_receipt(monkeypatch, argv, stage, push_flag, late):
    assert runner.wait_for_cutoff(
        lambda: late, lambda _: pytest.fail("must not wait after the cutoff"), stage,
    ) == late

    command = []
    def wait_for_cutoff(_clock, _pause, actual_stage):
        assert actual_stage == stage
        return late

    monkeypatch.setattr(runner, "wait_for_cutoff", wait_for_cutoff)
    monkeypatch.setattr(runner.os, "execv", lambda executable, args: command.extend(args))
    runner.main(argv)
    assert command[1:] == [
        str(runner.MAIN), "board-break", stage, "--input-by", "USER", push_flag, "--json",
    ]
