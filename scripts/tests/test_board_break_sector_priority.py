"""行业归属核验、保守范围、程序裁决与LLM回退。"""
import copy
import itertools
from statistics import median

import pytest

from services.board_break import pk, pk_context
from services.board_break import sector_priority as sp

DAY = "2026-11-13"


def block():
    return dict(status="partial", coverage=dict(eligible_count=4, sample_count=2, missing_codes=["600003.SH", "600004.SH"]),
                rows=[dict(code="600001.SH", industry="甲", pct_chg=1), dict(code="600002.SH", industry="乙", pct_chg=-1)],
                sources={k:dict(status="success", source="tushare:index_member_all" if k == "industry" else "tushare:daily",
                                fetched_at=DAY+"T20:00:00") for k in ("industry", "quotes")})


def auxiliary():
    return dict(status="partial", sources=[dict(source="tushare:index_member_all", fetched_at=DAY+"T20:01:00")],
                components=[dict(ts_code="600001.SH", industry="甲"), dict(ts_code="600003.SH", industry="丙")])


def audit(monkeypatch, b=None, extra=None):
    monkeypatch.setattr(sp.sector_increment, "validate", lambda raw, day: auxiliary() if extra is None else extra)
    return sp.coverage_audit(b or block(), {}, DAY)


def test_missing_located_by_industry_not_blanket(monkeypatch):
    a = audit(monkeypatch)
    assert a["status"] == "ok"
    assert a["mapped_missing"][0]["code"] == "600003.SH"
    assert a["unresolved_codes"] == ["600004.SH"]
    x = sp.industry_bounds([1, 2, 3, 4], "甲", a)
    assert x["known_missing_codes"] == [] and x["potential_missing_max"] == 1
    y = sp.industry_bounds([1, 2, 3, 4], "丙", a)
    assert y["known_missing_codes"] == ["600003.SH"] and y["potential_missing_max"] == 2


def test_other_industry_gaps_do_not_downgrade_complete_industry(monkeypatch):
    e = auxiliary(); e["components"].append(dict(ts_code="600004.SH", industry="丙"))
    a = audit(monkeypatch, extra=e)
    bounds = sp.industry_bounds([1, 2], "甲", a)
    assert bounds["status"] == "complete" and bounds["median_lower"] == bounds["median_upper"] == 1.5
    assert bounds["up_pct_lower"] == bounds["up_pct_upper"] == 100


@pytest.mark.parametrize("mutation", ["date", "conflict", "duplicate", "missing_source", "failed"])
def test_bad_auxiliary_cannot_resolve_missing_identity(monkeypatch, mutation):
    e = auxiliary()
    if mutation == "date": e["sources"][0]["fetched_at"] = "2026-11-16T20:00:00"
    if mutation == "conflict": e["components"][0]["industry"] = "错误行业"
    if mutation == "duplicate": e["components"].append(dict(e["components"][0]))
    if mutation == "missing_source": e["sources"] = []
    if mutation == "failed": e["status"] = "source_failed"
    a = audit(monkeypatch, extra=e)
    assert not a["mapped_missing"] and len(a["unresolved_codes"]) == 2


@pytest.mark.parametrize("extra_source", [
    dict(source="tushare:index_member_all", fetched_at=DAY+"T20:01:00"),
    dict(source="tushare:index_member_all", fetched_at="2026-11-16T20:01:00"),
    dict(source="tushare:index_member_all", fetched_at=DAY+"T20:01:00", status="source_failed"),
])
def test_duplicate_or_conflicting_industry_source_stays_unresolved(monkeypatch, extra_source):
    e = auxiliary(); e["sources"].append(extra_source)
    a = audit(monkeypatch, extra=e)
    assert not a["mapped_missing"] and len(a["unresolved_codes"]) == 2


