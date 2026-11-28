"""TushareProvider.get_stock_daily_range：mock pro.query，断言返回完整 OHLCV。

Stage 0（趋势主升 scanner）：原方法只回 {trade_date, pct_chg}，缺 close/open/vol，
导致贴 MA5 / 缩量阴线 / 远离 MA5 检测器无米下锅。本测试钉死「区间日线必须含 OHLCV」契约。
底层 Tushare daily 接口本就返回全列，扩字段向后兼容（消费方 regulatory.py 仅读 trade_date+pct_chg）。
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import MagicMock

import pandas as pd
import pytest
from requests import HTTPError, Response

from providers.base import DataResult
from providers.tushare_provider import TushareProvider


@pytest.fixture
def provider():
    """绕过 initialize，仅注入 mock pro（同 test_tushare_margin 模式）。"""
    ts = TushareProvider({})
    ts.name = "tushare"
    ts.config = {}
    ts.pro = MagicMock()
    return ts


def _daily_df() -> pd.DataFrame:
    """Tushare daily 接口返回列：ts_code/trade_date/open/high/low/close/pre_close/change/pct_chg/vol/amount。"""
    return pd.DataFrame(
        [
            {"ts_code": "600552.SH", "trade_date": "20260609", "open": 12.00, "high": 12.50,
             "low": 11.90, "close": 12.35, "pre_close": 11.97, "change": 0.38,
             "pct_chg": 3.17, "vol": 380000.0, "amount": 470000.0},
            {"ts_code": "600552.SH", "trade_date": "20260608", "open": 11.80, "high": 12.00,
             "low": 11.70, "close": 11.97, "pre_close": 11.80, "change": 0.17,
             "pct_chg": 1.44, "vol": 300000.0, "amount": 360000.0},
        ]
    )


def test_get_stock_daily_range_includes_ohlcv(provider):
    """区间日线每根 bar 必须含 open/high/low/close/vol/amount（不只 pct_chg）。"""
    provider.pro.query.return_value = _daily_df()

    r = TushareProvider.get_stock_daily_range(provider, "600552", "2026-06-08", "2026-06-09")

    assert r.success
    assert isinstance(r.data, list) and len(r.data) == 2
    row = next(b for b in r.data if b["trade_date"] == "2026-06-09")
    for key in ("open", "high", "low", "close", "vol", "amount"):
        assert key in row, f"区间日线缺字段 {key}"
    assert row["close"] == pytest.approx(12.35)
    assert row["open"] == pytest.approx(12.00)
    assert row["vol"] == pytest.approx(380000.0)
    assert row["amount"] == pytest.approx(470000.0)


def test_get_stock_daily_range_keeps_legacy_fields(provider):
    """向后兼容：trade_date / pct_chg 仍在（regulatory.py 依赖）。"""
    provider.pro.query.return_value = _daily_df()

    r = TushareProvider.get_stock_daily_range(provider, "600552", "2026-06-08", "2026-06-09")

    assert r.success
    row = r.data[0]
    assert "trade_date" in row and "pct_chg" in row
    assert row["trade_date"].count("-") == 2  # 归一为 YYYY-MM-DD


def test_get_stock_daily_range_sorted_ascending(provider):
    """即使 Tushare 倒序返回，输出也必须按 trade_date 升序（detector 契约：最后一根=今日）。"""
    provider.pro.query.return_value = _daily_df()  # fixture 为倒序 0609→0608
    r = TushareProvider.get_stock_daily_range(provider, "600552", "2026-06-08", "2026-06-09")
    dates = [b["trade_date"] for b in r.data]
    assert dates == sorted(dates)          # 升序
    assert dates[-1] == "2026-06-09"       # 最后一根=最新（今日）


@pytest.mark.parametrize("method,api,value", [
    ("get_stock_daily_range", "daily", "close"),
    ("get_stock_adj_factor_range", "adj_factor", "adj_factor"),
])
@pytest.mark.parametrize("damage", [None, "conflict", "wrong_stock", "future", "before_start"])
def test_single_stock_ranges_only_fold_identical_rows(provider, method, api, value, damage, monkeypatch):
    provider._initialized = True
    row = dict(ts_code="600552.SH", trade_date="20260609", **{value: 12.5})
    duplicate = row.copy()
    if damage == "conflict":
        duplicate[value] = 13.5
    elif damage == "wrong_stock":
        duplicate["ts_code"] = "600552.SZ"
    elif damage in {"future", "before_start"}:
        duplicate["trade_date"] = "20260610" if damage == "future" else "20260607"
    earlier = {**row, "trade_date": "20260608"}
    provider.pro.query.return_value = pd.DataFrame([row, duplicate, earlier])
    monkeypatch.setattr("providers.adj_factor_evidence.archived_factors", lambda *_: {})
    monkeypatch.setattr(provider, "get_adj_factor", lambda _: DataResult(None, "tushare", error="缺页"))

    result = getattr(provider, method)("600552", "2026-06-08", "2026-06-09")
    provider.pro.query.assert_called_once_with(
        api, ts_code="600552.SH", start_date="20260608", end_date="20260609")
    if damage:
        assert not result.success and result.data is None
    else:
        assert result.success
        assert [r["trade_date"] for r in result.data] == ["2026-06-08", "2026-06-09"]


@pytest.mark.parametrize("status,count", [(502, 2), (403, 1)])
def test_single_stock_factor_retries_gateway_errors_once(provider, status, count):
    provider._initialized = True
    response = Response()
    response.status_code = status
    error = HTTPError("request failed", response=response)
    result_data = pd.DataFrame([dict(ts_code="600552.SH", trade_date="20260609", adj_factor=12.5)])
    provider.pro.query.side_effect = [error, result_data]
    result = provider.get_stock_adj_factor_range("600552", "2026-06-08", "2026-06-09")
    assert provider.pro.query.call_count == count
    assert result.success == (status == 502)


@pytest.mark.parametrize("verified,expected", [(2.5, True), (4.0, False), (None, False)])
def test_factor_conflict_requires_matching_independent_batch(provider, monkeypatch, verified, expected):
    provider._initialized = True
    monkeypatch.setattr("providers.adj_factor_evidence.archived_factors", lambda *_: {})
    row = dict(ts_code="600552.SH", trade_date="20260609", adj_factor=2.5)
    provider.pro.query.return_value = pd.DataFrame([row, {**row, "adj_factor": 3.0}])
    batch = MagicMock(return_value=DataResult(
        [{**row, "adj_factor": verified}] if verified is not None else [], "tushare:adj_factor"))
    monkeypatch.setattr(provider, "get_adj_factor", batch)
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    for _ in range(2):
        result = provider.get_stock_adj_factor_range("600552", "2026-06-08", today)
        assert result.success is expected
        if expected:
            assert result.data == [dict(trade_date="2026-06-09", adj_factor=2.5)]
            assert "trade_date" in result.note and "2.5" in result.note and "3.0" in result.note
        else:
            assert "同日记录冲突" in result.error
    batch.assert_called_once_with("2026-06-09")


@pytest.mark.parametrize("code,expected", [("000001.SZ", True), ("000001.SH", False)])
def test_factor_conflict_uses_certified_snapshot_only_for_matching_identity(provider, monkeypatch, code, expected):
    provider._initialized = True
    monkeypatch.setattr("providers.adj_factor_evidence.archived_factors", lambda *_: {"000001": 2.5})
    row = dict(ts_code=code, trade_date="20260630", adj_factor=2.5)
    provider.pro.query.return_value = pd.DataFrame([row, {**row, "adj_factor": 3.0}])
    monkeypatch.setattr(provider, "get_adj_factor", lambda *_: pytest.fail("已有归档或身份冲突时不再请求"))
    result = provider.get_stock_adj_factor_range(code, "2026-06-01", "2026-07-10")
    assert result.success is expected
    if expected:
        assert "certified" in result.note
        assert result.data == [dict(trade_date="2026-06-30", adj_factor=2.5)]


def test_certified_factors_check_same_snapshot_source_and_shanghai_visibility(tmp_path):
    import sqlite3
    from db.schema import init_schema
    from providers.adj_factor_evidence import archived_factors
    from services.monthly_pattern import repository

    path = tmp_path / "market.db"
    day = "2026-09-30"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        init_schema(conn)
        repository.save_month_bars(conn, [dict(month_end=day, stock_code="000001",
            open=1, high=1, low=1, close=1, volume=1, amount=1, adj_factor=2.5,
            source="tushare:monthly+adj_factor")])
        manifest = dict(month_end=day, status="certified", universe_source="tushare:stock_basic",
                        universe_count=1, quote_count=1, factor_count=1, joined_count=1,
                        quote_coverage=1.0, factor_coverage=1.0, source_meta=dict(
                            factor_source="tushare:adj_factor", code_alias_evidence_schema_version=2,
                            raw_joined_code_count=1, normalized_joined_code_count=1,
                            factor_coverage_denominator=1, code_alias_normalizations=[]))
        repository.save_month_bar_manifest(conn, manifest)
        for table in ("monthly_pattern_bars", "monthly_pattern_bar_manifests"):
            conn.execute(f"UPDATE {table} SET fetched_at='2026-10-01 17:00:00'")
        conn.commit()
        assert archived_factors(day, "2026-10-01", path) == {}
        assert archived_factors(day, "2026-10-02", path) == {"000001": 2.5}
        conn.execute("UPDATE monthly_pattern_bars SET fetched_at='2026-10-01 17:00:01'")
        conn.commit()
        assert archived_factors(day, "2026-10-02", path) == {}
        conn.execute("UPDATE monthly_pattern_bars SET fetched_at='2026-10-01 17:00:00', source='other'")
        conn.commit()
        assert archived_factors(day, "2026-10-02", path) == {}
    finally:
        conn.close()
