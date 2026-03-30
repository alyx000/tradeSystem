"""
盘后数据采集增强功能测试

覆盖场景：
  T1. 封板率/炸板率计算
  T2. 成交量历史对比（vs_yesterday / MA5 / MA20）
  T3. 涨停按 10cm/20cm/30cm 分类
  T4. 市场宽度（涨跌家数）
  T5. 板块跌幅排名 + 资金净流入
  T6. 指数均线计算
  T7. 溢价率细分（10cm/20cm/30cm、一字板）
  T8. AI 自动分析摘要
  T9. 盘后报告生成（新增章节完整性）
  T10. get_sector_rankings 返回 top + bottom
  T11. 龙虎榜字段映射修复
  T12. collect_post_market 端到端集成
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from collectors.market import MarketCollector
from collectors.premium import PremiumCollector
from generators.report import ReportGenerator, _generate_auto_analysis
from providers.base import DataResult
from providers.registry import ProviderRegistry


# =====================================================================
# 测试辅助
# =====================================================================

def _mock_registry():
    """构造模拟 registry"""
    reg = MagicMock(spec=ProviderRegistry)
    reg.call = MagicMock(return_value=DataResult(data=None, source="mock", error="not configured"))
    return reg


def _write_history(tmp_path: Path, dates: list[str], volumes: list[float],
                   sh_closes: list[float] | None = None):
    """写入历史 post-market.yaml 用于成交量/均线测试"""
    for i, d in enumerate(dates):
        day_dir = tmp_path / "daily" / d
        day_dir.mkdir(parents=True, exist_ok=True)
        raw = {
            "total_volume": {
                "total_billion": volumes[i],
                "shanghai_billion": volumes[i] * 0.45,
                "shenzhen_billion": volumes[i] * 0.55,
            },
            "indices": {
                "shanghai": {"close": sh_closes[i] if sh_closes else 3000 + i * 10},
            },
        }
        data = {"date": d, "raw_data": raw, "holdings_data": []}
        with open(day_dir / "post-market.yaml", "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True)


def _make_limit_up_stocks():
    """构造涨停股列表（含不同涨幅类型）"""
    return {
        "count": 6,
        "stocks": [
            {"code": "000001.SZ", "name": "平安银行", "pct_chg": 10.01, "limit_times": 1,
             "first_time": "93000", "last_time": "93000", "close": 15.0},
            {"code": "000002.SZ", "name": "万科A", "pct_chg": 9.98, "limit_times": 1,
             "first_time": "100000", "last_time": "143000", "close": 12.0},
            {"code": "300001.SZ", "name": "特锐德", "pct_chg": 20.01, "limit_times": 1,
             "first_time": "95000", "last_time": "95000", "close": 30.0},
            {"code": "688001.SH", "name": "华兴源创", "pct_chg": 20.00, "limit_times": 1,
             "first_time": "110000", "last_time": "110000", "close": 50.0},
            {"code": "000003.SZ", "name": "连板A", "pct_chg": 10.05, "limit_times": 2,
             "first_time": "92500", "last_time": "92500", "close": 8.0},
            {"code": "000004.SZ", "name": "连板B", "pct_chg": 10.03, "limit_times": 3,
             "first_time": "92500", "last_time": "92500", "close": 6.0},
        ],
    }


# =====================================================================
# T1. 封板率/炸板率
# =====================================================================

class TestSealBrokenRate:
    def test_broken_board_count_integrated(self):
        """封板率/炸板率在涨停数据中正确计算"""
        reg = _mock_registry()
        limit_data = _make_limit_up_stocks()

        def mock_call(method, *args, **kwargs):
            if method == "get_limit_up_list":
                return DataResult(data=limit_data, source="test")
            if method == "get_limit_down_list":
                return DataResult(data={"count": 2, "stocks": []}, source="test")
            return DataResult(data=None, source="test", error="skip")

        reg.call = mock_call

        with tempfile.TemporaryDirectory() as tmp:
            with patch("collectors.market.BASE_DIR", Path(tmp)):
                collector = MarketCollector(reg)
                collector._get_broken_board_count = MagicMock(return_value=3)
                collector._rhythm_analyzer = MagicMock()
                collector._rhythm_analyzer.load_main_theme_names = MagicMock(return_value=[])
                collector._rhythm_analyzer.analyze = MagicMock(return_value=[])

                result = collector.collect_post_market("2026-03-28")

        lu = result["limit_up"]
        assert lu["seal_rate_pct"] == round(6 / 9 * 100, 1)
        assert lu["broken_count"] == 3
        assert lu["broken_rate_pct"] == round(3 / 9 * 100, 1)

    def test_zero_broken(self):
        """无炸板时封板率 100%"""
        reg = _mock_registry()
        limit_data = {"count": 5, "stocks": [
            {"code": f"0000{i}.SZ", "name": f"股票{i}", "pct_chg": 10.0,
             "limit_times": 1, "first_time": "93000", "last_time": "93000", "close": 10.0}
            for i in range(5)
        ]}

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
                collector._get_broken_board_count = MagicMock(return_value=0)
                collector._rhythm_analyzer = MagicMock()
                collector._rhythm_analyzer.load_main_theme_names = MagicMock(return_value=[])
                collector._rhythm_analyzer.analyze = MagicMock(return_value=[])

                result = collector.collect_post_market("2026-03-28")

        assert result["limit_up"]["seal_rate_pct"] == 100.0
        assert result["limit_up"]["broken_rate_pct"] == 0


# =====================================================================
# T2. 成交量历史对比
# =====================================================================

class TestVolumeComparison:
    def test_vs_yesterday(self):
        """较昨日放量/缩量百分比"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_history(tmp_path, ["2026-03-27"], [10000.0])

            with patch("collectors.market.BASE_DIR", tmp_path):
                collector = MarketCollector(_mock_registry())
                vol_data = {"total_billion": 12000.0}
                collector._enrich_volume_comparison(vol_data, "2026-03-28")

        assert vol_data["vs_yesterday_pct"] == 20.0

    def test_ma5_ma20(self):
        """MA5 / MA20 均量计算"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            dates = [f"2026-03-{str(i).zfill(2)}" for i in range(1, 26)]
            volumes = [10000.0 + i * 100 for i in range(25)]
            _write_history(tmp_path, dates, volumes)

            with patch("collectors.market.BASE_DIR", tmp_path):
                collector = MarketCollector(_mock_registry())
                vol_data = {"total_billion": 15000.0}
                collector._enrich_volume_comparison(vol_data, "2026-03-26")

        assert "ma5_billion" in vol_data
        assert "ma20_billion" in vol_data
        assert vol_data["vs_ma5"] in ("高于", "低于", "持平")

    def test_no_history(self):
        """无历史数据时不报错"""
        with tempfile.TemporaryDirectory() as tmp:
            with patch("collectors.market.BASE_DIR", Path(tmp)):
                collector = MarketCollector(_mock_registry())
                vol_data = {"total_billion": 10000.0}
                collector._enrich_volume_comparison(vol_data, "2026-03-28")

        assert "vs_yesterday_pct" not in vol_data


# =====================================================================
# T3. 涨停按涨幅分类
# =====================================================================

class TestLimitTypeSplit:
    def test_classification(self):
        """首板按 10cm/20cm/30cm 正确分类"""
        reg = _mock_registry()
        limit_data = _make_limit_up_stocks()

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
                collector._get_broken_board_count = MagicMock(return_value=0)
                collector._rhythm_analyzer = MagicMock()
                collector._rhythm_analyzer.load_main_theme_names = MagicMock(return_value=[])
                collector._rhythm_analyzer.analyze = MagicMock(return_value=[])

                result = collector.collect_post_market("2026-03-28")

        lu = result["limit_up"]
        assert lu["first_board_10cm"] == 2
        assert lu["first_board_20cm"] == 2
        assert lu["first_board_30cm"] == 0
        assert lu["first_board_count"] == 4
        assert lu["consecutive_board_count"] == 2


# =====================================================================
# T4. 市场宽度
# =====================================================================

class TestMarketBreadth:
    def test_breadth_in_result(self):
        """market_breadth 数据正确写入 result"""
        reg = _mock_registry()
        breadth_data = {"advance": 3000, "decline": 1500, "flat": 200,
                        "total": 4700, "advance_ratio": 2.0}

        def mock_call(method, *args, **kwargs):
            if method == "get_market_breadth":
                return DataResult(data=breadth_data, source="akshare:test")
            if method == "get_limit_up_list":
                return DataResult(data={"count": 0, "stocks": []}, source="test")
            if method == "get_limit_down_list":
                return DataResult(data={"count": 0, "stocks": []}, source="test")
            return DataResult(data=None, source="test", error="skip")

        reg.call = mock_call

        with tempfile.TemporaryDirectory() as tmp:
            with patch("collectors.market.BASE_DIR", Path(tmp)):
                collector = MarketCollector(reg)
                collector._rhythm_analyzer = MagicMock()
                collector._rhythm_analyzer.load_main_theme_names = MagicMock(return_value=[])
                collector._rhythm_analyzer.analyze = MagicMock(return_value=[])

                result = collector.collect_post_market("2026-03-28")

        assert result["breadth"]["advance"] == 3000
        assert result["breadth"]["advance_ratio"] == 2.0


# =====================================================================
# T5. 板块跌幅 + 资金流入
# =====================================================================

class TestSectorEnhancements:
    def test_sector_top_and_bottom(self):
        """板块排名返回 top + bottom"""
        reg = _mock_registry()
        sector_data = {
            "top": [{"name": "锂", "change_pct": 8.0, "volume_billion": 100, "top_stock": "A"}],
            "bottom": [{"name": "房地产", "change_pct": -3.0, "volume_billion": 50, "top_stock": "B"}],
        }

        def mock_call(method, *args, **kwargs):
            if method == "get_sector_rankings":
                return DataResult(data=sector_data, source="akshare:test")
            if method == "get_limit_up_list":
                return DataResult(data={"count": 0, "stocks": []}, source="test")
            if method == "get_limit_down_list":
                return DataResult(data={"count": 0, "stocks": []}, source="test")
            return DataResult(data=None, source="test", error="skip")

        reg.call = mock_call

        with tempfile.TemporaryDirectory() as tmp:
            with patch("collectors.market.BASE_DIR", Path(tmp)):
                collector = MarketCollector(reg)
                collector._rhythm_analyzer = MagicMock()
                collector._rhythm_analyzer.load_main_theme_names = MagicMock(return_value=[])
                collector._rhythm_analyzer.analyze = MagicMock(return_value=[])

                result = collector.collect_post_market("2026-03-28")

        assert result["sector_industry"]["data"][0]["name"] == "锂"
        assert result["sector_industry"]["bottom"][0]["name"] == "房地产"

    def test_fund_flow(self):
        """板块资金流向写入 result"""
        reg = _mock_registry()
        flow_data = [{"name": "AI", "net_inflow_billion": 50.0, "change_pct": 3.0}]

        def mock_call(method, *args, **kwargs):
            if method == "get_sector_fund_flow":
                return DataResult(data=flow_data, source="akshare:test")
            if method == "get_limit_up_list":
                return DataResult(data={"count": 0, "stocks": []}, source="test")
            if method == "get_limit_down_list":
                return DataResult(data={"count": 0, "stocks": []}, source="test")
            return DataResult(data=None, source="test", error="skip")

        reg.call = mock_call

        with tempfile.TemporaryDirectory() as tmp:
            with patch("collectors.market.BASE_DIR", Path(tmp)):
                collector = MarketCollector(reg)
                collector._rhythm_analyzer = MagicMock()
                collector._rhythm_analyzer.load_main_theme_names = MagicMock(return_value=[])
                collector._rhythm_analyzer.analyze = MagicMock(return_value=[])

                result = collector.collect_post_market("2026-03-28")

        assert result["sector_fund_flow"]["data"][0]["name"] == "AI"


# =====================================================================
# T6. 指数均线
# =====================================================================

class TestIndexMA:
    def test_compute_ma(self):
        """正确计算 MA5/10/20 和 above_ma* 标记"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            dates = [f"2026-03-{str(i).zfill(2)}" for i in range(1, 26)]
            closes = [3000.0 + i * 5 for i in range(25)]
            _write_history(tmp_path, dates, [10000.0] * 25, closes)

            reg = _mock_registry()
            today_close = 3200.0

            def mock_call(method, *args, **kwargs):
                if method == "get_index_daily" and args and args[0] == "shanghai":
                    return DataResult(data={"close": today_close, "change_pct": 0.5}, source="test")
                if method == "get_limit_up_list":
                    return DataResult(data={"count": 0, "stocks": []}, source="test")
                if method == "get_limit_down_list":
                    return DataResult(data={"count": 0, "stocks": []}, source="test")
                return DataResult(data=None, source="test", error="skip")

            reg.call = mock_call

            with patch("collectors.market.BASE_DIR", tmp_path):
                collector = MarketCollector(reg)
                collector._rhythm_analyzer = MagicMock()
                collector._rhythm_analyzer.load_main_theme_names = MagicMock(return_value=[])
                collector._rhythm_analyzer.analyze = MagicMock(return_value=[])

                result = collector.collect_post_market("2026-03-28")

        ma = result.get("moving_averages", {}).get("shanghai", {})
        assert "ma5" in ma
        assert "ma10" in ma
        assert "ma20" in ma
        assert isinstance(ma.get("above_ma5"), bool)

    def test_insufficient_history(self):
        """历史不足时不生成均线"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_history(tmp_path, ["2026-03-27"], [10000.0], [3000.0])

            reg = _mock_registry()

            def mock_call(method, *args, **kwargs):
                if method == "get_index_daily" and args and args[0] == "shanghai":
                    return DataResult(data={"close": 3050.0, "change_pct": 0.5}, source="test")
                if method == "get_limit_up_list":
                    return DataResult(data={"count": 0, "stocks": []}, source="test")
                if method == "get_limit_down_list":
                    return DataResult(data={"count": 0, "stocks": []}, source="test")
                return DataResult(data=None, source="test", error="skip")

            reg.call = mock_call

            with patch("collectors.market.BASE_DIR", tmp_path):
                collector = MarketCollector(reg)
                collector._rhythm_analyzer = MagicMock()
                collector._rhythm_analyzer.load_main_theme_names = MagicMock(return_value=[])
                collector._rhythm_analyzer.analyze = MagicMock(return_value=[])

                result = collector.collect_post_market("2026-03-28")

        assert "moving_averages" not in result


# =====================================================================
# T7. 溢价率细分
# =====================================================================

class TestPremiumSplit:
    def test_split_by_pct_type(self):
        """按 10cm/20cm/30cm 拆分首板溢价"""
        reg = _mock_registry()

        stocks = [
            {"code": "000001.SZ", "name": "A", "pct_chg": 10.0, "limit_times": 1,
             "close": 10.0, "first_time": "93000", "last_time": "93000"},
            {"code": "300001.SZ", "name": "B", "pct_chg": 20.0, "limit_times": 1,
             "close": 30.0, "first_time": "95000", "last_time": "95000"},
            {"code": "000002.SZ", "name": "C", "pct_chg": 10.0, "limit_times": 2,
             "close": 8.0, "first_time": "92500", "last_time": "92500"},
        ]

        def mock_call(method, *args, **kwargs):
            if method == "get_stock_daily":
                code = args[0]
                opens = {"000001.SZ": 10.5, "300001.SZ": 31.0, "000002.SZ": 8.5}
                return DataResult(data={"open": opens.get(code, 0)}, source="test")
            return DataResult(data=None, source="test", error="skip")

        reg.call = mock_call

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            prev_dir = tmp_path / "daily" / "2026-03-27"
            prev_dir.mkdir(parents=True)
            prev_data = {
                "raw_data": {"limit_up": {"stocks": stocks}},
                "date": "2026-03-27",
            }
            with open(prev_dir / "post-market.yaml", "w", encoding="utf-8") as f:
                yaml.dump(prev_data, f, allow_unicode=True)

            with patch("collectors.premium.DAILY_DIR", tmp_path / "daily"):
                collector = PremiumCollector(reg)
                result = collector.collect("2026-03-28", "2026-03-27")

        assert result is not None
        assert result["first_board"]["count"] == 2
        assert result["first_board_10cm"]["count"] == 1
        assert result["first_board_20cm"]["count"] == 1
        assert result["first_board_30cm"]["count"] == 0
        assert result["second_board"]["count"] == 1

    def test_yizi_detection(self):
        """一字板检测"""
        reg = _mock_registry()

        stocks = [
            {"code": "000001.SZ", "name": "一字A", "pct_chg": 10.0, "limit_times": 2,
             "close": 10.0, "first_time": "92500", "last_time": "92500"},
        ]

        def mock_call(method, *args, **kwargs):
            if method == "get_stock_daily":
                return DataResult(data={"open": 10.5}, source="test")
            return DataResult(data=None, source="test", error="skip")

        reg.call = mock_call

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            prev_dir = tmp_path / "daily" / "2026-03-27"
            prev_dir.mkdir(parents=True)
            prev_data = {
                "raw_data": {"limit_up": {"stocks": stocks}},
                "date": "2026-03-27",
            }
            with open(prev_dir / "post-market.yaml", "w", encoding="utf-8") as f:
                yaml.dump(prev_data, f, allow_unicode=True)

            with patch("collectors.premium.DAILY_DIR", tmp_path / "daily"):
                collector = PremiumCollector(reg)
                result = collector.collect("2026-03-28", "2026-03-27")

        assert result["yizi_first_open"]["count"] == 1

    def test_format_report_new_groups(self):
        """format_report 包含新分组"""
        collector = PremiumCollector(None)
        result = {
            "prev_date": "2026-03-27",
            "first_board": {"count": 3, "open_up_rate": 0.8, "premium_median": 2.0, "premium_mean": 2.5},
            "first_board_10cm": {"count": 2, "open_up_rate": 0.7, "premium_median": 1.5, "premium_mean": 2.0},
            "first_board_20cm": {"count": 1, "open_up_rate": 1.0, "premium_median": 3.0, "premium_mean": 3.0},
            "first_board_30cm": {"count": 0},
            "second_board": {"count": 1, "open_up_rate": 0.5, "premium_median": 1.0, "premium_mean": 1.0},
            "third_board_plus": {"count": 0},
            "yizi_first_open": {"count": 0},
        }
        text = collector.format_report(result)
        assert "10cm首板" in text
        assert "20cm首板" in text
        assert "一字首开" in text


# =====================================================================
# T8. AI 自动分析摘要
# =====================================================================

class TestAutoAnalysis:
    def test_volume_description(self):
        """成交额摘要"""
        raw = {
            "total_volume": {
                "total_billion": 12000, "vs_yesterday_pct": 15.0, "vs_ma5": "高于",
            },
        }
        items = _generate_auto_analysis(raw)
        assert any("12000" in s and "放量" in s for s in items)

    def test_breadth_description(self):
        """涨跌比摘要"""
        raw = {
            "breadth": {"advance": 3000, "decline": 1500, "flat": 200, "advance_ratio": 2.0},
        }
        items = _generate_auto_analysis(raw)
        assert any("赚钱效应" in s for s in items)

    def test_limit_description(self):
        """涨跌停摘要"""
        raw = {
            "limit_up": {"count": 65, "seal_rate_pct": 72.0, "broken_count": 25, "highest_board": 5},
            "limit_down": {"count": 8},
        }
        items = _generate_auto_analysis(raw)
        assert any("封板率" in s for s in items)
        assert any("5板" in s for s in items)

    def test_ma_description(self):
        """均线位置摘要"""
        raw = {
            "moving_averages": {
                "shanghai": {"ma5": 3100, "above_ma5": True, "ma5w": 3050, "above_ma5w": True,
                             "ma20": 3000, "above_ma20": True},
            },
        }
        items = _generate_auto_analysis(raw)
        assert any("5周线上方" in s for s in items)

    def test_empty_data(self):
        """空数据不报错"""
        items = _generate_auto_analysis({})
        assert items == []

    def test_breadth_inf_ratio_display(self):
        """decline=0 时 ratio=inf，显示为 ∞ 而非 inf"""
        raw = {
            "breadth": {"advance": 3000, "decline": 0, "flat": 0, "advance_ratio": float("inf")},
        }
        items = _generate_auto_analysis(raw)
        breadth_line = [s for s in items if "涨跌比" in s]
        assert breadth_line, "应生成涨跌比摘要"
        assert "∞" in breadth_line[0], f"inf 应显示为 ∞，实际: {breadth_line[0]}"
        assert "inf" not in breadth_line[0].lower(), f"不应出现 'inf'，实际: {breadth_line[0]}"
        assert "赚钱效应强" in breadth_line[0]

    def test_negative_sector_pct_sign(self):
        """板块涨跌幅为负时不应出现 '+' 号"""
        raw = {
            "sector_industry": {
                "data": [
                    {"name": "板块A", "change_pct": 3.5},
                    {"name": "板块B", "change_pct": -1.2},
                    {"name": "板块C", "change_pct": 0.0},
                ],
            },
        }
        items = _generate_auto_analysis(raw)
        sector_line = [s for s in items if "行业领涨" in s]
        assert sector_line, "应生成板块摘要"
        line = sector_line[0]
        assert "板块A(+3.5%)" in line
        assert "板块B(-1.2%)" in line
        assert "板块C(+0.0%)" in line or "板块C(+0%)" in line


# =====================================================================
# T9. 盘后报告生成
# =====================================================================

class TestPostMarketReport:
    def test_report_contains_new_sections(self):
        """报告包含所有新增章节"""
        with tempfile.TemporaryDirectory() as tmp:
            gen = ReportGenerator()
            gen.daily_dir = Path(tmp) / "daily"

            raw = {
                "date": "2026-03-28",
                "indices": {
                    "shanghai": {"close": 3900, "change_pct": 0.5, "amount_billion": 8000},
                },
                "total_volume": {
                    "total_billion": 12000, "vs_yesterday_pct": 10.0,
                    "ma5_billion": 11000, "vs_ma5": "高于",
                },
                "breadth": {"advance": 3000, "decline": 1500, "flat": 200, "advance_ratio": 2.0},
                "moving_averages": {
                    "shanghai": {"ma5": 3850, "above_ma5": True},
                },
                "limit_up": {
                    "count": 60, "first_board_count": 50, "first_board_10cm": 30,
                    "first_board_20cm": 15, "first_board_30cm": 5,
                    "consecutive_board_count": 10, "highest_board": 4,
                    "seal_rate_pct": 75.0, "broken_count": 20, "broken_rate_pct": 25.0,
                    "board_ladder": {"4": ["A"], "3": ["B"], "2": ["C", "D"], "1": ["E"]},
                },
                "limit_down": {"count": 5},
                "sector_industry": {
                    "data": [{"name": "锂", "change_pct": 8.0, "volume_billion": 100, "top_stock": "X"}],
                    "bottom": [{"name": "房地产", "change_pct": -3.0, "top_stock": "Y"}],
                },
                "sector_concept": {"data": [], "bottom": []},
                "sector_fund_flow": {
                    "data": [{"name": "AI", "net_inflow_billion": 50.0, "change_pct": 3.0}],
                },
                "northbound": {
                    "net_buy_billion": 80.0,
                    "top_active_stocks": [
                        {"name": "招行", "amount_yi": 16.5, "net_amount_yi": 5.0},
                        {"name": "中芯", "amount_yi": 12.3, "net_amount_yi": -3.0},
                    ],
                },
                "margin_data": {
                    "trade_date": "2026-03-28",
                    "total_rzye_yi": 15000.0, "total_rqye_yi": 800.0, "total_rzrqye_yi": 15800.0,
                },
                "dragon_tiger": {"data": []},
            }

            md, yaml_path = gen.generate_post_market("2026-03-28", raw)

        assert "封板率" in md
        assert "10cm" in md
        assert "涨跌家数" in md
        assert "均线" in md or "MA" in md
        assert "跌幅前5" in md
        assert "资金净流入" in md
        assert "融资" in md
        assert "数据摘要" in md

    def test_report_breadth_inf_displays_symbol(self):
        """报告中 decline=0 时涨跌比显示 ∞"""
        with tempfile.TemporaryDirectory() as tmp:
            gen = ReportGenerator()
            gen.daily_dir = Path(tmp) / "daily"
            raw = {
                "date": "2026-03-28",
                "indices": {},
                "total_volume": {},
                "breadth": {"advance": 5000, "decline": 0, "flat": 0, "advance_ratio": float("inf")},
                "limit_up": {},
                "limit_down": {},
                "sector_industry": {"data": []},
                "sector_concept": {"data": []},
                "northbound": {},
                "dragon_tiger": {"data": []},
            }
            md, _ = gen.generate_post_market("2026-03-28", raw)

        assert "涨跌比 **∞**" in md
        assert "inf" not in md.lower()

    def test_report_with_announcements(self):
        """持仓公告章节"""
        with tempfile.TemporaryDirectory() as tmp:
            gen = ReportGenerator()
            gen.daily_dir = Path(tmp) / "daily"

            raw = {
                "date": "2026-03-28",
                "indices": {},
                "total_volume": {},
                "limit_up": {},
                "limit_down": {},
                "sector_industry": {"data": []},
                "sector_concept": {"data": []},
                "northbound": {},
                "dragon_tiger": {"data": []},
            }
            anns = {
                "000001.SZ": {
                    "name": "平安银行",
                    "announcements": [{"title": "关于分红的公告", "ann_date": "20260328"}],
                },
            }
            md, _ = gen.generate_post_market("2026-03-28", raw, holdings_announcements=anns)

        assert "持仓盘后公告" in md
        assert "平安银行" in md


# =====================================================================
# T9b. 章节编号动态一致性
# =====================================================================

class TestSectionNumbering:
    def test_rhythm_section_uses_dynamic_idx(self):
        """板块节奏章节号应跟随动态 section_idx，不硬编码"""
        from generators.report import _render_sector_rhythm
        lines: list[str] = []
        raw = {
            "sector_rhythm_industry": [
                {"name": "AI", "rank_today": 1, "change_today": 3.0,
                 "consecutive_in_top30": 5, "cumulative_pct_5d": 10.0,
                 "phase": "主升", "confidence": "高", "evidence": ["连续上榜"]},
            ],
        }
        returned_idx = _render_sector_rhythm(lines, raw, 9)
        heading = [l for l in lines if "板块节奏分析" in l]
        assert heading, "应生成板块节奏章节"
        assert "九" in heading[0], f"应使用传入的 section_idx=9 → '九'，实际: {heading[0]}"
        assert returned_idx == 10

    def test_rhythm_empty_returns_same_idx(self):
        """无节奏数据时 section_idx 不变"""
        from generators.report import _render_sector_rhythm
        lines: list[str] = []
        idx = _render_sector_rhythm(lines, {}, 7)
        assert idx == 7
        assert lines == []

    def test_full_report_no_duplicate_section_numbers(self):
        """完整报告中各章节编号不重复"""
        with tempfile.TemporaryDirectory() as tmp:
            gen = ReportGenerator()
            gen.daily_dir = Path(tmp) / "daily"
            raw = {
                "date": "2026-03-28",
                "indices": {"shanghai": {"close": 3900, "change_pct": 0.5, "amount_billion": 8000}},
                "total_volume": {"total_billion": 12000},
                "limit_up": {"count": 50, "first_board_count": 40,
                             "first_board_10cm": 30, "first_board_20cm": 8, "first_board_30cm": 2,
                             "consecutive_board_count": 10, "highest_board": 3,
                             "seal_rate_pct": 80.0, "broken_count": 10, "broken_rate_pct": 20.0,
                             "board_ladder": {}},
                "limit_down": {"count": 3},
                "sector_industry": {"data": [{"name": "X", "change_pct": 1.0, "top_stock": "Y"}], "bottom": []},
                "sector_concept": {"data": []},
                "northbound": {"net_buy_billion": 50.0},
                "margin_data": {"trade_date": "2026-03-28", "total_rzye_yi": 1, "total_rqye_yi": 1, "total_rzrqye_yi": 2},
                "dragon_tiger": {"data": [{"name": "Z", "reason": "R", "net_amount": 100}]},
                "sector_rhythm_industry": [
                    {"name": "AI", "rank_today": 1, "change_today": 2.0,
                     "consecutive_in_top30": 3, "cumulative_pct_5d": 5.0,
                     "phase": "发酵", "confidence": "中", "evidence": []},
                ],
            }
            holdings = [{"name": "持仓A", "code": "000001.SZ", "close": 10, "change_pct": 1.0,
                         "cost": 9, "pnl_pct": 11.1, "amount_billion": 1}]
            anns = {"000001.SZ": {"name": "持仓A", "announcements": [{"title": "公告", "ann_date": "20260328"}]}}
            md, _ = gen.generate_post_market("2026-03-28", raw, holdings_data=holdings, holdings_announcements=anns)

        import re
        headings = re.findall(r"## (.、)", md)
        nums = [h[0] for h in headings]
        assert len(nums) == len(set(nums)), f"章节编号重复: {nums}"

    def test_section_numbers_consecutive_when_optional_skipped(self):
        """北向/融资融券缺失时，后续章节编号仍连续递增"""
        import re
        with tempfile.TemporaryDirectory() as tmp:
            gen = ReportGenerator()
            gen.daily_dir = Path(tmp) / "daily"
            raw = {
                "date": "2026-03-28",
                "indices": {},
                "total_volume": {},
                "limit_up": {},
                "limit_down": {},
                "sector_industry": {"data": []},
                "sector_concept": {"data": []},
                "northbound": {},
                "dragon_tiger": {"data": [{"name": "Z", "reason": "R", "net_amount": 100}]},
            }
            md, _ = gen.generate_post_market("2026-03-28", raw)

        headings = re.findall(r"## (.、)", md)
        nums = [h[0] for h in headings]
        assert len(nums) == len(set(nums)), f"章节编号重复: {nums}"
        expected_labels = ["一", "二", "三", "四"]
        assert nums == expected_labels, (
            f"北向/融资缺失时章节应为 {expected_labels}，实际: {nums}"
        )


# =====================================================================
# T9c. 北向个股不污染错误字典
# =====================================================================

class TestNorthboundErrorIsolation:
    def test_top_stocks_not_added_to_error_dict(self):
        """get_northbound 失败时，get_northbound_top_stocks 的数据不应写入错误字典"""
        reg = _mock_registry()

        def mock_call(method, *args, **kwargs):
            if method == "get_northbound":
                return DataResult(data=None, source="test", error="API不可用")
            if method == "get_northbound_top_stocks":
                return DataResult(
                    data={"top_active": [
                        {"name": "招行", "amount_yi": 16.5, "net_amount_yi": 5.0},
                    ]},
                    source="tushare:hsgt_top10",
                )
            if method == "get_limit_up_list":
                return DataResult(data={"count": 0, "stocks": []}, source="test")
            if method == "get_limit_down_list":
                return DataResult(data={"count": 0, "stocks": []}, source="test")
            return DataResult(data=None, source="test", error="skip")

        reg.call = mock_call

        with tempfile.TemporaryDirectory() as tmp:
            with patch("collectors.market.BASE_DIR", Path(tmp)):
                collector = MarketCollector(reg)
                collector._rhythm_analyzer = MagicMock()
                collector._rhythm_analyzer.load_main_theme_names = MagicMock(return_value=[])
                collector._rhythm_analyzer.analyze = MagicMock(return_value=[])

                result = collector.collect_post_market("2026-03-28")

        nb = result["northbound"]
        assert "error" in nb
        assert "top_active_stocks" not in nb


# =====================================================================
# T9d. _compute_index_ma 无收盘价时提前退出
# =====================================================================

class TestIndexMAEarlyReturn:
    def test_no_close_skips_ma(self):
        """today_close 为 0 时不应产生 above_ma* 为 False 的错误数据"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            dates = [f"2026-03-{str(i).zfill(2)}" for i in range(1, 26)]
            closes = [3000.0 + i * 5 for i in range(25)]
            _write_history(tmp_path, dates, [10000.0] * 25, closes)

            reg = _mock_registry()

            def mock_call(method, *args, **kwargs):
                if method == "get_index_daily" and args and args[0] == "shanghai":
                    return DataResult(data={"change_pct": 0.5}, source="test")
                if method == "get_limit_up_list":
                    return DataResult(data={"count": 0, "stocks": []}, source="test")
                if method == "get_limit_down_list":
                    return DataResult(data={"count": 0, "stocks": []}, source="test")
                return DataResult(data=None, source="test", error="skip")

            reg.call = mock_call

            with patch("collectors.market.BASE_DIR", tmp_path):
                collector = MarketCollector(reg)
                collector._rhythm_analyzer = MagicMock()
                collector._rhythm_analyzer.load_main_theme_names = MagicMock(return_value=[])
                collector._rhythm_analyzer.analyze = MagicMock(return_value=[])

                result = collector.collect_post_market("2026-03-28")

        assert "moving_averages" not in result, \
            "today_close 缺失时不应生成 moving_averages"


