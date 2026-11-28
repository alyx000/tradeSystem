from datetime import datetime
from pathlib import Path
import json
import sqlite3
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from providers.tdx_provider import TdxProvider
from services.index_volume_monitor.bar_archive import ENDS
from services.tdx_index_archive import NAMES, run_archive


DAY, PREV = "2026-11-27", "2026-11-26"


def moment(hm, day=DAY):
    return datetime.fromisoformat(day + "T" + hm).replace(tzinfo=ZoneInfo("Asia/Shanghai"))


def raw(codes=None):
    return [{"datetime": f"{day} {end}", "open": 10., "high": 11., "low": 9., "close": 10.5,
             **(codes or {})} for day in (PREV, DAY) for end in ENDS]


@pytest.fixture
def setup(tmp_path):
    db = tmp_path / "calendar.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE trade_calendar(exchange TEXT, date TEXT, is_open INTEGER)")
        conn.executemany("INSERT INTO trade_calendar VALUES('SSE',?,?)", [(PREV,1),(DAY,1),("2026-11-28",0)])
    return {"root": tmp_path / "tdx", "db_path": db, "input_by": "test", "clock": lambda: moment("10:02")}


def mock_tdx(data):
    provider = TdxProvider()
    calls = []
    def connect(category, count, code, *, transform, with_node, budget_seconds, socket_timeout):
        calls.append((category, count, code))
        best_error = "连接失败"
        for i, rows in enumerate(data.get(code, [])):
            try:
                selected = transform(rows, f"node-{i}:7709")
                return SimpleNamespace(disconnect=lambda:None), selected, ""
            except ValueError as exc:
                best_error = str(exc)
        return None, None, best_error
    provider._connect = connect
    return provider, calls


def get_day(setup, day=DAY):
    return json.loads((setup["root"] / (day + ".json")).read_text())


def test_real_slot_precise_two_requests_and_previous_complete(setup):
    p, calls = mock_tdx({code[:6]: [raw()] for code in NAMES})
    result = run_archive(p, **setup)
    assert result["status"] == "complete" and not result["pushed"]
    assert sorted(calls) == [(0,800,"880003"),(0,800,"880823")]
    assert get_day(setup, PREV)["valid_count"] == 96
    today = get_day(setup)
    assert today["valid_count"] == 12 and today["status"] == "partial"
    for code, idx in today["indices"].items():
        assert list(idx["bars"]) == list(ENDS[:6])
        assert list(idx["bars_30m"]) == ["10:00"]
        bar = idx["bars"]["10:00"]
        assert "volume" not in bar and "amount" not in bar
        assert bar["source_time"] == DAY + " 10:00" and bar["requested_code"] == code[:6]
        assert bar["market"] == 1 and bar["category"] == 0 and bar["node"] == "node-0:7709"
    assert run_archive(p, **setup)["status"] == "already_checked" and len(calls) == 2


@pytest.mark.parametrize("bad", ["identity","market","early_stamped","duplicate","reverse","ohlc","nan"])
def test_provider_does_not_accept_malformed_response_or_other_index(bad):
    source = raw({"code":"880003", "market":1})
    row = source[-1]
    if bad == "identity":
        row["code"] = "880823"
    elif bad == "market":
        row["market"] = 0
    elif bad == "early_stamped":
        row["datetime"] = DAY + " 14:59"
    elif bad == "duplicate":
        source.append(dict(row))
    elif bad == "reverse":
        source.reverse()
    elif bad == "ohlc":
        row["low"] = 15
    else:
        row["close"] = float("nan")
    p, _ = mock_tdx({"880003":[source]})
    result = p.get_tdx_index_5m("880003.TDX", (PREV,DAY), "15:00")
    assert not result.success
    assert not p.get_tdx_index_5m("000688.SH", (DAY,), "15:00").success


