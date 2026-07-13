from __future__ import annotations

import pytest

from services.wechat_teacher_feed.schedule import decide_phase


def _lookup(rows: dict[str, bool]):
    return lambda day: rows.get(day)


@pytest.mark.parametrize(
    ("run_date", "phase", "rows", "status", "target", "reason"),
    [
        (
            "2026-07-13",
            "post-market",
            {"2026-07-13": True},
            "run",
            "2026-07-13",
            "scheduled",
        ),
        (
            "2026-07-13",
            "pre-trading-eve",
            {"2026-07-14": True},
            "run",
            "2026-07-14",
            "scheduled",
        ),
        (
            "2026-07-10",
            "pre-trading-eve",
            {"2026-07-11": False},
            "skip",
            "2026-07-11",
            "phase_not_scheduled",
        ),
        (
            "2026-07-12",
            "pre-trading-eve",
            {"2026-07-13": True},
            "run",
            "2026-07-13",
            "scheduled",
        ),
        (
            "2026-10-08",
            "pre-trading-eve",
            {"2026-10-09": True},
            "run",
            "2026-10-09",
            "scheduled",
        ),
        (
            "2026-12-31",
            "pre-trading-eve",
            {},
            "blocked",
            "2027-01-01",
            "calendar_unavailable",
        ),
    ],
)
def test_decide_phase_uses_exact_calendar_day(
    run_date: str,
    phase: str,
    rows: dict[str, bool],
    status: str,
    target: str,
    reason: str,
) -> None:
    decision = decide_phase(run_date, phase, lookup=_lookup(rows))

    assert decision.status == status
    assert decision.run_date == run_date
    assert decision.phase == phase
    assert decision.target_trade_date == target
    assert decision.reason == reason


def test_force_only_turns_explicit_skip_into_run() -> None:
    forced = decide_phase(
        "2026-07-10",
        "pre-trading-eve",
        lookup=_lookup({"2026-07-11": False}),
        force=True,
    )
    missing = decide_phase(
        "2026-07-10",
        "pre-trading-eve",
        lookup=_lookup({}),
        force=True,
    )

    assert (forced.status, forced.reason) == ("run", "forced")
    assert (missing.status, missing.reason) == ("blocked", "calendar_unavailable")


def test_lookup_failure_is_calendar_blocked() -> None:
    def unavailable(_: str) -> bool:
        raise RuntimeError("missing trade_calendar table")

    decision = decide_phase("2026-07-13", "post-market", lookup=unavailable)

    assert decision.status == "blocked"
    assert decision.reason == "calendar_unavailable"


@pytest.mark.parametrize("run_date", ["2026-02-30", "20260713", ""])
def test_invalid_date_is_rejected(run_date: str) -> None:
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        decide_phase(run_date, "post-market", lookup=_lookup({}))


def test_invalid_phase_is_rejected_before_calendar_lookup() -> None:
    called = False

    def lookup(_: str) -> bool:
        nonlocal called
        called = True
        return True

    with pytest.raises(ValueError, match="unsupported phase"):
        decide_phase("2026-07-13", "evening", lookup=lookup)

    assert called is False
