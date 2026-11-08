"""瑞芯微严格跌破209.47元，11月9～13日；临时状态与模拟推送隔离。"""
import argparse
import json
from datetime import date, datetime, timedelta

import pytest

from services.intraday_monitor import ROCKCHIP_BELOW_209_47_20261109_13 as RULE
from services.intraday_monitor.rules import (
    DEFAULT_RULES, ROCKCHIP_BELOW_203_90_20260929_1008 as OLD_BELOW,
    ROCKCHIP_ABOVE_219_02_20260929_1008 as OLD_ABOVE,
)
from services.intraday_monitor.service import run_check
from tests.test_intraday_monitor_service import _Registry, _Pusher, _calendar, _Result, TZ

ACTIVE = ("2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13")
CLOSED = ("2026-11-07", "2026-11-08", "2026-11-14", "2026-11-15")


def _setup(tmp_path, day=ACTIVE[0]):
    db = _calendar(tmp_path, dates=("2026-11-06", *ACTIVE, "2026-11-16"), closed_dates=CLOSED)
    r = _Registry(price=209.46)
    r.now = datetime.fromisoformat(day+"T10:00:00").replace(tzinfo=TZ)
    return r, _Pusher(), db, tmp_path/"state.json"


def _run(setup, **kwargs):
    r,p,db,state = setup
    return run_check(r,rules=(RULE,),now=r.now,db_path=db,state_path=state,
                     pusher_factory=lambda:p,**kwargs)


@pytest.mark.parametrize("price,matched", [(209.46,True),(209.47,False),(209.48,False)])
def test_identity_threshold_dates_export_and_strict_boundary(price,matched):
    assert (RULE.code,RULE.instrument_name,RULE.provider) == ("603893.SH","瑞芯微","sina")
    assert RULE.rule_id == "rockchip-below-209-47-20261109-13"
    assert RULE.threshold == 209.47 and RULE.threshold_mode == "fixed"
    assert RULE.direction == "below" and not RULE.inclusive and RULE.emit_on_initial_match
    assert (RULE.valid_from,RULE.valid_until) == (date(2026,11,9),date(2026,11,13))
    assert RULE.value_label == "价格" and RULE.value_unit == "元" and RULE.action_text == "跌破"
    assert RULE in DEFAULT_RULES and len(ACTIVE) == 5
    assert RULE.is_active(price) is matched
    assert (OLD_BELOW.threshold,OLD_ABOVE.threshold) == (203.90,219.02)
    assert OLD_BELOW.valid_until == OLD_ABOVE.valid_until == date(2026,10,8)


@pytest.mark.parametrize("day", ACTIVE)
def test_every_effective_day_including_final_day(tmp_path,day):
    setup = _setup(tmp_path,day)
    result = _run(setup)
    assert result["status"] == "complete" and result["pushed"]
    assert setup[0].requested_codes == [[RULE.code]] and len(setup[1].messages) == 1


@pytest.mark.parametrize("day", ("2026-11-06",*CLOSED,"2026-11-16"))
def test_outside_validity_never_fetches_or_pushes(tmp_path,day):
    setup = _setup(tmp_path,day)
    assert _run(setup)["status"] == "no_active_rules"
    assert not setup[0].requested_codes and not setup[1].messages


def test_initial_match_daily_dedup_reentry_and_wording(tmp_path):
    setup = _setup(tmp_path)
    r,p,db,state = setup
    counts = []
    for price in (209.47,209.46,209.40,209.47,209.46):
        r.price = price
        counts.append(len(_run(setup)["events"]))
        r.now += timedelta(minutes=3)
    assert counts == [0,1,0,0,1]
    assert "瑞芯微" in p.messages[0][1] and "603893.SH" in p.messages[0][1]
    assert "已跌破监控线 **209.47**元" in p.messages[0][1]
    r.now = datetime(2026,11,10,10,tzinfo=TZ)
    assert _run(setup)["pushed"]  # 新交易日首次已在线下仍提醒。


@pytest.mark.parametrize("bad", ["calendar","source","stale_day","stale_minute","future"])
def test_failed_evidence_never_builds_baseline_or_alerts(tmp_path,bad):
    setup = _setup(tmp_path)
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
    result = _run(setup)
    assert result["status"] in {"blocked_calendar","source_failed"}
    assert not result["events"] and not p.messages
    if state.exists():
        assert RULE.rule_id not in json.loads(state.read_text())["rules"]


def test_dry_run_push_retry_and_expired_pending(tmp_path):
    setup = _setup(tmp_path,ACTIVE[-1])
    r,p,db,state = setup
    assert _run(setup,dry_run=True)["status"] == "dry_run"
    assert not state.exists() and not p.messages
    p.send_markdown = lambda **kw: False
    assert _run(setup)["status"] == "push_failed"
    r.now += timedelta(minutes=3)
    p.send_markdown = lambda **kw: True
    assert _run(setup)["pushed"]
    r.now += timedelta(minutes=3)
    assert not _run(setup)["pushed"]
    r.now += timedelta(minutes=3)
    r.price = 209.47
    _run(setup)
    r.now += timedelta(minutes=3)
    r.price = 209.46
    p.send_markdown = lambda **kw: False
    assert _run(setup)["status"] == "push_failed"
    r.now = datetime(2026,11,16,10,tzinfo=TZ)
    r.requested_codes.clear()
    p.send_markdown = lambda **kw: pytest.fail("到期后不得补发旧pending")
    assert _run(setup)["status"] == "no_active_rules"
    saved = json.loads(state.read_text())
    assert not saved["pending_events"] and saved["expired_event_ids"] and not r.requested_codes


def test_old_expired_rockchip_rules_do_not_share_new_state(tmp_path):
    r,p,db,state = _setup(tmp_path)
    out = run_check(r,rules=(OLD_BELOW,OLD_ABOVE,RULE),now=r.now,db_path=db,
                    state_path=state,pusher_factory=lambda:p)
    assert out["pushed"] and [e["rule_id"] for e in out["events"]] == [RULE.rule_id]
    assert r.requested_codes == [[RULE.code]]
    assert set(json.loads(state.read_text())["rules"]) == {RULE.rule_id}


def test_cli_selection_and_no_implicit_real_test_authorization(monkeypatch,capsys):
    from cli import intraday_monitor
    import main
    parser = argparse.ArgumentParser()
    intraday_monitor.register_subparser(parser.add_subparsers())
    args = parser.parse_args(["intraday-monitor","e2e-test","--rule-id",RULE.rule_id,
                              "--input-by","pytest","--json"])
    monkeypatch.setattr(main,"setup_providers",lambda *a: pytest.fail("未授权不得初始化真实源"))
    assert intraday_monitor.handle_command({},args) == 1
    assert '"status": "authorization_required"' in capsys.readouterr().out
