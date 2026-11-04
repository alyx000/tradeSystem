"""四指数固定15/30分钟成交额同比；全部合成数据，禁止真实推送。"""
import argparse
import json
import sqlite3
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from providers.base import DataResult
from providers.eastmoney_provider import EastmoneyProvider, _parse_index_bars, INTRADAY_INSTRUMENTS
from services.index_volume_monitor.core import calendar_basis, windows_at, compare_window, render_alert
from services.index_volume_monitor.service import run_index_volume
from services.index_volume_monitor.rules import INDEX_NAMES
from services.index_volume_monitor import rules as rule_config
from services.intraday_monitor.state import save_state

TZ = ZoneInfo("Asia/Shanghai")
CODE = "000001.SH"


def moment(hm="10:03", day="2026-11-04"):
    return datetime.fromisoformat(f"{day}T{hm}:00").replace(tzinfo=TZ)


def bars(code=CODE, current="150.00", previous="100.00"):
    return [{"ts_code": code, "trade_time": f"{day} {hm}", "period_minutes": 15,
             "amount": amount, "amount_unit": "CNY"}
            for day, amount in (("2026-11-03", previous), ("2026-11-04", current))
            for hm in ("09:45", "10:00", "10:15", "10:30", "11:15", "11:30", "13:15", "13:30", "14:45", "15:00")]


def envelope(code=CODE, lines=None):
    secid, name = INTRADAY_INSTRUMENTS[code]
    return {"rc": 0, "data": {"code": code[:6], "name": name, "market": int(secid[0]),
            "klines": lines or ["2026-11-03 10:00,100,101,102,99,123,100.00",
                                 "2026-11-04 10:00,100,101,102,99,123,150.00"]}}


@pytest.mark.parametrize("code", INDEX_NAMES)
def test_source_identity_amount_units_all_four(code):
    rows = _parse_index_bars(envelope(code), code, "2026-11-03", "2026-11-04")
    assert rows[-1]["amount"] == "150.00"
    assert rows[-1]["amount_unit"] == "CNY" and rows[-1]["ts_code"] == code
    assert len(_parse_index_bars(envelope(code), code, "2026-11-04", "2026-11-04")) == 1


@pytest.mark.parametrize("path,value", [
    (("rc",), False), (("rc",), 1), (("data", "code"), "399001"),
    (("data", "name"), "其他指数"), (("data", "market"), False),
    (("data", "market"), 0), (("data", "klines"), []), (("data", "klines"), None),
])
def test_source_rejects_wrong_envelope(path, value):
    body = envelope()
    target = body if len(path) == 1 else body[path[0]]
    target[path[-1]] = value
    with pytest.raises(ValueError):
        _parse_index_bars(body, CODE, "2026-11-03", "2026-11-04")


@pytest.mark.parametrize("line", [
    "2026-11-04 10:00,100,101,102,99,123,NaN",
    "2026-11-04 10:00,100,101,102,99,123,-1",
    "2026-11-04 10:00,100,101,102,99,123,1e6",
    "2026-11-04 10:00,100,101,102,99,123,true",
    "2026-11-04 10:01,100,101,102,99,123,1",
    "2026-11-04 13:00,100,101,102,99,123,1",
    "2026-11-04 09:30,100,101,102,99,123,1",
    "2026-11-04 10:00,100,101,100,99,123,1",
    "2026-11-04 10:00,100,101,102,99,-1,1",
    "2026-11-04 10:00,NaN,101,102,99,123,1",
    "2026-11-04 10:00,100,101,102,99,123,1,2",
])
def test_source_dirty_rows_fail_closed(line):
    with pytest.raises(ValueError):
        _parse_index_bars(envelope(lines=[line]), CODE, "2026-11-03", "2026-11-04")


def test_source_duplicates_reverse_zero_and_absent_range():
    lines = envelope()["data"]["klines"]
    for bad in (lines[::-1], [lines[0], lines[0]]):
        with pytest.raises(ValueError):
            _parse_index_bars(envelope(lines=bad), CODE, "2026-11-03", "2026-11-04")
    assert _parse_index_bars(envelope(lines=[lines[0].rsplit(",", 1)[0]+",0"]), CODE,
                             "2026-11-03", "2026-11-04")[0]["amount"] == "0"
    with pytest.raises(ValueError):
        _parse_index_bars(envelope(), CODE, "2026-11-05", "2026-11-05")


