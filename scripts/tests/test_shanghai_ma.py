"""上证日均线按交易所开放日和上海观测时点核验，所有源均为隔离的测试数据。"""
from datetime import date, datetime, timedelta
import json
import sqlite3
from unittest.mock import MagicMock

import pandas as pd
import pytest

from analyzers.node_signals import NodeSignalAnalyzer
from analyzers.shanghai_ma import CODE, SHANGHAI, collect
from collectors.market import MarketCollector
from generators.report import ReportGenerator
from providers.base import DataResult
from providers.tushare_provider import TushareProvider

DAY = "2026-11-27"
EVENING = datetime(2026, 11, 27, 20, 30, tzinfo=SHANGHAI)
LATER = EVENING + timedelta(days=1)


def quote(day=DAY, close=3600.0):
    return {"code": CODE, "trade_date": day, "_source": "tushare:index_daily", "close": close}


@pytest.fixture
def facts(tmp_path):
    folder = tmp_path / "data"
    folder.mkdir()
    db = sqlite3.connect(folder / "trade.db")
    db.executescript("""
        CREATE TABLE trade_calendar (exchange, date, is_open);
        CREATE TABLE market_timing_signal (index_code, trade_date, close, data_source, updated_at);
        CREATE TABLE daily_market (date, raw_data);
    """)
    opens = []
    for n in range(120, -1, -1):
        d = date.fromisoformat(DAY) - timedelta(days=n)
        db.execute("INSERT INTO trade_calendar VALUES ('SSE', ?, ?)", (d.isoformat(), int(d.weekday() < 5)))
        if d.weekday() < 5:
            opens.append(d.isoformat())
    for n, d in enumerate(opens[:-1]):
        db.execute("INSERT INTO market_timing_signal VALUES (?, ?, ?, 'tushare:index_daily', ?)",
                   (CODE, d, 3000 + n, d + " 12:30:00"))
    db.execute("INSERT INTO daily_market VALUES (?, ?)", (DAY, json.dumps({
        "date": DAY, "generated_at": DAY + "T20:01:00", "raw_data": {"date": DAY, "indices": {"shanghai": quote()}}
    })))
    db.commit()
    yield db, list(reversed(opens))
    db.close()


def write_archive(root, rows, stamp, code=CODE):
    path = root / "data/reports/review-evidence" / stamp[:10] / "tmp/market_raw_000001.SH.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(dict(code=code, fetched_at=stamp, source="tushare-mirror:index_daily", rows=rows)))


def test_all_periods_and_previous_share_exact_calendar(tmp_path, facts):
    db, opens = facts
    ma = collect(DAY, quote(), tmp_path, None, now=LATER)
    evidence = ma["daily_ma_evidence"]
    for period in (5, 10, 20, 60):
        values = [evidence["closes"][d]["close"] for d in opens[:period]]
        window = evidence["windows"][f"ma{period}"]
        assert ma[f"ma{period}"] == round(sum(values) / period, 2)
        assert window["start"] == opens[period - 1]
        assert window["observed"] == window["expected"] == period
        assert evidence["previous_windows"][f"ma{period}"]["start"] == opens[period]
    assert evidence["as_of"] == DAY + "T20:01:00+08:00"


def test_missing_session_never_replaced_by_an_older_close(tmp_path, facts, monkeypatch):
    db, opens = facts
    missing = opens[20]
    db.execute("DELETE FROM market_timing_signal WHERE trade_date=?", (missing,))
    db.commit()
    registry = MagicMock()
    registry.call.return_value = DataResult(data=None, source="mock", error="unavailable")
    monkeypatch.setattr("collectors.market.BASE_DIR", tmp_path)
    # 同一生产入口下每日实时和历史均可复算，固定历史时点使测试不依赖系统时钟。
    monkeypatch.setattr("analyzers.shanghai_ma.collect",
                        lambda day, q, root, reg: collect(day, q, root, reg, now=LATER))
    raw = {"indices": {"shanghai": quote()}}
    MarketCollector(registry)._compute_index_ma(raw, DAY)
    ma = raw["moving_averages"]["shanghai"]
    assert "ma60" not in ma and "above_ma60" not in ma and "ma20" in ma
    w = ma["daily_ma_evidence"]["windows"]["ma60"]
    assert w["observed"] == 59 and w["missing_dates"] == [missing]
    monkeypatch.setattr("generators.report.BASE_DIR", tmp_path)
    text, _ = ReportGenerator().generate_post_market(DAY, raw)
    assert "MA60 待核（59/60）" in text
    assert not any(c.args[0] == "get_index_daily_range" for c in registry.call.call_args_list)


