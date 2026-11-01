"""平均股价26元±1%区间及20个交易日；所有行情/推送均隔离。"""
import argparse
import json
from datetime import date, datetime, timedelta

import pytest

from cli import intraday_monitor
from providers.registry import ProviderRegistry
from providers.tdx_provider import TdxProvider
from services.intraday_monitor import AVG_PRICE_NEAR_26_20261102_27 as RULE
from services.intraday_monitor.rules import DEFAULT_RULES, SSE_COMPOSITE_RECLAIM_3955
from services.intraday_monitor.service import run_check, run_e2e_test
from tests.test_intraday_monitor_service import _Registry, _Pusher, _calendar, TZ
from tests.test_tdx_intraday_quotes import minute_bars


DAYS = tuple(date(2026, 11, 1) + timedelta(days=i) for i in range(30))
OPEN_DAYS = tuple(d.isoformat() for d in DAYS if d.weekday() < 5 and d.day <= 27)
CLOSED_DAYS = tuple(d.isoformat() for d in DAYS if d.weekday() >= 5)


def _setup(tmp_path, day="2026-11-02", bars=None):
    db = _calendar(tmp_path, dates=(*OPEN_DAYS, "2026-11-30"), closed_dates=CLOSED_DAYS)
    provider = TdxProvider()
    registry = ProviderRegistry()
    registry.register(provider)
    rows = minute_bars() if bars is None else bars
    for row in rows:
        row["datetime"] = day + " " + row["datetime"].split()[1]
    # 只隔离传输；解析与生产registry均真实运行。
    provider._connect = lambda *args, **kwargs: (_Api(), kwargs["transform"](rows), "")
    now = datetime.fromisoformat(day + "T10:00:00").replace(tzinfo=TZ)
    fetch = provider.get_realtime_quotes
    provider.get_realtime_quotes = lambda codes: fetch(codes, now=now)
    return registry, _Pusher(), db, tmp_path / "state.json", now


class _Api:
    def disconnect(self):
        pass


def _run(setup, **kwargs):
    registry, pusher, db, state, now = setup
    return run_check(registry, rules=(RULE,), now=now, db_path=db,
                     state_path=state, pusher_factory=lambda: pusher, **kwargs)


@pytest.mark.parametrize("price,matched", [(25.739, False), (25.74, True), (26, True),
                                         (26.26, True), (26.261, False)])
def test_fixed_band_includes_both_edges(price, matched):
    assert (RULE.code, RULE.provider, RULE.threshold, RULE.proximity_pct) == ("880003.TDX", "tdx", 26, 1)
    assert RULE.threshold_mode == "fixed" and RULE.inclusive and RULE.emit_on_initial_match
    assert RULE.is_active(price) is matched
    assert RULE.valid_from == date(2026, 11, 2) and RULE.valid_until == date(2026, 11, 27)
    assert RULE in DEFAULT_RULES and len(OPEN_DAYS) == 20


@pytest.mark.parametrize("day", OPEN_DAYS)
def test_twenty_open_days_including_final_day(tmp_path, day):
    setup = _setup(tmp_path, day)
    result = _run(setup)
    assert result["status"] == "complete" and len(result["events"]) == 1
    assert len(setup[1].messages) == 1


@pytest.mark.parametrize("day", ("2026-10-30", *CLOSED_DAYS, "2026-11-30"))
def test_no_quotes_or_push_outside_effective_open_days(tmp_path, day):
    setup = _setup(tmp_path, day)
    setup[0].get_provider("tdx")._connect = lambda *a, **k: pytest.fail("不应请求行情")
    result = _run(setup)
    assert result["status"] in {"non_trade_day", "no_active_rules"}
    assert not setup[1].messages


def test_initial_dedupe_exit_reentry_and_fixed_price_wording(tmp_path):
    setup = _setup(tmp_path)
    registry, pusher, db, state, now = setup
    counts = []
    for n, price in enumerate((26, 26.1, 26.261, 26.26)):
        rows = minute_bars()
        rows[-1].update({"close": price, "high": 27, "low": 25})
        from providers.tdx_provider import _minute_quote
        registry.get_provider("tdx")._connect = lambda *a, **k: (_Api(), _minute_quote(rows), "")
        result = _run((registry, pusher, db, state, now + timedelta(minutes=3*n)))
        counts.append(len(result["events"]))
    assert counts == [1, 0, 0, 1]
    message = pusher.messages[0][1]
    assert "平均股价" in message and "25.7400～26.2600元" in message
    assert "监控价±1%" in message and "MA±" not in message
    assert "tdx:880003_1min" in message and "分钟价" in message


