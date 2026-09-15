"""板块风险的日期、证据、代理边界及正式HTML集成。外网全部隔离。"""
import copy
import importlib.util
import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from services.sector_adjustment_risk import collector as c
from services.sector_adjustment_risk import detectors as d
from services.sector_adjustment_risk import renderer as r
from tests.test_daily_review_html_report import DATE, _load_assembler, _write_chunks


def days(end="2026-09-15", count=120):
    day = date.fromisoformat(end)
    result = []
    while len(result) < count:
        if day.weekday() < 5:
            result.append(day.isoformat())
        day -= timedelta(days=1)
    return result[::-1]


def bars(dates=None, peaks=False):
    dates = dates or days()
    knots = [(0, 100), (60, 105), (85, 160), (95, 140), (110, 161), (119, 135)] if peaks else [(0, 100), (119, 104)]
    output = []
    for i, stamp in enumerate(dates):
        a, b = next((a, b) for a, b in zip(knots, knots[1:]) if a[0] <= i <= b[0])
        close = a[1]+(b[1]-a[1])*(i-a[0])/(b[0]-a[0])
        output.append(dict(time=stamp, open=close-.1, high=close+.5, low=close-.5, close=close, vol=1000))
    return output


def raw(rows, code="801081.SI", minute=False):
    return [{**b, "ts_code": code, ("trade_time" if minute else "trade_date"): b["time"] if minute else b["time"].replace("-", "")} for b in rows]


def payload(report_date=DATE, peaks=True):
    dates = days(report_date)
    daily = bars(dates, peaks)
    row = {"code": "801081.SI", "name": "半导体", "kind": "industry", "status": "partial", "minute_status": "source_failed", "gaps": ["60分钟缺失"], "daily_bars": daily, "minute_bars": None, **d.analyze(daily)}
    p = c.failed(report_date, "")
    p.update(status="partial", rows=[row], gaps=[], expected_dates=dates)
    return p


def test_flat_low_level_not_top_and_missing_minutes_not_zero():
    result = d.analyze(bars())
    assert result["level"] == "not_applicable" and result["eligible"] is False
    assert result["minute_break"] is None


def test_actual_macd_double_peak_warns_without_minute_confirmation():
    result = d.analyze(bars(peaks=True))
    assert result["eligible"] and result["daily_divergence"]
    assert result["daily_divergence"]["second_dif"] < result["daily_divergence"]["first_dif"]
    assert result["level"] == "warning"
    assert result["minute_break"] is None


def test_actual_minute_backing_confirms_and_recovery_clears_break():
    minutes = bars(peaks=True)
    # 第二峰前最大量阳线低点，被末尾两根K线跌穿。
    minutes[105]["vol"] = 5000
    result = d.analyze(bars(peaks=True), minutes)
    assert result["minute_break"] and result["level"] == "confirmed"
    minutes[-1].update(open=158, close=158, high=158.5, low=157.5)
    assert d.analyze(bars(peaks=True), minutes)["level"] == "warning"


def test_new_high_invalidates_old_divergence():
    b = bars(peaks=True)
    b[-1].update(high=170, close=169, open=168, low=167)
    assert d.divergence(b, window=60) is None


@pytest.mark.parametrize("mutation", ["duplicate", "future", "identity", "missing", "nan", "bad_ohlc", "boolean"])
def test_rejects_bad_daily_data(mutation):
    rows = raw(bars())
    if mutation == "duplicate": rows.append(rows[-1])
    if mutation == "future": rows[-1]["trade_date"] = "20260916"
    if mutation == "identity": rows[-1]["ts_code"] = "000001.SH"
    if mutation == "missing": rows.pop(40)
    if mutation == "nan": rows[-1]["close"] = float("nan")
    if mutation == "bad_ohlc": rows[-1]["low"] = 500
    if mutation == "boolean": rows[-1]["vol"] = True
    with pytest.raises((ValueError, TypeError)):
        d.normalize(rows, "801081.SI", days())


def test_incomplete_minutes_rejected_even_when_last_bar_is_today_close():
    expected = [day+" "+t for day in days()[-30:] for t in ("10:30:00", "11:30:00", "14:00:00", "15:00:00")]
    rows = raw(bars(expected), minute=True)
    rows.pop(50)
    with pytest.raises(ValueError, match="缺口"):
        d.normalize(rows, "801081.SI", expected, minute=True)


