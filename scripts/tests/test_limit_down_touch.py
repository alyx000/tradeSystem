"""跌停触价：真实限价、同日证据与旧归档复盘。"""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import pickle
import sqlite3

import pytest

from analyzers import limit_down_touch as touch, verified_limit_down as ld
from providers.base import DataResult
from services import review_consecutive_pool as review_pool, review_limit_down_touch as review_touch
from services.dated_market_archive import read_source

DAY = "2026-11-25"


def session():
    d = DAY.replace("-", "")
    facts = {k: [] for k in ld.METHODS}
    for n in range(4003):
        code = f"{600000+n}.SH"
        common = {"ts_code": code, "trade_date": d}
        name = "*ST测试" if n == 3 else "测试" + str(n)
        facts["universe"].append(dict(ts_code=code, name=name, list_date="20200101"))
        facts["limits"].append(dict(common, down_limit=9.0, up_limit=11.0))
        if n == 3:
            facts["st"].append(dict(common, name=name))
        if n == 4:
            facts["suspended"].append(dict(common, suspend_type="S", suspend_timing=None))
        else:
            facts["quotes"].append(dict(common, high=10.0, low=9.0 if n in (0, 1, 3) else 9.01,
                                        close=9.0 if n in (0,3) else 9.5, vol=600))
    return facts


def compute(facts):
    aligned = ld.aligned(DAY, **facts)
    return touch.calculate(DAY, aligned, ld.closed_list(DAY, aligned), touch.SOURCES)


def test_exact_bounds_st_and_full_day_halt():
    data = compute(session())
    assert (data["touched"], data["closed_at_limit"], data["closed_above_limit"]) == (2, 1, 1)
    assert [r["code"] for r in data["stocks"]] == ["600000.SH", "600001.SH"]
    assert data["coverage"] == dict(active=4003, quoted=4002, suspended=1, excluded_st_or_delisting=1, unlimited=0)
    altered = deepcopy(data)
    altered["closed_above_limit"] += 1
    with pytest.raises(ValueError):
        touch.verified(altered, DAY)
    assert "待核" in touch.html(data, "2026-11-26")


@pytest.mark.parametrize("field,value", [("high", 8.9), ("low", 8.99), ("close", 11), ("vol", 0)])
def test_invalid_bar_rejects_breadth_even_for_nontouch(field, value):
    f = session()
    f["quotes"][10][field] = value
    with pytest.raises(ValueError):
        compute(f)


def test_real_zero_needs_full_input_and_date():
    f = session()
    for row in f["quotes"]:
        row["low"] = row["close"] = 9.7
    assert compute(f)["touched"] == 0
    f["suspended"] = []
    with pytest.raises(ValueError):
        compute(f)


def test_five_inputs_are_shared_and_bad_intraday_data_does_not_damage_closed(monkeypatch):
    facts = session()
    class Registry:
        def __init__(self):
            self.calls = []
        def call(self, method, day):
            self.calls.append(method)
            k = next(k for k, m in ld.METHODS.items() if m == method)
            return DataResult(facts[k], touch.SOURCES[k])
    reg = Registry()
    monkeypatch.setattr(touch, "closed_session", lambda *a, **kw: None)
    for corrupt in (False, True):
        reg.calls.clear()
        if corrupt:
            del facts["quotes"][0]["low"]
        raw = {k: reg.call(ld.METHODS[k], DAY) for k in ("quotes", "st")}
        closed, block = ld.collect_session(reg, DAY, db_path=None, **raw)
        assert sorted(reg.calls) == sorted(ld.METHODS.values())
        assert closed["count_ex_st"] == 1
        assert block["status"] == ("source_failed" if corrupt else "complete")
        if corrupt:
            assert block["touched"] is None


