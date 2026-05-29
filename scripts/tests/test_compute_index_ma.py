"""MarketCollector._compute_index_ma 解耦测试。

背景：原实现在上证 close 缺失（如 2026-05-25 tushare 无数据）时整体 return，
连带深证/创业板 5 周线也不计算。本测试钉死「单指数缺失不应拖垮其他指数」。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import yaml

from collectors.market import MarketCollector
from providers.base import DataResult


def _weekly_result(closes: list[float]) -> DataResult:
    rows = [
        {"trade_date": f"2026040{i}", "close": c, "open": c, "high": c, "low": c}
        for i, c in enumerate(closes, start=1)
    ]
    return DataResult(data=rows, source="test:weekly")


class TestComputeIndexMaDecoupling:
    def test_shanghai_missing_still_computes_other_indices_5w(self):
        """上证 close 缺失时，深证/创业板 5 周线仍应计算。"""
        registry = MagicMock()
        registry.call.return_value = _weekly_result([13000, 13100, 13200, 13300, 13400])
        collector = MarketCollector(registry)

        result = {
            "indices": {
                "shanghai": {"error": "所有数据源均失败"},  # 无 close
                "shenzhen": {"close": 13500.0},
                "chinext": {"close": 2800.0},
            }
        }
        collector._compute_index_ma(result, "2026-05-26")

        assert "moving_averages" in result, "单指数缺失不应导致 moving_averages 整块缺失"
        ma = result["moving_averages"]
        assert "shenzhen" in ma and "ma5w" in ma["shenzhen"]
        assert "chinext" in ma and "ma5w" in ma["chinext"]
        # 上证 close 缺失 → 上证不应出现
        assert "shanghai" not in ma

    def test_all_indices_missing_no_moving_averages_key(self):
        """全部指数 close 缺失时，不写 moving_averages（保持与历史行为一致）。"""
        registry = MagicMock()
        registry.call.return_value = _weekly_result([13000, 13100, 13200, 13300, 13400])
        collector = MarketCollector(registry)

        result = {"indices": {"shanghai": {"error": "x"}, "shenzhen": {"error": "x"}, "chinext": {"error": "x"}}}
        collector._compute_index_ma(result, "2026-05-26")
        assert "moving_averages" not in result

    def test_star50_5w_computed_when_close_present(self):
        """科创50 日收盘在 indices 时，应算出 moving_averages['star50'] 的 ma5w/above_ma5w。"""
        registry = MagicMock()
        registry.call.return_value = _weekly_result([1000, 1010, 1020, 1030, 1040])
        collector = MarketCollector(registry)

        result = {
            "indices": {
                "shanghai": {"error": "x"},  # 不影响 star50
                "star50": {"close": 1200.0},
            }
        }
        collector._compute_index_ma(result, "2026-05-26")

        ma = result.get("moving_averages", {})
        assert "star50" in ma and "ma5w" in ma["star50"]
        # 末根周线 2026040x（周14）非当周 → 追加 1200 → mean([1010,1020,1030,1040,1200])
        assert ma["star50"]["ma5w"] == round(sum([1010, 1020, 1030, 1040, 1200]) / 5, 2)
        assert ma["star50"]["above_ma5w"] is True  # 1200 > 1060

    def test_avg_price_5w_from_tdx_call_specific(self):
        """平均股价经 call_specific('tdx') 取 880003 周线 → moving_averages['avg_price']。

        关键：avg_price 无 indices 日收盘，day_close 取周线末根（当周 partial）；
        且必须走 call_specific（不能 registry.call，会被 tushare 空成功遮蔽）。
        """
        registry = MagicMock()
        # registry.call（沪深创科）返回空，聚焦 avg_price 路径
        registry.call.return_value = DataResult(data=[], source="test", error="skip")
        tdx_weekly = [
            {"trade_date": "20260424", "close": 30.26, "open": 30.0, "high": 30.5, "low": 29.9},
            {"trade_date": "20260430", "close": 30.92, "open": 30.3, "high": 31.0, "low": 30.2},
            {"trade_date": "20260508", "close": 32.36, "open": 31.0, "high": 32.5, "low": 30.9},
            {"trade_date": "20260515", "close": 32.62, "open": 32.4, "high": 32.8, "low": 32.1},
            {"trade_date": "20260522", "close": 32.96, "open": 32.7, "high": 33.1, "low": 32.5},
            {"trade_date": "20260529", "close": 31.28, "open": 32.4, "high": 32.4, "low": 31.0},
        ]

        def call_specific(provider, method, *args, **kwargs):
            if provider == "tdx" and method == "get_index_weekly" and args and args[0] == "avg_price":
                return DataResult(data=tdx_weekly, source="tdx:880003_weekly")
            return DataResult(data=None, source="test", error="no")

        registry.call_specific = call_specific
        collector = MarketCollector(registry)

        result = {"indices": {"shanghai": {"error": "x"}}}  # 无任何指数 close
        collector._compute_index_ma(result, "2026-05-29")

        ma = result.get("moving_averages", {})
        assert "avg_price" in ma and "ma5w" in ma["avg_price"]
        # 采集日 2026-05-29（周22）末根 20260529 同周 → 覆盖末根为 day_close(31.28，本就相同)
        # → mean([30.92,32.36,32.62,32.96,31.28])
        assert ma["avg_price"]["ma5w"] == round(sum([30.92, 32.36, 32.62, 32.96, 31.28]) / 5, 2)
        assert ma["avg_price"]["above_ma5w"] is False  # 31.28 < 32.03

    def test_avg_price_nan_day_close_skipped_not_nan_ma5w(self):
        """pytdx 周线末根 close 为 NaN → avg_price 锚点失效，跳过，不写 NaN 的 ma5w。"""
        registry = MagicMock()
        registry.call.return_value = DataResult(data=[], source="test", error="skip")
        tdx_weekly = [
            {"trade_date": "20260424", "close": 30.26, "open": 30.0, "high": 30.5, "low": 29.9},
            {"trade_date": "20260430", "close": 30.92, "open": 30.3, "high": 31.0, "low": 30.2},
            {"trade_date": "20260508", "close": 32.36, "open": 31.0, "high": 32.5, "low": 30.9},
            {"trade_date": "20260515", "close": 32.62, "open": 32.4, "high": 32.8, "low": 32.1},
            {"trade_date": "20260522", "close": 32.96, "open": 32.7, "high": 33.1, "low": 32.5},
            {"trade_date": "20260529", "close": float("nan"), "open": 32.4, "high": 32.4, "low": 31.0},
        ]

        def call_specific(provider, method, *args, **kwargs):
            if provider == "tdx" and args and args[0] == "avg_price":
                return DataResult(data=tdx_weekly, source="tdx:880003_weekly")
            return DataResult(data=None, source="test", error="no")

        registry.call_specific = call_specific
        collector = MarketCollector(registry)
        result = {"indices": {"shanghai": {"error": "x"}}}
        collector._compute_index_ma(result, "2026-05-29")

        # 锚点 NaN → 不写 avg_price（绝不写 NaN 的 ma5w 污染 DB）
        assert "avg_price" not in result.get("moving_averages", {})


class TestComputeIndexMaDailyPath:
    def test_shanghai_present_computes_daily_ma(self, tmp_path, monkeypatch):
        """上证 close 存在时，日线 MA5/MA20 正常计算（防解耦改动回归 daily 路径）。"""
        import collectors.market as market_mod
        monkeypatch.setattr(market_mod, "BASE_DIR", tmp_path)
        daily = tmp_path / "daily"
        # 写 6 天历史，上证 close 递增
        for i in range(6):
            d = daily / f"2026-05-1{i}"
            d.mkdir(parents=True)
            with open(d / "post-market.yaml", "w", encoding="utf-8") as f:
                yaml.dump({"raw_data": {"indices": {"shanghai": {"close": 4000.0 + i * 10}}}}, f, allow_unicode=True)

        registry = MagicMock()
        registry.call.return_value = _weekly_result([4100, 4110, 4120, 4130, 4140])
        collector = MarketCollector(registry)

        result = {"indices": {"shanghai": {"close": 4200.0}}}
        collector._compute_index_ma(result, "2026-05-26")

        assert "moving_averages" in result
        sh = result["moving_averages"]["shanghai"]
        assert "ma5" in sh and "above_ma5" in sh
        assert sh["above_ma5"] is True  # 今日 4200 高于历史均值