def test_provider_http_bounded_and_query_contract(monkeypatch):
    p = EastmoneyProvider()
    class Session:
        def __init__(self):
            self.calls, self.closed = [], False
        def __enter__(self):
            return self
        def __exit__(self, *a):
            self.closed = True
        def get(self, url, *, params, timeout):
            self.calls.append((url, params, timeout))
            if len(self.calls) == 1:
                raise OSError("transient")
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: envelope())
    s = Session()
    monkeypatch.setattr(p, "_make_session", lambda: s)
    result = p.get_index_intraday_bars(CODE, "2026-11-03", "2026-11-04")
    assert result.success and result.source == "eastmoney:index_15min" and s.closed
    assert len(s.calls) == 2 and s.calls[-1][2] == (2, 3)
    assert s.calls[-1][1]["secid"] == "1.000001"
    assert s.calls[-1][1]["klt"] == "15" and s.calls[-1][1]["fqt"] == "0"
    assert s.calls[-1][1]["fields2"] == "f51,f52,f53,f54,f55,f56,f57"
    assert not p.get_index_intraday_bars("bad", "2026-11-03", "2026-11-04").success
    assert len(s.calls) == 2
    monkeypatch.setattr(s, "get", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    assert not p.get_index_intraday_bars(CODE, "2026-11-03", "2026-11-04").success


@pytest.mark.parametrize("hm,periods", [
    ("09:45", []), ("09:46", [15]), ("09:48", [15]), ("09:50", [15]),
    ("09:51", []), ("10:03", [15, 30]), ("11:33", [15, 30]),
    ("13:00", []), ("13:18", [15]), ("13:33", [15, 30]),
    ("15:03", [15, 30]), ("15:06", []), ("22:00", []),
])
def test_completed_fixed_slot_grid(hm, periods):
    assert [p for p, _ in windows_at(moment(hm))] == periods


@pytest.mark.parametrize("minutes", [15, 30])
@pytest.mark.parametrize("current,matched", [("150", True), ("149.99999999", False), ("0", False), ("151", True)])
def test_exact_fifty_boundary_and_30min_sum(minutes, current, matched):
    result = compare_window(CODE, minutes, moment("10:00"), "2026-11-03", bars(current=current))
    assert result["matched"] is matched
    assert float(result["current_amount"]) == float(current)*(minutes//15)
    assert "2026-11-03" in render_alert([result])


@pytest.mark.parametrize("field,value", [("amount", "NaN"), ("amount", True), ("amount", -1),
                                        ("amount_unit", "万元"), ("ts_code", "399001.SZ"),
                                        ("period_minutes", 30), ("trade_time", "2026-11-03 10:01")])
def test_bad_data_not_zero_or_wrong_identity(field, value):
    rows = bars()
    rows[0][field] = value
    with pytest.raises((ValueError, TypeError)):
        compare_window(CODE, 15, moment("10:00"), "2026-11-03", rows)


def test_missing_half_does_not_substitute_cumulative_or_nearest_day():
    rows = [r for r in bars() if r["trade_time"] != "2026-11-03 09:45"]
    assert compare_window(CODE, 15, moment("10:00"), "2026-11-03", rows)["matched"]
    for period, end, prev, data in [
        (30, moment("10:00"), "2026-11-03", rows),
        (15, moment("10:00"), "2026-11-02", bars()),
        (15, moment("10:00"), "2026-11-03", bars(previous="0")),
        (30, moment("13:15"), "2026-11-03", bars()),
        (30, moment("09:45"), "2026-11-03", bars()),
        (15, moment("10:00"), "2026-11-03", bars()+bars()[:1]),
    ]:
        with pytest.raises(ValueError):
            compare_window(CODE, period, end, prev, data)


@pytest.fixture
def calendar(tmp_path):
    path = tmp_path/"calendar.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE trade_calendar(exchange TEXT,date TEXT,is_open INTEGER)")
    conn.executemany("INSERT INTO trade_calendar VALUES('SSE',?,?)", [
        ("2026-10-30", 1), ("2026-10-31", 0), ("2026-11-01", 0),
        ("2026-11-02", 1), ("2026-11-03", 1), ("2026-11-04", 1), ("2026-11-05", 1)])
    conn.commit()
    conn.close()
    return path


def test_calendar_strict_previous_open_day_natural_gap(calendar):
    assert calendar_basis("2026-11-02", calendar) == ("complete", "2026-10-30")
    assert calendar_basis("2026-11-01", calendar)[0] == "non_trade_day"
    assert calendar_basis("2026-11-06", calendar)[0] == "blocked_calendar"
    conn = sqlite3.connect(calendar)
    conn.execute("DELETE FROM trade_calendar WHERE date='2026-10-31'")
    conn.commit()
    conn.close()
    assert calendar_basis("2026-11-02", calendar)[0] == "blocked_calendar"


class Registry:
    def __init__(self):
        self.calls, self.failures, self.dirty = [], set(), {}
    def get_provider(self, name):
        assert name == "eastmoney"
        return SimpleNamespace(_initialized=True, supports=lambda c: c == "get_index_intraday_bars")
    def call_specific(self, provider, method, code, previous, today):
        self.calls.append((provider, method, code, previous, today))
        if code in self.failures:
            return DataResult(None, "eastmoney:index_15min", error="source failed")
        return DataResult(self.dirty.get(code, bars(code)), "eastmoney:index_15min")


class Pusher:
    def __init__(self):
        self.messages, self.ok = [], True
    def initialize(self):
        return True
    def send_markdown(self, **kw):
        self.messages.append(kw)
        return self.ok


@pytest.fixture
def setup(tmp_path, calendar):
    registry, pusher = Registry(), Pusher()
    options = {"input_by": "pytest", "state_path": tmp_path/"state.json", "report_root": tmp_path/"reports",
               "db_path": calendar, "clock": lambda: moment(), "pusher_factory": lambda: pusher}
    return registry, pusher, options


def test_full_four_index_eight_events_single_delivery_and_dedup(setup):
    registry, pusher, kw = setup
    result = run_index_volume(registry, **kw)
    assert result["status"] == "complete" and result["pushed"]
    assert len(result["events"]) == 8 and len(pusher.messages) == 1
    assert len(registry.calls) == 4 and all(c[3:] == ("2026-11-03", "2026-11-04") for c in registry.calls)
    state = json.loads(kw["state_path"].read_text())
    assert len(state["sent_event_ids"]) == 8 and not state["pending_events"]
    assert kw["state_path"].stat().st_mode & 0o777 == 0o600
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in kw["report_root"].rglob("*.json"))
    again = run_index_volume(registry, **kw)
    assert again["status"] == "already_checked" and len(registry.calls) == 4 and len(pusher.messages) == 1