@pytest.mark.parametrize(("stamp", "code", "duplicate", "accepted"), [
    ("2026-11-27T20:00:00+08:00", CODE, False, True),
    ("2026-11-27T12:00:00Z", CODE, False, True),
    ("2026-11-27T20:02:00+08:00", CODE, False, False),
    ("2026-11-28T08:00:00+08:00", CODE, False, False),
    ("2026-11-27T11:59:00+00:00", "000001.SZ", False, False),
    ("2026-11-27T11:59:00+00:00", CODE, True, False),
])
def test_local_replay_verifies_code_duplicates_and_visibility(tmp_path, facts, stamp, code, duplicate, accepted):
    db, opens = facts
    missing = opens[20]
    db.execute("DELETE FROM market_timing_signal WHERE trade_date=?", (missing,))
    db.commit()
    rows = [{"ts_code": code, "trade_date": missing, "close": 3400}]
    if duplicate:
        rows.append(dict(rows[0], close=3401))
    write_archive(tmp_path, rows, stamp)
    ma = collect(DAY, quote(), tmp_path, None, now=LATER)
    assert ("ma60" in ma) is accepted


def test_previous_incomplete_suppresses_daily_cross(tmp_path, facts):
    db, opens = facts
    db.execute("DELETE FROM market_timing_signal WHERE trade_date=?", (opens[60],))
    db.commit()
    ma = collect(DAY, quote(), tmp_path, None, now=LATER)
    assert ma["daily_ma_evidence"]["previous_windows"]["ma60"]["observed"] == 59
    assert "ma60" in ma
    raw = {"moving_averages": {"shanghai": ma}}
    assert not any("MA60" in s["signal"] for s in NodeSignalAnalyzer(tmp_path).analyze(raw, DAY))


def test_calendar_hole_stops_windows_and_before_close_suppresses_all(tmp_path, facts):
    db, opens = facts
    missing = opens[8]
    db.execute("DELETE FROM trade_calendar WHERE date=?", (missing,))
    db.commit()
    ma = collect(DAY, quote(), tmp_path, None, now=LATER)
    assert "ma5" in ma and "ma10" not in ma and missing in ma["daily_ma_evidence"]["problems"][0]
    early = collect(DAY, quote(), tmp_path, None, now=EVENING.replace(hour=14))
    assert "ma5" not in early and "目标日尚未收盘" in early["daily_ma_evidence"]["problems"]


def test_realtime_range_fills_only_gaps_once(tmp_path, facts):
    db, opens = facts
    missing = [opens[3], opens[20]]
    db.executemany("DELETE FROM market_timing_signal WHERE trade_date=?", [(d,) for d in missing])
    db.commit()
    reg = MagicMock()
    reg.call.return_value = DataResult(source="tushare:index_daily",
                                       data=[{"ts_code": CODE, "trade_date": d, "close": 3150} for d in missing])
    ma = collect(DAY, quote(), tmp_path, reg, now=EVENING)
    reg.call.assert_called_once_with("get_index_daily_range", CODE, min(missing), max(missing))
    assert "ma60" in ma
    reg.call.return_value.data[0]["ts_code"] = "000001.SZ"
    assert "ma5" not in collect(DAY, quote(), tmp_path, reg, now=EVENING)


