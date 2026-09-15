from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
import sqlite3
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
import yaml

from analyzers.ipo_effect import calculate_ipo_effect, collect_ipo_effect, render_ipo_effect
from providers.base import DataResult


DAY = "2026-09-14"
OPEN_DAYS = ["2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11", DAY]


def inputs():
    # 日龄精确触碰 0/1/90/91/183/184/365/366；含北交所、ST、B股。
    ages = [0, 1, 90, 91, 183, 184, 365, 366, 80, 50]
    codes = [f"{i:06d}.SZ" for i in range(1, 9)] + ["920001.BJ", "200001.SZ"]
    quotes, basic = [], []
    for i, (age, code) in enumerate(zip(ages, codes)):
        pct = [200, 10, -5, 0, 5, -1, 2, -2, 6, 80][i]
        quotes.append(dict(ts_code=code, trade_date="20260914", pre_close=10,
                           open=10, close=10*(1+pct/100), pct_chg=pct))
        basic.append(dict(ts_code=code, name=f"示例{i}",
                          list_date=(date.fromisoformat(DAY)-timedelta(days=age)).strftime("%Y%m%d")))
    return quotes, basic, [dict(ts_code="000006.SZ", trade_date="20260914", name="ST示例")]


def compute(quotes=None, basic=None, st=None):
    q, b, s = inputs()
    return calculate_ipo_effect(q if quotes is None else quotes, b if basic is None else basic,
                                s if st is None else st, DAY, recent_open_days=OPEN_DAYS,
                                min_market_count=1)


def test_exact_age_boundaries_exclusions_and_equal_weight_metrics():
    result = compute()
    assert result["status"] == "complete"
    groups = {g["key"]: g for g in result["cohorts"]}
    assert result["market_benchmark"]["sample_count"] == 7
    year = groups["listed_365d"]
    assert year["sample_count"] == 6
    assert year["pct_chg_median"] == 3.5
    assert year["pct_chg_mean"] == 3.0
    assert year["advance_count"] == 4
    assert year["advance_rate"] == 0.6667
    assert year["strong_gain_count"] == 3
    assert year["strong_loss_count"] == 1
    assert groups["listed_183d"]["sample_count"] == 5
    assert groups["listed_90d"]["sample_count"] == 3
    assert groups["listed_365d_ex_first5"]["sample_count"] == 5
    assert groups["listed_365d_shsz"]["sample_count"] == 5
    assert {r["code"] for r in result["constituents"]} == {"000002", "000003", "000004", "000005", "000007", "920001"}
    assert inputs()[0][0]["pct_chg"] == 200  # 首日不混入收益


@pytest.mark.parametrize("which", ["quote_duplicate", "basic_duplicate", "unknown_identity", "wrong_day", "invalid_list_date", "empty_st", "empty_quotes"])
def test_hard_source_errors_never_become_zero_samples(which):
    q, b, s = inputs()
    if which == "quote_duplicate": q.append(deepcopy(q[0]))
    if which == "basic_duplicate": b.append(deepcopy(b[0]))
    if which == "unknown_identity": b.pop()
    if which == "wrong_day": q[0]["trade_date"] = "20260911"
    if which == "invalid_list_date": b[0]["list_date"] = "20260230"
    if which == "empty_st": s.clear()
    if which == "empty_quotes": q.clear()
    result = compute(q, b, s)
    assert result["status"] == "source_failed"
    assert "cohorts" not in result


@pytest.mark.parametrize("pct", [float("nan"), float("inf"), True, 99])
def test_invalid_return_or_close_reference_fails_quality_gate(pct):
    q, b, s = inputs()
    q[1]["pct_chg"] = pct
    assert compute(q, b, s)["status"] == "source_failed"


def test_open_price_gap_keeps_close_metrics_but_marks_partial():
    q, b, s = inputs()
    q[1]["open"] = None
    result = compute(q, b, s)
    assert result["status"] == "partial"
    year = result["cohorts"][0]
    assert year["sample_count"] == 6
    assert year["intraday_sample_count"] == 5
    assert year["pct_chg_median"] == 3.5


def test_missing_quote_preserves_denominators_and_partial_not_fake_empty():
    q, b, s = inputs()
    for i in range(100):
        code = f"{100+i:06d}.SZ"
        b.append(dict(ts_code=code, name="老股", list_date="20000101"))
        q.append(dict(ts_code=code, trade_date=DAY, pre_close=10, open=10, close=10, pct_chg=0))
    q.pop(1)
    result = compute(q, b, s)
    assert result["status"] == "partial"
    assert result["cohorts"][0]["eligible_count"] == 6
    assert result["cohorts"][0]["sample_count"] == 5
    assert result["cohorts"][0]["missing_quote_codes"] == ["000002"]


def test_complete_empty_cohort_has_null_rates():
    q, b, s = inputs()
    for row in b: row["list_date"] = "20000101"
    result = compute(q, b, s)
    assert result["status"] == "complete"
    assert result["cohorts"][0]["sample_count"] == 0
    assert result["cohorts"][0]["pct_chg_median"] is None
    assert result["cohorts"][0]["advance_rate"] is None


