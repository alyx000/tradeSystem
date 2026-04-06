"""TushareProvider P0 接口单测。"""
from __future__ import annotations

import pandas as pd

from providers.tushare_provider import TushareProvider


class _StubPro:
    def __init__(self):
        self.query_calls: list[tuple[str, dict]] = []
        self.ths_index_calls: list[dict] = []
        self.ths_member_calls: list[dict] = []

    def query(self, api_name: str, **params):
        self.query_calls.append((api_name, params))
        if api_name == "daily_basic":
            return pd.DataFrame([
                {"ts_code": "300750.SZ", "trade_date": "20260403", "turnover_rate": 3.2},
            ])
        if api_name == "margin_detail":
            return pd.DataFrame([
                {"ts_code": "300750.SZ", "trade_date": "20260403", "rzmre": 123456789.0},
            ])
        if api_name == "disclosure_date":
            return pd.DataFrame([
                {"ts_code": "300750.SZ", "pre_date": "20260428", "ann_date": "20260403", "end_date": params["end_date"]},
            ])
        if api_name == "stock_st":
            return pd.DataFrame([
                {"ts_code": "600234.SH", "name": "*ST科新", "trade_date": "20260403"},
            ])
        return pd.DataFrame()

    def stock_basic(self, **_kwargs):
        return pd.DataFrame([
            {"ts_code": "300750.SZ", "name": "宁德时代", "market": "创业板"},
        ])

    def ths_index(self, **kwargs):
        self.ths_index_calls.append(kwargs)
        return pd.DataFrame([
            {"ts_code": "885001.TI", "name": "AI算力", "type": "N"},
            {"ts_code": "885002.TI", "name": "机器人", "type": "N"},
        ])

    def ths_member(self, **kwargs):
        self.ths_member_calls.append(kwargs)
        ts_code = kwargs["ts_code"]
        return pd.DataFrame([
            {"ts_code": ts_code, "con_code": "300750.SZ", "con_name": "宁德时代"},
        ])


def _provider() -> TushareProvider:
    provider = TushareProvider.__new__(TushareProvider)
    provider.name = "tushare"
    provider.priority = 1
    provider.config = {}
    provider.pro = _StubPro()
    provider._initialized = True
    provider._sw_l2_codes = None
    provider._ths_concept_map = None
    return provider


def test_get_daily_basic_returns_records():
    provider = _provider()

    result = provider.get_daily_basic("2026-04-03")

    assert result.success
    assert result.source == "tushare:daily_basic"
    assert result.data[0]["ts_code"] == "300750.SZ"
    assert result.data[0]["turnover_rate"] == 3.2


def test_get_margin_detail_adds_code_alias():
    provider = _provider()

    result = provider.get_margin_detail("2026-04-03")

    assert result.success
    assert result.data[0]["ts_code"] == "300750.SZ"
    assert result.data[0]["code"] == "300750.SZ"


def test_get_disclosure_dates_uses_recent_quarter_end():
    provider = _provider()

    result = provider.get_disclosure_dates("2026-04-05")

    assert result.success
    assert result.data[0]["report_end"] == "20260331"
    api_name, params = provider.pro.query_calls[-1]
    assert api_name == "disclosure_date"
    assert params["end_date"] == "20260331"


def test_get_stock_basic_list_returns_rows():
    provider = _provider()

    result = provider.get_stock_basic_list("2026-04-03")

    assert result.success
    assert result.source == "tushare:stock_basic"
    assert result.data[0]["name"] == "宁德时代"


def test_get_stock_st_returns_rows():
    provider = _provider()

    result = provider.get_stock_st("2026-04-03")

    assert result.success
    assert result.data[0]["code"] == "600234.SH"


def test_get_ths_member_uses_concept_index_scope():
    provider = _provider()

    result = provider.get_ths_member("2026-04-03")

    assert result.success
    assert provider.pro.ths_index_calls == [{"type": "N"}]
    assert provider.pro.ths_member_calls == [
        {"ts_code": "885001.TI"},
        {"ts_code": "885002.TI"},
    ]
    assert result.data[0]["index_type"] == "N"