@pytest.mark.parametrize("mutation", ["count", "duplicate", "overlap", "missing_list", "old_source", "failed_source"])
def test_bad_market_audit_cannot_force_priority(monkeypatch, mutation):
    b = block()
    if mutation == "count": b["coverage"]["eligible_count"] = 3
    if mutation == "duplicate": b["coverage"]["missing_codes"] = ["600003.SH"]*2
    if mutation == "overlap": b["coverage"]["missing_codes"][0] = "600001.SH"
    if mutation == "missing_list": b["coverage"].pop("missing_codes")
    if mutation == "old_source": b["sources"]["industry"]["fetched_at"] = "2026-11-12"
    if mutation == "failed_source": b["sources"]["quotes"]["status"] = "source_failed"
    assert audit(monkeypatch, b=b)["status"] == "missing"


def test_unknown_industry_quote_also_counts_as_potential_missing(monkeypatch):
    b = block(); b["rows"][1]["industry"] = "未分类"
    a = audit(monkeypatch, b=b)
    assert "600002.SH" in a["unresolved_codes"]


def test_bounds_enclose_every_completion():
    peers = [-3, -1, .5, 2, 5]
    a = dict(status="ok", mapped_missing=[], unresolved_codes=["x", "y"])
    bounds = sp.industry_bounds(peers, "甲", a)
    for count in range(3):
        for missing in itertools.product([-1000, -1, 0, 1, 1000], repeat=count):
            values = [*peers, *missing]
            assert bounds["median_lower"] <= median(values) <= bounds["median_upper"]
            up = 100*sum(x > 0 for x in values)/len(values)
            assert bounds["up_pct_lower"] <= up <= bounds["up_pct_upper"]


def card(code, industry, peers, missing=1):
    bounds = sp.industry_bounds(peers, industry, dict(status="ok", mapped_missing=[], unresolved_codes=["x"]*missing))
    return dict(code=code, gain10=10, pct_chg=-1,
                sector_context=dict(industry=industry, trade_date=DAY, source="daily_market.raw_data.market_strength",
                                    status="partial", coverage_audit=bounds))


def test_sector_priority_cannot_be_overridden_by_model_or_individual_gain():
    a = card("600001", "甲", [1, 2, 3, 4, 5]); b = card("600002", "乙", [-5, -4, -3, -2, -1])
    b.update(gain10=100, pct_chg=5)
    calls = []
    result = pk.run_pk([a, b], [dict(code="600001", total=0), dict(code="600002", total=9)],
                       lambda *args: calls.append(args) or '{"winner":"B","reason":"偏爱涨幅"}')
    assert not calls and result["wins"]["600001"] == 1
    assert "程序·板块优先" in result["matches"][0]["reason"]
    assert sp.decide(b, a)["winner"] == "B"


@pytest.mark.parametrize("case", ["overlap", "date", "same_industry", "unknown", "unbounded", "nonfinite", "conflict"])
def test_uncertain_comparison_falls_back_to_llm(case):
    a = card("600001", "甲", [1, 2, 3, 4, 5]); b = card("600002", "乙", [-5, -4, -3, -2, -1])
    if case == "overlap": b = card("600002", "乙", [1, 2, 3, 4, 5])
    if case == "date": b["sector_context"]["trade_date"] = "2026-11-12"
    if case == "same_industry": b["sector_context"]["industry"] = "甲"
    if case == "unknown": b["sector_context"]["coverage_audit"]["status"] = "unknown"
    if case == "unbounded": b = card("600002", "乙", [-1], missing=2)
    if case == "nonfinite": b["sector_context"]["coverage_audit"]["median_upper"] = float('nan')
    if case == "conflict": b["sector_context"]["coverage_audit"].update(up_pct_lower=100, up_pct_upper=100)
    calls = []
    winner, reason = pk._play_match(a, b, lambda *args: calls.append(args) or '{"winner":"B","reason":"个股依据"}')
    assert len(calls) == 1 and winner == "600002"