def test_nonmatch_and_partial_failure_preserve_truth(setup):
    registry, pusher, kw = setup
    registry.failures.add(CODE)
    for code in list(INDEX_NAMES)[1:]:
        registry.dirty[code] = bars(code, current="149")
    result = run_index_volume(registry, **kw)
    assert result["status"] == "partial" and not result["pushed"] and not pusher.messages
    assert len(result["evaluations"]) == 6 and len(result["errors"]) == 2
    assert len(json.loads(kw["state_path"].read_text())["checked_event_ids"]) == 6
    registry.failures.clear()
    result = run_index_volume(registry, **kw)
    assert result["status"] == "complete" and len(result["events"]) == 2 and len(registry.calls) == 5


def test_all_sources_fail_no_checked_advance_or_push(setup):
    registry, pusher, kw = setup
    registry.failures = set(INDEX_NAMES)
    result = run_index_volume(registry, **kw)
    assert result["status"] == "source_failed" and not pusher.messages
    assert json.loads(kw["state_path"].read_text())["checked_event_ids"] == []


def test_push_retry_same_window_no_refetch(setup):
    registry, pusher, kw = setup
    pusher.ok = False
    first = run_index_volume(registry, **kw)
    assert first["status"] == "push_failed" and first["pending_count"] == 8
    pusher.ok = True
    again = run_index_volume(registry, **kw)
    assert again["pushed"] and again["pending_count"] == 0
    assert len(registry.calls) == 4 and len(pusher.messages) == 2


def test_pending_expires_at_new_slot_not_resent(setup):
    registry, pusher, kw = setup
    pusher.ok = False
    run_index_volume(registry, **kw)
    old_ids = json.loads(kw["state_path"].read_text())["checked_event_ids"]
    pusher.ok = True
    kw["clock"] = lambda: moment("10:18")
    result = run_index_volume(registry, **kw)
    assert len(result["events"]) == 4 and result["pushed"]
    state = json.loads(kw["state_path"].read_text())
    assert set(old_ids) == set(state["expired_event_ids"])
    assert not set(old_ids) & set(state["sent_event_ids"])


