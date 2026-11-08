"""顺灏股份严格突破10.41元、5个开放日；复用隔离价格规则测试底座。"""
import argparse
import json
from datetime import date, datetime, timedelta

import pytest

from services.intraday_monitor import SHUNHAO_BREAKOUT_10_41_20261109_13 as RULE
from services.intraday_monitor.rules import DEFAULT_RULES
from tests.test_intraday_monitor_junda_antu_release import _setup, _run
from tests.test_intraday_monitor_service import _Result, TZ

ACTIVE = ("2026-11-09","2026-11-10","2026-11-11","2026-11-12","2026-11-13")


@pytest.mark.parametrize("price,matched", [(10.40,False),(10.41,False),(10.42,True)])
def test_identity_strict_boundary_and_five_day_window(price,matched):
    assert (RULE.code,RULE.instrument_name,RULE.provider) == ("002565.SZ","顺灏股份","sina")
    assert RULE.rule_id == "shunhao-breakout-10-41-20261109-13" and RULE.threshold == 10.41
    assert RULE.direction == "above" and not RULE.inclusive and RULE.emit_on_initial_match
    assert RULE.threshold_mode == "fixed" and RULE.value_label == "价格" and RULE.value_unit == "元"
    assert (RULE.valid_from,RULE.valid_until) == (date(2026,11,9),date(2026,11,13))
    assert RULE in DEFAULT_RULES and len(ACTIVE) == 5 and RULE.is_active(price) is matched


@pytest.mark.parametrize("day", ACTIVE)
def test_each_open_day_including_final_day(tmp_path,day):
    setup = _setup(tmp_path,RULE,day)
    assert _run(setup,RULE)["pushed"]
    assert setup[0].requested_codes == [[RULE.code]] and len(setup[1].messages) == 1


@pytest.mark.parametrize("day", ["2026-11-06","2026-11-07","2026-11-08",
                                 "2026-11-14","2026-11-15","2026-11-16"])
def test_before_after_and_weekends_no_fetch_or_push(tmp_path,day):
    setup = _setup(tmp_path,RULE,day)
    assert _run(setup,RULE)["status"] == "no_active_rules"
    assert not setup[0].requested_codes and not setup[1].messages


def test_initial_dedup_reentry_next_day_and_alert_wording(tmp_path):
    setup = _setup(tmp_path,RULE,ACTIVE[0])
    r,p,db,state = setup
    counts = []
    for price in (10.41,10.42,10.50,10.41,10.42):
        r.price = price
        counts.append(len(_run(setup,RULE)["events"]))
        r.now += timedelta(minutes=3)
    assert counts == [0,1,0,0,1]
    assert "顺灏股份" in p.messages[0][1] and "002565.SZ" in p.messages[0][1]
    assert "已突破监控线 **10.41**元" in p.messages[0][1]
    r.now = datetime(2026,11,10,10,tzinfo=TZ)
    assert _run(setup,RULE)["pushed"]


@pytest.mark.parametrize("bad", ["calendar","source","stale_day","stale_minute","future"])
def test_failed_evidence_no_alert_or_baseline(tmp_path,bad):
    setup = _setup(tmp_path,RULE,ACTIVE[0])
    r,p,db,state = setup
    if bad == "calendar":
        setup = r,p,tmp_path/"missing.db",state
    elif bad == "source":
        r.call_specific = lambda *a: _Result(None,error="模拟源失败")
    else:
        r.quote_overrides[RULE.code] = {
            "stale_day":{"quote_date":"2026-11-06"},
            "stale_minute":{"quote_time":"09:49:00"},
            "future":{"quote_time":"10:10:00"},
        }[bad]
    out = _run(setup,RULE)
    assert out["status"] in {"blocked_calendar","source_failed"}
    assert not out["events"] and not p.messages
    if state.exists():
        assert RULE.rule_id not in json.loads(state.read_text())["rules"]


def test_dry_run_retry_and_expired_pending(tmp_path):
    setup = _setup(tmp_path,RULE,ACTIVE[-1])
    r,p,db,state = setup
    assert _run(setup,RULE,dry_run=True)["status"] == "dry_run"
    assert not state.exists() and not p.messages
    p.send_markdown = lambda **kw: False
    assert _run(setup,RULE)["status"] == "push_failed"
    r.now += timedelta(minutes=3)
    p.send_markdown = lambda **kw: True
    assert _run(setup,RULE)["pushed"]
    r.now += timedelta(minutes=3)
    assert not _run(setup,RULE)["pushed"]
    r.now += timedelta(minutes=3); r.price = 10.41
    _run(setup,RULE)
    r.now += timedelta(minutes=3); r.price = 10.42
    p.send_markdown = lambda **kw: False
    assert _run(setup,RULE)["status"] == "push_failed"
    r.now = datetime(2026,11,16,10,tzinfo=TZ)
    r.requested_codes.clear()
    p.send_markdown = lambda **kw: pytest.fail("过期不得补发")
    assert _run(setup,RULE)["status"] == "no_active_rules"
    saved = json.loads(state.read_text())
    assert not saved["pending_events"] and saved["expired_event_ids"] and not r.requested_codes


def test_cli_selection_never_grants_real_push_authorization(monkeypatch,capsys):
    import main
    from cli import intraday_monitor
    parser = argparse.ArgumentParser()
    intraday_monitor.register_subparser(parser.add_subparsers())
    args = parser.parse_args(["intraday-monitor","e2e-test","--rule-id",RULE.rule_id,"--input-by","pytest","--json"])
    monkeypatch.setattr(main,"setup_providers",lambda *a: pytest.fail("未授权不得初始化真实源"))
    assert intraday_monitor.handle_command({},args) == 1
    assert '"status": "authorization_required"' in capsys.readouterr().out
