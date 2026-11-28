"""隔离的登记簿、开放日日历及逐规则回执；无网络/真实消息。"""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest

from cli import intraday_monitor as cli
from services.intraday_monitor import catalog, service
from services.intraday_monitor.rules import DEFAULT_RULES, ICBC_BELOW_8_88_20261125_1201 as ICBC
from tests.test_intraday_monitor_service import TZ, _Registry, _FailingQuoteRegistry, _Pusher


SAT = datetime(2026, 11, 28, 10, tzinfo=TZ)
MON = SAT + timedelta(days=2)


@pytest.fixture
def sandbox(tmp_path):
    db = tmp_path / "calendar.db"
    with sqlite3.connect(db) as conn:
        conn.execute("create table trade_calendar (date text primary key, is_open integer, exchange text)")
        for i in range(-5, 26):
            d = (SAT + timedelta(days=i)).date()
            conn.execute("insert into trade_calendar values (?, ?, 'SSE')", (str(d), int(d.weekday() < 5)))
    state = tmp_path / "run" / "state.json"
    return db, state, catalog.catalog_path(state)


def change(box, action="add", **options):
    db, _, book = box
    inputs = dict(input_by="tests", now=SAT, db_path=db, path=book)
    if action == "add":
        inputs.update(code="601398.SH", direction="below", threshold=8.75, days=2)
    return catalog.manage_rule(action, **{**inputs, **options})


def test_empty_defaults_and_preview_without_files(sandbox):
    _, state, book = sandbox
    assert catalog.read_book(book).enabled() == DEFAULT_RULES
    assert change(sandbox, dry_run=True)["status"] == "preview"
    assert not state.parent.exists()
    assert service.rule_status(state_path=state, now=SAT)["status"] == "ok"
    assert not state.parent.exists()


@pytest.mark.parametrize("now,start,first,last", [
    (SAT, None, "2026-11-30", "2026-12-01"),
    (MON.replace(hour=14, minute=59), None, "2026-11-30", "2026-12-01"),
    (MON.replace(hour=15), None, "2026-12-01", "2026-12-02"),
    (SAT, "2026-12-05", "2026-12-07", "2026-12-08"),
])
def test_calendar_window(sandbox, now, start, first, last):
    result = change(sandbox, now=now, start=start)
    assert (result["status"], result["valid_from"], result["valid_until"]) == ("ok", first, last)
    assert catalog.read_book(sandbox[2]).rules[result["rule_id"]].provider == "sina"


def test_date_is_decided_after_waiting_for_check_lock(sandbox, monkeypatch):
    from contextlib import contextmanager
    acquired = []
    @contextmanager
    def lock(_):
        acquired.append(True)
        yield
    monkeypatch.setattr(catalog, "state_lock", lock)
    monkeypatch.setattr(catalog, "shanghai_now",
                        lambda _: MON.replace(hour=15) if acquired else MON.replace(hour=14, minute=59))
    assert change(sandbox, days=1)["valid_until"] == "2026-12-01"


@pytest.mark.parametrize("bad", [
    {"days": 0}, {"days": True}, {"threshold": 0}, {"threshold": float("nan")}, {"threshold": 8.875},
    {"direction": "maybe"}, {"inclusive": "false"}, {"proximity_pct": 1},
    {"direction": "near"}, {"direction": "near", "proximity_pct": 110},
    {"code": "601398.SZ"}, {"code": "600000.SH", "name": None},
    {"code": "880999.TDX"}, {"name": "错误名称"}, {"input_by": "  "},
    {"rule_id": "not-a-custom-id"}, {"start": "2026-11-25"}, {"days": 249},
    {"threshold": 8.88},  # 与有效期重叠的内置工行规则冲突。
])
def test_invalid_registration_does_not_create_book(sandbox, bad):
    assert change(sandbox, **bad)["status"] == "error"
    assert not sandbox[2].exists()