def test_sse_closed_and_historical_live_identity_guards(tmp_path):
    path = tmp_path / "calendar.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE trade_calendar (date, is_open, exchange)")
        conn.execute("INSERT INTO trade_calendar VALUES (?,1,'SSE')", (DAY,))
    touch.closed_session(DAY, path, datetime(2026,11,25,16,tzinfo=touch.TZ), same_day=True)
    with pytest.raises(ValueError):
        touch.closed_session(DAY, path, datetime(2026,11,25,14,tzinfo=touch.TZ))
    with pytest.raises(ValueError):
        touch.closed_session(DAY, path, datetime(2026,11,26,16,tzinfo=touch.TZ), same_day=True)


def test_nonempty_untrusted_time_is_not_source_proof(tmp_path):
    raw = pickle.dumps([])
    path = tmp_path / "market.pkl"
    path.write_bytes(raw)
    row = dict(capability="get_market_daily_quotes", args=[DAY], source="tushare:daily",
               success=True, error=None)
    manifest = {"source_sha256": {str(path.resolve()): hashlib.sha256(raw).hexdigest()}, "source_receipts":[row]}
    for invalid in ("bad", "2026-11-25T16:00:00", "2026-11-25T13:59:00+08:00", "2099-11-25T16:00:00+08:00"):
        row["fetched_at"] = invalid
        with pytest.raises(ValueError):
            read_source(tmp_path, manifest, DAY, path.name, row["capability"], row["source"])


def test_review_uses_strict_fact_day_and_rebuilds_from_local_proof(tmp_path):
    block = compute(session())
    path = tmp_path / "data/reports/limit-down-touch" / (DAY + ".json")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(dict(trade_date=DAY, mode="archived_sources", limit_down_touch=block)))
    good = review_touch.load(tmp_path/"daily", DAY, {})
    assert good["status"] == "complete"
    data = dict(report_date=DAY, fact_date=DAY, calendar=[(DAY,1)], points=[], gaps=[], limit_down_touch=good)
    rendered, _ = review_pool.render(data, DAY)
    assert rendered.count('data-limit-down-touch="v1"') == 1 and "收盘已离开跌停 1 只" in rendered
    wrong, _ = review_pool.render(dict(data, fact_date="2026-11-26"), DAY)
    assert 'data-limit-down-touch="missing"' in wrong and "收盘已离开跌停 1 只" not in wrong
    failed = touch.unavailable(DAY,"离线")
    assert touch.retain_previous(failed,tmp_path/"daily")["touched"] == 2


def test_formal_assembler_places_day_measure_in_emotion_section(tmp_path):
    from tests.test_daily_review_html_report import _load_assembler, _write_chunks
    from tests.test_review_consecutive_pool import history
    from html.parser import HTMLParser
    asm = _load_assembler()
    db = history(tmp_path, report_date=DAY)
    block = compute(session())
    path = tmp_path / 'data/reports/limit-down-touch' / (DAY+'.json')
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(dict(mode='archived_sources',trade_date=DAY,limit_down_touch=block)))
    prepared = review_pool.load(tmp_path/'daily', db, DAY)
    _write_chunks(tmp_path, date=DAY)
    output = asm.render_report(tmp_path, DAY, consecutive_pool_trend=prepared)
    class Reader(HTMLParser):
        section = None
        markers = []
        def handle_starttag(self, tag, attrs):
            attr = dict(attrs)
            if tag == 'section':
                self.section = attr.get('id')
            if attr.get('data-limit-down-touch') == 'v1':
                self.markers.append(self.section)
    reader = Reader()
    reader.feed(output)
    assert reader.markers == ['s3'] and "收盘已离开跌停 1 只" in output


def test_st_identity_and_high_limit_mismatch_fail_closed():
    f = session()
    f["st"][0]["ts_code"] = "601234.SH"
    with pytest.raises(ValueError,match="ST名单"):
        compute(f)
    f = session()
    f["quotes"][10]["high"] = 11.01
    with pytest.raises(ValueError,match="低高收"):
        compute(f)