def test_complete_fallback_preferred_and_verified_partial_retained():
    full = raw()
    p, _ = mock_tdx({"880003":[full[:-1],full]})
    a = p.get_tdx_index_5m("880003.TDX", (PREV,DAY), "15:00")
    assert a.success and len(a.data) == 96 and {r["node"] for r in a.data} == {"node-1:7709"}
    p, _ = mock_tdx({"880003":[full[:50], full[:80]]})
    a = p.get_tdx_index_5m("880003.TDX", (DAY,), "15:00")
    assert a.success and len(a.data) == 32 and a.data[0]["node"] == "node-1:7709"


def test_failure_one_index_and_retry_preserve_old_data(setup):
    p, _ = mock_tdx({"880003":[raw()]})
    result = run_archive(p, **setup)
    assert result["status"] == "partial" and set(result["errors"]) == {"880823.TDX"}
    before = get_day(setup)["indices"]["880003.TDX"]["bars"]
    broken, _ = mock_tdx({})
    assert run_archive(broken, **setup)["status"] == "source_failed"
    assert get_day(setup)["indices"]["880003.TDX"]["bars"] == before
    good, _ = mock_tdx({code[:6]:[raw()] for code in NAMES})
    assert run_archive(good, **setup)["status"] == "complete"
    assert len(list((setup["root"] / "attempts" / DAY).glob("*.json"))) == 3


def test_calendar_and_completed_bars_dryrun_only(setup):
    p, calls = mock_tdx({code[:6]:[raw()] for code in NAMES})
    assert run_archive(p, day=DAY, **setup)["status"] == "not_closed"
    assert run_archive(p, **{**setup, "clock":lambda:moment("12:30")})["status"] == "outside_slot"
    assert run_archive(p, **{**setup, "clock":lambda:moment("10:02", "2026-11-28")})["status"] == "non_trade_day"
    assert not calls
    assert run_archive(p, dry_run=True, **setup)["status"] == "dry_run"
    assert not setup["root"].exists()
    kw = {**setup, "clock":lambda:moment("12:02", "2026-11-28")}
    assert run_archive(p, day=DAY, **kw)["status"] == "complete"
    assert get_day(setup)["valid_count"] == 96
    assert not (setup["root"] / (PREV + ".json")).exists()


def test_current_day_partial_takes_precedence_over_old_only_node():
    data = raw()
    p, _ = mock_tdx({"880003":[data[:48], data[48:54]]})
    a = p.get_tdx_index_5m("880003.TDX", (PREV,DAY), "10:00")
    assert len(a.data) == 6 and a.data[-1]["trade_time"] == DAY + " 10:00"


def test_complete_old_day_does_not_mask_sparse_retry(setup):
    data = raw()
    kw = {**setup, "clock":lambda:moment("12:02", "2026-11-28")}
    full, _ = mock_tdx({code[:6]:[data] for code in NAMES})
    assert run_archive(full, day=DAY, **kw)["status"] == "complete"
    partial, _ = mock_tdx({code[:6]:[data[-1:]] for code in NAMES})
    result = run_archive(partial, day=DAY, **kw)
    assert result["status"] == "partial" and result["attempt_coverage"][DAY]["status"] == "partial"
    assert result["archives"][DAY]["status"] == "complete" and get_day(setup)["valid_count"] == 96


def test_live_entry_exposes_archive_failure_after_original_checks(monkeypatch):
    import services.index_volume_monitor as volume
    import services.intraday_monitor.market_scan as scan
    import services.intraday_monitor.service as monitor
    import services.tdx_index_archive as tdx

    order = []
    def step(name, status="complete"):
        def run(*args, **kwargs):
            order.append(name)
            return {"status":status, "events":[], "errors":[], "pushed":False}
        return run
    monkeypatch.setattr(scan, "run_market_scan", step("scan"))
    monkeypatch.setattr(volume, "run_index_volume", step("volume"))
    monkeypatch.setattr(monitor, "run_check", step("threshold"))
    monkeypatch.setattr(tdx, "run_archive", step("tdx", "source_failed"))
    result = monitor.run_all_checks(SimpleNamespace(get_provider=lambda name: object()), input_by="test")
    assert order == ["scan", "volume", "threshold", "tdx"]
    assert result["status"] == "complete" and result["tdx_index_5m_archive"]["status"] == "source_failed"
    assert result["events"] == [] and not result["pushed"]
