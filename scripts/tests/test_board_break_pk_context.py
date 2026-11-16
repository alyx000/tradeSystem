"""只读PK证据、缺口和真实编排接线回归。"""
import copy
import json
import sqlite3
from datetime import date, timedelta

import pytest

from analyzers import market_strength
from services.board_break import pk_context as ctx, pk, scorer, renderer


@pytest.fixture
def sample():
    conn = sqlite3.connect(":memory:")
    conn.execute("create table trade_calendar(date text,is_open integer,exchange text)")
    conn.execute("create table daily_market(date text,raw_data text)")
    for i in range(10):
        d = date(2026, 11, 4) + timedelta(days=i)
        conn.execute("insert into trade_calendar values(?,?,?)", (d.isoformat(), int(d.weekday()<5), "SSE"))
        raw = dict(date=d.isoformat(), limit_up=dict(_source="tushare:limit_list_d", count=1,
                                                    stocks=[dict(code="601188", limit_times=3)]))
        conn.execute("insert into daily_market values(?,?)", (d.isoformat(), json.dumps(raw)))
    bars = [dict(trade_date=f"2026-11-{n:02}", open=10, high=11, low=10, close=11, vol=10) for n in (6,9,10,11,12,13)]
    cand = dict(code="601188", industry="公路", limit_times=3, bars=bars)
    yield conn, cand
    conn.close()


def run(sample):
    conn, cand = sample
    return ctx.build_contexts(conn, [cand], "2026-11-13", "2026-11-12")[cand["code"]]


def test_original_path_full_and_exact_dates(sample):
    sample[1]["bars"][-2].update(open=11, low=11)
    p = run(sample)["board_path"]
    assert (p["status"], p["one_price_count"], p["traded_count"]) == ("complete", 1, 2)
    assert [r["date"] for r in p["rows"]] == ["2026-11-10", "2026-11-11", "2026-11-12"]


def test_rebreak_keeps_two_segments(sample):
    c = sample[1]
    c.update(branch="rebreak", limit_times=1,
             pattern_evidence=dict(dates=[f"2026-11-{i}" for i in (10,11,12,13)], prior_limit_times=3))
    raw = ctx._archive(sample[0], "2026-11-12")
    raw["limit_up"]["stocks"][0].update(limit_times=1, limit_times_verified=True)
    sample[0].execute("update daily_market set raw_data=? where date='2026-11-12'", (json.dumps(raw),))
    p = run(sample)["board_path"]
    assert p["status"] == "complete" and p["expected_count"] == 4
    assert [r["date"] for r in p["rows"]] == ["2026-11-06", "2026-11-09", "2026-11-10", "2026-11-12"]
    assert c["limit_times"] == 1


@pytest.mark.parametrize("change", [dict(vol=0), dict(vol=None), dict(low=12), dict(close=float('nan')),
                                   dict(ts_code="600001.SH"), dict(open=True)])
def test_bad_bars_are_unknown_not_traded(sample, change):
    sample[1]["bars"][-2].update(change)
    p = run(sample)["board_path"]
    assert p["status"] == "partial" and len(p["rows"]) == 2 and p["gaps"]


def test_duplicate_missing_and_unverified_days(sample):
    conn, c = sample
    c["bars"].append(copy.deepcopy(c["bars"][-2]))
    conn.execute("delete from daily_market where date='2026-11-10'")
    conn.execute("update daily_market set raw_data=? where date='2026-11-11'",
                 (json.dumps(dict(date="2026-11-11", limit_up=dict(stocks=[], count=0))),))
    p = run(sample)["board_path"]
    assert p["status"] == "partial" and len(p["gaps"]) == 3 and not p["rows"]


def test_calendar_gap_not_skipped(sample):
    sample[0].execute("delete from trade_calendar where date='2026-11-11'")
    assert run(sample)["board_path"]["status"] == "missing"


@pytest.mark.parametrize("bad", [dict(status="source_failed"), dict(error="失败"), dict(_error="失败"),
                                  dict(errors=["失败"]), dict(_source="unknown"), dict(success=False),
                                  dict(status="partial")])
def test_failed_archive_not_treated_as_success(sample, bad):
    conn, _ = sample
    raw = ctx._archive(conn, "2026-11-12")
    raw["limit_up"].update(bad)
    conn.execute("update daily_market set raw_data=? where date='2026-11-12'", (json.dumps(raw),))
    p = run(sample)["board_path"]
    assert p["status"] == "partial" and len(p["rows"]) == 2


@pytest.mark.parametrize("n", [0, 31, True, 2.5])
def test_invalid_streak(sample, n):
    sample[1]["limit_times"] = n
    assert run(sample)["board_path"]["status"] == "missing"


def test_archive_date_mismatch(sample):
    sample[0].execute("update daily_market set raw_data=? where date='2026-11-12'",
                      (json.dumps(dict(date="2026-11-11", limit_up=dict(stocks=[]))),))
    assert run(sample)["board_path"]["status"] == "partial"


