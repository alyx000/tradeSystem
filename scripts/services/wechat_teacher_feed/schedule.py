from __future__ import annotations

from datetime import date, timedelta
from typing import Callable, Optional

from .models import PhaseDecision


PHASES = ("post-market", "pre-trading-eve")


def _parse_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("date must be valid YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise ValueError("date must be valid YYYY-MM-DD")
    return parsed


def decide_phase(
    run_date: str,
    phase: str,
    *,
    lookup: Callable[[str], Optional[bool]],
    force: bool = False,
) -> PhaseDecision:
    if phase not in PHASES:
        raise ValueError(f"unsupported phase: {phase}")
    parsed = _parse_date(run_date)
    target = parsed if phase == "post-market" else parsed + timedelta(days=1)
    try:
        is_open = lookup(target.isoformat())
    except Exception:  # DB/table/connection absence is a blocked calendar decision.
        is_open = None
    if is_open is None:
        return PhaseDecision(
            "blocked", run_date, phase, target.isoformat(), "calendar_unavailable"
        )
    if not isinstance(is_open, bool):
        return PhaseDecision(
            "blocked", run_date, phase, target.isoformat(), "calendar_unavailable"
        )
    if is_open is False and not force:
        return PhaseDecision(
            "skip", run_date, phase, target.isoformat(), "phase_not_scheduled"
        )
    reason = "forced" if is_open is False else "scheduled"
    return PhaseDecision("run", run_date, phase, target.isoformat(), reason)
