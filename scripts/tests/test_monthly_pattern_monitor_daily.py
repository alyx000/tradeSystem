from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from services.monthly_pattern import monitor_daily


SEED_MONTH_END = "2026-06-30"


def _item(
    code: str,
    target_date: str,
    *,
    support: bool | None = True,
    stage: str = "monthly_seeded",
    daily_above: bool = False,
    daily_bullish: bool = False,
    weekly_above: bool = False,
    weekly_bullish: bool = False,
    name: str | None = None,
    current_close: float = 10.0,
) -> dict:
    return {
        "stock_code": code,
        "stock_name": name or f"测试{code}",
        "stage": stage,
        "industry": "测试行业",
        "mainline_match": False,
        "daily_evidence": {
            "target_date": target_date,
            "reason": "fixture",
            "reentry_date": (
                target_date
                if stage in {"daily_reactivated", "resonance_observed"}
                else None
            ),
            "dynamic_monthly_ma5": {
                "support_held": support,
                "current_close": current_close,
                "ma5": 9.8,
            },
            "daily_macd": {
                "above_zero": daily_above,
                "bullish_on_zero": daily_bullish,
            },
            "weekly_macd": {
                "above_zero": weekly_above,
                "bullish_on_zero": weekly_bullish,
            },
        },
    }


def _summary(
    target_date: str,
    *,
    status: str = "complete",
    candidate: dict | None = None,
    waiting: dict | None = None,
    indeterminate: dict | None = None,
    issue: dict | None = None,
    st_item: dict | None = None,
    seed_month_end: str = SEED_MONTH_END,
    error: str | None = None,
) -> dict:
    candidates = [candidate] if candidate else []
    waiting_items = [waiting] if waiting else []
    indeterminate_items = [indeterminate] if indeterminate else []
    issues = [issue] if issue else []
    st_items = [st_item] if st_item else []
    total = (
        len(candidates)
        + len(waiting_items)
        + len(indeterminate_items)
        + len(issues)
        + len(st_items)
    )
    return {
        "requested_date": target_date,
        "target_date": target_date,
        "seed_month": seed_month_end[:7] if status != "blocked" else None,
        "seed_month_end": seed_month_end if status != "blocked" else None,
        "status": status,
        "source_status": {
            "daily": (
                "success"
                if status == "complete"
                else "partial"
                if status == "partial"
                else "blocked"
            )
        },
        "counts": {
            "monthly_seed_total": total,
            "monthly_seed_truncated": 0,
            "blocked": 1 if status == "partial" else 0,
            "daily_blocked": len(issues),
            "daily_insufficient": len(indeterminate_items),
            "indeterminate_current_month_ma5": len(indeterminate_items),
        },
        "candidates": candidates,
        "waiting_monthly_reclaim": waiting_items,
        "indeterminate_current_month_ma5": indeterminate_items,
        "data_issues": issues,
        "st_excluded_items": st_items,
        "error": error,
    }


def _state() -> dict:
    return monitor_daily.load_state(Path("/definitely/missing/state.json"))


def _types(events: list[dict]) -> list[str]:
    return [event["event_type"] for event in events]


def test_normalize_separates_state_hash_from_evidence_hash() -> None:
    date = "2026-07-24"
    base = _summary(date, candidate=_item("600001", date))
    renamed = _summary(
        date,
        candidate=_item(
            "600001",
            date,
            name="新简称",
            current_close=10.8,
        ),
    )

    first = monitor_daily.normalize_summary(base)
    second = monitor_daily.normalize_summary(renamed)

    assert first["state_hash"] == second["state_hash"]
    assert first["evidence_hash"] != second["evidence_hash"]


def test_normalize_rejects_duplicate_bucket_and_count_drift() -> None:
    date = "2026-07-24"
    duplicate = _summary(
        date,
        candidate=_item("600001", date),
        waiting=_item("600001", date, support=False),
    )
    with pytest.raises(monitor_daily.MonitorDailyError, match="同时出现在"):
        monitor_daily.normalize_summary(duplicate)

    drift = _summary(date, candidate=_item("600001", date))
    drift["counts"]["monthly_seed_total"] = 2
    with pytest.raises(monitor_daily.MonitorDailyError, match="身份不守恒"):
        monitor_daily.normalize_summary(drift)