def test_only_verified_closed_rows_excluded_with_receipt():
    rows = raw(bars())
    weekend = dict(rows[-1], trade_date="20260913", close=float("nan"))
    kept, excluded = c.exclude_closed_rows(rows+[weekend], "801081.SI", days())
    assert len(kept) == 120 and excluded == ["2026-09-13"]
    assert len(d.normalize(kept, "801081.SI", days())) == 120
    with pytest.raises(ValueError):
        c.exclude_closed_rows(rows+[dict(weekend, trade_date="20260916")], "801081.SI", days())
    rows[-1]["close"] = float("nan")
    kept, _ = c.exclude_closed_rows(rows+[weekend], "801081.SI", days())
    with pytest.raises(ValueError):
        d.normalize(kept, "801081.SI", days())


def test_post_pipeline_invokes_module_and_isolates_failure(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    from collectors.market import MarketCollector
    from tests.test_post_market_enhancements import _mock_registry
    monkeypatch.setattr("collectors.market.BASE_DIR", tmp_path)
    reg = _mock_registry()
    expected = c.failed("2026-09-15", "test")
    call = MagicMock(return_value=expected)
    monkeypatch.setattr(c, "collect", call)
    collector = MarketCollector(reg)
    collector._rhythm_analyzer = MagicMock()
    result = collector.collect_post_market("2026-09-15")
    assert result["sector_adjustment_risk"] == expected
    call.assert_called_once_with(reg, "2026-09-15")
    call.side_effect = RuntimeError("test")
    result = collector.collect_post_market("2026-09-15")
    assert result["sector_adjustment_risk"]["status"] == "source_failed"
    assert "indices" in result


def test_calendar_complete_sse_and_after_close(tmp_path):
    db = tmp_path/"calendar.db"
    end = date(2026, 9, 15)
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE trade_calendar(date TEXT,is_open INTEGER,exchange TEXT)")
        for i in range(265):
            day = end-timedelta(days=i)
            conn.execute("INSERT INTO trade_calendar VALUES(?,?,?)", (str(day), int(day.weekday()<5), "SSE"))
    now = datetime(2026, 9, 15, 16, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert len(c.calendar(db, str(end), now)) == 120
    assert c.calendar(db, "2026-09-13", now) == []
    with pytest.raises(ValueError, match="16:00"):
        c.calendar(db, str(end), now.replace(hour=15))
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM trade_calendar WHERE date='2026-09-06'")
    with pytest.raises(ValueError, match="日历不完整"):
        c.calendar(db, str(end), now)


def test_activity_five_days_coverage_not_sparse_average():
    dates = days()[-5:]
    snapshots = {day: [dict(ts_code="A", trade_date=day.replace("-", ""), amount=10, pct_change=1), dict(ts_code="B", trade_date=day.replace("-", ""), amount=20, pct_change=-1)] for day in dates}
    selected = c.select_active(snapshots, {"A":"a", "B":"b"}, dates, kind="industry")
    assert selected[0]["code"] == "B" and selected[0]["activity_mean"] == 20
    snapshots[dates[0]].pop()
    remaining = c.select_active(snapshots, {"A":"a", "B":"b"}, dates, kind="industry")
    assert [x["code"] for x in remaining] == ["A"]


class Pro:
    def __init__(self, fail_concept=False):
        self.minute_calls = 0
        self.fail_concept = fail_concept
    def index_classify(self, **kwargs):
        return pd.DataFrame([dict(index_code=x, industry_name=x) for x in ("801081.SI", "801082.SI")])
    def ths_index(self, **kwargs):
        return pd.DataFrame([dict(ts_code="885001.TI", name="测试概念")])
    def index_daily(self, ts_code, start_date, end_date):
        rows = [dict(x, amount=10000) for x in raw(bars(), ts_code) if start_date <= x["trade_date"] <= end_date]
        return pd.DataFrame(rows)
    def sw_daily(self, ts_code=None, trade_date=None, **kwargs):
        if ts_code:
            return pd.DataFrame(raw(bars(peaks=True), ts_code))
        return pd.DataFrame([dict(ts_code=x, trade_date=trade_date, amount=100, pct_change=2) for x in ("801081.SI", "801082.SI")])
    def ths_daily(self, ts_code=None, trade_date=None, **kwargs):
        if self.fail_concept: raise RuntimeError("offline")
        if ts_code: return pd.DataFrame(raw(bars(), ts_code))
        return pd.DataFrame([dict(ts_code="885001.TI", trade_date=trade_date, turnover_rate=3, pct_change=1)])
    def sw_mins(self, **kwargs):
        self.minute_calls += 1
        raise RuntimeError("您的权限不足")


@pytest.mark.parametrize("fail_concept", [False, True])
def test_collection_permission_circuit_breaker_and_branch_isolation(monkeypatch, fail_concept):
    monkeypatch.setattr(c, "calendar", lambda *a, **k: days())
    pro = Pro(fail_concept)
    p = c.collect(SimpleNamespace(get_provider=lambda _: SimpleNamespace(pro=pro)), "2026-09-15", db_path="unused")
    assert p["status"] == "partial" and pro.minute_calls == 1
    assert p["coverage"]["daily_valid"] == (2 if fail_concept else 3)
    assert p["coverage"]["minute_valid"] == 0
    assert not any(row["level"] == "confirmed" for row in p["rows"])
    r.validate(p, p["date"])


def test_market_increment_uses_three_separate_prior_twenty_windows():
    pro = Pro()
    base = pro.index_daily
    def index_daily(**kwargs):
        frame = base(**kwargs)
        frame.loc[frame["trade_date"].isin([d.replace("-", "") for d in days()[-3:]]), "amount"] = 12000
        return frame
    pro.index_daily = index_daily
    result = c.market_context(pro, days())
    assert result["sustained_increment"]
    assert result["three_ratios"][0] > result["three_ratios"][1] > result["three_ratios"][2]


def test_market_increment_is_explicitly_combined_not_each_exchange():
    pro = Pro()
    base = pro.index_daily
    def index_daily(**kwargs):
        frame = base(**kwargs)
        ratio = .5 if kwargs["ts_code"] == "000001.SH" else 2.0
        frame.loc[frame["trade_date"].isin([day.replace("-", "") for day in days()[-3:]]), "amount"] = 10000*ratio
        return frame
    pro.index_daily = index_daily
    result = c.market_context(pro, days())
    assert result["sustained_increment"] is True
    assert result["three_ratios"][0] == 1.25 and "合计" in result["definition"]
    p = payload("2026-09-15"); p["market"] = result
    r.validate(p, p["date"])
    assert "沪深合计成交额" in r.render(p, p["date"])[0]


def test_renderer_recomputes_and_rejects_forged_confirmed():
    p = payload()
    p["rows"][0]["level"] = "confirmed"
    with pytest.raises(ValueError, match="证据不一致"):
        r.validate(p, DATE)
    html, gaps = r.render(p, DATE)
    assert "数据无效" in html or "证据无效" in html
    assert gaps


def test_load_never_substitutes_old_date(tmp_path):
    reports = tmp_path/"reports"; reports.mkdir()
    (reports/"2026-09-14.json").write_text(json.dumps(payload("2026-09-14")))
    assert r.load(tmp_path/"daily", reports, "2026-09-15")["status"] == "source_failed"


@pytest.mark.parametrize("newer,valid", [(True, True), (False, True), (True, False)])
def test_same_day_recovery_reaches_formal_html_only_with_new_valid_evidence(tmp_path, newer, valid):
    import yaml
    daily = tmp_path/"daily"/DATE; daily.mkdir(parents=True)
    reports = tmp_path/"reports"; reports.mkdir()
    old = c.failed(DATE, "原接口失败")
    old["generated_at"] = DATE+"T20:00:00+08:00"
    (daily/"post-market.yaml").write_text(yaml.safe_dump({"date": DATE, "raw_data": {"sector_adjustment_risk": old}}))
    recovered = payload()
    recovered["generated_at"] = DATE+("T21:00:00+08:00" if newer else "T19:00:00+08:00")
    if not valid:
        recovered["rows"][0]["level"] = "confirmed"
    (reports/f"{DATE}.json").write_text(json.dumps(recovered))
    selected = r.load(tmp_path/"daily", reports, DATE)
    if newer and valid:
        assert selected["status"] == "partial" and "原接口失败" in selected["recovery_note"]
        assembler = _load_assembler(); chunks = tmp_path/"chunks"; chunks.mkdir(); _write_chunks(chunks)
        html = assembler.render_report(chunks, DATE, sector_adjustment_risk=selected)
        assert "同日较新补采" in html and "日线动能预警" in html
        assembler.validate_report(html, sector_adjustment_risk=selected)
    else:
        assert selected["status"] == "source_failed"


@pytest.mark.parametrize("newer", [True, False])
def test_equal_coverage_corrected_evidence_reaches_formal_html(tmp_path, newer):
    import yaml
    daily = tmp_path/"daily"/DATE; daily.mkdir(parents=True)
    reports = tmp_path/"reports"; reports.mkdir()
    old = payload(peaks=False); old["generated_at"] = DATE+"T20:00:00+08:00"
    new = payload(peaks=True)
    new["generated_at"] = DATE+("T21:00:00+08:00" if newer else "T19:00:00+08:00")
    assert old["rows"][0]["level"] != new["rows"][0]["level"]
    (daily/"post-market.yaml").write_text(yaml.safe_dump({"date": DATE, "raw_data": {"sector_adjustment_risk": old}}))
    (reports/f"{DATE}.json").write_text(json.dumps(new))
    selected = r.load(tmp_path/"daily", reports, DATE)
    assert selected["rows"] == (new if newer else old)["rows"]
    assembler = _load_assembler(); chunks = tmp_path/"chunks"; chunks.mkdir(); _write_chunks(chunks)
    html = assembler.render_report(chunks, DATE, sector_adjustment_risk=selected)
    assembler.validate_report(html, sector_adjustment_risk=selected)
    assert ("日线动能预警" in html) == newer


def test_formal_assembler_injects_once_matches_source_and_refuses_override(tmp_path):
    assembler = _load_assembler(); _write_chunks(tmp_path)
    p = payload()
    html = assembler.render_report(tmp_path, DATE, sector_adjustment_risk=p)
    assert html.count('data-sector-adjustment-risk="v1"') == 1
    assert "日线动能预警" in html and "60分钟缺失" in html
    assembler.validate_report(html, sector_adjustment_risk=p)
    with pytest.raises(assembler.ReportValidationError, match="source_mismatch"):
        assembler.validate_report(html.replace("日线动能预警", "绝对安全"), sector_adjustment_risk=p)
    chunk = tmp_path/f"b{DATE}_s2.html"
    chunk.write_text(chunk.read_text()+'<div data-sector-adjustment-risk="v1"></div>')
    with pytest.raises(assembler.ReportValidationError, match="duplicate_sector"):
        assembler.render_report(tmp_path, DATE)


def test_report_quality_regression_keeps_previous_and_records_attempt(tmp_path):
    path = Path(__file__).parents[1]/"tools/sector_adjustment_risk.py"
    spec = importlib.util.spec_from_file_location("sector_risk_tool", path)
    tool = importlib.util.module_from_spec(spec); spec.loader.exec_module(tool)
    p = payload(); assert tool.save(p, tmp_path) == "saved"
    assert tool.save(c.failed(DATE, "down"), tmp_path) == "fallback_preserved"
    assert json.loads((tmp_path/f"{DATE}.json").read_text())["rows"]
    assert json.loads((tmp_path/f"{DATE}.attempt.json").read_text())["status"] == "source_failed"


@pytest.mark.parametrize("new_codes,expected", [(["B", "C"], "fallback_preserved"), (["A", "B", "C"], "saved"), (["A", "B"], "saved")])
def test_report_save_preserves_concrete_codes_not_just_count(tmp_path, new_codes, expected):
    path = Path(__file__).parents[1]/"tools/sector_adjustment_risk.py"
    spec = importlib.util.spec_from_file_location("sector_risk_tool_sets", path)
    tool = importlib.util.module_from_spec(spec); spec.loader.exec_module(tool)
    def with_codes(codes):
        p = payload(); row = p["rows"][0]
        p["rows"] = [{**copy.deepcopy(row), "code": code, "name": code} for code in codes]
        return p
    tool.save(with_codes(["A", "B"]), tmp_path)
    before = (tmp_path/f"{DATE}.json").read_bytes()
    assert tool.save(with_codes(new_codes), tmp_path) == expected
    if expected == "fallback_preserved":
        assert (tmp_path/f"{DATE}.json").read_bytes() == before
        attempt = json.loads((tmp_path/f"{DATE}.attempt.json").read_text())
        assert attempt["coverage_change"]["lost_daily"] == ["A"]
        assert attempt["coverage_change"]["added_daily"] == ["C"]
    else:
        assert {r["code"] for r in json.loads((tmp_path/f"{DATE}.json").read_text())["rows"]} == set(new_codes)
