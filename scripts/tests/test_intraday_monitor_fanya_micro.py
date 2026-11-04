"""已下线泛亚微透的历史规则回归：显式指定规则仅用于隔离测试。"""
import json
from datetime import date, datetime, timedelta

import pytest

from services.intraday_monitor import FANYA_MICRO_NEAR_46_56_20261104_10 as RULE
from services.intraday_monitor.rules import DEFAULT_RULES
from services.intraday_monitor.service import run_check
from tests.test_intraday_monitor_service import _Registry, _Pusher, _calendar, TZ


OPEN = ("2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06",
        "2026-11-09", "2026-11-10", "2026-11-11")
CLOSED = ("2026-11-07", "2026-11-08")


def _setup(tmp_path, day="2026-11-04"):
    db = _calendar(tmp_path, dates=OPEN, closed_dates=CLOSED)
    registry = _Registry(price=46.56)
    registry.now = datetime.fromisoformat(day + "T10:00:00").replace(tzinfo=TZ)
    return registry, _Pusher(), db, tmp_path / "state.json"


def _run(setup, **kwargs):
    registry, pusher, db, state = setup
    return run_check(registry, rules=(RULE,), now=registry.now, db_path=db,
                     state_path=state, pusher_factory=lambda: pusher, **kwargs)


@pytest.mark.parametrize("price,matched", [(46.0943, False), (46.0944, True),
                                         (46.56, True), (47.0256, True), (47.0257, False)])
def test_identity_fixed_band_and_exact_inclusive_edges(price, matched):
    assert RULE.code == "688386.SH" and RULE.instrument_name == "泛亚微透"
    assert RULE.provider == "sina" and RULE.threshold_mode == "fixed"
    assert RULE.threshold == 46.56 and RULE.direction == "near"
    assert RULE.proximity_pct == 1 and RULE.inclusive and RULE.emit_on_initial_match
    assert RULE.valid_from == date(2026,11,4) and RULE.valid_until == date(2026,11,10)
    assert RULE not in DEFAULT_RULES
    assert RULE.is_active(price) is matched
    assert len([d for d in OPEN if "2026-11-04" <= d <= "2026-11-10"]) == 5


@pytest.mark.parametrize("day", OPEN[1:-1])
def test_all_five_open_days_include_final_day(tmp_path, day):
    setup = _setup(tmp_path, day)
    result = _run(setup)
    assert result["status"] == "complete" and result["pushed"]
    assert len(setup[1].messages) == 1


@pytest.mark.parametrize("day", (OPEN[0], *CLOSED, OPEN[-1]))
def test_outside_validity_or_non_trade_day_never_fetches(tmp_path, day):
    setup = _setup(tmp_path, day)
    result = _run(setup)
    assert result["status"] in {"no_active_rules", "non_trade_day"}
    assert not setup[0].requested_codes and not setup[1].messages


def test_dedupe_reentry_next_day_and_alert_wording(tmp_path):
    setup = _setup(tmp_path)
    registry, pusher, db, state = setup
    counts = []
    for price in (46.56, 46.60, 47.03, 47.0256):
        registry.price = price
        result = _run(setup)
        counts.append(len(result["events"]))
        registry.now += timedelta(minutes=3)
    assert counts == [1, 0, 0, 1]
    message = pusher.messages[0][1]
    assert "泛亚微透" in message and "688386.SH" in message
    assert "46.0944～47.0256元" in message and "监控价±1%" in message
    assert "MA±" not in message
    registry.now = datetime(2026,11,5,10,tzinfo=TZ)
    assert _run(setup)["pushed"]  # 保留既有每天首次命中语义。


@pytest.mark.parametrize("bad", ["stale", "future", "calendar", "source"])
def test_missing_evidence_no_baseline_or_push(tmp_path, bad):
    setup = _setup(tmp_path)
    registry, pusher, db, state = setup
    if bad == "stale":
        registry.quote_overrides[RULE.code] = {"quote_date":"2026-11-03"}
    elif bad == "future":
        registry.quote_overrides[RULE.code] = {"quote_time":"10:10:00"}
    elif bad == "calendar":
        setup = registry, pusher, tmp_path / "missing.db", state
    else:
        from tests.test_intraday_monitor_service import _Result
        registry.call_specific = lambda *args: _Result(None, error="模拟源失败")
    result = _run(setup)
    assert result["status"] in {"source_failed", "blocked_calendar"}
    assert not result["events"] and not pusher.messages
    if state.exists():
        assert RULE.rule_id not in json.loads(state.read_text())["rules"]


def test_dry_run_push_failure_retry_and_expired_pending(tmp_path):
    setup = _setup(tmp_path, "2026-11-10")
    registry, pusher, db, state = setup
    preview = _run(setup, dry_run=True)
    assert preview["status"] == "dry_run" and preview["events"]
    assert not state.exists() and not pusher.messages
    pusher.send_markdown = lambda **kwargs: False
    assert _run(setup)["status"] == "push_failed"
    registry.now += timedelta(minutes=3)
    pusher.send_markdown = lambda **kwargs: True
    assert _run(setup)["pushed"]
    registry.now += timedelta(minutes=3)
    registry.price = 48
    _run(setup)
    registry.now += timedelta(minutes=3)
    registry.price = 46.56
    pusher.send_markdown = lambda **kwargs: False
    assert _run(setup)["status"] == "push_failed"
    assert json.loads(state.read_text())["pending_events"]
    registry.now = datetime(2026,11,11,10,tzinfo=TZ)
    registry.requested_codes.clear()
    pusher.send_markdown = lambda **kwargs: pytest.fail("过期事件不得发送")
    assert _run(setup)["status"] == "no_active_rules"
    saved = json.loads(state.read_text())
    assert not saved["pending_events"] and saved["expired_event_ids"]
    assert not registry.requested_codes