def test_normalize_requires_exact_dates_and_completed_seed_month() -> None:
    invalid_target = _summary(
        "2026-07-24T15:30:00",
        candidate=_item("600001", "2026-07-24T15:30:00"),
    )
    with pytest.raises(monitor_daily.MonitorDailyError, match="target_date 非法"):
        monitor_daily.normalize_summary(invalid_target)

    future_seed = _summary(
        "2026-07-24",
        candidate=_item("600001", "2026-07-24"),
        seed_month_end="2026-07-31",
    )
    with pytest.raises(
        monitor_daily.MonitorDailyError,
        match="不得晚于 target_date",
    ):
        monitor_daily.normalize_summary(future_seed)


def test_normalize_includes_st_identity_in_conservation() -> None:
    date = "2026-07-24"
    summary = _summary(
        date,
        candidate=_item("600001", date),
        st_item={
            "stock_code": "600002",
            "stock_name": "ST样本",
            "stage": "excluded_st",
            "reason": "target_date_st",
        },
    )

    snapshot = monitor_daily.normalize_summary(summary)

    assert snapshot["stocks"]["600002"]["bucket"] == "excluded_st"
    assert len(snapshot["stocks"]) == 2


def test_first_complete_only_initializes_baseline() -> None:
    date = "2026-07-24"
    current = monitor_daily.normalize_summary(
        _summary(date, candidate=_item("600001", date))
    )

    events, complete, health = monitor_daily.plan_transition(
        _state(),
        current,
    )

    assert _types(events) == ["baseline_initialized"]
    assert events[0]["notify"] is False
    assert complete["target_date"] == date
    assert health["run_status"] == "complete"


def test_partial_does_not_advance_complete_baseline() -> None:
    first_date = "2026-07-23"
    first = monitor_daily.normalize_summary(
        _summary(first_date, candidate=_item("600001", first_date))
    )
    state = _state()
    _, state["last_complete"], state["last_health"] = (
        monitor_daily.plan_transition(state, first)
    )
    partial = monitor_daily.normalize_summary(
        _summary("2026-07-24", status="partial", error="coverage gap")
    )

    events, complete, health = monitor_daily.plan_transition(state, partial)

    assert _types(events) == ["run_degraded"]
    assert complete["target_date"] == first_date
    assert health["run_status"] == "partial"
    assert "seed_exited_scope" not in _types(events)


def test_partial_issue_identity_change_updates_health_fingerprint() -> None:
    first_issue = {
        "stock_code": "600001",
        "stock_name": "缺口一",
        "stage": "blocked",
        "source": {
            "status": "daily_processing_failed",
            "error": "timeout",
        },
    }
    second_issue = {
        **first_issue,
        "stock_code": "600002",
        "stock_name": "缺口二",
    }
    first = monitor_daily.normalize_summary(
        _summary("2026-07-23", status="partial", issue=first_issue)
    )
    second = monitor_daily.normalize_summary(
        _summary("2026-07-24", status="partial", issue=second_issue)
    )
    state = _state()
    _, state["last_complete"], state["last_health"] = (
        monitor_daily.plan_transition(state, first)
    )

    events, _, _ = monitor_daily.plan_transition(state, second)

    assert first["health_fingerprint"] != second["health_fingerprint"]
    assert _types(events) == ["run_degradation_changed"]