# =====================================================================
# T9e. 单位转换正确性
# =====================================================================

class TestUnitConversions:
    def test_index_daily_amount_to_billion(self):
        """index_daily amount 千元 → 亿（/1e5）"""
        from providers.tushare_provider import TushareProvider
        provider = TushareProvider.__new__(TushareProvider)
        provider.config = {}
        provider.pro = MagicMock()

        import pandas as pd
        mock_df = pd.DataFrame([{
            "trade_date": "20260327",
            "open": 3900.0, "high": 3920.0, "low": 3880.0,
            "close": 3910.0, "pct_chg": 0.5,
            "vol": 300000000.0,
            "amount": 799696430.9,  # 千元
        }])
        provider.pro.index_daily = MagicMock(return_value=mock_df)

        result = provider.get_index_daily("shanghai", "2026-03-27")
        assert result.success
        assert abs(result.data["amount_billion"] - 7996.96) < 0.1

    def test_market_volume_to_billion(self):
        """market_volume amount 千元 → 亿（/1e5）"""
        from providers.tushare_provider import TushareProvider
        provider = TushareProvider.__new__(TushareProvider)
        provider.config = {}
        provider.pro = MagicMock()

        import pandas as pd
        sh_df = pd.DataFrame([{"amount": 799696430.9}])
        sz_df = pd.DataFrame([{"amount": 1053565323.0}])
        provider.pro.index_daily = MagicMock(side_effect=[sh_df, sz_df])

        result = provider.get_market_volume("2026-03-27")
        assert result.success
        assert abs(result.data["shanghai_billion"] - 7996.96) < 0.1
        assert abs(result.data["shenzhen_billion"] - 10535.65) < 0.1
        assert abs(result.data["total_billion"] - 18532.61) < 1.0

    def test_northbound_to_billion(self):
        """moneyflow_hsgt north_money 万元 → 亿（/10000）"""
        from providers.tushare_provider import TushareProvider
        provider = TushareProvider.__new__(TushareProvider)
        provider.config = {}
        provider.pro = MagicMock()

        import pandas as pd
        mock_df = pd.DataFrame([{
            "trade_date": "20260327",
            "north_money": 244568.3,  # 万元
            "south_money": 53253.43,
        }])
        provider.pro.moneyflow_hsgt = MagicMock(return_value=mock_df)

        result = provider.get_northbound("2026-03-27")
        assert result.success
        assert abs(result.data["net_buy_billion"] - 24.46) < 0.1

    def test_northbound_top_stocks_to_yi(self):
        """hsgt_top10 amount 元 → 亿（/1e8）"""
        from providers.tushare_provider import TushareProvider
        provider = TushareProvider.__new__(TushareProvider)
        provider.config = {}
        provider.pro = MagicMock()

        import pandas as pd
        df1 = pd.DataFrame([{
            "ts_code": "300750.SZ", "name": "宁德时代",
            "close": 200.0, "change": 5.0, "rank": 1, "market_type": "1",
            "amount": 3695423094.0,  # 元
            "net_amount": 0.0, "buy": 0.0, "sell": 0.0,
        }])
        df2 = pd.DataFrame()
        provider.pro.hsgt_top10 = MagicMock(side_effect=[df1, df2])

        result = provider.get_northbound_top_stocks("2026-03-27")
        assert result.success
        top = result.data["top_active"]
        assert len(top) == 1
        assert abs(top[0]["amount_yi"] - 36.95) < 0.1

    def test_dragon_tiger_display_in_yi(self):
        """龙虎榜金额在报告中以亿为单位展示"""
        with tempfile.TemporaryDirectory() as tmp:
            gen = ReportGenerator()
            gen.daily_dir = Path(tmp) / "daily"
            raw = {
                "date": "2026-03-28",
                "indices": {},
                "total_volume": {},
                "limit_up": {},
                "limit_down": {},
                "sector_industry": {"data": []},
                "sector_concept": {"data": []},
                "northbound": {},
                "dragon_tiger": {"data": [
                    {"name": "深南电A", "reason": "换手率", "net_amount": -119949495.75},
                    {"name": "赣锋锂业", "reason": "涨幅偏离", "net_amount": 823010980.59},
                ]},
            }
            md, _ = gen.generate_post_market("2026-03-28", raw)

        assert "1.20亿" in md
        assert "8.23亿" in md
        assert "119949496万" not in md

    def test_northbound_active_display(self):
        """北向十大活跃股以亿为单位展示"""
        with tempfile.TemporaryDirectory() as tmp:
            gen = ReportGenerator()
            gen.daily_dir = Path(tmp) / "daily"
            raw = {
                "date": "2026-03-28",
                "indices": {},
                "total_volume": {},
                "limit_up": {},
                "limit_down": {},
                "sector_industry": {"data": []},
                "sector_concept": {"data": []},
                "northbound": {
                    "net_buy_billion": 24.46,
                    "top_active_stocks": [
                        {"name": "宁德时代", "amount_yi": 36.95, "net_amount_yi": 0.0},
                    ],
                },
                "dragon_tiger": {"data": []},
            }
            md, _ = gen.generate_post_market("2026-03-28", raw)

        assert "十大活跃股" in md
        assert "37.0亿" in md
        assert "3695423094" not in md