@pytest.mark.parametrize("failure", ["hole", "other_exchange", "closed_flag_invalid"])
def test_calendar_fail_closed_including_intervening_weekend(sandbox, failure):
    with sqlite3.connect(sandbox[0]) as conn:
        sql = {"hole": "delete from trade_calendar", "other_exchange": "update trade_calendar set exchange='SZSE'",
               "closed_flag_invalid": "update trade_calendar set is_open=2"}[failure]
        conn.execute(sql + " where date='2026-11-29'")
    assert change(sandbox)["status"] == "error" and not sandbox[2].exists()


def test_extend_disable_preserve_identity_and_audit(sandbox):
    _, state, path = sandbox
    result = change(sandbox, "extend", rule_id=ICBC.rule_id, days=3)
    assert (result["valid_from"], result["valid_until"]) == ("2026-11-25", "2026-12-04")
    assert change(sandbox, "extend", rule_id=ICBC.rule_id, days=3, expected_until="2026-12-01")["status"] == "error"
    assert catalog.read_book(path).rules[ICBC.rule_id].threshold == ICBC.threshold
    before = path.read_bytes()
    assert change(sandbox, "disable", rule_id=ICBC.rule_id, dry_run=True)["status"] == "preview"
    assert path.read_bytes() == before
    assert change(sandbox, "disable", rule_id=ICBC.rule_id)["status"] == "ok"
    assert ICBC.rule_id not in {r.rule_id for r in catalog.read_book(path).enabled()}
    row = next(r for r in service.rule_status(now=MON, state_path=state, include_inactive=True)["rules"]
               if r["rule_id"] == ICBC.rule_id)
    assert row["disabled"] and not row["effective_on_date"] and row["today"] is None
    assert change(sandbox, "extend", rule_id=ICBC.rule_id, days=1)["status"] == "error"
    assert [i["action"] for i in json.loads(path.read_text())["changes"]] == ["extend", "disable"]
    assert not state.exists()


def test_expired_does_not_fill_gap_and_racing_duplicate_rejected(sandbox):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: change(sandbox), range(2)))
    assert sorted(r["status"] for r in results) == ["error", "ok"]
    key = next(r["rule_id"] for r in results if r["status"] == "ok")
    renewal = change(sandbox, "extend", rule_id=key, days=2, now=MON+timedelta(days=4))
    assert (renewal["valid_from"], renewal["valid_until"]) == ("2026-12-04", "2026-12-07")
    assert not catalog.read_book(sandbox[2]).rules[key].is_effective_on((MON+timedelta(days=3)).date())


def test_bindings_near_source_and_price_units(sandbox):
    for code, source, unit in (("880003.TDX","tdx","元"), ("932000.CSI","eastmoney","点"),
                               ("883421.THS","tonghuashun","点"), ("000001.SH","sina","点")):
        result = change(sandbox, code=code, direction="near", threshold=20.4, proximity_pct=1)
        rule = catalog.read_book(sandbox[2]).rules[result["rule_id"]]
        assert (rule.provider,rule.value_unit,rule.inclusive) == (source,unit,True)
        assert rule.is_active(20.4 * 1.01) and not rule.is_active(20.7)
    entered = change(sandbox, code="600000.SH", name="浦发银行", direction="above", inclusive=True)
    rule = catalog.read_book(sandbox[2]).rules[entered["rule_id"]]
    assert rule.is_active(8.75) and rule.action_text == "达到或高于"
    assert change(sandbox, "disable", rule_id=rule.rule_id)["status"] == "ok"
    assert change(sandbox, code="600000.SH", name="其他股票")["status"] == "error"
    assert change(sandbox, code="600000.SH", threshold=11.11)["name"] == "浦发银行"