@pytest.fixture
def calendar_db(tmp_path):
    path = tmp_path / "calendar.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE trade_calendar(date TEXT, exchange TEXT, is_open INTEGER)")
    start = date(2026, 8, 25)
    for i in range(22):
        day = start+timedelta(days=i)
        conn.execute("INSERT INTO trade_calendar VALUES(?,?,?)", (day.isoformat(), "SSE", int(day.weekday() < 5)))
    conn.commit()
    conn.close()
    return path


@pytest.mark.parametrize("day,hour,status", [("2026-09-13", 20, "skipped"), (DAY, 15, "skipped"), ("2026-09-15", 20, "source_failed")])
def test_date_gates_make_no_provider_calls(calendar_db, day, hour, status):
    registry = MagicMock()
    result = collect_ipo_effect(registry, day, stock_st_result=DataResult([], "test"),
                                now=datetime(2026, 9, 14, hour, tzinfo=ZoneInfo("Asia/Shanghai")), db_path=calendar_db)
    assert result["status"] == status
    registry.call.assert_not_called()


def test_missing_calendar_fails_closed(calendar_db):
    conn = sqlite3.connect(calendar_db)
    conn.execute("DELETE FROM trade_calendar WHERE date='2026-09-10'")
    conn.commit(); conn.close()
    registry = MagicMock()
    result = collect_ipo_effect(registry, DAY, stock_st_result=DataResult([], "test"),
                                now=datetime(2026, 9, 14, 20, tzinfo=ZoneInfo("Asia/Shanghai")), db_path=calendar_db)
    assert result["status"] == "source_failed"
    registry.call.assert_not_called()


def test_collection_uses_target_date_identity_and_preserves_failure(calendar_db, monkeypatch):
    import analyzers.ipo_effect as module
    q, b, s = inputs()
    registry = MagicMock()
    registry.call.side_effect = [DataResult(q, "test:daily"), DataResult(b, "test:identity")]
    calculate = MagicMock(return_value=compute())
    monkeypatch.setattr(module, "calculate_ipo_effect", calculate)
    result = collect_ipo_effect(registry, DAY, stock_st_result=DataResult(s, "test:st"),
                                now=datetime(2026, 9, 14, 20, tzinfo=ZoneInfo("Asia/Shanghai")), db_path=calendar_db)
    assert result["status"] == "complete"
    assert [c.args for c in registry.call.call_args_list] == [("get_market_daily_quotes", DAY), ("get_stock_universe_as_of", DAY)]
    assert calculate.call_args.kwargs["recent_open_days"] == OPEN_DAYS
    registry.call.side_effect = RuntimeError("network unavailable")
    failed = collect_ipo_effect(registry, DAY, stock_st_result=DataResult(s, "test:st"),
                                now=datetime(2026, 9, 14, 20, tzinfo=ZoneInfo("Asia/Shanghai")), db_path=calendar_db)
    assert failed["status"] == "source_failed"


def test_post_collector_wires_full_st_result(monkeypatch, tmp_path):
    from collectors.market import MarketCollector
    monkeypatch.setattr("collectors.market.BASE_DIR", tmp_path)
    st = DataResult(inputs()[2], "test:full_st")
    registry = MagicMock()
    registry.call.side_effect = lambda method, *a, **k: st if method == "get_stock_st" else DataResult(None, "test", error="unavailable")
    expected = compute()
    collect = MagicMock(return_value=expected)
    monkeypatch.setattr("analyzers.ipo_effect.collect_ipo_effect", collect)
    collector = MarketCollector(registry)
    collector._compute_index_ma = MagicMock()
    collector._collect_research_coverage = MagicMock(return_value=[])
    collector._rhythm_analyzer = MagicMock()
    collector._rhythm_analyzer.load_main_theme_names.return_value = []
    collector._rhythm_analyzer.analyze.return_value = []
    result = collector.collect_post_market(DAY)
    assert result["ipo_effect"] == expected
    assert collect.call_args.kwargs["stock_st_result"] is st


def test_post_generator_persists_full_effect_and_renders_status(tmp_path):
    from generators.report import ReportGenerator
    generator = ReportGenerator()
    generator._ensure_dir = MagicMock(return_value=tmp_path)
    effect = compute()
    text, path = generator.generate_post_market(date=DAY, raw_data={"ipo_effect": effect})
    payload = yaml.safe_load(Path(path).read_text())
    assert payload["raw_data"]["ipo_effect"] == effect
    from db.dual_write import _extract_market_row, parse_post_market_envelope
    stored = _extract_market_row(DAY, payload)
    assert parse_post_market_envelope(stored["raw_data"])["raw_data"]["ipo_effect"] == effect
    assert "次新股赚钱效应 [事实·计算]" in text
    assert "上市90天内" in text and "66.7%" in text
    failed = render_ipo_effect(dict(status="source_failed", error="行情缺失"))
    assert "未计算，不代表样本为0" in "".join(failed)
    assert "行情样本/应有" not in "".join(failed)


def test_existing_launchd_is_single_2000_post_schedule():
    import plistlib
    root = Path(__file__).resolve().parents[2]
    config = plistlib.loads((root/"deploy/launchd/com.alyx.tradesystem.today-post.plist").read_bytes())
    schedules = config["StartCalendarInterval"]
    assert {(s["Weekday"], s["Hour"], s["Minute"]) for s in schedules} == {(d, 20, 0) for d in range(1, 6)}
    assert config["ProgramArguments"][-1] == "post"
