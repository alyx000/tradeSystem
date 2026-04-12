"""
ST 过滤与 5 周均线改进测试

覆盖场景：
  T1. _is_st_stock 辅助函数
  T2. 涨停统计 ex_st 字段
  T3. 跌停统计 ex_st 字段
  T4. dual_write 优先读取 ex_st 字段
  T5. review.py ladder_rows 过滤 ST
  T6. get_index_weekly mock 测试
  T7. _compute_index_ma 使用周线数据
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from collectors.market import MarketCollector, _is_st_stock
from providers.base import DataResult
from providers.registry import ProviderRegistry


# =====================================================================
# T1. _is_st_stock
# =====================================================================

class TestIsStStock:
    @pytest.mark.parametrize("name,expected", [
        ("ST中天", True),
        ("*ST金科", True),
        ("SST集成", True),
        ("S*ST海纳", True),
        ("st测试", True),
        ("宁德时代", False),
        ("平安银行", False),
        ("特锐德", False),
        ("", False),
        ("  ST前空格", True),
    ])
    def test_is_st_stock(self, name, expected):
        assert _is_st_stock(name) == expected


# =====================================================================
# T2. 涨停 ex_st 字段
# =====================================================================

class TestLimitUpExSt:
    def test_ex_st_fields(self):
        """涨停统计包含排除 ST 后的字段"""
        reg = MagicMock(spec=ProviderRegistry)
        limit_data = {
            "count": 5,
            "stocks": [
                {"code": "000001.SZ", "name": "平安银行", "pct_chg": 10.0, "limit_times": 1},
                {"code": "000002.SZ", "name": "*ST金科", "pct_chg": 5.0, "limit_times": 1},
                {"code": "000003.SZ", "name": "连板A", "pct_chg": 10.0, "limit_times": 3},
                {"code": "000004.SZ", "name": "ST中天", "pct_chg": 5.0, "limit_times": 2},
                {"code": "000005.SZ", "name": "万科A", "pct_chg": 10.0, "limit_times": 2},
            ],
        }

        def mock_call(method, *args, **kwargs):
            if method == "get_limit_up_list":
                return DataResult(data=limit_data, source="test")
            if method == "get_limit_down_list":
                return DataResult(data={"count": 0, "stocks": []}, source="test")
            return DataResult(data=None, source="test", error="skip")

        reg.call = mock_call

        with tempfile.TemporaryDirectory() as tmp:
            with patch("collectors.market.BASE_DIR", Path(tmp)):
                collector = MarketCollector(reg)
                collector._get_broken_board_count = MagicMock(return_value=2)
                collector._rhythm_analyzer = MagicMock()
                collector._rhythm_analyzer.load_main_theme_names = MagicMock(return_value=[])
                collector._rhythm_analyzer.analyze = MagicMock(return_value=[])

                result = collector.collect_post_market("2026-04-10")

        lu = result["limit_up"]
        assert lu["count"] == 5
        assert lu["count_ex_st"] == 3
        assert lu["consecutive_board_count_ex_st"] == 2
        assert lu["highest_board_ex_st"] == 3
        assert "board_ladder_ex_st" in lu
        assert "seal_rate_pct_ex_st" in lu
        assert "broken_rate_pct_ex_st" in lu


# =====================================================================
# T3. 跌停 ex_st 字段
# =====================================================================

class TestLimitDownExSt:
    def test_ex_st_count(self):
        """跌停统计包含排除 ST 后的 count"""
        reg = MagicMock(spec=ProviderRegistry)
        limit_down_data = {
            "count": 4,
            "stocks": [
                {"code": "000001.SZ", "name": "万科A", "pct_chg": -10.0},
                {"code": "000002.SZ", "name": "*ST步森", "pct_chg": -5.0},
                {"code": "000003.SZ", "name": "ST中天", "pct_chg": -5.0},
                {"code": "000004.SZ", "name": "特锐德", "pct_chg": -20.0},
            ],
        }

        def mock_call(method, *args, **kwargs):
            if method == "get_limit_down_list":
                return DataResult(data=limit_down_data, source="test")
            if method == "get_limit_up_list":
                return DataResult(data={"count": 0, "stocks": []}, source="test")
            return DataResult(data=None, source="test", error="skip")

        reg.call = mock_call

        with tempfile.TemporaryDirectory() as tmp:
            with patch("collectors.market.BASE_DIR", Path(tmp)):
                collector = MarketCollector(reg)
                collector._rhythm_analyzer = MagicMock()
                collector._rhythm_analyzer.load_main_theme_names = MagicMock(return_value=[])
                collector._rhythm_analyzer.analyze = MagicMock(return_value=[])

                result = collector.collect_post_market("2026-04-10")

        ld = result["limit_down"]
        assert ld["count"] == 4
        assert ld["count_ex_st"] == 2


# =====================================================================
# T4. dual_write 优先 ex_st
# =====================================================================

class TestDualWriteExSt:
    def test_prefer_ex_st_fields(self):
        """_extract_market_row 优先使用 _ex_st 后缀字段"""
        from db.dual_write import _extract_market_row

        envelope = {
            "raw_data": {
                "indices": {
                    "shanghai": {"close": 3200.0, "change_pct": 0.5},
                    "shenzhen": {"close": 10000.0, "change_pct": 0.3},
                },
                "total_volume": {"total_billion": 12000},
                "limit_up": {
                    "count": 60,
                    "count_ex_st": 55,
                    "seal_rate_pct": 75.0,
                    "seal_rate_pct_ex_st": 78.0,
                    "broken_rate_pct": 25.0,
                    "broken_rate_pct_ex_st": 22.0,
                    "highest_board": 5,
                    "highest_board_ex_st": 5,
                },
                "limit_down": {
                    "count": 10,
                    "count_ex_st": 6,
                },
                "breadth": {},
                "northbound": {},
                "margin_data": {},
                "moving_averages": {},
            },
        }

        row = _extract_market_row("2026-04-10", envelope)
        assert row["limit_up_count"] == 55
        assert row["limit_down_count"] == 6
        assert row["seal_rate"] == 78.0
        assert row["broken_rate"] == 22.0
        assert row["highest_board"] == 5

    def test_fallback_without_ex_st(self):
        """无 _ex_st 字段时降级到原字段"""
        from db.dual_write import _extract_market_row

        envelope = {
            "raw_data": {
                "indices": {
                    "shanghai": {"close": 3200.0, "change_pct": 0.5},
                    "shenzhen": {"close": 10000.0, "change_pct": 0.3},
                },
                "total_volume": {"total_billion": 12000},
                "limit_up": {
                    "count": 60,
                    "seal_rate_pct": 75.0,
                    "broken_rate_pct": 25.0,
                    "highest_board": 5,
                },
                "limit_down": {
                    "count": 10,
                },
                "breadth": {},
                "northbound": {},
                "margin_data": {},
                "moving_averages": {},
            },
        }

        row = _extract_market_row("2026-04-10", envelope)
        assert row["limit_up_count"] == 60
        assert row["limit_down_count"] == 10
        assert row["seal_rate"] == 75.0
        assert row["broken_rate"] == 25.0


# =====================================================================
# T5. ladder_rows 过滤 ST
# =====================================================================

class TestLadderRowsFilterSt:
    def test_st_filtered_from_ladder(self):
        """_build_review_signals 的 ladder_rows 不包含 ST 股"""
        from api.routes.review import _build_review_signals

        market = {
            "limit_step": {
                "data": [
                    {"name": "高标A", "nums": "5"},
                    {"name": "*ST退市", "nums": "8"},
                    {"name": "ST中天", "nums": "6"},
                    {"name": "连板B", "nums": "3"},
                ]
            },
        }

        signals = _build_review_signals(market)
        ladder = signals["emotion"]["ladder_rows"]
        names = [r["name"] for r in ladder]
        assert "高标A" in names
        assert "连板B" in names
        assert "*ST退市" not in names
        assert "ST中天" not in names


# =====================================================================
# T6. get_index_weekly mock 测试
# =====================================================================

class TestGetIndexWeekly:
    def test_tushare_index_weekly(self):
        """Tushare get_index_weekly 返回周线数据"""
        from providers.tushare_provider import TushareProvider
        import pandas as pd

        provider = TushareProvider.__new__(TushareProvider)
        provider.config = {}
        provider.pro = MagicMock()

        mock_df = pd.DataFrame([
            {"trade_date": "20260403", "close": 3200.0, "open": 3180.0, "high": 3220.0, "low": 3170.0},
            {"trade_date": "20260327", "close": 3190.0, "open": 3170.0, "high": 3210.0, "low": 3160.0},
            {"trade_date": "20260320", "close": 3180.0, "open": 3160.0, "high": 3200.0, "low": 3150.0},
            {"trade_date": "20260313", "close": 3170.0, "open": 3150.0, "high": 3190.0, "low": 3140.0},
            {"trade_date": "20260306", "close": 3160.0, "open": 3140.0, "high": 3180.0, "low": 3130.0},
        ])
        provider.pro.index_weekly = MagicMock(return_value=mock_df)

        result = provider.get_index_weekly("shanghai", "2026-02-01", "2026-04-10")
        assert result.success
        assert len(result.data) == 5
        assert result.data[0]["close"] == 3200.0
        assert result.source == "tushare:index_weekly"


# =====================================================================
# T7. _compute_index_ma 使用周线
# =====================================================================

class TestComputeIndexMaWeekly:
    def test_weekly_ma5w_from_provider(self):
        """_compute_index_ma 通过 get_index_weekly 计算 5 周均线"""
        reg = MagicMock(spec=ProviderRegistry)
        weekly_data = [
            {"trade_date": "20260306", "close": 3160.0, "open": 3140.0, "high": 3180.0, "low": 3130.0},
            {"trade_date": "20260313", "close": 3170.0, "open": 3150.0, "high": 3190.0, "low": 3140.0},
            {"trade_date": "20260320", "close": 3180.0, "open": 3160.0, "high": 3200.0, "low": 3150.0},
            {"trade_date": "20260327", "close": 3190.0, "open": 3170.0, "high": 3210.0, "low": 3160.0},
            {"trade_date": "20260403", "close": 3200.0, "open": 3180.0, "high": 3220.0, "low": 3170.0},
        ]

        def mock_call(method, *args, **kwargs):
            if method == "get_index_daily" and args and args[0] == "shanghai":
                return DataResult(data={"close": 3250.0, "change_pct": 0.5}, source="test")
            if method == "get_index_daily" and args and args[0] == "shenzhen":
                return DataResult(data={"close": 10500.0, "change_pct": 0.3}, source="test")
            if method == "get_index_daily" and args and args[0] == "chinext":
                return DataResult(data={"close": 2050.0, "change_pct": 0.4}, source="test")
            if method == "get_limit_up_list":
                return DataResult(data={"count": 0, "stocks": []}, source="test")
            if method == "get_limit_down_list":
                return DataResult(data={"count": 0, "stocks": []}, source="test")
            if method == "get_index_weekly":
                return DataResult(data=weekly_data, source="tushare:index_weekly")
            return DataResult(data=None, source="test", error="skip")

        reg.call = mock_call

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            dates = [f"2026-03-{str(i).zfill(2)}" for i in range(1, 26)]
            for d in dates:
                day_dir = tmp_path / "daily" / d
                day_dir.mkdir(parents=True, exist_ok=True)
                raw = {
                    "total_volume": {"total_billion": 10000.0},
                    "indices": {"shanghai": {"close": 3200.0 + int(d[-2:]) * 2}},
                }
                data = {"date": d, "raw_data": raw}
                with open(day_dir / "post-market.yaml", "w", encoding="utf-8") as f:
                    yaml.dump(data, f, allow_unicode=True)

            with patch("collectors.market.BASE_DIR", tmp_path):
                collector = MarketCollector(reg)
                collector._rhythm_analyzer = MagicMock()
                collector._rhythm_analyzer.load_main_theme_names = MagicMock(return_value=[])
                collector._rhythm_analyzer.analyze = MagicMock(return_value=[])

                result = collector.collect_post_market("2026-04-10")

        ma = result.get("moving_averages", {})
        sh = ma.get("shanghai", {})
        assert "ma5w" in sh
        expected_ma5w = round(sum([3160.0, 3170.0, 3180.0, 3190.0, 3200.0]) / 5, 2)
        assert sh["ma5w"] == expected_ma5w
        assert sh["above_ma5w"] is True

        assert "shenzhen" in ma
        assert "ma5w" in ma["shenzhen"]
        assert "chinext" in ma
        assert "ma5w" in ma["chinext"]
