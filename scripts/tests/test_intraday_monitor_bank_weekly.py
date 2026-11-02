"""通达信研究行业银行：原始指数周线、严格穿线、缺源及期限。"""
import json
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from providers.base import DataResult
from providers.tdx_provider import BANK_CODE, AVG_PRICE_CODE, TdxProvider, _minute_quote, _daily_rows
from services.intraday_monitor import BANK_BELOW_WEEKLY_MA5_20261103_16 as RULE
from services.intraday_monitor.rules import DEFAULT_RULES
from services.intraday_monitor.service import run_check
from services.intraday_monitor.weekly import weekly_history_dates
from tests.test_intraday_monitor_antubio_weekly import OPEN, CLOSED
from tests.test_intraday_monitor_service import _Pusher, _calendar, TZ
from tests.test_tdx_intraday_quotes import minute_bars, NOW


class Registry:
    def __init__(self, db, day="2026-11-03"):
        self.provider = TdxProvider()
        self.provider.initialize()
        self.now = datetime.fromisoformat(day + "T10:00:00").replace(tzinfo=TZ)
        self.price = 90
        self.calls = []
        history, basis = weekly_history_dates(day, 4, db_path=db)
        prices = dict(zip(basis, (80, 90, 100, 130)))
        # 周中昨收不同，指数历史不应按它缩放。
        self.rows = [dict(trade_date=d, ts_code=BANK_CODE, close=prices.get(d, 200)) for d in history]

    def get_provider(self, name):
        assert name == "tdx"
        return self.provider

    def call_specific(self, name, capability, *args):
        assert name == "tdx"
        self.calls.append((capability, args))
        if capability == "get_realtime_quotes":
            return DataResult([dict(code=BANK_CODE, price=self.price,
                                   quote_date=self.now.date().isoformat(),
                                   quote_time=self.now.time().isoformat())], "tdx:881385_1min")
        assert capability == "get_index_daily_range", "不得调用股票日线或复权因子"
        return DataResult(self.rows, "tdx:881385_daily")


def setup(tmp_path, day="2026-11-03"):
    db = _calendar(tmp_path, dates=OPEN, closed_dates=CLOSED)
    with sqlite3.connect(db) as c:
        c.execute("alter table trade_calendar add column exchange text not null default 'SSE'")
    return Registry(db, day), _Pusher(), db, tmp_path / "state.json"


def run(s, **kwargs):
    r, p, db, state = s
    return run_check(r, rules=(RULE,), now=r.now, db_path=db,
                     state_path=state, pusher_factory=lambda: p, **kwargs)


def test_rule_identity_and_ten_open_day_expiry():
    assert RULE in DEFAULT_RULES and RULE.code == BANK_CODE == "881385.TDX"
    assert RULE.provider == RULE.threshold_provider == "tdx"
    assert RULE.threshold_history_mode == "index" and RULE.threshold_period == "week"
    assert RULE.valid_from == date(2026,11,3) and RULE.valid_until == date(2026,11,16)
    assert len([d for d in OPEN if "2026-11-03" <= d <= "2026-11-16"]) == 10
    assert not RULE.inclusive and RULE.emit_on_initial_match
    for change in ({"threshold_history_mode":"other"}, {"threshold_mode":"fixed", "threshold":1}):
        with pytest.raises(ValueError): replace(RULE, **change)


def test_raw_index_samples_no_stock_factors_no_preclose_rescaling(tmp_path):
    s = setup(tmp_path)
    r, p, db, state = s
    out = run(s)
    event = out["events"][0]
    assert out["status"] == "complete" and out["pushed"]
    assert event["threshold"] == pytest.approx(98)  # (80+90+100+130+90)/5
    assert event["threshold_anchor_pre_close"] is None
    assert event["threshold_source"] == "tdx:881385_daily"
    assert "指数原始点位" in p.messages[0][1] and "前复权" not in p.messages[0][1]
    assert "本周最新价" in p.messages[0][1]
    assert r.calls[-1] == ("get_index_daily_range", (BANK_CODE, "2026-10-08", "2026-11-02"))


def test_equality_dedupe_reentry_and_cache(tmp_path):
    s=setup(tmp_path); r,p,db,state=s
    counts=[]
    for price in (100,99,98,100,99):
        r.price=price
        out=run(s); assert out["status"] == "complete"
        counts.append(len(out["events"]))
        saved=json.loads(state.read_text())["rules"][RULE.rule_id]
        assert saved["last_threshold"] == pytest.approx((400+price)/5)
        r.now += timedelta(minutes=3)
    assert counts == [0,1,0,0,1]
    assert sum(c[0]=="get_index_daily_range" for c in r.calls)==1