def test_partial_insufficient_identity_change_updates_health_fingerprint() -> None:
    first = _summary(
        "2026-07-23",
        status="partial",
        candidate=_item(
            "600001",
            "2026-07-23",
            stage="insufficient_history",
        ),
    )
    first["counts"]["daily_insufficient"] = 1
    second = _summary(
        "2026-07-24",
        status="partial",
        candidate=_item(
            "600002",
            "2026-07-24",
            stage="insufficient_history",
        ),
    )
    second["counts"]["daily_insufficient"] = 1
    first_snapshot = monitor_daily.normalize_summary(first)
    second_snapshot = monitor_daily.normalize_summary(second)
    state = _state()
    _, state["last_complete"], state["last_health"] = (
        monitor_daily.plan_transition(state, first_snapshot)
    )

    events, _, _ = monitor_daily.plan_transition(
        state,
        second_snapshot,
    )

    assert (
        first_snapshot["health_fingerprint"]
        != second_snapshot["health_fingerprint"]
    )
    assert _types(events) == ["run_degradation_changed"]


def test_recovery_compares_with_last_complete_baseline() -> None:
    first_date = "2026-07-22"
    first = monitor_daily.normalize_summary(
        _summary(first_date, candidate=_item("600001", first_date))
    )
    state = _state()
    _, state["last_complete"], state["last_health"] = (
        monitor_daily.plan_transition(state, first)
    )
    partial = monitor_daily.normalize_summary(
        _summary("2026-07-23", status="partial", error="coverage gap")
    )
    _, state["last_complete"], state["last_health"] = (
        monitor_daily.plan_transition(state, partial)
    )
    recovered_date = "2026-07-24"
    recovered = monitor_daily.normalize_summary(
        _summary(
            recovered_date,
            waiting=_item(
                "600001",
                recovered_date,
                support=False,
            ),
        )
    )

    events, complete, _ = monitor_daily.plan_transition(state, recovered)

    assert "run_recovered" in _types(events)
    assert "monthly_ma5_lost" in _types(events)
    assert complete["target_date"] == recovered_date


def test_same_day_rerun_is_idempotent_and_revisions_are_separate() -> None:
    date = "2026-07-24"
    first = monitor_daily.normalize_summary(
        _summary(date, candidate=_item("600001", date))
    )
    state = _state()
    _, state["last_complete"], state["last_health"] = (
        monitor_daily.plan_transition(state, first)
    )

    events, _, _ = monitor_daily.plan_transition(state, first)
    assert events == []

    evidence_revision = monitor_daily.normalize_summary(
        _summary(
            date,
            candidate=_item("600001", date, current_close=10.9),
        )
    )
    events, _, _ = monitor_daily.plan_transition(state, evidence_revision)
    assert _types(events) == ["same_day_evidence_revised"]
    assert events[0]["notify"] is False

    state_revision = monitor_daily.normalize_summary(
        _summary(
            date,
            candidate=_item(
                "600001",
                date,
                stage="daily_reactivated",
                daily_above=True,
            ),
        )
    )
    events, _, _ = monitor_daily.plan_transition(state, state_revision)
    assert _types(events) == ["same_day_state_revised"]
    assert events[0]["notify"] is True


def test_seed_month_roll_does_not_emit_mass_stock_diff() -> None:
    first_date = "2026-07-31"
    first = monitor_daily.normalize_summary(
        _summary(first_date, candidate=_item("600001", first_date))
    )
    state = _state()
    _, state["last_complete"], state["last_health"] = (
        monitor_daily.plan_transition(state, first)
    )
    next_date = "2026-08-03"
    rolled = monitor_daily.normalize_summary(
        _summary(
            next_date,
            candidate=_item("600999", next_date),
            seed_month_end="2026-07-31",
        )
    )

    events, complete, _ = monitor_daily.plan_transition(state, rolled)

    assert _types(events) == ["seed_month_rolled", "baseline_initialized"]
    assert "seed_entered_scope" not in _types(events)
    assert "seed_exited_scope" not in _types(events)
    assert complete["stocks"].keys() == {"600999"}


