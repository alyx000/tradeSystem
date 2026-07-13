"""TushareProvider P0 接口单测。"""
from __future__ import annotations

import pandas as pd
import pytest

from providers.tushare_provider import TushareProvider


class _StubPro:
    def __init__(self):
        self.query_calls: list[tuple[str, dict]] = []
        self.ths_index_calls: list[dict] = []
        self.ths_member_calls: list[dict] = []
        self.daily_calls: list[dict] = []
        self.index_global_calls: list[dict] = []

    def index_global(self, **kwargs):
        self.index_global_calls.append(kwargs)
        latest = {"DJI": 50285.66, "N225": 63339.07, "KS11": 7847.71}
        code = kwargs.get("ts_code")
        if code in latest:
            return pd.DataFrame([
                {"trade_date": "20260521", "close": latest[code] - 10, "pct_chg": 0.10},
                {"trade_date": "20260522", "close": latest[code], "pct_chg": 0.41},
            ])
        return pd.DataFrame()

    def query(self, api_name: str, **params):
        self.query_calls.append((api_name, params))
        if api_name == "daily_basic":
            if params.get("ts_code") == "300750.SZ" and params.get("trade_date") == "20260403":
                return pd.DataFrame([
                    {"ts_code": "300750.SZ", "trade_date": "20260403", "turnover_rate": 3.2},
                ])
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

    def daily(self, **kwargs):
        self.daily_calls.append(kwargs)
        ts_code = kwargs["ts_code"]
        if ts_code == "300750.SZ" and kwargs["start_date"] == kwargs["end_date"]:
            return pd.DataFrame([
                {
                    "ts_code": ts_code,
                    "open": 180.0,
                    "high": 185.0,
                    "low": 178.0,
                    "close": 182.0,
                    "pct_chg": 1.11,
                    "vol": 123456.0,
                    "amount": 987654.0,
                    "pre_close": 180.0,
                },
            ])
        if ts_code == "300750.SZ":
            return pd.DataFrame([
                {"trade_date": "20260328", "close": 176.0, "vol": 1000.0},
                {"trade_date": "20260329", "close": 177.0, "vol": 1100.0},
                {"trade_date": "20260330", "close": 178.0, "vol": 1200.0},
                {"trade_date": "20260331", "close": 179.0, "vol": 1300.0},
                {"trade_date": "20260401", "close": 180.0, "vol": 1400.0},
                {"trade_date": "20260402", "close": 181.0, "vol": 1500.0},
                {"trade_date": "20260403", "close": 182.0, "vol": 1600.0},
            ])
        return pd.DataFrame()


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


def test_get_global_index_nikkei_maps_to_n225():
    """日经225 应注册到 Tushare 镜像 N225（与美股同源的可靠主源）。"""
    provider = _provider()

    result = provider.get_global_index("nikkei")

    assert result.success
    assert result.source == "tushare:index_global"
    assert provider.pro.index_global_calls[-1]["ts_code"] == "N225"
    assert result.data["name"] == "日经225"
    assert result.data["close"] == 63339.07
    assert result.data["change_pct"] == 0.41


def test_get_global_index_kospi_maps_to_ks11():
    """韩国综指 应注册到 Tushare 镜像 KS11。"""
    provider = _provider()

    result = provider.get_global_index("kospi")

    assert result.success
    assert provider.pro.index_global_calls[-1]["ts_code"] == "KS11"
    assert result.data["name"] == "韩国综指"
    assert result.data["close"] == 7847.71


def test_get_global_index_returns_error_when_close_is_nan():
    """镜像偶发返回 NaN 行时，主源应返回 error 让 registry 降级，而非静默输出 nan%。"""
    provider = _provider()
    nan_df = pd.DataFrame([
        {"trade_date": "20260522", "close": float("nan"), "pct_chg": float("nan")},
    ])
    provider.pro.index_global = lambda **_kw: nan_df

    result = provider.get_global_index("nikkei")

    assert not result.success
    assert result.data is None


def test_get_global_index_exposes_as_of_trade_date():
    """国际指数须带 as_of（数据交易日，YYYY-MM-DD），让简报标注「截至 日期」可验证。

    背景：美股跨周末/美国节假日时，盘前简报显示的是上一个美股交易日收盘（如周一休市→上周五），
    不标日期会被误读为「日期不对」。as_of 让混合市场日期一目了然。
    """
    provider = _provider()
    result = provider.get_global_index("nikkei")
    assert result.data["as_of"] == "2026-05-22", "trade_date 20260522 应规范化为 2026-05-22"


def test_a50_not_handled_by_tushare_falls_through_to_akshare_futures():
    """A50 盘前是「隔夜」语境，应走 akshare A50 期货（夜盘价），而非 tushare XIN9 指数前日收盘。

    tushare 不再认领 a50，返回 unsupported，让 registry 降级到 akshare futures_global_spot_em。
    """
    provider = _provider()
    result = provider.get_global_index("a50")
    assert not result.success
    assert "未支持" in (result.error or ""), "a50 应从 tushare code_map 移除，触发 registry 降级"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("20260522", "2026-05-22"),       # 8 位纯数字
        ("2026-05-22", "2026-05-22"),     # 已带横杠
        ("2026-05-22 00:00:00", "2026-05-22"),  # 带时分秒（剥掉）
        (None, None),                     # 缺失
        (float("nan"), None),             # 浮点 NaN
        ("nan", None),                    # 字符串 nan
        ("None", None),                   # 字符串 None
        ("", None),                       # 空串
    ],
)
def test_normalize_trade_date_handles_dirty_values(raw, expected):
    """脏值（NaN/None/空/带时分秒）须规范化为 None 或干净 YYYY-MM-DD，
    避免「截至 nan」「截至 2026-05-22 00:00:00」泄漏到简报。"""
    from providers.tushare_provider import _normalize_trade_date
    assert _normalize_trade_date(raw) == expected