@pytest.mark.parametrize("bad", ["gap", "duplicate", "wrong_code", "nonpositive", "nan", "bool", "calendar", "stale"])
def test_invalid_evidence_never_creates_bank_baseline_or_pushes(tmp_path,bad):
    s=setup(tmp_path); r,p,db,state=s
    if bad=="gap": r.rows.pop()
    elif bad=="duplicate": r.rows.append(r.rows[-1])
    elif bad=="wrong_code": r.rows[0]["ts_code"]="881155.THS"
    elif bad in {"nonpositive","nan","bool"}: r.rows[0]["close"]={"nonpositive":0,"nan":float("nan"),"bool":True}[bad]
    elif bad=="calendar":
        with sqlite3.connect(db) as c: c.execute("delete from trade_calendar where date='2026-10-11'")
    else:
        original=r.call_specific
        def stale(name,cap,*args):
            out=original(name,cap,*args)
            if cap=="get_realtime_quotes": out.data[0]["quote_date"]="2026-11-02"
            return out
        r.call_specific=stale
    out=run(s)
    assert out["status"]=="source_failed" and not out["events"] and not p.messages
    if state.exists(): assert RULE.rule_id not in json.loads(state.read_text())["rules"]


def test_next_day_and_week_refresh_and_final_day(tmp_path):
    s=setup(tmp_path); r,p,db,state=s
    run(s)
    for day in ("2026-11-04","2026-11-09","2026-11-16"):
        r2=Registry(db,day); r.now=r2.now; r.rows=r2.rows
        assert run(s)["status"]=="complete"
    assert sum(c[0]=="get_index_daily_range" for c in r.calls)==4


@pytest.mark.parametrize("day",["2026-11-02","2026-11-07","2026-11-17"])
def test_inactive_dates_never_fetch(tmp_path,day):
    s=setup(tmp_path,day);out=run(s)
    assert not s[0].calls and not out["events"] and not s[1].messages


def test_dry_run_and_push_failure_retry(tmp_path):
    s=setup(tmp_path);r,p,db,state=s
    out=run(s,dry_run=True)
    assert out["events"] and not state.exists() and not p.messages
    p.send_markdown=lambda **kwargs: False
    assert run(s)["status"]=="push_failed"
    p.send_markdown=lambda **kwargs: True
    r.now += timedelta(minutes=3)
    assert run(s)["pushed"]
    r.now += timedelta(minutes=3)
    assert not run(s)["pushed"]


def test_bank_minute_identity_batch_and_partial_failure():
    apis=[MagicMock(),MagicMock()]
    for api in apis:
        api.connect.return_value=True;api.get_index_bars.return_value=minute_bars()
    with patch("pytdx.hq.TdxHq_API",side_effect=apis):
        out=TdxProvider({"servers":[("test",7709)]}).get_realtime_quotes([AVG_PRICE_CODE,BANK_CODE],now=NOW)
    assert [q["code"] for q in out.data]==[AVG_PRICE_CODE,BANK_CODE]
    apis[1].get_index_bars.assert_called_once_with(8,1,"881385",0,2)
    assert "pre_close" not in out.data[1]
    for api in apis: api.disconnect.assert_called_once()
    apis[0].reset_mock();apis[1].reset_mock();apis[0].get_index_bars.return_value=[]
    with patch("pytdx.hq.TdxHq_API",side_effect=apis):
        out=TdxProvider({"servers":[("test",7709)]}).get_realtime_quotes([AVG_PRICE_CODE,BANK_CODE],now=NOW)
    assert out.success and [q["code"] for q in out.data]==[BANK_CODE]
    assert AVG_PRICE_CODE in out.note


def test_bank_rejects_wrong_minute_identity_and_daily_identity():
    bars=minute_bars();bars[-1]["code"]="880003"
    with pytest.raises(ValueError,match="身份"): _minute_quote(bars,BANK_CODE)
    # 800根协议深度验证后，仍须拒绝回包显式身份错位。
    rows=[dict(datetime="2026-11-02 15:00",code="881365")]*800
    with pytest.raises(ValueError,match="身份"): _daily_rows(rows,BANK_CODE,"2026-10-08","2026-11-02")


def test_bank_daily_history_contract_and_fallback():
    # 800根日线的深度及日期覆盖沿用现有严格契约。
    end=date(2026,11,2)
    days=[end-timedelta(days=i) for i in range(1200) if (end-timedelta(days=i)).weekday()<5][:800]
    rows=[dict(datetime=str(d)+" 15:00", open=100,close=101,high=102,low=99,
               code="881385",market=1) for d in sorted(days)]
    bad,good=MagicMock(),MagicMock()
    for api in (bad,good): api.connect.return_value=True
    bad.get_index_bars.return_value=rows[-5:]
    good.get_index_bars.return_value=rows
    with patch("pytdx.hq.TdxHq_API",side_effect=[bad,good]):
        out=TdxProvider({"servers":[("a",7709),("b",7709)]}).get_index_daily_range(
            BANK_CODE,"2026-10-08","2026-11-02")
    assert out.success and out.source=="tdx:881385_daily"
    assert out.data[0]["trade_date"]=="2026-10-08" and out.data[-1]["trade_date"]=="2026-11-02"
    assert all(r["ts_code"]==BANK_CODE for r in out.data)
    good.get_index_bars.assert_called_once_with(9,1,"881385",0,800)
    for api in (bad,good): api.disconnect.assert_called_once()
