"""AkshareProvider 免费后端降级路径单测（mock self.ak / yfinance，无网络）。

覆盖本轮落地的三类零风险替代：
- 外盘：get_global_index 美股三大指数走 yfinance（东财兜底）
- 披露：get_disclosure_dates 走巨潮 stock_report_disclosure
- 行情：get_index_daily / get_index_weekly / get_market_volume / get_stock_daily / get_stock_ma 走 sina（东财兜底）
"""
from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from providers.akshare_provider import AkshareProvider


@pytest.fixture
def ak() -> AkshareProvider:
    p = AkshareProvider({})
    p._initialized = True
    p.ak = MagicMock()
    p._df_global_spot_em = None
    p._df_futures_global_spot_em = None
    return p


def _yf_hist(closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2026-05-27", periods=len(closes), freq="D")
    return pd.DataFrame({"Close": closes}, index=idx)


# ---------------- ① 外盘：美股三大指数 yfinance ----------------

@patch("yfinance.Ticker")
def test_global_index_dow_jones_uses_yfinance(mock_ticker, ak: AkshareProvider):
    mock_ticker.return_value.history.return_value = _yf_hist([50000.0, 50668.97])
    r = ak.get_global_index("dow_jones")
    assert r.success
    assert r.data["close"] == 50668.97
    assert r.data["name"] == "道琼斯"
    assert "yfinance" in r.source
    assert "^DJI" in str(mock_ticker.call_args)


@patch("yfinance.Ticker")
def test_global_index_nasdaq_uses_yfinance(mock_ticker, ak: AkshareProvider):
    mock_ticker.return_value.history.return_value = _yf_hist([26000.0, 26917.47])
    r = ak.get_global_index("nasdaq")
    assert r.success
    assert r.data["close"] == 26917.47
    assert "^IXIC" in str(mock_ticker.call_args)


@patch("yfinance.Ticker")
def test_global_index_sp500_uses_yfinance(mock_ticker, ak: AkshareProvider):
    mock_ticker.return_value.history.return_value = _yf_hist([7500.0, 7563.63])
    r = ak.get_global_index("sp500")
    assert r.success
    assert r.data["close"] == 7563.63
    assert "^GSPC" in str(mock_ticker.call_args)


@patch("yfinance.Ticker")
def test_global_index_us_falls_back_to_em_when_yfinance_empty(mock_ticker, ak: AkshareProvider):
    """yfinance 无数据时，美股指数退回东财 index_global_spot_em。"""
    mock_ticker.return_value.history.return_value = pd.DataFrame()
    ak.ak.index_global_spot_em.return_value = pd.DataFrame(
        {"名称": ["道琼斯"], "最新价": [50668.97], "涨跌幅": [1.23]}
    )
    r = ak.get_global_index("dow_jones")
    assert r.success
    assert r.data["close"] == 50668.97
    assert "index_global_spot_em" in r.source


# ---------------- ② 披露：巨潮 stock_report_disclosure ----------------

def _disclosure_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "股票代码": ["000001", "600519"],
            "股票简称": ["平安银行", "贵州茅台"],
            "首次预约": [date(2026, 4, 25), date(2026, 4, 25)],
            "初次变更": [pd.NaT, pd.NaT],
            "二次变更": [pd.NaT, pd.NaT],
            "三次变更": [pd.NaT, pd.NaT],
            "实际披露": [date(2026, 4, 25), date(2026, 4, 24)],
        }
    )


def test_disclosure_dates_q1_period_and_field_mapping(ak: AkshareProvider):
    """5 月所在季度披露的是一季报：period=2026一季，report_end=20260331。"""
    ak.ak.stock_report_disclosure.return_value = _disclosure_df()
    r = ak.get_disclosure_dates("2026-05-30")
    assert r.success
    assert "2026一季" in str(ak.ak.stock_report_disclosure.call_args)
    rec = {x["code"]: x for x in r.data}
    assert rec["000001.SZ"]["ts_code"] == "000001.SZ"
    assert rec["000001.SZ"]["pre_date"] == "20260425"
    assert rec["000001.SZ"]["report_end"] == "20260331"
    assert rec["600519.SH"]["actual_date"] == "20260424"


def test_disclosure_dates_annual_period_for_q1_months(ak: AkshareProvider):
    """1~3 月披露的是上一年年报：period=2025年报，report_end=20251231。"""
    ak.ak.stock_report_disclosure.return_value = _disclosure_df()
    r = ak.get_disclosure_dates("2026-03-30")
    assert r.success
    assert "2025年报" in str(ak.ak.stock_report_disclosure.call_args)
    assert r.data[0]["report_end"] == "20251231"