# =====================================================================
# T9f. Tushare 板块/市场宽度新方法
# =====================================================================

class TestTushareMarketBreadth:
    def test_breadth_from_daily(self):
        """Tushare daily → 涨跌家数统计"""
        from providers.tushare_provider import TushareProvider
        import pandas as pd

        provider = TushareProvider.__new__(TushareProvider)
        provider.config = {}
        provider.pro = MagicMock()

        pcts = [5.0, 3.0, -1.0, -2.0, 0.0, 0.0, 1.5, -0.5]
        mock_df = pd.DataFrame([{"ts_code": f"0{i}.SZ", "pct_chg": p} for i, p in enumerate(pcts)])
        provider.pro.daily = MagicMock(return_value=mock_df)

        result = provider.get_market_breadth("2026-03-27")
        assert result.success
        assert result.data["advance"] == 3
        assert result.data["decline"] == 3
        assert result.data["flat"] == 2
        assert result.data["advance_ratio"] == 1.0
        assert result.source == "tushare:daily"

    def test_breadth_no_decline(self):
        """全市场无下跌时 ratio=inf"""
        from providers.tushare_provider import TushareProvider
        import pandas as pd

        provider = TushareProvider.__new__(TushareProvider)
        provider.config = {}
        provider.pro = MagicMock()

        mock_df = pd.DataFrame([
            {"ts_code": "01.SZ", "pct_chg": 5.0},
            {"ts_code": "02.SZ", "pct_chg": 3.0},
            {"ts_code": "03.SZ", "pct_chg": 0.0},
        ])
        provider.pro.daily = MagicMock(return_value=mock_df)

        result = provider.get_market_breadth("2026-03-27")
        assert result.data["advance_ratio"] == float("inf")

    def test_breadth_empty_market(self):
        """空行情返回错误"""
        from providers.tushare_provider import TushareProvider
        import pandas as pd

        provider = TushareProvider.__new__(TushareProvider)
        provider.config = {}
        provider.pro = MagicMock()
        provider.pro.daily = MagicMock(return_value=pd.DataFrame())

        result = provider.get_market_breadth("2026-03-27")
        assert not result.success


