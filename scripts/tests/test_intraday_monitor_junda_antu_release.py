"""钧达/安图价格规则与泛亚下线；临时日历、行情和推送均隔离。"""
import json
from datetime import date, datetime, timedelta

import pytest

import services.intraday_monitor.service as service
from services.intraday_monitor import (
    JUNDA_BREAKOUT_42_15_20261105_18 as JUNDA,
    ANTU_BIO_BREAKOUT_39_44_20261105_1202 as ANTU,
    ANTU_BIO_NEAR_WEEKLY_MA5_20261103_30 as WEEKLY,
    FANYA_MICRO_NEAR_46_56_20261104_10 as RETIRED,
)
from services.intraday_monitor.rules import DEFAULT_RULES
from tests.test_intraday_monitor_service import _Registry, _Pusher, _calendar, _Result, TZ


DAYS = tuple(date(2026,11,4) + timedelta(days=i) for i in range(30))
OPEN = tuple(d.isoformat() for d in DAYS if d.weekday() < 5)
CLOSED = tuple(d.isoformat() for d in DAYS if d.weekday() >= 5)
SPECS = ((JUNDA, "002865.SZ", "钧达股份", 42.15, 10, date(2026,11,18)),
         (ANTU, "603658.SH", "安图生物", 39.44, 20, date(2026,12,2)))


def _setup(tmp_path, rule, day="2026-11-05"):
    db = _calendar(tmp_path, dates=OPEN, closed_dates=CLOSED)
    r = _Registry(price=rule.threshold + 0.01)
    r.now = datetime.fromisoformat(day + "T10:00:00").replace(tzinfo=TZ)
    return r, _Pusher(), db, tmp_path / "state.json"


def _run(setup, rule, **kwargs):
    r, p, db, state = setup
    return service.run_check(r, rules=(rule,), now=r.now, db_path=db,
                             state_path=state, pusher_factory=lambda: p, **kwargs)


@pytest.mark.parametrize("rule,code,name,threshold,count,end", SPECS)
def test_identity_strict_boundary_open_day_count_and_default(rule,code,name,threshold,count,end):
    assert (rule.code, rule.instrument_name, rule.threshold) == (code,name,threshold)
    assert rule.provider == "sina" and rule.threshold_mode == "fixed"
    assert rule.direction == "above" and not rule.inclusive and rule.emit_on_initial_match
    assert rule.valid_from == date(2026,11,5) and rule.valid_until == end
    assert rule in DEFAULT_RULES
    assert not rule.is_active(threshold - 0.01) and not rule.is_active(threshold)
    assert rule.is_active(threshold + 0.01)
    assert len([d for d in OPEN if str(rule.valid_from) <= d <= str(end)]) == count
    assert RETIRED not in DEFAULT_RULES


@pytest.mark.parametrize("rule,day", [(r,d) for r,*_ in SPECS for d in OPEN
                                      if str(r.valid_from) <= d <= str(r.valid_until)])
def test_every_effective_open_day_including_last(tmp_path,rule,day):
    setup = _setup(tmp_path,rule,day)
    result = _run(setup,rule)
    assert result["status"] == "complete" and result["pushed"]
    assert setup[0].requested_codes == [[rule.code]]


@pytest.mark.parametrize("rule", [JUNDA,ANTU])
@pytest.mark.parametrize("kind", ["before", "weekend", "after"])
def test_inactive_dates_never_fetch_or_push(tmp_path,rule,kind):
    day = {"before":"2026-11-04", "weekend":"2026-11-07",
           "after":str(rule.valid_until + timedelta(days=1))}[kind]
    setup = _setup(tmp_path,rule,day)
    result = _run(setup,rule)
    assert result["status"] in {"no_active_rules", "non_trade_day"}
    assert not setup[0].requested_codes and not setup[1].messages


@pytest.mark.parametrize("rule", [JUNDA,ANTU])
def test_equality_dedupe_reentry_next_day_and_wording(tmp_path,rule):
    setup = _setup(tmp_path,rule)
    r,p,db,state = setup
    counts = []
    for price in (rule.threshold, rule.threshold+0.01, rule.threshold+0.02,
                  rule.threshold, rule.threshold+0.01):
        r.price = price
        result = _run(setup,rule)
        counts.append(len(result["events"]))
        r.now += timedelta(minutes=3)
    assert counts == [0,1,0,0,1]
    message = p.messages[0][1]
    assert rule.instrument_name in message and rule.code in message
    assert f"已突破监控线 **{rule.threshold:.2f}**元" in message
    r.now = datetime(2026,11,6,10,tzinfo=TZ)
    assert _run(setup,rule)["pushed"]


@pytest.mark.parametrize("rule", [JUNDA,ANTU])
@pytest.mark.parametrize("bad", ["calendar", "source", "stale_day", "stale_minute", "future"])
def test_failed_evidence_never_builds_rule_baseline(tmp_path,rule,bad):
    setup = _setup(tmp_path,rule)
    r,p,db,state = setup
    if bad == "calendar": setup = r,p,tmp_path / "missing.db",state
    elif bad == "source": r.call_specific = lambda *args: _Result(None,error="模拟源失败")
    else:
        r.quote_overrides[rule.code] = {
            "stale_day":{"quote_date":"2026-11-04"},
            "stale_minute":{"quote_time":"09:49:00"},
            "future":{"quote_time":"10:10:00"},
        }[bad]
    result = _run(setup,rule)
    assert result["status"] in {"blocked_calendar", "source_failed"}
    assert not result["events"] and not p.messages
    if state.exists(): assert rule.rule_id not in json.loads(state.read_text())["rules"]