def test_normalize_trade_date_handles_timestamp():
    """pandas Timestamp 应转成 YYYY-MM-DD，不带时分秒。"""
    from providers.tushare_provider import _normalize_trade_date
    ts = pd.Timestamp("2026-05-22 09:30:00")
    assert _normalize_trade_date(ts) == "2026-05-22"


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


def test_get_stock_basic_list_returns_clear_error_when_uninitialized():
    provider = TushareProvider.__new__(TushareProvider)
    provider.name = "tushare"
    provider.priority = 1
    provider.config = {}
    provider.pro = None
    provider._initialized = False
    provider._sw_l2_codes = None
    provider._ths_concept_map = None

    result = provider.get_stock_basic_list("2026-04-03")

    assert not result.success
    assert result.error == "provider_not_initialized: get_stock_basic_list"


def test_get_stock_basic_list_returns_clear_error_when_half_initialized():
    provider = TushareProvider.__new__(TushareProvider)
    provider.name = "tushare"
    provider.priority = 1
    provider.config = {}
    provider.pro = _StubPro()
    provider._initialized = False
    provider._sw_l2_codes = None
    provider._ths_concept_map = None

    result = provider.get_stock_basic_list("2026-04-03")

    assert not result.success
    assert result.error == "provider_not_initialized: get_stock_basic_list"


def test_get_stock_basic_batch_returns_clear_error_when_uninitialized():
    provider = TushareProvider.__new__(TushareProvider)
    provider.name = "tushare"
    provider.priority = 1
    provider.config = {}
    provider.pro = None
    provider._initialized = False
    provider._sw_l2_codes = None
    provider._ths_concept_map = None

    result = provider.get_stock_basic_batch(["300750"])

    assert not result.success
    assert result.error == "provider_not_initialized: get_stock_basic_batch"


def test_get_stock_basic_batch_returns_clear_error_when_half_initialized():
    provider = TushareProvider.__new__(TushareProvider)
    provider.name = "tushare"
    provider.priority = 1
    provider.config = {}
    provider.pro = _StubPro()
    provider._initialized = False
    provider._sw_l2_codes = None
    provider._ths_concept_map = None

    result = provider.get_stock_basic_batch(["300750"])

    assert not result.success
    assert result.error == "provider_not_initialized: get_stock_basic_batch"


def test_get_stock_st_returns_rows():
    provider = _provider()

    result = provider.get_stock_st("2026-04-03")

    assert result.success
    assert result.data[0]["code"] == "600234.SH"


def test_get_suspend_change_reasons_skips_single_fallback_for_large_lists():
    provider = _provider()
    calls = []

    def fake_query_records(api_name, **params):
        calls.append((api_name, params))
        return []

    provider._query_records = fake_query_records
    codes = [f"{i:06d}.SZ" for i in range(25)]

    result = provider.get_suspend_change_reasons("2026-05-15", codes)

    assert result.success
    assert result.data == []
    assert calls == [
        (
            "suspend",
            {
                "start_date": "20260505",
                "end_date": "20260518",
            },
        )
    ]


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


def test_get_ths_member_filters_by_concept_names():
    provider = _provider()

    result = provider.get_ths_member("2026-04-03", concept_names=["机器人"])

    assert result.success
    assert provider.pro.ths_index_calls == [{"type": "N"}]
    assert provider.pro.ths_member_calls == [{"ts_code": "885002.TI"}]
    assert result.data[0]["index_name"] == "机器人"


def test_get_stock_concept_memberships_capability_is_declared():
    provider = _provider()

    assert "get_stock_concept_memberships" in provider.get_capabilities()


def test_get_stock_daily_normalizes_plain_code():
    provider = _provider()

    result = provider.get_stock_daily("300750", "2026-04-03")

    assert result.success
    assert result.data["code"] == "300750.SZ"
    assert result.data["turnover_rate"] == 3.2
    assert provider.pro.daily_calls[-1]["ts_code"] == "300750.SZ"


def test_get_stock_ma_normalizes_plain_code():
    provider = _provider()

    result = provider.get_stock_ma("300750", "2026-04-03")

    assert result.success
    assert result.data["ma5"] == 180.0
    assert result.data["volume_ma5"] == 1400.0
    assert provider.pro.daily_calls[-1]["ts_code"] == "300750.SZ"


class _TopVolumePro:
    """get_top_volume_stocks 专用桩：daily 走 trade_date 全市场查询。"""

    def daily(self, **kwargs):
        return pd.DataFrame([
            {"ts_code": "600519.SH", "name": "贵州茅台", "close": 1500.0,
             "amount": 5_000_000.0, "pct_chg": 1.2},  # amount 单位千元 → 50 亿
        ])


def test_get_top_volume_stocks_amount_billion_uses_qianyuan_to_yi():
    """tushare daily.amount 单位是千元，转亿应 /1e5（千元→元×1e3，元→亿÷1e8）。

    回归：曾误用 /1e4 → amount_billion 偏大 10 倍。
    """
    provider = TushareProvider.__new__(TushareProvider)
    provider.name = "tushare"
    provider.config = {}
    provider.pro = _TopVolumePro()
    provider._initialized = True

    result = provider.get_top_volume_stocks("2026-05-29", top_n=20)

    assert result.success
    # 5_000_000 千元 = 50 亿，而非偏大 10 倍的 500 亿
    assert result.data[0]["amount_billion"] == pytest.approx(50.0)