def test_disclosure_dates_empty_returns_empty_list(ak: AkshareProvider):
    ak.ak.stock_report_disclosure.return_value = pd.DataFrame()
    r = ak.get_disclosure_dates("2026-05-30")
    assert r.success
    assert r.data == []


# ---------------- ③ 行情：指数日线 sina 优先（东财兜底） ----------------

def _sina_index_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": [pd.Timestamp("2026-05-27"), pd.Timestamp("2026-05-28")],
            "open": [4080.0, 4080.30],
            "high": [4111.0, 4110.78],
            "low": [4050.0, 4055.83],
            "close": [4090.0, 4098.64],
            "volume": [60000000000, 62117995000],  # sina 单位：股
        }
    )


def test_index_daily_uses_sina_first(ak: AkshareProvider):
    ak.ak.stock_zh_index_daily.return_value = _sina_index_df()
    r = ak.get_index_daily("shanghai", "2026-05-28")
    assert r.success
    assert "stock_zh_index_daily" in r.source
    assert r.data["close"] == 4098.64
    assert r.data["open"] == 4080.30
    # pct 自算：(4098.64-4090)/4090*100
    assert r.data["change_pct"] == pytest.approx(0.21, abs=0.02)
    # volume 股→手 /100
    assert r.data["volume"] == pytest.approx(621179950, rel=1e-6)
    # sina 日线无成交额列
    assert r.data["amount_billion"] is None
    assert "sh000001" in str(ak.ak.stock_zh_index_daily.call_args)


def test_index_daily_no_date_match_in_sina_falls_back_to_em(ak: AkshareProvider):
    """sina 无目标日 → 退东财 index_zh_a_hist（不得拿邻近日当当日）。"""
    ak.ak.stock_zh_index_daily.return_value = _sina_index_df()  # 只有 05-27/05-28
    ak.ak.index_zh_a_hist.return_value = pd.DataFrame(
        [{"日期": "2026-05-29", "开盘": 4098.0, "收盘": 4068.57, "最高": 4100.0,
          "最低": 4060.0, "成交量": 6.0e8, "成交额": 1.5e12, "涨跌幅": -0.73}]
    )
    r = ak.get_index_daily("shanghai", "2026-05-29")
    assert r.success
    assert "index_zh_a_hist" in r.source
    assert r.data["close"] == pytest.approx(4068.57)


def test_index_daily_sina_failure_falls_back_to_em(ak: AkshareProvider):
    ak.ak.stock_zh_index_daily.side_effect = Exception("sina down")
    ak.ak.index_zh_a_hist.return_value = pd.DataFrame(
        [{"日期": "2026-05-28", "开盘": 4080.30, "收盘": 4098.64, "最高": 4110.78,
          "最低": 4055.83, "成交量": 6.2e8, "成交额": 1.5e12, "涨跌幅": 0.21}]
    )
    r = ak.get_index_daily("shanghai", "2026-05-28")
    assert r.success
    assert "index_zh_a_hist" in r.source
    assert r.data["amount_billion"] == pytest.approx(15000.0, rel=0.01)


# ---------------- ④ 行情：指数周线 sina resample 优先 ----------------

def _sina_weekly_src() -> pd.DataFrame:
    days = ["2026-05-18", "2026-05-19", "2026-05-20", "2026-05-21", "2026-05-22",
            "2026-05-25", "2026-05-26", "2026-05-27", "2026-05-28", "2026-05-29"]
    closes = [4135.0, 4120.0, 4110.0, 4115.0, 4112.90,
              4100.0, 4145.0, 4130.0, 4098.0, 4068.57]
    return pd.DataFrame({
        "date": [pd.Timestamp(d) for d in days],
        "open": [c - 5 for c in closes],
        "high": [c + 10 for c in closes],
        "low": [c - 10 for c in closes],
        "close": closes,
        "volume": [1e10] * len(days),
    })


def test_index_weekly_uses_sina_resample(ak: AkshareProvider):
    ak.ak.stock_zh_index_daily.return_value = _sina_weekly_src()
    r = ak.get_index_weekly("shanghai", "2026-05-18", "2026-05-29")
    assert r.success
    assert "stock_zh_index_daily" in r.source
    closes = [x["close"] for x in r.data]
    assert len(r.data) == 2
    assert closes[0] == pytest.approx(4112.90)
    assert closes[-1] == pytest.approx(4068.57)
    # trade_date 用当周最后交易日
    assert r.data[-1]["trade_date"] == "20260529"