def test_historical_snapshot_is_audit_only_and_keeps_watermarks() -> None:
    latest_date = "2026-07-24"
    latest = monitor_daily.normalize_summary(
        _summary(latest_date, candidate=_item("600001", latest_date))
    )
    state = _state()
    _, state["last_complete"], state["last_health"] = (
        monitor_daily.plan_transition(state, latest)
    )
    historical_date = "2026-07-23"
    historical = monitor_daily.normalize_summary(
        _summary(
            historical_date,
            waiting=_item("600001", historical_date, support=False),
        )
    )

    events, complete, health = monitor_daily.plan_transition(
        state,
        historical,
    )

    assert _types(events) == ["historical_snapshot_ignored"]
    assert events[0]["notify"] is False
    assert complete["target_date"] == latest_date
    assert health["target_date"] == latest_date


def test_complete_diff_emits_ma5_stage_and_macd_transitions() -> None:
    first_date = "2026-07-23"
    second_date = "2026-07-24"
    previous = monitor_daily.normalize_summary(
        _summary(
            first_date,
            waiting=_item("600001", first_date, support=False),
        )
    )
    current = monitor_daily.normalize_summary(
        _summary(
            second_date,
            candidate=_item(
                "600001",
                second_date,
                support=True,
                stage="resonance_observed",
                daily_above=True,
                daily_bullish=True,
                weekly_above=True,
                weekly_bullish=True,
            ),
        )
    )

    event_types = _types(
        monitor_daily.diff_complete_snapshots(previous, current)
    )

    assert "monthly_ma5_reclaimed" in event_types
    assert "resonance_observed" in event_types
    assert "daily_macd_above_zero_entered" in event_types
    assert "weekly_macd_bullish_entered" in event_types


def test_stock_event_uses_run_context_and_is_unique_across_dates() -> None:
    first_previous = monitor_daily.normalize_summary(
        _summary(
            "2026-07-23",
            waiting=_item("600001", "2026-07-23", support=False),
        )
    )
    first_current = monitor_daily.normalize_summary(
        _summary(
            "2026-07-24",
            candidate=_item("600001", "2026-07-24", support=True),
        )
    )
    second_previous = monitor_daily.normalize_summary(
        _summary(
            "2026-07-27",
            waiting=_item("600001", "2026-07-27", support=False),
        )
    )
    second_current = monitor_daily.normalize_summary(
        _summary(
            "2026-07-28",
            candidate=_item("600001", "2026-07-28", support=True),
        )
    )

    first_event = monitor_daily.diff_complete_snapshots(
        first_previous,
        first_current,
    )[0]
    second_event = monitor_daily.diff_complete_snapshots(
        second_previous,
        second_current,
    )[0]

    assert first_event["event_type"] == "monthly_ma5_reclaimed"
    assert first_event["target_date"] == "2026-07-24"
    assert first_event["seed_month_end"] == SEED_MONTH_END
    assert second_event["target_date"] == "2026-07-28"
    assert first_event["event_id"] != second_event["event_id"]


def test_resonance_downgrade_is_reported_as_resonance_lost() -> None:
    previous = monitor_daily.normalize_summary(
        _summary(
            "2026-07-23",
            candidate=_item(
                "600001",
                "2026-07-23",
                stage="resonance_observed",
                daily_above=True,
                daily_bullish=True,
                weekly_above=True,
                weekly_bullish=True,
            ),
        )
    )
    current = monitor_daily.normalize_summary(
        _summary(
            "2026-07-24",
            candidate=_item(
                "600001",
                "2026-07-24",
                stage="daily_reactivated",
                daily_above=True,
                daily_bullish=True,
                weekly_above=True,
                weekly_bullish=True,
            ),
        )
    )

    event_types = _types(
        monitor_daily.diff_complete_snapshots(previous, current)
    )

    assert "resonance_lost" in event_types
    assert "daily_reactivated_observed" not in event_types


def test_complete_diff_distinguishes_st_identity_from_unknown_ma5() -> None:
    first_date = "2026-07-23"
    second_date = "2026-07-24"
    previous = monitor_daily.normalize_summary(
        _summary(
            first_date,
            candidate=_item("600001", first_date),
        )
    )
    current = monitor_daily.normalize_summary(
        _summary(
            second_date,
            st_item={
                "stock_code": "600001",
                "stock_name": "ST样本",
                "stage": "excluded_st",
                "reason": "target_date_st",
            },
        )
    )

    event_types = _types(
        monitor_daily.diff_complete_snapshots(previous, current)
    )

    assert event_types == ["st_excluded"]
    assert "state_became_indeterminate" not in event_types


