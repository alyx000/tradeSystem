from __future__ import annotations

from datetime import date, timedelta
import copy
import sqlite3

import pytest

from services.emotion_leader import tracking, state, service
from services.emotion_leader.renderer import render_daily


def calendar():
    start, end = date(2026, 9, 1), date(2026, 10, 29)
    # 明确的测试日历含国庆休市；生产逻辑不推断周末/节日。
    return [{"date": (start + timedelta(days=i)).isoformat(),
             "is_open": int((start + timedelta(days=i)).weekday() < 5 and not date(2026, 10, 1) <= start + timedelta(days=i) <= date(2026, 10, 7))}
            for i in range((end - start).days + 1)]


def row(code="000001.SZ", **kw):
    return {"code": code, "name": code, "metric_status": "ok", "metric_as_of": "2026-10-29",
            "manual_confirmed": False, "max_height": 6, "last_limit_up_date": "2026-10-23",
            "today_pct_chg": -8, "wave_label": "单波", "distance_from_peak_pct": -15,
            "launch_date": "2026-09-23", **kw}


def payload(rows, **kw):
    return {"date": "2026-10-29", "tracking_calendar": calendar(), "active": rows, "archived": [],
            "status": "partial", "summary": {"active_count": len(rows)}, **kw}


@pytest.mark.parametrize("age,status,group", [(0,"fresh","current"),(4,"fresh","current"),(5,"expired","history")])
def test_manual_expiry_inclusive_confirmation_day(age, status, group):
    dates = tracking.open_dates(calendar(), "2026-10-29")
    r = row(manual_confirmed=True, manual_last_seen_date=dates[-age-1], last_limit_up_date=None)
    result = tracking.annotate(r, "2026-10-29", dates)
    assert result["manual_age_open_days"] == age
    assert result["manual_priority_status"] == status
    assert result["tracking_group"] == group
    assert result["manual_confirmed"] is True


@pytest.mark.parametrize("age,group", [(9,"current"),(10,"history")])
def test_recent_limit_window_keeps_negative_feedback(age, group):
    dates = tracking.open_dates(calendar(), "2026-10-29")
    r = tracking.annotate(row(last_limit_up_date=dates[-age-1]), "2026-10-29", dates)
    assert r["tracking_group"] == group
    assert r["today_pct_chg"] == -8


def test_holidays_not_counted_and_gap_invalidates_calendar():
    dates = tracking.open_dates(calendar(), "2026-10-29")
    assert tracking.trade_age(dates, "2026-09-30", "2026-10-08") == 1
    incomplete = calendar(); incomplete.pop(32)  # 即使缺的是休市日也拒绝。
    assert tracking.open_dates(incomplete, "2026-10-29") == []
    bad = tracking.tracking_view(payload([row(manual_confirmed=True, manual_last_seen_date="2026-10-28")], tracking_calendar=incomplete))
    assert len(bad["unverified"]) == 1 and not bad["history"]


@pytest.mark.parametrize("since", [None,"","2026-10-30","2026-10-25","bad"])
def test_unknown_manual_date_does_not_grant_priority(since):
    r = row(manual_confirmed=True, manual_last_seen_date=since, last_limit_up_date=None)
    out = tracking.tracking_view(payload([r]))
    assert out["unverified"][0]["manual_priority_status"] == "unknown"


def test_old_manual_cannot_displace_daye_jiuyang_and_reconfirmed_is_not_above_height():
    old = [row(f"600{i:03d}.SH", manual_confirmed=True, manual_last_seen_date="2026-09-23",
               last_limit_up_date=None, max_height=0) for i in range(20)]
    recent = [row("603278.SH", name="大业股份", new_peak_today=True), row("002242.SZ", name="九阳股份", today_pct_chg=2.77, new_peak_today=True)]
    fresh = row("300223.SZ", manual_confirmed=True, manual_last_seen_date="2026-10-28", last_limit_up_date=None, max_height=0)
    data = payload(old + recent + [fresh]); before=copy.deepcopy(data)
    view = tracking.tracking_view(data)
    assert [r["name"] for r in view["current"][:2]] == ["大业股份", "九阳股份"]
    assert len(view["history"]) == 20
    assert data == before
    text=render_daily(data, max_rows=12)
    assert "大业股份" in text.split("## 历史核心")[0]
    assert "九阳股份" in text.split("## 历史核心")[0]
    assert "600000.SH" not in text.split("## 历史核心")[0]
    assert "人工已到期" in text


def test_old_manual_new_peak_can_reenter_and_failed_metrics_are_not_retreat():
    out=tracking.tracking_view(payload([
        row("000001.SZ", last_limit_up_date="2026-09-01", new_peak_today=True),
        row("000002.SZ", metric_status="source_failed", today_pct_chg=None),
        row("000003.SZ", metric_status="suspended", today_pct_chg=None),
    ]))
    assert len(out["current"]) == 1 and len(out["unverified"]) == 2 and not out["history"]