def test_index_weekly_falls_back_to_em(ak: AkshareProvider):
    ak.ak.stock_zh_index_daily.side_effect = Exception("sina down")
    ak.ak.index_zh_a_hist.return_value = pd.DataFrame([
        {"日期": "2026-05-22", "开盘": 4100.0, "收盘": 4112.90, "最高": 4120.0, "最低": 4090.0},
        {"日期": "2026-05-29", "开盘": 4098.0, "收盘": 4068.57, "最高": 4100.0, "最低": 4060.0},
    ])
    r = ak.get_index_weekly("shanghai", "2026-05-18", "2026-05-29")
    assert r.success
    assert "index_zh_a_hist" in r.source
    assert r.data[-1]["close"] == pytest.approx(4068.57)


# ---------------- ⑤ 行情：两市成交额 sina 实时当日（东财兜底） ----------------

def test_market_volume_today_uses_sina_realtime(ak: AkshareProvider):
    today = datetime.now().strftime("%Y-%m-%d")
    ak.ak.stock_zh_index_spot_sina.return_value = pd.DataFrame({
        "代码": ["sh000001", "sz399001"],
        "名称": ["上证指数", "深证成指"],
        "成交额": [1.532067e12, 1.786965e12],  # 元
    })
    r = ak.get_market_volume(today)
    assert r.success
    assert "stock_zh_index_spot_sina" in r.source
    assert r.data["shanghai_billion"] == pytest.approx(15320.67, abs=0.01)
    assert r.data["total_billion"] == pytest.approx(33190.32, abs=0.02)


def test_market_volume_historical_uses_em_amounts(ak: AkshareProvider):
    """历史日不走实时快照，用东财指数日线成交额加总。"""
    def _se(symbol, **kwargs):
        amt = 1.461685e12 if symbol == "000001" else 1.782007e12
        return pd.DataFrame([{"日期": "2026-05-26", "开盘": 0.0, "收盘": 4145.373,
                              "最高": 0.0, "最低": 0.0, "成交量": 0.0, "成交额": amt, "涨跌幅": 0.0}])
    ak.ak.index_zh_a_hist.side_effect = _se
    r = ak.get_market_volume("2026-05-26")
    assert r.success
    assert "index_zh_a_hist" in r.source
    assert r.data["total_billion"] == pytest.approx(32436.92, abs=0.02)


# ---------------- ⑥ 行情：个股日线 sina stock_zh_a_daily ----------------

def _sina_stock_df() -> pd.DataFrame:
    return pd.DataFrame({
        "date": [pd.Timestamp("2026-04-28"), pd.Timestamp("2026-04-29")],
        "open": [11.40, 11.43],
        "high": [11.50, 11.54],
        "low": [11.35, 11.39],
        "close": [11.45, 11.52],
        "volume": [120000000, 138000000],  # sina 单位：股
        "amount": [1.4e9, 1583294869.0],   # sina 单位：元
        "outstanding_share": [1.9e10, 1.9e10],
        "turnover": [0.0065, 0.00710],     # sina 为小数
    })


def test_stock_daily_uses_sina(ak: AkshareProvider):
    ak.ak.stock_zh_a_daily.return_value = _sina_stock_df()
    r = ak.get_stock_daily("000001.SZ", "2026-04-29")
    assert r.success
    assert "stock_zh_a_daily" in r.source
    assert r.data["close"] == 11.52
    assert r.data["open"] == 11.43
    assert r.data["volume"] == pytest.approx(1380000)          # 股→手 /100
    assert r.data["amount_billion"] == pytest.approx(15.8329, abs=0.001)  # 元→亿 /1e8
    assert r.data["turnover_rate"] == pytest.approx(0.710, abs=0.001)     # 小数→% ×100
    assert r.data["change_pct"] == pytest.approx(0.61, abs=0.02)          # (11.52-11.45)/11.45
    assert r.data["amplitude_pct"] == pytest.approx(1.31, abs=0.02)       # (11.54-11.39)/11.45
    assert "sz000001" in str(ak.ak.stock_zh_a_daily.call_args)


def test_stock_daily_no_date_match_returns_error(ak: AkshareProvider):
    ak.ak.stock_zh_a_daily.return_value = _sina_stock_df()
    r = ak.get_stock_daily("000001.SZ", "2026-05-29")
    assert not r.success


