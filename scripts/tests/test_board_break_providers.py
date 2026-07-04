"""scripts/tests/test_board_break_providers.py

board-break Stage 0：Tushare provider 新增 2 方法（区间复权因子 / 股东增减持）
+ 修复 get_stock_announcements 裸码透传 bug（未 normalize 就直传 pro.query）。
"""
from unittest.mock import MagicMock
import pandas as pd
from providers.tushare_provider import TushareProvider
from providers.base import DataResult


def _provider_with_pro():
    p = TushareProvider(config={"token": "x"})
    p.pro = MagicMock()
    p._initialized = True
    return p


class TestStockAdjFactorRange:
    def test_returns_records(self):
        p = _provider_with_pro()
        p.pro.query.return_value = pd.DataFrame(
            [{"ts_code": "600000.SH", "trade_date": "20260703", "adj_factor": 12.5}]
        )
        r = p.get_stock_adj_factor_range("600000", "2026-06-01", "2026-07-03")
        assert not r.error  # DataResult.error 默认 ""（base.py:48），不能断言 is None
        assert r.data[0]["adj_factor"] == 12.5

    def test_empty_is_success_empty_list(self):
        p = _provider_with_pro()
        p.pro.query.return_value = pd.DataFrame()
        r = p.get_stock_adj_factor_range("600000", "2026-06-01", "2026-07-03")
        assert not r.error and r.data == []

    def test_exception_is_error(self):
        p = _provider_with_pro()
        p.pro.query.side_effect = RuntimeError("boom")
        r = p.get_stock_adj_factor_range("600000", "2026-06-01", "2026-07-03")
        assert r.error  # 异常路径 error 非空串


class TestHolderTrade:
    def test_direction_enum_passthrough(self):
        p = _provider_with_pro()
        p.pro.query.return_value = pd.DataFrame(
            [{"ts_code": "600000.SH", "ann_date": "20260628", "holder_name": "张三",
              "holder_type": "G", "in_de": "DE", "change_vol": 100.0}]
        )
        r = p.get_holder_trade("600000", "2026-06-01", "2026-07-03")
        assert r.data[0]["in_de"] == "DE"

    def test_empty_is_success(self):
        p = _provider_with_pro()
        p.pro.query.return_value = pd.DataFrame()
        r = p.get_holder_trade("600000", "2026-06-01", "2026-07-03")
        assert not r.error and r.data == []


class TestCapabilities:
    def test_new_methods_declared(self):
        p = TushareProvider(config={"token": "x"})
        caps = p.get_capabilities()
        assert "get_stock_adj_factor_range" in caps
        assert "get_holder_trade" in caps


class TestRegistryFallback:
    def test_provider_without_capability_skipped(self):
        # spec: 未声明 capability 的 provider 必须被 registry 跳过（memory: 漏声明会静默降级到坏源）。
        # NoCapProvider 排优先级更高但未声明 capability，若被误调用会返回可辨识的假数据；
        # 断言真正拿到的是 CapProvider 的数据，证明 NoCapProvider 被 registry.call 按 capability 跳过。
        from providers.registry import ProviderRegistry

        class NoCapProvider:
            name = "nocap"
            priority = 1

            def get_capabilities(self):
                return []

            def supports(self, data_key):
                return data_key in self.get_capabilities()

            def get_holder_trade(self, *a, **k):
                return DataResult(data=[{"holder_name": "不应被调用"}], source="nocap")

        class CapProvider:
            name = "cap"
            priority = 2

            def get_capabilities(self):
                return ["get_holder_trade"]

            def supports(self, data_key):
                return data_key in self.get_capabilities()

            def get_holder_trade(self, *a, **k):
                return DataResult(data=[{"holder_name": "真实来源"}], source="cap")

        reg = ProviderRegistry()
        reg.register(NoCapProvider())
        reg.register(CapProvider())
        result = reg.call("get_holder_trade", "600000", "2026-06-01", "2026-07-03")
        assert not result.error
        assert result.data[0]["holder_name"] == "真实来源"


class TestAnnouncementsNormalize:
    def test_bare_code_normalized(self):
        p = _provider_with_pro()
        p.pro.query.return_value = pd.DataFrame()
        p.get_stock_announcements("600000", "2026-06-01", "2026-07-03")
        _, kwargs = p.pro.query.call_args
        assert kwargs.get("ts_code") == "600000.SH"