def test_disabling_failed_event_does_not_claim_it_will_retry(sandbox):
    db, state, _ = sandbox
    key = change(sandbox)["rule_id"]
    reg, p = _Registry(price=8.7), _Pusher(succeed=False)
    reg.now = MON
    service.run_check(reg, now=MON, db_path=db, state_path=state, pusher_factory=lambda:p)
    assert any(e['rule_id']==key for e in json.loads(state.read_text())['pending_events'])
    assert change(sandbox,"disable",rule_id=key,now=MON)["status"]=="ok"
    later = MON+timedelta(minutes=3)
    p.succeed, reg.now = True, later
    receipt = service.run_check(reg, now=later, db_path=db, state_path=state, pusher_factory=lambda:p)
    assert not any(e['rule_id']==key for e in receipt.get('events',[]))
    assert not any(e['rule_id']==key for e in json.loads(state.read_text())['pending_events'])
    assert not any("8.75" in msg for _,msg in p.messages[1:])
    row = next(r for r in service.rule_status(now=later,state_path=state,include_inactive=True)["rules"]
               if r['rule_id']==key)
    assert row['today']['last_delivery']=='retired' and row['today']['sent']==0


def test_corrupt_book_is_never_fallback_or_overwritten(sandbox):
    _, state, book = sandbox
    book.parent.mkdir()
    book.write_text('{"version":1,"changes":[{"rule_id":"invalid"}]}')
    prior = book.read_bytes()
    assert change(sandbox)["status"] == "error"
    assert service.rule_status(state_path=state, now=MON)["status"] == "state_error"
    registry, p = _Registry(), _Pusher()
    r = service.run_check(registry, state_path=state, db_path=sandbox[0], now=MON, pusher_factory=lambda:p)
    assert r["status"] == "state_error" and not r["pushed"]
    assert registry.call_count == 0 and not p.messages and not state.exists() and book.read_bytes() == prior


def test_deploy_guard_replays_book_without_executing_target(sandbox, monkeypatch):
    from tests.test_intraday_monitor_launchd import _load_transition_guard
    import subprocess

    guard = _load_transition_guard()
    key = change(sandbox)["rule_id"]
    assert change(sandbox, "disable", rule_id=ICBC.rule_id)["status"] == "ok"
    sources = {
        "scripts/services/intraday_monitor/catalog.py": "raise Exception('不得执行')\nCATALOG_VERSION=1",
        guard.RULE_SOURCES[0][0]: (
            f"A = MonitorRule('{ICBC.rule_id}', '工商银行', '601398.SH', 8.88, "
            "valid_until=date(2026,12,1), value_label='价格', value_unit='元')\nDEFAULT_RULES=(A,)"),
    }
    def show(repo, commit, path):
        if path not in sources:
            raise subprocess.CalledProcessError(1, "fixture")
        return sources[path]
    monkeypatch.setattr(guard, "_show", show)
    def allowed():
        return guard._book_target_rules(None, "target", sandbox[2], {ICBC.rule_id, "volume-rule"})
    assert allowed() == {key, "volume-rule"}
    prior = sources[guard.RULE_SOURCES[0][0]]
    sources[guard.RULE_SOURCES[0][0]] = "DEFAULT_RULES = ()"
    with pytest.raises(ValueError, match="不存在"):
        allowed()
    sources[guard.RULE_SOURCES[0][0]] = prior
    sources["scripts/services/intraday_monitor/catalog.py"] = "CATALOG_VERSION=2"
    with pytest.raises(ValueError,match="不兼容"):
        allowed()
    del sources["scripts/services/intraday_monitor/catalog.py"]
    with pytest.raises(ValueError,match="禁止回滚"):
        allowed()


