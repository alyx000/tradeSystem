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
    p._hk_index_from_yfinance = MagicMock(return_value=None)
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

    def test_date_mismatch_keeps_target_day_missing(self):
        """当前日线缺失，不以较早的报价补当天收盘。"""
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
        assert not r.success

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

    def test_falls_back_to_yfinance_when_akshare_unavailable(self, monkeypatch):
        p = _make_provider()
        p.ak.stock_hk_index_daily_em = MagicMock(side_effect=Exception("eastmoney down"))
        fallback_rows = {
            "HSI": {
                "code": "HSI",
                "name": "恒生指数",
                "close": 26163.24,
                "change_pct": -1.22,
                "open": 26303.60,
                "high": 26303.60,
                "low": 26073.45,
                "_source": "yfinance:^HSI",
            },
            "HSTECH": {
                "code": "HSTECH",
                "name": "恒生科技指数",
                "close": 3.56,
                "change_pct": -1.98,
                "open": 5009.76,
                "high": 5009.76,
                "low": 4940.09,
                "_source": "yfinance:3033.HK",
            },
        }
        monkeypatch.setattr(
            p,
            "_hk_index_from_yfinance",
            lambda symbol, date: fallback_rows.get(symbol),
        )

        r = p.get_hk_indices("2026-04-22")

        assert r.success
        assert r.data["hsi"]["close"] == pytest.approx(26163.24, abs=0.01)
        assert r.data["hstech"]["close"] is None
        assert r.data["hstech"]["status"] == "missing"
        assert r.data["hstech_etf_proxy"]["close"] == 3.56
        assert r.data["hstech_etf_proxy"]["code"] == "3033.HK"
        assert r.data["hstech_etf_proxy"]["trade_date"] is None


def test_installed_akshare_daily_shape_and_target_only():
    p = _make_provider()
    p.ak.stock_hk_index_daily_em.return_value = pd.DataFrame({
        "date": ["2026-11-27", "2026-11-26", "2026-11-30"],
        "latest": [5200, 5000, 4800], "涨跌额": [200, 0, -400],
    })
    result = p.get_hk_indices("2026-11-27")
    assert [call.kwargs["symbol"] for call in p.ak.stock_hk_index_daily_em.call_args_list] == ["HSI", "HSTECH"]
    quote = result.data["hstech"]
    assert (quote["close"], quote["change_pct"], quote["trade_date"], quote["unit"]) == (5200, 4.0, "2026-11-27", "points")
    assert "hstech_etf_proxy" not in result.data
    assert not p.get_hk_indices("2026-11-25").success


def test_date_keyed_ticker_fallback_prefers_spot_over_more_complete_proxy(monkeypatch):
    p = AkshareProvider()
    p.ak = MagicMock()
    p.ak.stock_hk_index_daily_em.side_effect = ValueError("offline")
    histories = {
        "^HSI": pd.DataFrame(),
        "HSTECH.HK": pd.DataFrame({"Close": [5100]}, index=pd.to_datetime(["2026-11-27"])),
        "3033.HK": pd.DataFrame({"Close": [3.35, 3.56]}, index=pd.to_datetime(["2026-11-26", "2026-11-27"])),
    }
    seen = []

    def ticker(symbol):
        seen.append(symbol)
        mock = MagicMock()
        mock.history.return_value = histories[symbol]
        return mock

    monkeypatch.setattr("yfinance.Ticker", ticker)
    got = p.get_hk_indices("2026-11-27").data
    assert got["hstech"]["close"] == 5100
    assert got["hstech"]["change_pct"] is None
    assert "3033.HK" not in seen

    histories["HSTECH.HK"].index = pd.to_datetime(["2026-11-30"])
    got = p.get_hk_indices("2026-11-27").data
    assert got["hstech"]["close"] is None
    proxy = got["hstech_etf_proxy"]
    assert (proxy["code"], proxy["unit"], proxy["close"], proxy["trade_date"]) == ("3033.HK", "HKD", 3.56, "2026-11-27")
    assert proxy["change_pct"] == 6.27


def test_legacy_proxy_identity_in_public_summary_and_daily_report(tmp_path):
    import json
    from api.market_enrich import enrich_daily_market_row
    from generators.report import ReportGenerator

    section = {"hstech": {"code": "HSTECH", "_source": "yfinance:3033.HK", "name": "恒生科技指数", "close": 3.56}}
    row = {"date": "2026-11-27", "hk_indices": json.dumps(section)}
    view = json.loads(enrich_daily_market_row(row)["hk_indices"])
    assert view["hstech"]["close"] is None
    assert view["hstech_etf_proxy"]["trade_date"] is None
    generator = ReportGenerator()
    generator.daily_dir = tmp_path
    text, _ = generator.generate_post_market("2026-11-27", {"hk_indices": section})
    assert "恒生科技指数：当日行情缺失" in text
    assert "（3033.HK）：3.56 港元，涨跌幅未核验，行情日未核验" in text


def test_conflicting_hk_symbol_or_lookalike_spot_cannot_supply_index(monkeypatch):
    from utils.hk_indices import hk_indices_view
    wrong = hk_indices_view({"hstech": {"code": "HSI", "close": 26000, "_source": "yfinance:^HSI"}})
    assert wrong["hstech"]["close"] is None
    p = _make_provider()
    p._index_from_yfinance = MagicMock(return_value=None)
    p.ak.index_global_spot_em.return_value = pd.DataFrame({
        "名称": ["恒生科技指数两倍杠杆"], "代码": ["HSTEC2L"], "最新价": [42],
        "最新行情时间": ["2026-11-27 16:00:00"]})
    assert not p.get_global_index("hstech").success
