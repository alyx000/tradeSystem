"""market-timing Stage 0：指数日线取数扩展单测（全程 mock，不触网络）。

覆盖：
  - tushare get_index_daily_range 返回完整 OHLCV+vol+amount（底分型/MA5/缩量/地量依赖），
    且保留 pct_chg/ts_code 向后兼容 regulatory 消费方。
  - tdx get_index_daily_range("avg_price") 走【日线】(KLINE category=9)，归一化 YYYY-MM-DD。
  - tdx 非 avg_price 显式拒绝。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd

from providers.tushare_provider import TushareProvider
from providers.tdx_provider import TdxProvider


class _StubIndexPro:
    """index_daily 返回 tushare 原生列（含 OHLCV+vol+amount+pct_chg）。"""

    def index_daily(self, ts_code, start_date, end_date):
        return pd.DataFrame([
            {"ts_code": ts_code, "trade_date": "20260612", "open": 3394.08, "high": 3419.96,
             "low": 3346.70, "close": 3363.51, "vol": 337202617.0, "amount": 610139652.6, "pct_chg": 0.4971},
            {"ts_code": ts_code, "trade_date": "20260611", "open": 3380.0, "high": 3400.0,
             "low": 3370.0, "close": 3346.8, "vol": 320000000.0, "amount": 600000000.0, "pct_chg": -0.5},
        ])


class TestTushareIndexDailyRangeOHLCV:
    def test_returns_full_ohlcv(self):
        prov = TushareProvider({})
        prov.pro = _StubIndexPro()
        r = prov.get_index_daily_range("932000.CSI", "2026-05-01", "2026-06-13")
        assert r.success
        row = r.data[-1]                                   # 最新一行在末尾（升序契约）
        assert row["trade_date"] == "2026-06-12"          # 归一化 YYYY-MM-DD
        assert row["open"] == 3394.08
        assert row["high"] == 3419.96
        assert row["low"] == 3346.70
        assert row["close"] == 3363.51
        assert row["vol"] == 337202617.0
        assert row["amount"] == 610139652.6

    def test_normalizes_to_ascending_order(self):
        """tushare index_daily 倒序返回 → 输出必须升序（最旧在前），契合检测器 bars[-1]=今日。"""
        prov = TushareProvider({})
        prov.pro = _StubIndexPro()  # stub 故意倒序（0612 在前、0611 在后）
        r = prov.get_index_daily_range("932000.CSI", "2026-05-01", "2026-06-13")
        dates = [row["trade_date"] for row in r.data]
        assert dates == sorted(dates)
        assert dates[0] == "2026-06-11" and dates[-1] == "2026-06-12"

    def test_keeps_pct_chg_and_ts_code_backcompat(self):
        prov = TushareProvider({})
        prov.pro = _StubIndexPro()
        r = prov.get_index_daily_range("932000.CSI", "2026-05-01", "2026-06-13")
        row = r.data[-1]                                   # 末尾=最新(0612)
        assert row["pct_chg"] == 0.4971
        assert row["ts_code"] == "932000.CSI"

    def test_empty_df_returns_empty_list(self):
        prov = TushareProvider({})
        prov.pro = MagicMock()
        prov.pro.index_daily.return_value = pd.DataFrame()
        r = prov.get_index_daily_range("000001.SH", "2026-05-01", "2026-06-13")
        assert r.success
        assert r.data == []


def _daily_bars(rows):
    """[(datetime, o,h,l,c,v), ...] → pytdx get_index_bars 返回格式。"""
    return [
        {"datetime": dt, "open": o, "high": h, "low": low, "close": c, "vol": v, "amount": v * c}
        for dt, o, h, low, c, v in rows
    ]


DAILY = _daily_bars([
    ("2026-06-10 15:00", 30.44, 30.68, 29.88, 30.24, 13179204.0),
    ("2026-06-11 15:00", 29.97, 30.26, 29.70, 30.02, 12470207.0),
    ("2026-06-12 15:00", 30.53, 30.67, 29.95, 30.04, 15434249.0),
])


class TestTdxIndexDailyRange:
    def test_avg_price_daily_uses_daily_category_and_normalizes(self):
        api = MagicMock()
        api.connect.return_value = True
        api.get_index_bars.return_value = DAILY
        with patch("pytdx.hq.TdxHq_API", return_value=api):
            prov = TdxProvider({"servers": [("1.1.1.1", 7709)]})
            r = prov.get_index_daily_range("avg_price", "2026-05-01", "2026-06-13")
        assert r.success
        # 日线 K 线 category=9（区别于周线 5），市场=1 沪市，code=880003
        cat, market, code = api.get_index_bars.call_args[0][:3]
        assert cat == 9
        assert market == 1
        assert code == "880003"
        assert r.data[-1]["trade_date"] == "2026-06-12"   # 归一化 YYYY-MM-DD（与 tushare 对齐）
        assert r.data[-1]["close"] == 30.04
        assert r.data[-1]["open"] == 30.53
        assert "vol" in r.data[-1]
        api.disconnect.assert_called_once()

    def test_avg_price_daily_in_capabilities(self):
        prov = TdxProvider({"servers": [("1.1.1.1", 7709)]})
        assert "get_index_daily_range" in prov.get_capabilities()

    def test_non_avg_price_rejected(self):
        with patch("pytdx.hq.TdxHq_API") as cls:
            prov = TdxProvider({"servers": [("1.1.1.1", 7709)]})
            r = prov.get_index_daily_range("shanghai", "2026-05-01", "2026-06-13")
        assert not r.success
        assert "avg_price" in r.error
        cls.assert_not_called()
