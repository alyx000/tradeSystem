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