def test_stock_daily_sh_symbol_mapping(ak: AkshareProvider):
    ak.ak.stock_zh_a_daily.return_value = _sina_stock_df()
    ak.get_stock_daily("600519.SH", "2026-04-29")
    assert "sh600519" in str(ak.ak.stock_zh_a_daily.call_args)


# ---------------- ⑦ 行情：个股均线 sina 自算 ----------------

def _sina_ma_df():
    days = pd.bdate_range(end="2026-05-28", periods=25)
    closes = [float(i) for i in range(1, 26)]
    vols = [i * 1_000_000 for i in range(1, 26)]  # 股
    df = pd.DataFrame({
        "date": list(days),
        "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": vols,
        "amount": [c * v for c, v in zip(closes, vols)],
        "turnover": [0.01] * 25,
    })
    return df, days[-1].strftime("%Y-%m-%d")


def test_stock_ma_uses_sina(ak: AkshareProvider):
    df, target = _sina_ma_df()
    ak.ak.stock_zh_a_daily.return_value = df
    r = ak.get_stock_ma("000001.SZ", target)
    assert r.success
    assert "stock_zh_a_daily" in r.source
    assert r.data["ma5"] == pytest.approx(23.0)    # mean(21..25)
    assert r.data["ma10"] == pytest.approx(20.5)   # mean(16..25)
    assert r.data["ma20"] == pytest.approx(15.5)   # mean(6..25)
    assert r.data["volume_ma5"] == pytest.approx(230000)  # mean(21..25)e6 股 /100


def test_stock_ma_respects_date_cutoff(ak: AkshareProvider):
    """MA 只用截止目标日（含）的历史，不得用未来 bar。"""
    df, _ = _sina_ma_df()
    ak.ak.stock_zh_a_daily.return_value = df
    target = pd.Timestamp(df["date"].iloc[14]).strftime("%Y-%m-%d")  # 第 15 个交易日 close=15
    r = ak.get_stock_ma("000001.SZ", target)
    assert r.success
    assert r.data["ma5"] == pytest.approx(13.0)   # mean(11..15)


# ---------------- ⑧ 能力声明（registry 降级依赖） ----------------

def test_new_capabilities_declared(ak: AkshareProvider):
    caps = ak.get_capabilities()
    for c in ("get_disclosure_dates", "get_stock_daily", "get_stock_ma"):
        assert c in caps, f"缺能力声明 {c}，registry 不会降级到 akshare"
    # 防回归：原有能力仍在
    for c in ("get_index_daily", "get_market_volume", "get_global_index"):
        assert c in caps


# ---------------- 审查补强：边界与降级分支 ----------------

def test_market_volume_realtime_keyed_on_cn_today(ak: AkshareProvider, monkeypatch):
    """当日判定按上海时区（_today_cn），而非进程本地时区。"""
    monkeypatch.setattr(ak, "_today_cn", lambda: "2026-05-20", raising=False)
    ak.ak.stock_zh_index_spot_sina.return_value = pd.DataFrame({
        "代码": ["sh000001", "sz399001"], "名称": ["上证", "深证"],
        "成交额": [1.0e12, 1.0e12],
    })
    r = ak.get_market_volume("2026-05-20")
    assert r.success
    assert "stock_zh_index_spot_sina" in r.source


def test_market_volume_non_today_skips_realtime(ak: AkshareProvider, monkeypatch):
    monkeypatch.setattr(ak, "_today_cn", lambda: "2026-05-20", raising=False)
    ak.ak.index_zh_a_hist.return_value = pd.DataFrame([
        {"日期": "2026-05-19", "开盘": 0.0, "收盘": 4100.0, "最高": 0.0,
         "最低": 0.0, "成交量": 0.0, "成交额": 1.0e12, "涨跌幅": 0.0}])
    r = ak.get_market_volume("2026-05-19")
    assert r.success
    assert "index_zh_a_hist" in r.source


def test_market_volume_today_sina_fail_falls_back_to_em(ak: AkshareProvider, monkeypatch):
    monkeypatch.setattr(ak, "_today_cn", lambda: "2026-05-20", raising=False)
    ak.ak.stock_zh_index_spot_sina.side_effect = Exception("sina down")
    ak.ak.index_zh_a_hist.side_effect = lambda symbol, **kw: pd.DataFrame([
        {"日期": "2026-05-20", "开盘": 0.0, "收盘": 4100.0, "最高": 0.0,
         "最低": 0.0, "成交量": 0.0, "成交额": 1.0e12, "涨跌幅": 0.0}])
    r = ak.get_market_volume("2026-05-20")
    assert r.success
    assert "index_zh_a_hist" in r.source