def block():
    return dict(status="partial", rows=[dict(code="601188.SH", industry="公路", pct_chg=10),
                                        dict(code="600001.SH", industry="公路", pct_chg=2),
                                        dict(code="600002.SH", industry="公路", pct_chg=-1)],
                gaps=["宇宙缺行情"], sources={})


def test_sector_excludes_self_and_keeps_gaps():
    s = ctx.sector_context(block(), dict(code="601188", industry="旧名称"), "2026-11-13")
    assert (s["industry"], s["sample_count"], s["mean_pct"], s["up_pct"]) == ("公路", 2, .5, 50)
    assert s["status"] == "partial" and s["gaps"]
    assert s["leadership"]["status"] == "not_computed"


def test_sector_supports_ts_code_contract():
    b = block()
    for r in b["rows"]:
        r["ts_code"] = r.pop("code")
    s = ctx.sector_context(b, dict(code="601188"), "2026-11-13")
    assert s["sample_count"] == 2 and s["mean_pct"] == .5
    assert "全市场覆盖限制" in s["gaps"][0]


def test_missing_sector_not_zero():
    s = ctx.sector_context(block(), dict(code="600003"), "2026-11-13")
    assert s["status"] == "missing" and "mean_pct" not in s


def test_source_failed_no_stats():
    b = block(); b["status"] = "source_failed"
    assert "sample_count" not in ctx.sector_context(b, dict(code="601188"), "2026-11-13")


def test_loader_rejects_corrupt_sector_and_no_writes(sample):
    conn, _ = sample
    raw = dict(date="2026-11-13", market_strength=block())
    conn.execute("update daily_market set raw_data=? where date='2026-11-13'", (json.dumps(raw),))
    changes = conn.total_changes
    assert run(sample)["sector_context"]["status"] == "missing"
    assert conn.total_changes == changes


def test_scorer_pipeline_context_reaches_pk_and_report(sample, monkeypatch):
    from types import SimpleNamespace
    conn, c = sample
    c.update(name="龙江交通", close=10, pct_chg=-1)
    monkeypatch.setattr(scorer, "_fetch_earnings_rows", lambda *a: [])
    registry = SimpleNamespace(call=lambda *a: SimpleNamespace(success=False, data=None))
    result = dict(date="2026-11-13", prev_trade_date="2026-11-12", candidates=[c])
    card = scorer.build_fact_cards(conn, registry, result)[0]
    payload = pk._fact_payload(card)
    assert payload["board_path"]["status"] == "complete"
    assert payload["sector_context"]["status"] == "missing"
    # 附加证据不影响原八维分数。
    assert scorer.score_candidate(card)["total"] == scorer.score_candidate({k:v for k,v in card.items()
                                                        if k not in {"board_path", "sector_context"}})["total"]
    p, s = ctx.describe(card)
    assert "非一价3板" in p and "带动板块：未核验" in s
    assert "total" not in payload


def test_policy_all_pairs_use_same_direction_and_evidence():
    assert "板块评价的重要性高于个股评价" in pk._PROMPT
    assert "先启动" in pk._PROMPT and "不能认定带动" in pk._PROMPT
    assert "下限>50%" in pk._PROMPT
    assert "不能因partial整体弃用" in pk._PROMPT
    assert "程序直接执行板块优先" in pk._PROMPT


def test_partial_majority_lower_bound(sample):
    conn, cand = sample
    cand["limit_times"] = 4
    raw = ctx._archive(conn, "2026-11-12")
    raw["limit_up"]["stocks"][0].update(limit_times=4, limit_times_verified=True)
    conn.execute("update daily_market set raw_data=? where date='2026-11-12'", (json.dumps(raw),))
    conn.execute("delete from daily_market where date='2026-11-09'")
    for b in cand["bars"]:
        if b["trade_date"] in {"2026-11-10", "2026-11-11", "2026-11-12"}:
            b.update(open=11, low=11)
    p = run(sample)["board_path"]
    assert p["status"] == "partial" and p["denominator_status"] == "verified"
    assert (p["one_price_pct_lower"], p["one_price_pct_upper"]) == (75, 100)
    assert p["majority_one_price"] is True and not p["all_traded"]
    assert "已确定一价居多" in ctx.describe(dict(board_path=p))[0]


@pytest.mark.parametrize("one,traded,majority", [(2, 2, False), (2, 1, None), (3, 0, True), (0, 4, False)])
def test_path_bound_threshold(one, traded, majority):
    p = dict(expected_count=4, rows=[{}]*(one+traded), denominator_status="verified",
             one_price_count=one, traded_count=traded)
    ctx.path_bounds(p)
    assert p["majority_one_price"] is majority
    assert p["all_traded"] == (traded == 4)


def test_bad_denominator_cannot_establish_majority(sample):
    conn, cand = sample
    cand["limit_times"] = 4  # 源仅确认3板，不允许拿候选提示值当分母。
    for b in cand["bars"]:
        b.update(open=11, low=11)
    p = run(sample)["board_path"]
    assert p["denominator_status"] == "missing" and "one_price_pct_lower" not in p