@pytest.mark.parametrize("kind", ["missing_calendar", "stale_day", "stale_minute", "future", "source_failed"])
def test_missing_or_stale_evidence_preserves_baseline_and_no_alert(tmp_path, kind):
    setup = _setup(tmp_path)
    registry, pusher, db, state, now = setup
    provider = registry.get_provider("tdx")
    if kind == "missing_calendar":
        db = tmp_path / "missing.db"
    elif kind == "source_failed":
        provider._connect = lambda *a, **k: (None, None, "模拟源失败")
    else:
        rows = minute_bars()
        old = now - timedelta(days=1) if kind == "stale_day" else now + timedelta(minutes=60) if kind == "future" else now - timedelta(minutes=11)
        for i, row in enumerate(rows):
            row["datetime"] = (old + timedelta(minutes=i-1)).strftime("%Y-%m-%d %H:%M")
        from providers.tdx_provider import _minute_quote
        provider._connect = lambda *a, **k: (_Api(), _minute_quote(rows), "")
    result = _run((registry, pusher, db, state, now))
    assert result["status"] == ("blocked_calendar" if kind == "missing_calendar" else "source_failed")
    assert not result["events"] and not pusher.messages
    if state.exists():
        assert RULE.rule_id not in json.loads(state.read_text())["rules"]


def test_preview_does_not_write_state_or_push(tmp_path):
    setup = _setup(tmp_path)
    result = _run(setup, dry_run=True)
    assert result["status"] == "dry_run" and len(result["events"]) == 1
    assert not setup[3].exists() and not setup[1].messages


def test_failed_push_retried_without_duplicate(tmp_path):
    setup = _setup(tmp_path)
    setup[1].succeed = False
    first = _run(setup)
    assert first["status"] == "push_failed"
    setup[1].succeed = True
    second = _run((*setup[:4], setup[4] + timedelta(minutes=3)))
    assert second["status"] == "complete" and not second["events"]
    assert not json.loads(setup[3].read_text())["pending_events"]


def test_source_failure_does_not_mask_other_source_alerts(tmp_path):
    setup = _setup(tmp_path)
    registry = _Registry(price=3900)
    registry.now = setup[4]
    original = registry.call_specific
    from providers.base import DataResult
    registry.call_specific = lambda provider, *args: DataResult(None, "tdx", error="模拟故障") if provider == "tdx" else original(provider, *args)
    result = run_check(registry, rules=(RULE, SSE_COMPOSITE_RECLAIM_3955), now=setup[4],
                       db_path=setup[2], state_path=setup[3], pusher_factory=lambda: setup[1])
    assert result["status"] == "partial" and not result["events"]
    assert "tdx" in " ".join(result["errors"])
    registry.price = 4000
    registry.now += timedelta(minutes=3)
    result = run_check(registry, rules=(RULE, SSE_COMPOSITE_RECLAIM_3955), now=registry.now,
                       db_path=setup[2], state_path=setup[3], pusher_factory=lambda: setup[1])
    assert result["status"] == "partial" and result["events"][0]["rule_id"] == SSE_COMPOSITE_RECLAIM_3955.rule_id
    assert len(setup[1].messages) == 1


def test_expired_pending_not_sent_through_other_active_rule(tmp_path):
    setup = _setup(tmp_path, "2026-11-27")
    setup[1].succeed = False
    first = _run(setup)
    assert first["status"] == "push_failed"
    registry = _Registry(price=3900)
    registry.now = datetime(2026, 11, 30, 10, tzinfo=TZ)
    setup[1].succeed = True
    setup[1].messages.clear()
    result = run_check(registry, rules=(RULE, SSE_COMPOSITE_RECLAIM_3955), now=registry.now,
                       db_path=setup[2], state_path=setup[3], pusher_factory=lambda: setup[1])
    assert result["status"] == "complete" and not setup[1].messages
    assert all(RULE.code not in codes for codes in registry.requested_codes)
    assert first["events"][0]["event_id"] in json.loads(setup[3].read_text())["expired_event_ids"]


def test_e2e_choice_and_mock_message_does_not_call_a_real_pusher(tmp_path):
    parser = argparse.ArgumentParser()
    intraday_monitor.register_subparser(parser.add_subparsers())
    args = parser.parse_args(["intraday-monitor", "e2e-test", "--rule-id", RULE.rule_id, "--input-by", "pytest"])
    assert args.rule_id == RULE.rule_id
    setup = _setup(tmp_path)
    result = run_e2e_test(setup[0], rule=RULE, now=setup[4], db_path=setup[2],
                          confirm_real_push=True, input_by="pytest", pusher_factory=lambda: setup[1])
    assert result["status"] == "complete" and result["production_proximity_pct"] == 1
    assert "监控价±1%" in setup[1].messages[0][1] and not setup[3].exists()


def test_0959_scan_runs_before_slow_threshold_sources(monkeypatch):
    from services.intraday_monitor import service, market_scan
    clock = [datetime(2026, 11, 2, 9, 59, 58, tzinfo=TZ)]
    calls = []
    def scan(*a, **k):
        calls.append("scan")
        assert clock[0].hour == 9
        return {"status": "complete", "events": []}
    def threshold(*a, **k):
        calls.append("threshold")
        clock[0] += timedelta(seconds=36)
        return {"status": "source_failed", "events": []}
    monkeypatch.setattr(market_scan, "run_market_scan", scan)
    monkeypatch.setattr(service, "run_check", threshold)
    result = service.run_all_checks(object())
    assert calls == ["scan", "threshold"] and result["market_scan"]["status"] == "complete"