@pytest.mark.parametrize("clock_values", [["10:03", "10:06"], ["10:03", "10:03", "10:06"]])
def test_expiry_during_fetch_or_before_send_never_pushes(setup, clock_values):
    registry, pusher, kw = setup
    values = iter(clock_values)
    kw["clock"] = lambda: moment(next(values))
    result = run_index_volume(registry, **kw)
    assert result["status"] == "expired_window" and not pusher.messages
    if kw["state_path"].exists():
        assert not json.loads(kw["state_path"].read_text())["pending_events"]


@pytest.mark.parametrize("corrupt", ["not-json", '{"version":2}', '{"version":1,"rules":{"old":{}}}'])
def test_corrupt_or_other_state_never_overwritten(setup, corrupt):
    registry, pusher, kw = setup
    kw["state_path"].write_text(corrupt)
    assert run_index_volume(registry, **kw)["status"] == "state_error"
    assert kw["state_path"].read_text() == corrupt and not registry.calls and not pusher.messages


@pytest.mark.parametrize("field,value", [("growth_pct", "9999"), ("start_time", "00:00"),
                                        ("previous_amount", "0"), ("previous_trade_date", "bad"),
                                        ("matched", False), ("threshold_pct", 0)])
def test_corrupt_pending_payload_cannot_send(setup, field, value):
    registry, pusher, kw = setup
    pusher.ok = False
    run_index_volume(registry, **kw)
    state = json.loads(kw["state_path"].read_text())
    state["pending_events"][0][field] = value
    save_state(kw["state_path"], state)
    before = kw["state_path"].read_bytes()
    pusher.ok = True
    assert run_index_volume(registry, **kw)["status"] == "state_error"
    assert kw["state_path"].read_bytes() == before and len(pusher.messages) == 1


def test_dry_run_no_files_no_pusher(setup):
    registry, pusher, kw = setup
    result = run_index_volume(registry, dry_run=True, **kw)
    assert result["status"] == "dry_run" and len(result["events"]) == 8
    assert not pusher.messages and not kw["state_path"].exists() and not kw["report_root"].exists()


@pytest.mark.parametrize("case,expected", [("outside", "outside_slot"), ("closed", "non_trade_day"),
                                         ("missing", "blocked_calendar"), ("blank", "invalid_request")])
def test_guards_no_calls_or_files(setup, case, expected):
    registry, pusher, kw = setup
    if case == "outside":
        kw["clock"] = lambda: moment("12:00")
    elif case == "closed":
        kw["clock"] = lambda: moment(day="2026-11-01")
    elif case == "missing":
        kw["clock"] = lambda: moment(day="2026-11-06")
    else:
        kw["input_by"] = " "
    assert run_index_volume(registry, **kw)["status"] == expected
    assert not registry.calls and not pusher.messages and not kw["state_path"].exists()


def test_unavailable_capability_no_silent_fallback(setup, monkeypatch):
    registry, pusher, kw = setup
    monkeypatch.setattr(registry, "get_provider", lambda name: None)
    assert run_index_volume(registry, **kw)["status"] == "source_failed"
    assert not registry.calls and not pusher.messages


def test_new_phase_order_exception_isolation_and_actor(monkeypatch):
    import services.index_volume_monitor as volume
    from services.intraday_monitor import service, market_scan
    order = []
    monkeypatch.setattr(market_scan, "run_market_scan", lambda *a, **k: order.append("scan") or {"status": "complete"})
    def fail(*a, **kw):
        order.append("volume")
        assert kw["input_by"] == "pytest"
        raise OSError("broken")
    monkeypatch.setattr(volume, "run_index_volume", fail)
    monkeypatch.setattr(service, "run_check", lambda *a, **k: order.append("price") or {"status": "complete"})
    result = service.run_all_checks(object(), input_by="pytest")
    assert order == ["scan", "volume", "price"] and result["status"] == "state_error"
    order.clear()
    assert service.run_all_checks(object(), input_by=" ")["status"] == "invalid_request" and not order


def test_invalid_multiple_config_propagates_to_combined_receipt(setup, monkeypatch):
    registry, pusher, kw = setup
    monkeypatch.setattr(rule_config, "DEFAULT_INDEX_VOLUME_RULES", (rule_config.INDEX_VOLUME_GROWTH,)*2)
    assert run_index_volume(registry, **kw)["status"] == "invalid_request"
    assert not registry.calls and not pusher.messages and not kw["state_path"].exists()
    import services.index_volume_monitor as volume
    from services.intraday_monitor import service, market_scan
    monkeypatch.setattr(volume, "run_index_volume", lambda *a, **k: {"status": "invalid_request", "errors": ["非法配置"]})
    monkeypatch.setattr(market_scan, "run_market_scan", lambda *a, **k: {"status": "complete"})
    monkeypatch.setattr(service, "run_check", lambda *a, **k: {"status": "complete"})
    assert service.run_all_checks(object())["status"] == "invalid_request"


