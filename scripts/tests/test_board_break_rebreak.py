"""反包后再断板：严格相邻开放日、独立降级及旧分支回归。"""
import copy
import sqlite3
from datetime import date, timedelta

import pytest

from providers.base import DataResult
from services.board_break import scanner, scorer, renderer, pk, rebreak


def stock(code="601188.SH", height=1):
    return dict(code=code, name="龙江交通", limit_times=height,
                limit_times_verified=True, industry="铁路公路")


@pytest.fixture
def calendar_conn():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE trade_calendar(date TEXT PRIMARY KEY,is_open INTEGER,exchange TEXT)")
    start = date(2026, 9, 1)
    for n in range(100):
        d = start + timedelta(days=n)
        conn.execute("INSERT INTO trade_calendar VALUES (?,?,?)", (d.isoformat(), int(d.weekday()<5), "SSE"))
    yield conn
    conn.close()


class Registry:
    def __init__(self):
        self.limits = {
            ("get_limit_up_list", "2026-11-10"): [stock(height=3)],
            ("get_limit_up_list", "2026-11-11"): [],
            ("get_limit_down_list", "2026-11-11"): [],
            ("get_limit_up_list", "2026-11-12"): [stock(), stock("600002.SH", 2)],
            ("get_limit_up_list", "2026-11-13"): [],
            ("get_limit_down_list", "2026-11-13"): [],
        }
        self.bars = [dict(trade_date=d, close=c, pct_chg=p, vol=100)
                     for d,c,p in [("2026-11-10",4.61,10.0239), ("2026-11-11",4.29,-6.9414),
                                   ("2026-11-12",4.72,10.0233), ("2026-11-13",4.30,-8.8983)]]
        self.errors = set()
        self.calls = []

    def call(self, method, *args):
        self.calls.append((method,*args))
        if (method,args[0]) in self.errors:
            return DataResult(data=None, source="test", error="缺源")
        if method == "get_stock_daily_range":
            bars = self.bars if args[0] == "601188" else [dict(trade_date="2026-11-13",close=10,pct_chg=2)]
            return DataResult(data=copy.deepcopy(bars), source="tushare:daily")
        if (method,args[0]) in self.limits:
            rows = self.limits[method,args[0]]
            return DataResult(data=dict(stocks=copy.deepcopy(rows), count=len(rows)), source="tushare:limit_list_d")
        raise AssertionError((method,args))


def run(conn, reg, monkeypatch):
    monkeypatch.setattr(scanner, "_prev_trade_date", lambda *_: "2026-11-12")
    monkeypatch.setattr(scanner, "_main_sectors", lambda *_: (set(),False))
    return scanner.run_daily(conn,reg,"2026-11-13")


def test_longjiang_enters_independent_branch_and_keeps_original(calendar_conn,monkeypatch):
    result = run(calendar_conn,Registry(),monkeypatch)
    assert result["status"] == "ok"
    by_code = {c["code"]:c for c in result["candidates"]}
    assert set(by_code) == {"600002","601188"}
    c = by_code["601188"]
    assert c["branch"] == "rebreak" and c["limit_times"] == 1
    assert c["pattern_evidence"]["prior_limit_times"] == 3
    assert c["pattern_evidence"]["dates"] == ["2026-11-10","2026-11-11","2026-11-12","2026-11-13"]
    assert c["pct_chg"] == -8.8983
    assert result["rebreak"]["status"] == "complete"
    assert by_code["600002"]["limit_times"] == 2


@pytest.mark.parametrize("case", ["no_prior_chain","first_break_up","first_break_down","today_up","today_down",
                                  "first_break_over6","today_over6","st","growth_board"])
def test_invalid_pattern_filtered(calendar_conn,monkeypatch,case):
    reg = Registry()
    if case == "no_prior_chain": reg.limits["get_limit_up_list","2026-11-10"][0]["limit_times"] = 1
    if case == "first_break_up": reg.limits["get_limit_up_list","2026-11-11"] = [stock()]
    if case == "first_break_down": reg.limits["get_limit_down_list","2026-11-11"] = [stock()]
    if case == "today_up": reg.limits["get_limit_up_list","2026-11-13"] = [stock()]
    if case == "today_down": reg.limits["get_limit_down_list","2026-11-13"] = [stock()]
    if case == "first_break_over6": reg.bars[1]["pct_chg"] = 6.01
    if case == "today_over6": reg.bars[-1]["pct_chg"] = 6.01
    if case == "st": reg.limits["get_limit_up_list","2026-11-12"][0]["name"] = "ST龙江"
    if case == "growth_board": reg.limits["get_limit_up_list","2026-11-12"][0]["code"] = "300001.SZ"
    result = run(calendar_conn,reg,monkeypatch)
    assert [c["code"] for c in result["candidates"]] == ["600002"]


@pytest.mark.parametrize("case", ["calendar_gap","calendar_wrong_exchange","history_failed","bar_missing",
                                  "duplicate_bar","nan_pct","zero_volume","unverified_height","duplicate_limit"])