def test_blocked_fingerprint_is_deduplicated_and_recovery_is_reported() -> None:
    blocked = monitor_daily.normalize_summary(
        _summary(
            "2026-07-23",
            status="blocked",
            error="calendar failed",
        )
    )
    state = _state()
    events, state["last_complete"], state["last_health"] = (
        monitor_daily.plan_transition(state, blocked)
    )
    assert _types(events) == ["run_blocked"]

    same = monitor_daily.normalize_summary(
        _summary(
            "2026-07-24",
            status="blocked",
            error="calendar failed",
        )
    )
    events, _, health = monitor_daily.plan_transition(state, same)
    assert events == []
    state["last_health"] = health

    changed = monitor_daily.normalize_summary(
        _summary(
            "2026-07-25",
            status="blocked",
            error="monthly source failed",
        )
    )
    events, _, health = monitor_daily.plan_transition(state, changed)
    assert _types(events) == ["run_block_reason_changed"]
    state["last_health"] = health

    recovered_date = "2026-07-27"
    recovered = monitor_daily.normalize_summary(
        _summary(
            recovered_date,
            candidate=_item("600001", recovered_date),
        )
    )
    events, _, _ = monitor_daily.plan_transition(state, recovered)
    assert "run_recovered" in _types(events)
    assert "baseline_initialized" in _types(events)


class _Pusher:
    def __init__(self, *, initialized: bool, send_ok: bool = True):
        self.initialized = initialized
        self.send_ok = send_ok
        self.messages: list[tuple[str, str]] = []

    def initialize(self) -> bool:
        return self.initialized

    def send_markdown(self, title: str, content: str) -> bool:
        self.messages.append((title, content))
        return self.send_ok


class _SequencePusher(_Pusher):
    def __init__(self, results: list[bool]):
        super().__init__(initialized=True)
        self.results = iter(results)

    def send_markdown(self, title: str, content: str) -> bool:
        self.messages.append((title, content))
        return next(self.results)