def test_archived_cache_does_not_look_like_today_and_original_asof_survives():
    item=row(manual_confirmed=True, manual_last_seen_date="2026-09-01", manual_priority_status="expired", manual_priority_active=False)
    prior={"date":"2026-10-28", "active":[], "archived":[row(metric_as_of="2026-09-30", archived=True)]}
    refreshed, cached, _=state.plan_incremental_refresh([item],prior,set(),"2026-10-29")
    assert not refreshed and cached[0]["metric_as_of"] == "2026-09-30"
    result=tracking.tracking_view(payload([], archived=cached))
    assert result["cached_count"] == 1 and not result["history"]
    item.update(manual_priority_active=True,manual_priority_status="fresh")
    assert state.plan_incremental_refresh([item],prior,set(),"2026-10-29")[0]


def test_calendar_loader_readonly_and_requires_every_natural_day():
    conn=sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE trade_calendar(date TEXT,is_open INTEGER)")
    conn.executemany("INSERT INTO trade_calendar VALUES (?,?)",[(r["date"],r["is_open"]) for r in calendar()])
    before=conn.total_changes
    assert tracking.open_dates(tracking.load_tracking_calendar(conn,"2026-10-29"),"2026-10-29")
    assert conn.total_changes == before


def test_service_expired_manual_archives_without_changing_confirmation(monkeypatch):
    monkeypatch.setattr(service,"load_history",lambda *a,**k: {"target_ok":True,"coverage":{},"missing_dates":[],"errors":[]})
    monkeypatch.setattr(service,"load_tracking_calendar",lambda *a: calendar())
    old=row(manual_confirmed=True,manual_last_seen_date="2026-09-01",last_limit_up_date=None)
    monkeypatch.setattr(service,"discover_lifecycles",lambda *a: {"promoted":[old],"height_breakthrough":{"status":"none"},"current_limit_up_codes":set(),"current_down_codes":set(),"candidates":[]})
    monkeypatch.setattr(service,"_industry_map",lambda *a: ({},None,""))
    monkeypatch.setattr(service,"fetch_metrics",lambda *a: {"metric_status":"ok","distance_from_peak_pct":-40,"today_pct_chg":-2})
    monkeypatch.setattr(service,"load_suspensions",lambda *a: {})
    out=service.run_daily(None,None,"2026-10-29")
    assert not out["active"] and out["archived"][0]["manual_confirmed"] is True
    assert len(tracking.tracking_view(out)["history"]) == 1


def test_stale_active_metrics_cannot_be_shown_as_today():
    data=payload([row(metric_as_of="2026-10-28",new_peak_today=True)])
    result=tracking.tracking_view(data)
    assert result["unverified"][0]["today_pct_chg"] is None
    assert result["unverified"][0]["feedback_type"] == "未计算"
    assert not result["current"]


def test_current_peak_not_archived_even_with_deep_intraday_drawdown():
    dates=tracking.open_dates(calendar(),"2026-10-29")
    item=tracking.annotate(row(last_limit_up_date="2026-09-01",new_peak_today=True,distance_from_peak_pct=-35),"2026-10-29",dates)
    assert not tracking.should_archive(item)


def test_service_today_single_limit_is_classified_before_archiving(monkeypatch):
    monkeypatch.setattr(service,"load_history",lambda *a,**k: {"target_ok":True,"coverage":{},"missing_dates":[],"errors":[]})
    monkeypatch.setattr(service,"load_tracking_calendar",lambda *a: calendar())
    old=row(last_limit_up_date="2026-09-01")
    monkeypatch.setattr(service,"discover_lifecycles",lambda *a: {"promoted":[old],"height_breakthrough":{"status":"none"},"current_limit_up_codes":{old["code"]},"current_down_codes":set(),"candidates":[]})
    monkeypatch.setattr(service,"_industry_map",lambda *a: ({},None,""))
    monkeypatch.setattr(service,"fetch_metrics",lambda *a: {"metric_status":"ok","distance_from_peak_pct":-40,"today_pct_chg":10,"new_peak_today":False})
    monkeypatch.setattr(service,"load_suspensions",lambda *a: {})
    out=service.run_daily(None,None,"2026-10-29")
    assert out["summary"]["active_count"] == out["summary"]["today_limit_up_count"] == 1
    assert not out["archived"] and len(tracking.tracking_view(out)["current"]) == 1


def test_recent_single_limit_reactivates_cached_row_after_missed_run():
    dates=tracking.open_dates(calendar(),"2026-10-29")
    item=tracking.annotate(row(last_limit_up_date="2026-10-28"),"2026-10-29",dates)
    previous={"date":"2026-10-27","active":[],"archived":[row(last_limit_up_date="2026-09-01",archived=True,metric_as_of="2026-10-20")]}
    refreshed,cached,_=state.plan_incremental_refresh([item],previous,set(),"2026-10-29")
    assert refreshed == [item] and not cached