def test_cli_actor_and_blank_validation(monkeypatch, capsys):
    from cli import intraday_monitor
    import main
    parser = argparse.ArgumentParser()
    intraday_monitor.register_subparser(parser.add_subparsers())
    args = parser.parse_args(["intraday-monitor", "check", "--input-by", "launchd", "--json"])
    calls = []
    monkeypatch.setattr(main, "setup_providers", lambda c: object())
    monkeypatch.setattr(intraday_monitor, "run_all_checks", lambda r, **kw: calls.append(kw) or {"status": "complete"})
    assert intraday_monitor.handle_command({}, args) == 0 and calls[0]["input_by"] == "launchd"
    args.input_by = " "
    assert intraday_monitor.handle_command({}, args) == 1 and len(calls) == 1


def test_lunch_grace_does_not_relax_price_session_guard():
    from services.intraday_monitor.guards import is_intraday_session
    assert windows_at(moment("11:33")) and not is_intraday_session(moment("11:33"))


@pytest.mark.parametrize("with_pending", [False, True])
def test_disabled_default_stops_fetch_push_and_preserves_pending(setup, monkeypatch, with_pending):
    registry, pusher, kw = setup
    if with_pending:
        pusher.ok = False
        assert run_index_volume(registry, **kw)["pending_count"] == 8
    before = kw["state_path"].read_bytes() if kw["state_path"].exists() else None
    calls, messages = len(registry.calls), len(pusher.messages)
    monkeypatch.setattr(rule_config, "DEFAULT_INDEX_VOLUME_RULES", ())
    result = run_index_volume(registry, **kw)
    assert result["status"] == "no_rules" and not result["pushed"]
    assert len(registry.calls) == calls and len(pusher.messages) == messages
    assert (kw["state_path"].read_bytes() if kw["state_path"].exists() else None) == before


@pytest.mark.parametrize("current,matched", [("199.99999999", False), ("200", True)])
def test_actual_rule_codes_periods_threshold_consumed(setup, monkeypatch, current, matched):
    registry, pusher, kw = setup
    rule = rule_config.IndexVolumeRule("configured-100pct", growth_pct=100, periods=(30,), codes=(CODE,))
    monkeypatch.setattr(rule_config, "DEFAULT_INDEX_VOLUME_RULES", (rule,))
    registry.dirty[CODE] = bars(current=current)
    result = run_index_volume(registry, **kw)
    assert result["status"] == "complete" and result["pushed"] is matched
    assert len(registry.calls) == 1 and len(result["evaluations"]) == 1
    e = result["evaluations"][0]
    assert e["period_minutes"] == 30 and e["threshold_pct"] == 100 and e["rule_id"] == rule.rule_id
    assert rule.rule_id in e["event_id"]
    if matched:
        assert "触发条件≥100%" in pusher.messages[0]["content"]


def test_rule_identity_change_does_not_inherit_checked_baseline(setup, monkeypatch):
    registry, pusher, kw = setup
    run_index_volume(registry, **kw)
    rule = rule_config.IndexVolumeRule("replacement-rule")
    monkeypatch.setattr(rule_config, "DEFAULT_INDEX_VOLUME_RULES", (rule,))
    result = run_index_volume(registry, **kw)
    assert result["pushed"] and len(result["events"]) == 8 and len(registry.calls) == 8


@pytest.mark.parametrize("changes", [{"growth_pct": True}, {"growth_pct": 0}, {"periods": (60,)},
                                      {"periods": ()}, {"codes": ()}, {"codes": ("bad",)}])
def test_rule_configuration_validation(changes):
    with pytest.raises(ValueError):
        rule_config.IndexVolumeRule("invalid", **changes)


def test_expiry_during_push_initialization_never_sends(setup, monkeypatch):
    registry, pusher, kw = setup
    now = [moment()]
    kw["clock"] = lambda: now[0]
    def slow_init():
        now[0] = moment("10:06")
        return True
    monkeypatch.setattr(pusher, "initialize", slow_init)
    result = run_index_volume(registry, **kw)
    assert result["status"] == "expired_window" and not pusher.messages
    assert not json.loads(kw["state_path"].read_text())["pending_events"]