@pytest.mark.parametrize("rule", [JUNDA,ANTU])
def test_preview_push_retry_and_expired_pending(tmp_path,rule):
    setup = _setup(tmp_path,rule,str(rule.valid_until))
    r,p,db,state = setup
    out = _run(setup,rule,dry_run=True)
    assert out["status"] == "dry_run" and out["events"]
    assert not state.exists() and not p.messages
    p.send_markdown = lambda **kwargs: False
    assert _run(setup,rule)["status"] == "push_failed"
    r.now += timedelta(minutes=3)
    p.send_markdown = lambda **kwargs: True
    assert _run(setup,rule)["pushed"]
    r.now += timedelta(minutes=3)
    assert not _run(setup,rule)["pushed"]
    r.now += timedelta(minutes=3)
    r.price = rule.threshold
    _run(setup,rule)
    r.now += timedelta(minutes=3)
    r.price = rule.threshold+0.01
    p.send_markdown = lambda **kwargs: False
    assert _run(setup,rule)["status"] == "push_failed"
    r.now = datetime.combine(rule.valid_until+timedelta(days=1), datetime.min.time()).replace(hour=10,tzinfo=TZ)
    r.requested_codes.clear()
    p.send_markdown = lambda **kwargs: pytest.fail("过期pending不得发送")
    assert _run(setup,rule)["status"] == "no_active_rules"
    saved = json.loads(state.read_text())
    assert not saved["pending_events"] and saved["expired_event_ids"]
    assert not r.requested_codes


def test_antu_price_and_weekly_rules_coexist_with_one_quote_and_separate_states(tmp_path):
    from tests.test_intraday_monitor_antubio_weekly import _setup as weekly_setup
    r,p,db,state = weekly_setup(tmp_path,"2026-11-05")
    r.price = r.pre_close = 39.45
    r.history_closes = [39.45]*len(r.history_dates)
    r.history_factors = [1]*len(r.history_dates)
    assert WEEKLY in DEFAULT_RULES and ANTU in DEFAULT_RULES
    assert len([rule for rule in DEFAULT_RULES if rule.code == ANTU.code]) == 2
    out = service.run_check(r,rules=(WEEKLY,ANTU),now=r.now,db_path=db,
                            state_path=state,pusher_factory=lambda:p)
    assert out["status"] == "complete" and len(out["events"]) == 2
    assert r.requested_codes == [[ANTU.code]]  # 同股合批取价，不覆盖两个规则身份。
    saved = json.loads(state.read_text())
    assert set(saved["rules"]) == {WEEKLY.rule_id,ANTU.rule_id}
    assert len(set(saved["sent_event_ids"])) == 2 and len(p.messages) == 1
    r.now += timedelta(minutes=3); r.price = 39.44
    out = service.run_check(r,rules=(WEEKLY,ANTU),now=r.now,db_path=db,
                            state_path=state,pusher_factory=lambda:p)
    assert not out["events"]
    r.now += timedelta(minutes=3); r.price = 39.45
    out = service.run_check(r,rules=(WEEKLY,ANTU),now=r.now,db_path=db,
                            state_path=state,pusher_factory=lambda:p)
    assert [e["rule_id"] for e in out["events"]] == [ANTU.rule_id]


def test_production_default_retires_fanya_pending_without_erasing_history(tmp_path,monkeypatch):
    setup = _setup(tmp_path,RETIRED)
    r,p,db,state = setup
    r.price = 46.56
    p.send_markdown = lambda **kwargs: False
    assert _run(setup,RETIRED)["status"] == "push_failed"
    before = json.loads(state.read_text())
    old_event = before["pending_events"][0]["event_id"]
    requested = []
    def quotes(registry,rules):
        requested.extend(rule.code for rule in rules)
        return {JUNDA.code:{"code":JUNDA.code,"price":42.16,"_source":"sina",
                            "quote_date":"2026-11-05","quote_time":"10:03:00"}},[]
    monkeypatch.setattr(service,"_fetch_quotes",quotes)
    p = _Pusher()
    r.now += timedelta(minutes=3)
    out = service.run_check(r,now=r.now,db_path=db,state_path=state,pusher_factory=lambda:p)
    assert out["status"] == "partial"  # 其他行情缺失不冒充全部检查成功。
    assert RETIRED.code not in requested and JUNDA.code in requested
    assert out["pushed"] and all(e["rule_id"] != RETIRED.rule_id for e in out["events"])
    saved = json.loads(state.read_text())
    assert saved["rules"][RETIRED.rule_id] == before["rules"][RETIRED.rule_id]
    assert old_event in saved["expired_event_ids"] and old_event not in saved["sent_event_ids"]
    assert not saved["pending_events"]
    assert all("泛亚微透" not in message for _,message in p.messages)
