from itertools import permutations
from types import SimpleNamespace

import pytest

from services.trend_leader import research_evidence as E
from providers.tushare_provider import TushareProvider


def holders():
    return [dict(ts_code="600000.SH", end_date="20260331", ann_date="20260430", holder_num=1000),
            dict(ts_code="600000.SH", end_date="20260630", ann_date="20260831", holder_num=800)]


def test_point_in_time_latest_two_periods():
    assert E.holder_summary(holders(), "600000", "2026-08-30")["change_pct"] is None
    r = E.holder_summary(holders(), "600000", "2026-08-31")
    assert r["change_pct"] == pytest.approx(-20)
    assert r["latest"]["ann_date"] == "2026-08-31" and r["status"] == "complete"


def test_duplicate_identical_folded_conflict_rejected():
    rows = holders()
    rows.append(rows[-1].copy())
    assert E.holder_summary(rows, "600000", "2026-09-18")["status"] == "complete"
    rows[-1]["holder_num"] = 999
    with pytest.raises(ValueError, match="冲突"):
        E.holder_summary(rows, "600000", "2026-09-18")


def test_future_correction_does_not_replace_known_value():
    rows = holders()+[dict(ts_code="600000.SH", end_date="20260630", ann_date="20260919", holder_num=100)]
    r = E.holder_summary(rows, "600000", "2026-09-18")
    assert r["latest"]["value"] == 800
    assert r["future_rows_excluded"] == 1


def test_unknown_date_and_invalid_counts_remain_partial():
    rows = holders()
    rows.append(dict(ts_code="600000.SH", end_date="20260901", holder_num=1))
    assert E.holder_summary(rows, "600000", "2026-09-18")["status"] == "partial"
    for value in (0, -1, float("nan"), True, 1.5):
        rows = holders()
        rows[-1]["holder_num"] = value
        assert E.holder_summary(rows, "600000", "2026-09-18")["change_pct"] is None


def income():
    return [dict(ts_code="600000.SH", end_date="20250630", ann_date="20250831", report_type="1", n_income_attr_p=100),
            dict(ts_code="600000.SH", end_date="20260630", ann_date="20260830", f_ann_date="20260831", report_type="1", n_income_attr_p=130)]


def test_income_same_period_and_actual_announcement_gate():
    r = E.income_summary(income(), "600000", "2026-08-30")
    assert r["latest"]["end_date"] == "2025-06-30"
    r = E.income_summary(income(), "600000", "2026-08-31")
    assert r["profit_change_pct"] == 30
    rows = income()
    rows[0]["n_income_attr_p"] = 0
    assert E.income_summary(rows, "600000", "2026-09-18")["profit_change_pct"] is None


@pytest.mark.parametrize("reverse", [False, True])
def test_income_same_announcement_revision_selected_without_source_failure(reverse):
    rows = income()
    rows[-1]["update_flag"] = "0"
    rows.append(dict(rows[-1], update_flag="1", n_income_attr_p=150))
    if reverse:
        rows.reverse()
    class Registry:
        def call(self, name, *args):
            return SimpleNamespace(success=True, data=holders() if name == "get_stock_holder_numbers" else rows, source="test")
    card = E.collect(Registry(), "600000", "2026-09-18")
    assert card["status"] == "complete"
    assert card["income"]["profit_change_pct"] == 50
    assert card["income"]["latest"]["update_flag"] == 1


@pytest.mark.parametrize("report_type", [1, 1.0, "1.0", 4, 4.0, "4", "4.0"])
def test_income_accepts_numeric_consolidated_statement_types(report_type):
    rows = income()
    rows[-1]["report_type"] = report_type
    result = E.income_summary(rows, "600000", "2026-09-18")
    assert result["status"] == "complete"
    assert result["profit_change_pct"] == 30


@pytest.mark.parametrize("update_flag", [0, 1])
def test_income_revised_and_adjusted_priority_after_visibility_gate(update_flag):
    rows = income()
    rows.append(dict(rows[-1], report_type=4, update_flag=update_flag, n_income_attr_p=150))
    # A later original statement must not displace the visible adjusted revision.
    rows.append(dict(rows[-1], report_type=1, update_flag=0, ann_date="20260901", n_income_attr_p=160))
    # A still-future revision must never win, even at the highest version rank.
    rows.append(dict(rows[-1], report_type=4, update_flag=1, f_ann_date="20260919", n_income_attr_p=190))
    result = E.income_summary(rows, "600000", "2026-09-18")
    assert result["latest"]["value"] == 150
    assert result["latest"]["report_type"] == 4
    assert result["future_rows_excluded"] == 1


@pytest.mark.parametrize("report_type", [2, 3, 5, 6])
def test_income_still_excludes_non_comparable_statement_types(report_type):
    rows = income()
    rows[-1]["report_type"] = report_type
    result = E.income_summary(rows, "600000", "2026-09-18")
    assert result["status"] == "partial"
    assert result["latest"]["end_date"] == "2025-06-30"


def test_income_same_version_conflict_remains_rejected():
    rows = income()
    rows.append(dict(rows[-1], n_income_attr_p=150))
    with pytest.raises(ValueError, match="冲突"):
        E.income_summary(rows, "600000", "2026-09-18")


def test_failure_is_not_empty_and_sources_independent():
    class Registry:
        def call(self, name, *args):
            if name == "get_stock_holder_numbers":
                return SimpleNamespace(success=False, error="offline")
            return SimpleNamespace(success=True, data=income(), source="test")
    r = E.collect(Registry(), "600000", "2026-09-18")
    assert r["holders"]["status"] == "source_failed"
    assert r["income"]["status"] == "complete" and r["status"] == "partial"
    rendered = "\n".join(E.render([r], dict(collected=1, eligible=2, not_collected=["000001"])))
    assert "offline" in rendered and "未采集：000001" in rendered


def test_provider_capability_dates_and_empty_code(monkeypatch):
    p = TushareProvider.__new__(TushareProvider)
    monkeypatch.setattr(p, "_ensure_pro", lambda method: None)
    calls = []
    monkeypatch.setattr(p, "_query_records", lambda method, **kw: calls.append((method, kw)) or holders())
    assert "get_stock_holder_numbers" in p.get_capabilities()
    assert not p.get_stock_holder_numbers("", "2026-01-01", "2026-09-18").success
    assert not calls
    result = p.get_stock_holder_numbers("600000", "2026-01-01", "2026-09-18")
    assert result.success
    assert calls[0][1]["start_date"] == "20260101" and calls[0][1]["ts_code"] == "600000.SH"
    assert "ann_date" in calls[0][1]["fields"]


@pytest.mark.parametrize("order", permutations(range(3)))
def test_superseded_version_conflict_rejected_in_every_source_order(order):
    rows = income()
    original = dict(rows[-1], update_flag=0, n_income_attr_p=130)
    conflict = dict(original, n_income_attr_p=140)
    revision = dict(original, update_flag=1, n_income_attr_p=150)
    versions = [original, conflict, revision]
    with pytest.raises(ValueError, match="冲突"):
        E.income_summary([rows[0]] + [versions[i] for i in order], "600000", "2026-09-18")
