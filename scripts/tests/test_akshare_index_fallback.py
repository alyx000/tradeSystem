"""AkShareProvider 指数日线 + 两市成交额降级实现。

背景：tushare 一限流（HTTP 429）/缺口，指数与成交额无兜底直接归零（2026-05-15 实测）。
本测试钉死 akshare 降级的输出形状必须与 tushare 对齐，且成交额单位换算正确。
mock self.ak.index_zh_a_hist，不触外网。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from providers.akshare_provider import AkshareProvider


@pytest.fixture
def ak() -> AkshareProvider:
    p = AkshareProvider({})
    p._initialized = True
    p.ak = MagicMock()
    return p


def _daily_df(date: str, *, close: float, amount_yuan: float,
              open_: float = 0.0, high: float = 0.0, low: float = 0.0,
              volume: float = 0.0, change_pct: float = 0.0) -> pd.DataFrame:
    """构造 ak.index_zh_a_hist(period='daily') 形态的单行 DataFrame。"""
    return pd.DataFrame([{
        "日期": date,
        "开盘": open_,
        "收盘": close,
        "最高": high,
        "最低": low,
        "成交量": volume,
        "成交额": amount_yuan,  # akshare 单位：元
        "涨跌幅": change_pct,
    }])


class TestGetIndexDaily:
    def test_returns_tushare_compatible_shape(self, ak: AkshareProvider):
        ak.ak.index_zh_a_hist.return_value = _daily_df(
            "2026-05-26", close=4145.373, amount_yuan=1.461685e12,
            open_=4137.32, high=4150.29, low=4104.46, change_pct=-0.17,
        )
        r = ak.get_index_daily("shanghai", "2026-05-26")
        assert r.success
        for k in ("open", "high", "low", "close", "change_pct", "volume", "amount_billion"):
            assert k in r.data, f"缺字段 {k}"
        assert r.data["close"] == pytest.approx(4145.373)
        assert "akshare" in r.source

    def test_amount_unit_yuan_to_yi(self, ak: AkshareProvider):
        """成交额 akshare 是元，需 /1e8 转亿；对齐 tushare 05-26 上证 14616.85 亿。"""
        ak.ak.index_zh_a_hist.return_value = _daily_df(
            "2026-05-26", close=4145.373, amount_yuan=1.461685e12,
        )
        r = ak.get_index_daily("shanghai", "2026-05-26")
        assert r.data["amount_billion"] == pytest.approx(14616.85, abs=0.01)

    def test_picks_row_matching_date(self, ak: AkshareProvider):
        """区间返回多行时，取日期匹配的那一行（而非首行）。"""
        df = pd.concat([
            _daily_df("2026-05-25", close=4100.0, amount_yuan=1.0e12),
            _daily_df("2026-05-26", close=4145.373, amount_yuan=1.461685e12),
        ], ignore_index=True)
        ak.ak.index_zh_a_hist.return_value = df
        r = ak.get_index_daily("shanghai", "2026-05-26")
        assert r.data["close"] == pytest.approx(4145.373)

    def test_empty_df_returns_error(self, ak: AkshareProvider):
        """空 df → 不成功，让注册表继续降级而非吞成 0。"""
        ak.ak.index_zh_a_hist.return_value = pd.DataFrame()
        r = ak.get_index_daily("shanghai", "2026-05-26")
        assert not r.success

    def test_no_exact_date_match_returns_error(self, ak: AkshareProvider):
        """无目标日期匹配行时不得回退到邻近日期当当日事实（事实层污染）。"""
        ak.ak.index_zh_a_hist.return_value = _daily_df(
            "2026-05-25", close=4100.0, amount_yuan=1.0e12,  # 只有 05-25，无 05-26
        )
        r = ak.get_index_daily("shanghai", "2026-05-26")
        assert not r.success, "邻近日期不应被当作目标日的事实返回 success"

    def test_nan_close_returns_error(self, ak: AkshareProvider):
        """close 为 NaN 时返回 error，避免 NaN 穿透到 MA / 节点信号造成伪信号。"""
        ak.ak.index_zh_a_hist.return_value = _daily_df(
            "2026-05-26", close=float("nan"), amount_yuan=1.0e12,
        )
        r = ak.get_index_daily("shanghai", "2026-05-26")
        assert not r.success

    def test_exception_returns_error(self, ak: AkshareProvider):
        ak.ak.index_zh_a_hist.side_effect = Exception("akshare 网络异常")
        r = ak.get_index_daily("shanghai", "2026-05-26")
        assert not r.success
        assert "akshare 网络异常" in r.error

    def test_strips_tushare_suffix_for_csi_indices(self, ak: AkshareProvider):
        """采集器对 csi300/csi1000 传入 ts-code（000300.SH），akshare 需去后缀的 6 位码。"""
        ak.ak.index_zh_a_hist.return_value = _daily_df(
            "2026-05-26", close=4500.0, amount_yuan=1.0e12,
        )
        ak.get_index_daily("000300.SH", "2026-05-26")
        _, kwargs = ak.ak.index_zh_a_hist.call_args
        assert kwargs["symbol"] == "000300", "ts-code 后缀未剥离，akshare 取数会失败"


class TestGetMarketVolume:
    def test_sums_shanghai_and_shenzhen(self, ak: AkshareProvider):
        def _side_effect(symbol, **kwargs):
            amount = 1.461685e12 if symbol == "000001" else 1.782007e12
            close = 4145.373 if symbol == "000001" else 13500.0
            return _daily_df("2026-05-26", close=close, amount_yuan=amount)
        ak.ak.index_zh_a_hist.side_effect = _side_effect

        r = ak.get_market_volume("2026-05-26")
        assert r.success
        assert r.data["shanghai_billion"] == pytest.approx(14616.85, abs=0.01)
        assert r.data["shenzhen_billion"] == pytest.approx(17820.07, abs=0.01)
        assert r.data["total_billion"] == pytest.approx(32436.92, abs=0.02)

    def test_error_when_index_fails(self, ak: AkshareProvider):
        ak.ak.index_zh_a_hist.return_value = pd.DataFrame()
        r = ak.get_market_volume("2026-05-26")
        assert not r.success


class TestCapabilities:
    def test_market_volume_capability_declared(self, ak: AkshareProvider):
        assert "get_market_volume" in ak.get_capabilities()

    def test_index_daily_capability_declared(self, ak: AkshareProvider):
        assert "get_index_daily" in ak.get_capabilities()


class TestRegistryFallback:
    """tushare 失败时，注册表应按能力+优先级降级到 akshare 真实现接管。"""

    def test_index_daily_falls_back_to_akshare(self):
        from providers.registry import ProviderRegistry
        from providers.base import DataProvider, DataResult

        class _TushareStub(DataProvider):
            name = "tushare"
            priority = 1

            def initialize(self) -> bool:
                return True

            def get_capabilities(self) -> list[str]:
                return ["get_index_daily"]

            def get_index_daily(self, index_code: str, date: str) -> DataResult:
                return DataResult(data=None, source="tushare", error="HTTP 429")

        ak_provider = AkshareProvider({})
        ak_provider._initialized = True
        ak_provider.ak = MagicMock()
        ak_provider.ak.index_zh_a_hist.return_value = _daily_df(
            "2026-05-26", close=4145.373, amount_yuan=1.461685e12,
        )

        reg = ProviderRegistry()
        reg.register(_TushareStub())
        reg.register(ak_provider)

        r = reg.call("get_index_daily", "shanghai", "2026-05-26")
        assert r.success, "tushare 429 时应由 akshare 接管"
        assert "akshare" in r.source
        assert r.data["close"] == pytest.approx(4145.373)