def test_missing_evidence_only_degrades_new_branch(calendar_conn,monkeypatch,case):
    reg = Registry()
    if case == "calendar_gap": calendar_conn.execute("DELETE FROM trade_calendar WHERE date='2026-11-11'")
    if case == "calendar_wrong_exchange": calendar_conn.execute("UPDATE trade_calendar SET exchange='SZSE' WHERE date='2026-11-11'")
    if case == "history_failed": reg.errors.add(("get_limit_up_list","2026-11-10"))
    if case == "bar_missing": reg.bars.pop(1)
    if case == "duplicate_bar": reg.bars.insert(1,reg.bars[1].copy())
    if case == "nan_pct": reg.bars[1]["pct_chg"] = float("nan")
    if case == "zero_volume": reg.bars[1]["vol"] = 0
    if case == "unverified_height": reg.limits["get_limit_up_list","2026-11-12"][0]["limit_times_verified"] = False
    if case == "duplicate_limit": reg.limits["get_limit_up_list","2026-11-10"].append(stock(height=3))
    result = run(calendar_conn,reg,monkeypatch)
    assert result["status"] == "partial"
    assert result["rebreak"]["status"] in {"partial","source_failed"}
    assert result["rebreak"]["gaps"]
    assert [c["code"] for c in result["candidates"]] == ["600002"]
    md = renderer.render_daily(result,[],None)
    assert "缺口" in md and "真实空候选" not in md


def test_exact_six_percent_kept(calendar_conn,monkeypatch):
    reg=Registry()
    reg.bars[1]["pct_chg"] = reg.bars[-1]["pct_chg"] = 6.0
    assert len(run(calendar_conn,reg,monkeypatch)["candidates"]) == 2


def test_branch_survives_scoring_rendering_and_pk(calendar_conn,monkeypatch):
    result=run(calendar_conn,Registry(),monkeypatch)
    cand=next(c for c in result["candidates"] if c["code"]=="601188")
    card=scorer.build_fact_card(cand,main_sectors=set(),ann_result=None,holder_result=None,earnings_rows=[],adj_factors=None)
    scored=scorer.score_all([card])
    md=renderer.render_daily(result,scored,None)
    assert "反包后再断板" in md and "前段3板" in md and "昨日1板" in md
    payload=pk._fact_payload(card)
    assert payload["branch"]=="rebreak" and payload["limit_times"]==1
    assert payload["pattern_evidence"]["prior_limit_times"]==3
    assert "total" not in payload and "evidences" not in payload


def test_no_new_entrance_needs_no_history(calendar_conn,monkeypatch):
    reg=Registry()
    reg.limits["get_limit_up_list","2026-11-12"]=[stock("600002.SH",2)]
    result=run(calendar_conn,reg,monkeypatch)
    assert result["status"]=="ok"
    assert not any(call[1]=="2026-11-10" for call in reg.calls)


def test_calendar_weekend_and_closed_day_gap(calendar_conn):
    assert rebreak.recent_dates(calendar_conn,"2026-11-16","2026-11-13") == [
        "2026-11-11","2026-11-12","2026-11-13","2026-11-16"]
    calendar_conn.execute("DELETE FROM trade_calendar WHERE date='2026-11-14'")
    with pytest.raises(ValueError,match="日历缺失"):
        rebreak.recent_dates(calendar_conn,"2026-11-16","2026-11-13")


def test_calendar_does_not_substitute_another_previous_day(calendar_conn):
    with pytest.raises(ValueError,match="不一致"):
        rebreak.recent_dates(calendar_conn,"2026-11-13","2026-11-11")


@pytest.mark.parametrize("case", ["wrong_day","count_mismatch","missing_stocks"])
def test_malformed_historical_list_rejected(case):
    payload=dict(stocks=[stock()],count=1)
    if case=="wrong_day": payload["trade_date"]="20261112"
    if case=="count_mismatch": payload["count"]=2
    if case=="missing_stocks": payload.pop("stocks")
    with pytest.raises(ValueError):
        rebreak.limit_rows(DataResult(data=payload,source="tushare:limit_list_d"),"2026-11-10")


def test_empty_down_provider_contract():
    assert rebreak.limit_rows(DataResult(data=[],source="tushare:limit_list_d"),"2026-11-11",allow_empty_list=True)=={}


def test_new_branch_without_old_candidates(calendar_conn,monkeypatch):
    reg=Registry()
    reg.limits["get_limit_up_list","2026-11-12"]=[stock()]
    result=run(calendar_conn,reg,monkeypatch)
    assert result["status"]=="ok" and result["empty_kind"] is None
    assert [c["code"] for c in result["candidates"]]==["601188"]


def test_missing_new_branch_without_old_candidates_never_claims_zero(calendar_conn,monkeypatch):
    reg=Registry()
    reg.limits["get_limit_up_list","2026-11-12"]=[stock()]
    reg.errors.add(("get_limit_up_list","2026-11-10"))
    result=run(calendar_conn,reg,monkeypatch)
    assert result["status"]=="partial" and result["empty_kind"] is None
    md=renderer.render_daily(result,[],None)
    assert "不能认定全量无候选" in md and "真实空候选" not in md