def test_default_check_consumes_new_rule_with_identical_alerts_and_health(sandbox):
    db, state, _ = sandbox
    key = change(sandbox)["rule_id"]
    registry, pusher = _Registry(), _Pusher()
    def tick(minute, price, *, registry=registry, dry=False):
        now = MON + timedelta(minutes=minute)
        registry.price, registry.now = price, now
        receipt = service.run_check(registry, now=now, state_path=state, db_path=db,
                                    pusher_factory=lambda:pusher, dry_run=dry)
        row = next(r for r in service.rule_status(state_path=state, now=now, include_inactive=True)["rules"]
                   if r["rule_id"] == key)
        return [e for e in receipt.get("events", []) if e["rule_id"] == key], row
    assert tick(0,8.70,dry=True)[1]["today"] is None and not state.exists()
    ticks = [tick(i*3,price) for i,price in enumerate((8.74,8.72,8.75,8.74))]
    assert [len(events) for events,_ in ticks] == [1,0,0,1]
    assert [r["today"]["last_result"] for _,r in ticks] == [
        "triggered","matched_no_event","not_triggered","triggered"]
    before = ticks[-1][1]
    assert (before["today"]["triggers"], before["today"]["sent"]) == (2,2)
    events, missing = tick(12,8.73,registry=_FailingQuoteRegistry())
    daily = missing["today"]
    assert not events and daily["last_result"] == "quote_gap" and daily["last_delivery"] == "sent"
    assert (daily["checks"], daily["quote_gaps"], daily["calculation_gaps"], daily["sent"]) == (5,1,0,2)
    assert missing["last_quote_at"] == before["last_quote_at"]
    # 重入进程、节流、dry-run、跨日：统计不多算；缺少覆盖不能冒充零。
    old = state.read_bytes()
    tick(12,8.70)
    tick(15,8.70,dry=True)
    assert state.read_bytes() == old
    next_day = service.rule_status(state_path=state,now=MON+timedelta(days=1))["rules"]
    assert next(r for r in next_day if r["rule_id"]==key)["today"] is None
    assert change(sandbox,"disable",rule_id=key)["status"]=="ok"
    assert not tick(18,8.70)[0] and not tick(21,8.74)[1]["effective_on_date"]


def test_failed_delivery_retried_on_gap_is_attributed_only_to_pending_rule(sandbox):
    db, state, _ = sandbox
    first = change(sandbox)["rule_id"]
    second = change(sandbox,threshold=7.99)["rule_id"]
    # 使用与生产相同的两条规则对象，隔离其它市场规则或发送的影响。
    book = catalog.read_book(sandbox[2])
    rules = (book.rules[first],book.rules[second])
    r, p = _Registry(price=8.7), _Pusher(succeed=False)
    def tick(reg, at):
        reg.now = at
        return service.run_check(reg, rules=rules, state_path=state, db_path=db, now=at,pusher_factory=lambda:p)
    assert tick(r,MON)["status"] == "push_failed"
    rows = service.rule_status(rules=rules, now=MON, state_path=state)["rules"]
    assert [x["today"]["last_delivery"] for x in rows] == ["failed",None]
    p.succeed=True
    later=MON+timedelta(minutes=3)
    receipt = tick(_FailingQuoteRegistry(),later)
    assert receipt["status"]=="source_failed" and receipt["pushed"]
    rows = service.rule_status(rules=rules, now=later, state_path=state)["rules"]
    assert [x["today"]["last_result"] for x in rows] == ["quote_gap","quote_gap"]
    assert [x["today"]["sent"] for x in rows] == [1,0]
    assert rows[0]["today"]["last_delivery"] == "sent"
    assert len(p.messages)==2


def test_cli_preview_and_unknown_e2e_never_setup_providers(sandbox,monkeypatch,capsys):
    import main
    from cli import intraday_rule_management as management
    monkeypatch.setattr(management,"manage_rule",lambda action,**kw: change(sandbox,action,**kw))
    monkeypatch.setattr(main,"setup_providers",lambda *_: pytest.fail("管理不应取数"))
    parser=main.build_parser()
    args=parser.parse_args(["intraday-monitor","rule","add","--code","601398.SH","--threshold","8.75",
                            "--direction","below","--days","2","--input-by","pytest","--dry-run","--json"])
    assert cli.handle_command({},args)==0
    assert json.loads(capsys.readouterr().out)["status"]=="preview" and not sandbox[2].exists()
    args=parser.parse_args(["intraday-monitor","e2e-test","--input-by","pytest","--confirm-real-push","--json"])
    args.rule_id="custom-missing"
    assert cli.handle_command({},args)==1
    assert not json.loads(capsys.readouterr().out)["pushed"]