class TestTushareSectorRankings:
    def test_industry_sw_daily(self):
        """申万二级行业排名（sw_daily）"""
        from providers.tushare_provider import TushareProvider
        import pandas as pd

        provider = TushareProvider.__new__(TushareProvider)
        provider.config = {}
        provider.pro = MagicMock()
        provider._sw_l2_codes = {"801010.SI", "801030.SI", "801040.SI"}
        provider._ths_concept_map = None

        mock_df = pd.DataFrame([
            {"ts_code": "801010.SI", "name": "农林牧渔", "pct_change": 3.5, "amount": 5000000.0},
            {"ts_code": "801030.SI", "name": "基础化工", "pct_change": 1.2, "amount": 8000000.0},
            {"ts_code": "801040.SI", "name": "钢铁", "pct_change": -0.8, "amount": 3000000.0},
            {"ts_code": "801001.SI", "name": "申万50", "pct_change": 0.5, "amount": 20000000.0},
        ])
        provider.pro.sw_daily = MagicMock(return_value=mock_df)

        result = provider.get_sector_rankings("2026-03-27", "industry")
        assert result.success

        top = result.data["top"]
        assert top[0]["name"] == "农林牧渔"
        assert top[0]["change_pct"] == 3.5
        assert top[0]["volume_billion"] == 500.0  # 5000000万元 / 10000

        bottom = result.data["bottom"]
        assert bottom[0]["name"] == "钢铁"
        assert bottom[0]["change_pct"] == -0.8

        # 申万50 被过滤（不在 L2 codes 中）
        all_names = [s["name"] for s in top] + [s["name"] for s in bottom]
        assert "申万50" not in all_names

    def test_concept_ths_daily(self):
        """同花顺概念指数排名（ths_daily）"""
        from providers.tushare_provider import TushareProvider
        import pandas as pd

        provider = TushareProvider.__new__(TushareProvider)
        provider.config = {}
        provider.pro = MagicMock()
        provider._sw_l2_codes = None
        provider._ths_concept_map = {
            "883300.TI": "沪深300样本股",
            "700469.TI": "机器人概念",
        }

        mock_df = pd.DataFrame([
            {"ts_code": "883300.TI", "pct_change": 2.5, "vol": 1000000},
            {"ts_code": "700469.TI", "pct_change": -1.0, "vol": 500000},
            {"ts_code": "999999.TI", "pct_change": 5.0, "vol": 200000},
        ])
        provider.pro.ths_daily = MagicMock(return_value=mock_df)

        result = provider.get_sector_rankings("2026-03-27", "concept")
        assert result.success

        top = result.data["top"]
        assert top[0]["name"] == "沪深300样本股"
        assert top[0]["change_pct"] == 2.5

        # 999999.TI 不在概念映射中，被过滤
        all_names = [s["name"] for s in top] + [s["name"] for s in result.data["bottom"]]
        assert "999999.TI" not in all_names

    def test_lazy_load_sw_l2(self):
        """首次调用时惰性加载 L2 代码"""
        from providers.tushare_provider import TushareProvider
        import pandas as pd

        provider = TushareProvider.__new__(TushareProvider)
        provider.config = {}
        provider.pro = MagicMock()
        provider._sw_l2_codes = None
        provider._ths_concept_map = None

        classify_df = pd.DataFrame([
            {"index_code": "801011.SI", "industry_name": "林业Ⅱ"},
            {"index_code": "801033.SI", "industry_name": "化学原料"},
        ])
        provider.pro.index_classify = MagicMock(return_value=classify_df)

        codes = provider._ensure_sw_l2_codes()
        assert "801011.SI" in codes
        assert len(codes) == 2
        provider.pro.index_classify.assert_called_once()

        provider._ensure_sw_l2_codes()
        provider.pro.index_classify.assert_called_once()

    def test_sw_daily_amount_unit(self):
        """sw_daily amount 万元 → 亿（/10000）验证"""
        from providers.tushare_provider import TushareProvider
        import pandas as pd

        provider = TushareProvider.__new__(TushareProvider)
        provider.config = {}
        provider.pro = MagicMock()
        provider._sw_l2_codes = {"801010.SI"}
        provider._ths_concept_map = None

        mock_df = pd.DataFrame([
            {"ts_code": "801010.SI", "name": "农林牧渔", "pct_change": 1.0, "amount": 1850846.0},
        ])
        provider.pro.sw_daily = MagicMock(return_value=mock_df)

        result = provider.get_sector_rankings("2026-03-27", "industry")
        assert result.data["top"][0]["volume_billion"] == 185.08  # 1850846万元 / 10000