def test_index_weekly_sina_drops_nan_close(ak: AkshareProvider):
    """末日收盘 NaN 时，周线取该周最后一个有效收盘，不得写入 NaN。"""
    import math
    df = _sina_weekly_src()
    df.loc[df.index[-1], "close"] = float("nan")  # 05-29 close NaN
    ak.ak.stock_zh_index_daily.return_value = df
    r = ak.get_index_weekly("shanghai", "2026-05-18", "2026-05-29")
    assert r.success
    for row in r.data:
        assert not math.isnan(row["close"])
    assert r.data[-1]["close"] == pytest.approx(4098.0)  # 05-28 收盘


def test_index_daily_sina_nan_close_falls_back_to_em(ak: AkshareProvider):
    df = _sina_index_df()
    df.loc[df.index[-1], "close"] = float("nan")  # 05-28 close NaN
    ak.ak.stock_zh_index_daily.return_value = df
    ak.ak.index_zh_a_hist.return_value = pd.DataFrame([
        {"日期": "2026-05-28", "开盘": 4080.30, "收盘": 4098.64, "最高": 4110.78,
         "最低": 4055.83, "成交量": 6.2e8, "成交额": 1.5e12, "涨跌幅": 0.21}])
    r = ak.get_index_daily("shanghai", "2026-05-28")
    assert r.success
    assert "index_zh_a_hist" in r.source


def test_disclosure_period_half_year_and_q3(ak: AkshareProvider):
    ak.ak.stock_report_disclosure.return_value = _disclosure_df()
    ak.get_disclosure_dates("2026-08-15")  # month 8 → 上一个完结报告期 0630
    assert "2026半年报" in str(ak.ak.stock_report_disclosure.call_args)
    ak.get_disclosure_dates("2026-11-15")  # month 11 → 0930
    assert "2026三季" in str(ak.ak.stock_report_disclosure.call_args)


def test_stock_daily_bj_symbol_mapping(ak: AkshareProvider):
    ak.ak.stock_zh_a_daily.return_value = _sina_stock_df()
    ak.get_stock_daily("920802.BJ", "2026-04-29")
    assert "bj920802" in str(ak.ak.stock_zh_a_daily.call_args)


def test_index_daily_ts_code_form_maps_to_sina_symbol(ak: AkshareProvider):
    """market.py 会用 ts_code 形态(000300.SH)调用，需正确映射 sina sh000300。"""
    df = _sina_index_df()
    ak.ak.stock_zh_index_daily.return_value = df
    ak.get_index_daily("000300.SH", "2026-05-28")
    assert "sh000300" in str(ak.ak.stock_zh_index_daily.call_args)


def test_disclosure_nat_actual_date_falls_back_to_pre_date(ak: AkshareProvider):
    """未实际披露(实际披露=NaT)时 ann_date 回退首次预约。"""
    df = pd.DataFrame({
        "股票代码": ["000001"], "股票简称": ["平安银行"],
        "首次预约": [date(2026, 4, 25)],
        "初次变更": [pd.NaT], "二次变更": [pd.NaT], "三次变更": [pd.NaT],
        "实际披露": [pd.NaT],
    })
    ak.ak.stock_report_disclosure.return_value = df
    r = ak.get_disclosure_dates("2026-05-30")
    assert r.success
    rec = r.data[0]
    assert rec["actual_date"] == ""
    assert rec["ann_date"] == "20260425"  # 回退 pre_date


def test_market_volume_realtime_zero_falls_back_to_em(ak: AkshareProvider, monkeypatch):
    """盘前实时成交额为 0 时不冒充全日，退东财兜底。"""
    monkeypatch.setattr(ak, "_today_cn", lambda: "2026-05-20", raising=False)
    ak.ak.stock_zh_index_spot_sina.return_value = pd.DataFrame({
        "代码": ["sh000001", "sz399001"], "名称": ["上证", "深证"],
        "成交额": [0.0, 0.0],
    })
    ak.ak.index_zh_a_hist.side_effect = lambda symbol, **kw: pd.DataFrame([
        {"日期": "2026-05-20", "开盘": 0.0, "收盘": 4100.0, "最高": 0.0,
         "最低": 0.0, "成交量": 0.0, "成交额": 1.0e12, "涨跌幅": 0.0}])
    r = ak.get_market_volume("2026-05-20")
    assert r.success
    assert "index_zh_a_hist" in r.source