def test_process_summary_persists_pending_and_retries_after_failure(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "runs"
    report_dir = tmp_path / "reports"
    baseline_date = "2026-07-23"
    baseline = _summary(
        baseline_date,
        candidate=_item("600001", baseline_date),
    )
    first_pusher = _Pusher(initialized=True)
    first = monitor_daily.process_summary(
        baseline,
        state_dir=state_dir,
        report_dir=report_dir,
        monitor_markdown="# monitor\n",
        dry_run=False,
        push=True,
        push_allowed=True,
        now=datetime(2026, 7, 23, 22, 20),
        pusher_factory=lambda: first_pusher,
    )
    assert first["push_status"] == "no_changes"
    assert first_pusher.messages == []

    changed_date = "2026-07-24"
    changed = _summary(
        changed_date,
        waiting=_item("600001", changed_date, support=False),
    )
    missing_credentials = _Pusher(initialized=False)
    failed = monitor_daily.process_summary(
        changed,
        state_dir=state_dir,
        report_dir=report_dir,
        monitor_markdown="# monitor\n",
        dry_run=False,
        push=True,
        push_allowed=True,
        now=datetime(2026, 7, 24, 22, 20),
        pusher_factory=lambda: missing_credentials,
    )
    assert failed["push_status"] == "credentials_missing"
    assert failed["pending_count"] >= 1

    retry_pusher = _Pusher(initialized=True)
    retried = monitor_daily.process_summary(
        changed,
        state_dir=state_dir,
        report_dir=report_dir,
        monitor_markdown="# monitor\n",
        dry_run=False,
        push=True,
        push_allowed=True,
        now=datetime(2026, 7, 24, 22, 25),
        pusher_factory=lambda: retry_pusher,
    )
    assert retried["push_status"] == "success"
    assert retried["pending_count"] == 0
    assert retried["sent_count"] >= 1
    assert retry_pusher.messages
    assert Path(retried["snapshot_path"]).exists()
    assert Path(retried["report_path"]).exists()
    state = json.loads((state_dir / "state.json").read_text(encoding="utf-8"))
    assert state["pending_events"] == []
    assert state["sent_event_ids"]
    final_records = sorted(
        (state_dir / "attempts").glob("*/*-final.json")
    )
    planned_records = sorted(
        (state_dir / "attempts").glob("*/*-planned.json")
    )
    delivery_records = sorted(
        (state_dir / "attempts").glob("*/*-delivery-*.json")
    )
    assert len(final_records) == 3
    assert len(planned_records) == 3
    assert len(delivery_records) == 1
    failed_audit = json.loads(
        next(
            path
            for path in final_records
            if json.loads(path.read_text(encoding="utf-8"))["result"][
                "push_status"
            ]
            == "credentials_missing"
        ).read_text(encoding="utf-8")
    )
    sent_audit = json.loads(
        delivery_records[0].read_text(encoding="utf-8")
    )
    assert failed_audit["result"]["events"]
    assert sent_audit["status"] == "sent"
    assert {
        event["event_id"] for event in failed_audit["result"]["events"]
        if event["notify"]
    } == {
        event["event_id"] for event in sent_audit["events"]
    }


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    date = "2026-07-24"
    result = monitor_daily.process_summary(
        _summary(date, candidate=_item("600001", date)),
        state_dir=tmp_path / "runs",
        report_dir=tmp_path / "reports",
        monitor_markdown="# monitor\n",
        dry_run=True,
        push=False,
        push_allowed=False,
        now=datetime(2026, 7, 24, 22, 20),
    )

    assert result["push_status"] == "dry_run"
    assert not (tmp_path / "runs").exists()
    assert not (tmp_path / "reports").exists()


def test_push_gate_is_preview_only_and_does_not_advance_state(
    tmp_path: Path,
) -> None:
    result = monitor_daily.process_summary(
        _summary(
            "2026-07-24",
            candidate=_item("600001", "2026-07-24"),
        ),
        state_dir=tmp_path / "runs",
        report_dir=tmp_path / "reports",
        monitor_markdown="# monitor\n",
        dry_run=False,
        push=True,
        push_allowed=False,
        now=datetime(2026, 7, 24, 22, 20),
    )

    assert result["mode"] == "push-gated"
    assert result["push_status"] == "gate_blocked"
    assert result["snapshot_path"] is None
    assert result["attempt_path"] is None
    assert not (tmp_path / "runs").exists()
    assert not (tmp_path / "reports").exists()


def test_automatic_gate_failure_persists_blocked_pending_without_push(
    tmp_path: Path,
) -> None:
    pusher = _Pusher(initialized=True)
    result = monitor_daily.process_summary(
        _summary(
            "2026-07-24",
            status="blocked",
            error="calendar unavailable",
        ),
        state_dir=tmp_path / "runs",
        report_dir=tmp_path / "reports",
        monitor_markdown="# blocked\n",
        dry_run=False,
        push=True,
        push_allowed=False,
        persist_gate_blocked=True,
        now=datetime(2026, 7, 24, 22, 20),
        pusher_factory=lambda: pusher,
    )

    assert result["mode"] == "push-gated-persisted"
    assert result["push_status"] == "gate_blocked"
    assert result["pending_count"] == 1
    assert pusher.messages == []
    assert Path(result["snapshot_path"]).exists()
    assert Path(result["attempt_path"]).exists()
    state = json.loads(
        (tmp_path / "runs" / "state.json").read_text(encoding="utf-8")
    )
    assert state["last_health"]["run_status"] == "blocked"
    assert len(state["pending_events"]) == 1
    assert state["suppressed_event_ids"] == []


def test_pending_chunks_respect_count_and_utf8_byte_budget() -> None:
    events = [
        {
            "event_id": f"event-{index:03d}",
            "event_type": "monthly_ma5_reclaimed",
            "target_date": "2026-07-24",
            "seed_month_end": SEED_MONTH_END,
            "stock_code": f"{index:06d}",
            "stock_name": "超长中文简称" * 80,
            "before": False,
            "after": True,
            "notify": True,
        }
        for index in range(65)
    ]

    chunks = monitor_daily._pending_chunks(events)

    assert [event["event_id"] for chunk in chunks for event in chunk] == [
        event["event_id"] for event in events
    ]
    assert all(len(chunk) <= monitor_daily.PUSH_CHUNK_SIZE for chunk in chunks)
    assert all(
        len(monitor_daily.render_push_markdown(chunk).encode("utf-8"))
        <= monitor_daily.PUSH_MAX_BYTES
        for chunk in chunks
    )


def test_partial_chunk_delivery_is_durable_and_reconstructable(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "runs"
    report_dir = tmp_path / "reports"
    first_date = "2026-07-23"
    baseline = _summary(first_date)
    baseline["candidates"] = [
        _item(f"{index:06d}", first_date, support=True)
        for index in range(65)
    ]
    baseline["counts"]["monthly_seed_total"] = 65
    monitor_daily.process_summary(
        baseline,
        state_dir=state_dir,
        report_dir=report_dir,
        monitor_markdown="# monitor\n",
        dry_run=False,
        push=True,
        push_allowed=True,
        now=datetime(2026, 7, 23, 22, 20),
        pusher_factory=lambda: _Pusher(initialized=True),
    )
    second_date = "2026-07-24"
    changed = _summary(second_date)
    changed["waiting_monthly_reclaim"] = [
        _item(f"{index:06d}", second_date, support=False)
        for index in range(65)
    ]
    changed["counts"]["monthly_seed_total"] = 65
    pusher = _SequencePusher([True, False])

    result = monitor_daily.process_summary(
        changed,
        state_dir=state_dir,
        report_dir=report_dir,
        monitor_markdown="# monitor\n",
        dry_run=False,
        push=True,
        push_allowed=True,
        now=datetime(2026, 7, 24, 22, 20),
        pusher_factory=lambda: pusher,
    )

    assert result["push_status"] == "failed"
    assert result["sent_count"] == monitor_daily.PUSH_CHUNK_SIZE
    assert result["pending_count"] == 65 - monitor_daily.PUSH_CHUNK_SIZE
    delivery_records = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(
            (state_dir / "attempts" / second_date).glob(
                "*-delivery-*.json"
            )
        )
    ]
    assert [record["status"] for record in delivery_records] == [
        "sent",
        "failed",
    ]
    assert sum(
        len(record["events"]) for record in delivery_records
    ) == 60
    final_record = json.loads(
        Path(result["attempt_path"]).read_text(encoding="utf-8")
    )
    assert final_record["result"]["pending_count"] == 35
    assert len(final_record["result"]["events"]) == 65


def test_no_push_suppresses_new_events_but_keeps_old_pending(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "runs"
    report_dir = tmp_path / "reports"
    first_date = "2026-07-23"
    monitor_daily.process_summary(
        _summary(first_date, candidate=_item("600001", first_date)),
        state_dir=state_dir,
        report_dir=report_dir,
        monitor_markdown="# monitor\n",
        dry_run=False,
        push=False,
        push_allowed=False,
        now=datetime(2026, 7, 23, 22, 20),
    )
    second_date = "2026-07-24"
    result = monitor_daily.process_summary(
        _summary(
            second_date,
            waiting=_item("600001", second_date, support=False),
        ),
        state_dir=state_dir,
        report_dir=report_dir,
        monitor_markdown="# monitor\n",
        dry_run=False,
        push=False,
        push_allowed=True,
        now=datetime(2026, 7, 24, 22, 20),
    )

    assert result["push_status"] == "not_requested"
    assert result["pending_count"] == 0
    state = json.loads((state_dir / "state.json").read_text(encoding="utf-8"))
    assert state["suppressed_event_ids"]
