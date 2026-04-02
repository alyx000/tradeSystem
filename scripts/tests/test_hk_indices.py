"""
港股指数（get_hk_indices）单元测试
"""
from unittest.mock import MagicMock

import pandas as pd
import pytest

from providers.akshare_provider import AkshareProvider


def _make_provider():
    p = AkshareProvider()
    p._initialized = True
    p.ak = MagicMock()
    return p


def _make_hk_df(date: str = "2026-04-01",
                close: float = 20000.0,
                change_pct: float = -1.2,
                open_: float = 20200.0,
                high: float = 20300.0,
                low: float = 19900.0) -> pd.DataFrame:
    return pd.DataFrame({
        "日期": [date],
        "收盘": [close],
        "涨跌幅": [change_pct],
        "开盘": [open_],
        "最高": [high],
        "最低": [low],
    })


class TestGetHkIndices:
    def test_returns_dict_with_hsi_and_hstech(self):
        p = _make_provider()
        p.ak.stock_hk_index_daily_em = MagicMock(return_value=_make_hk_df())
        r = p.get_hk_indices("2026-04-01")
        assert r.success
        assert isinstance(r.data, dict)
        assert "hsi" in r.data
        assert "hstech" in r.data

    def test_entry_has_required_fields(self):
        p = _make_provider()
        p.ak.stock_hk_index_daily_em = MagicMock(return_value=_make_hk_df())
        r = p.get_hk_indices("2026-04-01")
        assert r.success
        for key in ("hsi", "hstech"):
            entry = r.data[key]
            assert "code" in entry
            assert "name" in entry
            assert "close" in entry
            assert "change_pct" in entry
            assert "open" in entry
            assert "high" in entry
            assert "low" in entry

    def test_close_value_correct(self):
        p = _make_provider()
        p.ak.stock_hk_index_daily_em = MagicMock(return_value=_make_hk_df(close=21500.5))
        r = p.get_hk_indices("2026-04-01")
        assert r.success
        assert r.data["hsi"]["close"] == pytest.approx(21500.5, abs=0.01)

    def test_change_pct_correct(self):
        p = _make_provider()
        p.ak.stock_hk_index_daily_em = MagicMock(return_value=_make_hk_df(change_pct=-1.23))
        r = p.get_hk_indices("2026-04-01")
        assert r.success
        assert r.data["hsi"]["change_pct"] == pytest.approx(-1.23, abs=0.01)

    def test_date_mismatch_uses_latest(self):
        """请求日期不存在时，应取 DataFrame 最新一行"""
        df = pd.DataFrame({
            "日期": ["2026-03-28", "2026-03-31"],
            "收盘": [20100.0, 20200.0],
            "涨跌幅": [-0.5, 0.8],
            "开盘": [20150.0, 20050.0],
            "最高": [20200.0, 20250.0],
            "最低": [20000.0, 19950.0],
        })
        p = _make_provider()
        p.ak.stock_hk_index_daily_em = MagicMock(return_value=df)
        r = p.get_hk_indices("2026-04-01")
        assert r.success
        assert r.data["hsi"]["close"] == pytest.approx(20200.0, abs=0.01)

    def test_empty_dataframe_returns_error(self):
        p = _make_provider()
        p.ak.stock_hk_index_daily_em = MagicMock(return_value=pd.DataFrame())
        r = p.get_hk_indices("2026-04-01")
        assert not r.success

    def test_exception_returns_error(self):
        p = _make_provider()
        p.ak.stock_hk_index_daily_em = MagicMock(side_effect=Exception("网络超时"))
        r = p.get_hk_indices("2026-04-01")
        assert not r.success

    def test_partial_failure_returns_available_index(self):
        """一个指数失败，另一个成功时，只返回成功的"""
        call_count = {"n": 0}

        def _side_effect(symbol, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return _make_hk_df()
            raise Exception("HSTECH 失败")

        p = _make_provider()
        p.ak.stock_hk_index_daily_em = MagicMock(side_effect=_side_effect)
        r = p.get_hk_indices("2026-04-01")
        assert r.success
        assert len(r.data) == 1

    def test_missing_ohlc_columns_are_none_not_zero(self):
        """列缺失时 open/high/low 应为 None，不得误写 0.0"""
        df = pd.DataFrame({
            "日期": ["2026-04-01"],
            "收盘": [20000.0],
        })
        p = _make_provider()
        p.ak.stock_hk_index_daily_em = MagicMock(return_value=df)
        r = p.get_hk_indices("2026-04-01")
        assert r.success
        entry = r.data["hsi"]
        assert entry["close"] == pytest.approx(20000.0, abs=0.01)
        assert entry["open"] is None
        assert entry["high"] is None
        assert entry["low"] is None

    def test_zero_close_preserved_when_column_present(self):
        """数据源给出真实 0 收盘价时保留 0.0，与「缺失」区分"""
        df = pd.DataFrame({
            "日期": ["2026-04-01"],
            "收盘": [0.0],
            "涨跌幅": [0.0],
        })
        p = _make_provider()
        p.ak.stock_hk_index_daily_em = MagicMock(return_value=df)
        r = p.get_hk_indices("2026-04-01")
        assert r.success
        assert r.data["hsi"]["close"] == pytest.approx(0.0, abs=0.01)

    def test_missing_change_pct_is_none(self):
        """无涨跌幅类列时 change_pct 为 None，不得默认 0.0"""
        df = pd.DataFrame({
            "日期": ["2026-04-01"],
            "收盘": [20000.0],
            "开盘": [19900.0],
            "最高": [20100.0],
            "最低": [19800.0],
        })
        p = _make_provider()
        p.ak.stock_hk_index_daily_em = MagicMock(return_value=df)
        r = p.get_hk_indices("2026-04-01")
        assert r.success
        assert r.data["hsi"]["change_pct"] is None

    def test_no_close_column_skips_index(self):
        """无任何收盘价列时该指数不入结果并记错"""
        df = pd.DataFrame({
            "日期": ["2026-04-01"],
            "开盘": [20000.0],
        })
        p = _make_provider()
        p.ak.stock_hk_index_daily_em = MagicMock(return_value=df)
        r = p.get_hk_indices("2026-04-01")
        assert not r.success
        assert r.data is None