# =====================================================================
# T10. get_sector_rankings 返回 top + bottom
# =====================================================================

class TestSectorRankingsFormat:
    def test_old_format_compatible(self):
        """旧格式（纯列表）兼容性"""
        reg = _mock_registry()
        old_data = [{"name": "A", "change_pct": 5.0}]

        def mock_call(method, *args, **kwargs):
            if method == "get_sector_rankings":
                return DataResult(data=old_data, source="test")
            if method == "get_limit_up_list":
                return DataResult(data={"count": 0, "stocks": []}, source="test")
            if method == "get_limit_down_list":
                return DataResult(data={"count": 0, "stocks": []}, source="test")
            return DataResult(data=None, source="test", error="skip")

        reg.call = mock_call

        with tempfile.TemporaryDirectory() as tmp:
            with patch("collectors.market.BASE_DIR", Path(tmp)):
                collector = MarketCollector(reg)
                collector._rhythm_analyzer = MagicMock()
                collector._rhythm_analyzer.load_main_theme_names = MagicMock(return_value=[])
                collector._rhythm_analyzer.analyze = MagicMock(return_value=[])

                result = collector.collect_post_market("2026-03-28")

        assert result["sector_industry"]["data"] == old_data


# =====================================================================
# T11. 龙虎榜字段映射
# =====================================================================