def test_historical_input_cannot_replace_missing_or_conflicting_original(tmp_path, facts):
    db, _ = facts
    assert "ma5" not in collect(DAY, quote(close=3601), tmp_path, None, now=LATER)
    db.execute("DELETE FROM daily_market WHERE date=?", (DAY,))
    db.commit()
    assert "ma5" not in collect(DAY, quote(), tmp_path, None, now=LATER)


@pytest.mark.parametrize(("code", "day", "count"), [
    ("000001.SZ", "20261127", 1), (CODE, "20261126", 1), (CODE, "20261127", 2), (CODE, "20261127garbage", 1),
])
def test_tushare_does_not_relabel_wrong_rows(code, day, count):
    provider = TushareProvider.__new__(TushareProvider)
    provider.pro = MagicMock()
    provider.pro.index_daily.return_value = pd.DataFrame([dict(ts_code=code, trade_date=day, close=3500,
                                                             high=3500, low=3500, open=3500, pct_chg=0)] * count)
    assert not provider.get_index_daily("shanghai", DAY).success
    assert not provider.get_index_daily_range(CODE, DAY, DAY).success


def test_intraday_archive_keeps_earlier_close_only(tmp_path, facts):
    db, opens = facts
    partial_day, prior = opens[1:3]
    db.executemany("DELETE FROM market_timing_signal WHERE trade_date=?", [(partial_day,), (prior,)])
    db.commit()
    rows = [{"ts_code": CODE, "trade_date": d, "close": 3210} for d in (prior, partial_day)]
    write_archive(tmp_path, rows, partial_day + "T14:59:59+08:00")
    evidence = collect(DAY, quote(), tmp_path, None, now=LATER)["daily_ma_evidence"]
    assert prior in evidence["closes"] and partial_day not in evidence["closes"]
    assert evidence["windows"]["ma5"]["missing_dates"] == [partial_day]


def test_read_view_uses_only_callers_db_and_preserves_legacy(tmp_path, facts):
    from api.market_enrich import enrich_daily_market_row

    db, _ = facts
    old = {"ma60": 1.0, "above_ma60": False, "ma5w": 3650.0}
    raw = json.dumps({"date": DAY, "raw_data": {"indices": {"shanghai": quote()},
                                               "moving_averages": {"shanghai": old}}})
    source_signals = [{"type": "ma_cross", "signal": "跌破MA60", "value": 1},
                      {"type": "ma_cross", "signal": "突破5周均线", "value": 3650}]
    row = {"date": DAY, "raw_data": raw, "node_signals": json.dumps(source_signals)}
    view = enrich_daily_market_row(dict(row), conn=db)
    ma = view["moving_averages"]["shanghai"]
    assert ma["ma60"] > 3000 and ma["ma5w"] == old["ma5w"]
    assert ma["archived_unverified"]["values"]["ma60"] == 1
    assert ma["archived_unverified"]["node_signals"] == source_signals[:1]
    assert source_signals[0] not in json.loads(view["node_signals"])
    assert source_signals[1] in json.loads(view["node_signals"])
    empty = sqlite3.connect(":memory:")
    try:
        for conn in (empty, None):
            missing = enrich_daily_market_row(dict(row), conn=conn)["moving_averages"]["shanghai"]
            assert "ma60" not in missing and missing["archived_unverified"]["values"]["ma60"] == 1
            assert missing["daily_ma_evidence"]["windows"]["ma60"]["observed"] == 0
    finally:
        empty.close()


def test_historical_original_timestamp_missing_excludes_late_target_price(tmp_path, facts):
    db, _ = facts
    db.execute("DELETE FROM daily_market WHERE date=?", (DAY,))
    db.execute("INSERT INTO market_timing_signal VALUES (?, ?, 3600, 'tushare:index_daily', ?)",
               (CODE, DAY, DAY + " 13:40:00"))
    db.commit()
    ma = collect(DAY, quote(), tmp_path, None, now=LATER)
    assert "ma5" not in ma and ma["daily_ma_evidence"]["as_of"] == DAY + "T15:00:00+08:00"