class TestDragonTigerFields:
    def test_l_buy_l_sell_mapping(self):
        """Tushare top_list l_buy/l_sell 正确映射"""
        from providers.tushare_provider import TushareProvider
        provider = TushareProvider.__new__(TushareProvider)
        provider.config = {}
        provider.pro = MagicMock()

        import pandas as pd
        mock_df = pd.DataFrame([{
            "ts_code": "000001.SZ",
            "name": "平安银行",
            "reason": "涨幅偏离",
            "l_buy": 500000000.0,
            "l_sell": 200000000.0,
            "net_amount": 300000000.0,
        }])
        provider.pro.top_list = MagicMock(return_value=mock_df)

        result = provider.get_dragon_tiger("2026-03-28")
        assert result.success
        assert result.data[0]["buy_amount"] == 500000000.0
        assert result.data[0]["sell_amount"] == 200000000.0


# =====================================================================
# T12. collect_post_market 端到端（全流程不报错）
# =====================================================================

class TestCollectPostMarketE2E:
    def test_all_fail_gracefully(self):
        """所有 API 失败时不抛错"""
        reg = _mock_registry()
        reg.call = MagicMock(
            return_value=DataResult(data=None, source="test", error="API 不可用")
        )

        with tempfile.TemporaryDirectory() as tmp:
            with patch("collectors.market.BASE_DIR", Path(tmp)):
                collector = MarketCollector(reg)
                collector._rhythm_analyzer = MagicMock()
                collector._rhythm_analyzer.load_main_theme_names = MagicMock(return_value=[])
                collector._rhythm_analyzer.analyze = MagicMock(return_value=[])

                result = collector.collect_post_market("2026-03-28")

        assert result["date"] == "2026-03-28"
        assert "generated_at" in result
