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

    def test_report_renders_four_index_5w(self):
        """报告渲染四大指数（沪/深/创业板/科创50）5 周线状态全景。"""
        with tempfile.TemporaryDirectory() as tmp:
            gen = ReportGenerator()
            gen.daily_dir = Path(tmp) / "daily"
            raw = {
                "date": "2026-03-28",
                "indices": {"shanghai": {"close": 3900, "change_pct": 0.5}},
                "total_volume": {},
                "breadth": {},
                "moving_averages": {
                    "shanghai": {"ma5w": 4124.0, "above_ma5w": True},
                    "shenzhen": {"ma5w": 15354.0, "above_ma5w": True},
                    "chinext": {"ma5w": 3801.0, "above_ma5w": False},
                    "star50": {"ma5w": 1100.0, "above_ma5w": False},
                    "avg_price": {"ma5w": 32.03, "above_ma5w": False},
                },
                "limit_up": {},
                "limit_down": {},
                "sector_industry": {"data": []},
                "sector_concept": {"data": []},
                "northbound": {},
                "dragon_tiger": {"data": []},
            }
            md, _ = gen.generate_post_market("2026-03-28", raw)

        assert "5周线:" in md
        # 五个标的都要出现在 5 周线汇总行（含平均股价）
        for label in ("上证", "深证", "创业板", "科创50", "平均股价"):
            assert label in md
        assert "15354.0(上)" in md   # 深证站上
        assert "3801.0(下)" in md    # 创业板跌破
        assert "32.03(下)" in md     # 平均股价跌破

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
                    "disclosure_dates": [{"ann_date": "20260420", "report_end": "20260331"}],
                },
            }
            md, _ = gen.generate_post_market("2026-03-28", raw, holdings_announcements=anns)

        assert "持仓盘后公告" in md
        assert "平安银行" in md
        assert "预约披露: 20260420（报告期 20260331）" in md

    def test_report_renders_p0_enhancement_sections(self):
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
                "limit_step": {
                    "data": [
                        {"name": "高标A", "nums": "5"},
                        {"name": "高标B", "nums": "3"},
                    ]
                },
                "limit_cpt_list": {
                    "data": [
                        {"rank": 1, "name": "人工智能", "up_nums": 12, "cons_nums": 4, "pct_chg": 3.2, "up_stat": "5天4板"},
                    ]
                },
                "market_moneyflow_dc": {
                    "data": [
                        {"net_amount": 2500000000.0, "net_amount_rate": 2.8, "buy_elg_amount": 1200000000.0, "buy_lg_amount": 800000000.0},
                    ]
                },
                "sector_moneyflow_ths": {
                    "data": [
                        {"industry": "软件开发", "net_amount": 22.5, "pct_change": 4.8, "lead_stock": "高标A"},
                    ]
                },
                "sector_moneyflow_dc": {
                    "data": [
                        {"name": "芯片", "content_type": "行业", "net_amount": 1800000000.0, "pct_change": 3.2},
                    ]
                },
                "concept_moneyflow_ths": {
                    "data": [
                        {"name": "人工智能", "net_amount": 18.5, "pct_change": 3.2, "lead_stock": "高标A"},
                    ]
                },
                "daily_info": {
                    "data": [
                        {"market": "沪市主板", "amount": 5234.5, "vol": 123456789},
                    ]
                },
            }
            md, _ = gen.generate_post_market("2026-03-28", raw)

        assert "情绪与资金增强" in md
        assert "连板天梯" in md
        assert "最强板块" in md
        assert "大盘资金流向" in md
        assert "行业资金流入前列" in md
        assert "概念板块资金流入前列" in md
        # 瘦身：删除次要表（资金撤离 / 交易所市场统计摘录），减轻钉钉渲染负担
        assert "行业资金撤离前列" not in md
        assert "概念板块资金撤离前列" not in md
        assert "交易所市场统计摘录" not in md

    def test_report_renders_etf_flow(self):
        """ETF 净申购在情绪与资金增强章内渲染，净申购/净赎回符号正确"""
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
                "etf_flow": [
                    {"code": "510300", "name": "沪深300ETF(华泰)",
                     "shares_change_billion": 1.25, "total_shares_billion": 279.69},
                    {"code": "588000", "name": "科创50ETF",
                     "shares_change_billion": -0.8, "total_shares_billion": 330.36},
                ],
            }
            md, _ = gen.generate_post_market("2026-03-28", raw)

        assert "ETF 净申购" in md
        assert "当前份额(亿份)" in md     # 列名为份额（主源 fund_share 给的是份额非元规模）
        assert "沪深300ETF(华泰)" in md
        assert "科创50ETF" in md
        assert "+1.25" in md   # 净申购为正
        assert "-0.8" in md    # 净赎回为负
        assert "279.69" in md  # 当前份额列读 total_shares_billion，不再恒为 -

    def test_report_etf_flow_empty_no_subsection(self):
        """etf_flow 为空时不渲染 ETF 小节（但不影响其他 p0 增强章）"""
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
                "limit_step": {"data": [{"name": "高标A", "nums": "5"}]},
                "etf_flow": [],
            }
            md, _ = gen.generate_post_market("2026-03-28", raw)

        assert "连板天梯" in md       # 其他增强章正常
        assert "ETF 净申购" not in md  # 空 ETF 不打印小节

    def test_report_etf_flow_all_none_shares_no_section(self):
        """etf_flow 项全部缺 shares_change_billion 时，ETF 小节不渲染（过滤生效）"""
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
                "etf_flow": [
                    {"code": "510300", "name": "沪深300ETF(华泰)", "fund_size_billion": 600.0},
                    {"code": "588000", "name": "科创50ETF", "total_shares_billion": 30.0},
                ],
            }
            md, _ = gen.generate_post_market("2026-03-28", raw)

        assert "ETF 净申购" not in md  # 无 shares_change_billion 项被过滤，整节不出现

    def test_report_etf_flow_nonnumeric_shares_skipped(self):
        """shares_change_billion 为非数值占位符（AkShare 历史 '--'）时跳过该行，不崩报告"""
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
                "etf_flow": [
                    {"code": "510300", "name": "沪深300ETF(华泰)", "shares_change_billion": "--"},
                    {"code": "588000", "name": "科创50ETF", "shares_change_billion": 0.6},
                ],
            }
            md, _ = gen.generate_post_market("2026-03-28", raw)  # 不应抛 ValueError

        assert "科创50ETF" in md          # 有效行正常渲染
        assert "沪深300ETF(华泰)" not in md  # 脏数据行被过滤

    def test_report_etf_flow_alone_triggers_section(self):
        """仅有 etf_flow（其余 p0 增强皆空）时，情绪与资金增强章仍应出现"""
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
                "etf_flow": [
                    {"code": "510300", "name": "沪深300ETF(华泰)",
                     "shares_change_billion": 1.25},
                ],
            }
            md, _ = gen.generate_post_market("2026-03-28", raw)

        assert "情绪与资金增强" in md
        assert "ETF 净申购" in md

    def test_report_renders_block_trade(self):
        """大宗交易在龙虎榜后渲染，amount(万元)换算为亿"""
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
                "block_trade": {
                    "data": [
                        {"code": "000001.SZ", "ts_code": "000001.SZ", "price": 10.5,
                         "vol": 100.0, "amount": 5000.0,
                         "buyer": "机构专用", "seller": "中信证券上海分公司"},
                        {"code": "300750.SZ", "ts_code": "300750.SZ", "price": 200.0,
                         "vol": 50.0, "amount": 10000.0,
                         "buyer": "华泰证券", "seller": "机构专用"},
                    ]
                },
            }
            md, _ = gen.generate_post_market("2026-03-28", raw)

        assert "大宗交易" in md
        assert "000001.SZ" in md
        assert "300750.SZ" in md
        assert "0.50" in md   # 5000万元 → 0.50亿
        assert "1.00" in md   # 10000万元 → 1.00亿
        assert "机构专用" in md

    def test_report_block_trade_empty_no_section(self):
        """无大宗交易数据时不渲染该章"""
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
                "block_trade": {"data": []},
            }
            md, _ = gen.generate_post_market("2026-03-28", raw)

        assert "大宗交易" not in md


class TestBlockTradeCollection:
    def test_block_trade_collected(self):
        """collect_post_market 把 get_block_trade 数据写入 result['block_trade']"""
        reg = _mock_registry()
        bt_data = [
            {"code": "000001.SZ", "ts_code": "000001.SZ", "price": 10.5,
             "vol": 100.0, "amount": 5000.0, "buyer": "机构专用", "seller": "营业部A"},
        ]

        def mock_call(method, *args, **kwargs):
            if method == "get_block_trade":
                return DataResult(data=bt_data, source="tushare:block_trade")
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

        assert "block_trade" in result
        assert result["block_trade"]["data"][0]["code"] == "000001.SZ"


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
# T9c. 北向净额已下线：采集器不再产出 northbound
# =====================================================================

class TestNorthboundDecommissioned:
    def test_collector_does_not_emit_northbound(self):
        """北向净额下线（口径存疑）：盘后采集器不再调用 get_northbound / 写入 northbound 块。

        停更后 tushare moneyflow_hsgt.north_money 口径存疑（个股净额全 0、聚合非 0），
        十大活跃股净额同样全 0 且无消费方，故整块从采集层移除，避免假净额流入
        daily_market / market_fact_snapshots / obsidian。
        """
        reg = _mock_registry()
        called = {"northbound": False}

        def mock_call(method, *args, **kwargs):
            if method in ("get_northbound", "get_northbound_top_stocks"):
                called["northbound"] = True
                return DataResult(data={"net_buy_billion": 99.9}, source="test")
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

        assert "northbound" not in result
        assert called["northbound"] is False


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

    def test_northbound_section_removed(self):
        """北向资金 section 已下线:即便 northbound 有完整净额/活跃股数据,盘后报告也不再出现北向。

        沪深交易所 2024-08-16 起停更北向每日净额,tushare moneyflow_hsgt.north_money 口径存疑
        (同份数据十大活跃股净额全 0、聚合 north_money 却非 0,自相矛盾),故整段下线。
        """
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

        assert "北向" not in md, "北向资金已下线,盘后报告不应再出现北向"
        assert "十大活跃股" not in md


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


def test_index_table_handles_none_amount_and_pct():
    """tushare 失败、akshare sina 降级时 amount_billion/change_pct 可能为 None：
    指数表成交额列应显示 '-' 而非字面 'None'，且不因 None>=0 崩溃。"""
    with tempfile.TemporaryDirectory() as tmp:
        gen = ReportGenerator()
        gen.daily_dir = Path(tmp) / "daily"
        raw = {
            "date": "2026-03-28",
            "indices": {
                "shanghai": {"close": 3900.0, "change_pct": None, "amount_billion": None},
            },
        }
        md, _ = gen.generate_post_market("2026-03-28", raw)
    line = [l for l in md.splitlines() if l.startswith("| 上证指数 |")][0]
    assert "None" not in line, f"指数行渲染出字面 None: {line}"
    assert line.count("-") >= 2  # 涨跌幅/成交额缺失均以 - 兜底


# =====================================================================
# T13. 涨停行业归因（A 层：所属行业 + 涨停统计 → industry_ranking）
# =====================================================================

class TestLimitUpIndustryExtraction:
    """Provider 层：get_limit_up_list 提取 industry / limit_stat。"""

    def test_akshare_extracts_industry_and_stat(self):
        from providers.akshare_provider import AkshareProvider
        import pandas as pd

        p = AkshareProvider({})
        p._initialized = True
        p.ak = MagicMock()
        p.ak.stock_zt_pool_em.return_value = pd.DataFrame([
            {"代码": "300001", "名称": "特锐德", "涨跌幅": 20.0, "成交额": 1e8,
             "换手率": 5.0, "首次封板时间": "093000", "最后封板时间": "093000",
             "连板数": 1, "封板资金": 1e7, "涨停统计": "1/1", "所属行业": "电网设备"},
            {"代码": "000004", "名称": "连板B", "涨跌幅": 10.0, "成交额": 2e8,
             "换手率": 8.0, "首次封板时间": "092500", "最后封板时间": "092500",
             "连板数": 3, "封板资金": 2e7, "涨停统计": "5/3", "所属行业": "电网设备"},
        ])
        r = p.get_limit_up_list("2026-05-29")
        assert r.success
        stocks = r.data["stocks"]
        assert stocks[0]["industry"] == "电网设备"
        assert stocks[0]["limit_stat"] == "1/1"
        assert stocks[1]["limit_stat"] == "5/3"

    def test_akshare_missing_columns_degrade_to_empty(self):
        """旧/精简列（无所属行业/涨停统计）不抛错，降级为空串。"""
        from providers.akshare_provider import AkshareProvider
        import pandas as pd

        p = AkshareProvider({})
        p._initialized = True
        p.ak = MagicMock()
        p.ak.stock_zt_pool_em.return_value = pd.DataFrame([
            {"代码": "300001", "名称": "特锐德", "涨跌幅": 20.0, "成交额": 1e8,
             "换手率": 5.0, "首次封板时间": "093000", "最后封板时间": "093000",
             "连板数": 1, "封板资金": 1e7},
        ])
        r = p.get_limit_up_list("2026-05-29")
        assert r.success
        assert r.data["stocks"][0]["industry"] == ""
        assert r.data["stocks"][0]["limit_stat"] == ""

    def test_tushare_maps_up_stat_and_industry(self):
        from providers.tushare_provider import TushareProvider
        import pandas as pd

        p = TushareProvider.__new__(TushareProvider)
        p.name = "tushare"
        p.config = {}
        p.pro = MagicMock()
        p.pro.limit_list_d.return_value = pd.DataFrame([
            {"ts_code": "300001.SZ", "name": "特锐德", "close": 30.0, "pct_chg": 20.0,
             "amount": 100000.0, "turnover_rate_f": 5.0, "fd_amount": 1e7,
             "first_time": "093000", "last_time": "093000", "limit_times": 1,
             "up_stat": "1/1", "industry": "电网设备"},
        ])
        r = p.get_limit_up_list("2026-05-29")
        assert r.success
        assert r.data["stocks"][0]["limit_stat"] == "1/1"
        assert r.data["stocks"][0]["industry"] == "电网设备"

    def test_tushare_missing_fields_degrade(self):
        """limit_list_d 无 up_stat/industry 时降级为空串，不报错。"""
        from providers.tushare_provider import TushareProvider
        import pandas as pd

        p = TushareProvider.__new__(TushareProvider)
        p.name = "tushare"
        p.config = {}
        p.pro = MagicMock()
        p.pro.limit_list_d.return_value = pd.DataFrame([
            {"ts_code": "300001.SZ", "name": "特锐德", "close": 30.0, "pct_chg": 20.0,
             "amount": 100000.0, "turnover_rate_f": 5.0, "fd_amount": 1e7,
             "first_time": "093000", "last_time": "093000", "limit_times": 1},
        ])
        r = p.get_limit_up_list("2026-05-29")
        assert r.success
        assert r.data["stocks"][0]["limit_stat"] == ""
        assert r.data["stocks"][0]["industry"] == ""

    def test_akshare_nan_industry_value_becomes_empty(self):
        """列存在但值为 NaN/None → 空串（不能产出字面 'nan' 脏值）。"""
        from providers.akshare_provider import AkshareProvider
        import numpy as np
        import pandas as pd

        p = AkshareProvider({})
        p._initialized = True
        p.ak = MagicMock()
        p.ak.stock_zt_pool_em.return_value = pd.DataFrame([
            {"代码": "300001", "名称": "特锐德", "涨跌幅": 20.0, "成交额": 1e8,
             "换手率": 5.0, "首次封板时间": "093000", "最后封板时间": "093000",
             "连板数": 1, "封板资金": 1e7, "涨停统计": np.nan, "所属行业": np.nan},
        ])
        r = p.get_limit_up_list("2026-05-29")
        assert r.success
        assert r.data["stocks"][0]["industry"] == ""
        assert r.data["stocks"][0]["limit_stat"] == ""

    def test_tushare_nan_value_becomes_empty(self):
        from providers.tushare_provider import TushareProvider
        import numpy as np
        import pandas as pd

        p = TushareProvider.__new__(TushareProvider)
        p.name = "tushare"
        p.config = {}
        p.pro = MagicMock()
        p.pro.limit_list_d.return_value = pd.DataFrame([
            {"ts_code": "300001.SZ", "name": "特锐德", "close": 30.0, "pct_chg": 20.0,
             "amount": 100000.0, "turnover_rate_f": 5.0, "fd_amount": 1e7,
             "first_time": "093000", "last_time": "093000", "limit_times": 1,
             "up_stat": np.nan, "industry": np.nan},
        ])
        r = p.get_limit_up_list("2026-05-29")
        assert r.success
        assert r.data["stocks"][0]["limit_stat"] == ""
        assert r.data["stocks"][0]["industry"] == ""


def _make_limit_up_stocks_with_industry():
    """带 industry 的涨停股列表，用于聚合测试。"""
    return {
        "count": 5,
        "stocks": [
            {"code": "300001.SZ", "name": "电网1", "pct_chg": 20.0, "limit_times": 1,
             "first_time": "93000", "last_time": "93000", "close": 30.0, "industry": "电网设备"},
            {"code": "300002.SZ", "name": "电网2", "pct_chg": 20.0, "limit_times": 3,
             "first_time": "92500", "last_time": "92500", "close": 18.0, "industry": "电网设备"},
            {"code": "000001.SZ", "name": "化工1", "pct_chg": 10.0, "limit_times": 1,
             "first_time": "93000", "last_time": "93000", "close": 12.0, "industry": "化学制品"},
            {"code": "000002.SZ", "name": "ST垃圾", "pct_chg": 5.0, "limit_times": 1,
             "first_time": "93000", "last_time": "93000", "close": 3.0, "industry": "电网设备"},
            {"code": "000003.SZ", "name": "无行业", "pct_chg": 10.0, "limit_times": 1,
             "first_time": "93000", "last_time": "93000", "close": 8.0, "industry": ""},
        ],
    }


class TestIndustryRankingAggregation:
    """Collector 层：第3节按 industry 聚合 industry_ranking / _ex_st。"""

    def _run(self, limit_data):
        reg = _mock_registry()

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
                return collector.collect_post_market("2026-03-28")["limit_up"]

    def test_ranking_count_order_and_max_board(self):
        lu = self._run(_make_limit_up_stocks_with_industry())
        ranking = lu["industry_ranking"]
        # 电网设备 3 家(含 ST)、化学制品 1 家；空行业被过滤
        assert ranking[0]["industry"] == "电网设备"
        assert ranking[0]["count"] == 3
        assert ranking[0]["max_board"] == 3
        assert all(item["industry"] for item in ranking)  # 无空行业
        names = {item["industry"] for item in ranking}
        assert names == {"电网设备", "化学制品"}

    def test_ranking_ex_st_excludes_st(self):
        lu = self._run(_make_limit_up_stocks_with_industry())
        ex_st = {item["industry"]: item["count"] for item in lu["industry_ranking_ex_st"]}
        # 排除 ST 后电网设备只剩 2 家
        assert ex_st["电网设备"] == 2

    def test_non_str_industry_does_not_crash(self):
        """industry 为 None/NaN 时不崩溃，按缺失过滤。"""
        data = {"count": 2, "stocks": [
            {"code": "1", "name": "A", "limit_times": 1, "industry": None},
            {"code": "2", "name": "B", "limit_times": 1},  # 完全无 industry 键
        ]}
        lu = self._run(data)
        assert lu["industry_ranking"] == []

    def test_tie_break_is_deterministic_by_industry_name(self):
        """同 count 同 max_board 时按行业名稳定排序，避免报告抖动。"""
        # 插入顺序故意与目标顺序相反，验证排序确实生效（而非沿用遍历顺序）
        data = {"count": 2, "stocks": [
            {"code": "1", "name": "x", "limit_times": 1, "industry": "B行业"},
            {"code": "2", "name": "y", "limit_times": 1, "industry": "A行业"},
        ]}
        ranking = self._run(data)["industry_ranking"]
        # 两行业 count=1、max_board=1，按行业名升序：A行业 在前
        assert [item["industry"] for item in ranking] == ["A行业", "B行业"]


class TestIndustryRankingReport:
    """Report 层：涨跌停章节渲染涨停行业分布。"""

    def test_report_renders_industry_distribution(self):
        with tempfile.TemporaryDirectory() as tmp:
            gen = ReportGenerator()
            gen.daily_dir = Path(tmp) / "daily"
            raw = {
                "date": "2026-03-28",
                "limit_up": {
                    "count": 4,
                    "industry_ranking": [
                        {"industry": "电网设备", "count": 3, "max_board": 3, "names": ["a", "b", "c"]},
                        {"industry": "化学制品", "count": 1, "max_board": 1, "names": ["d"]},
                    ],
                },
            }
            md, _ = gen.generate_post_market("2026-03-28", raw)
        line = [l for l in md.splitlines() if "行业分布" in l]
        assert line, "报告未渲染涨停行业分布"
        assert "电网设备" in line[0]
        assert "3家" in line[0]

    def test_report_no_industry_ranking_skips(self):
        """无 industry_ranking 时不渲染行业分布行，且不报错。"""
        with tempfile.TemporaryDirectory() as tmp:
            gen = ReportGenerator()
            gen.daily_dir = Path(tmp) / "daily"
            raw = {"date": "2026-03-28", "limit_up": {"count": 0}}
            md, _ = gen.generate_post_market("2026-03-28", raw)
        assert not [l for l in md.splitlines() if "行业分布" in l]

    def test_report_empty_industry_ranking_skips(self):
        """有 count 但 industry_ranking 为空列表时仍不渲染行业分布行。"""
        with tempfile.TemporaryDirectory() as tmp:
            gen = ReportGenerator()
            gen.daily_dir = Path(tmp) / "daily"
            raw = {"date": "2026-03-28", "limit_up": {"count": 5, "industry_ranking": []}}
            md, _ = gen.generate_post_market("2026-03-28", raw)
        assert not [l for l in md.splitlines() if "行业分布" in l]


# =====================================================================
# T14. 盘后报告瘦身（钉钉单条消息渲染减负）
#   背景：单条 markdown 376 行 / 154 表格行在钉钉客户端渲染卡顿，
#   通过删次要表、收紧行数上限、删超宽列把单条压到约一半体量。
#   仅作用于盘后；盘前简报 _render_stock_info_section(compact=False) 不变。
# =====================================================================

def _render_p0(raw: dict) -> str:
    from generators.report import _render_p0_market_enhancements
    lines: list[str] = []
    _render_p0_market_enhancements(lines, raw, 4)
    return "\n".join(lines)


class TestPostMarketSlimming:
    def test_industry_inflow_capped_at_5(self):
        """行业资金流入只取净额前 5（原前 8）。"""
        raw = {"sector_moneyflow_ths": {"data": [
            {"name": f"行业{i}", "net_amount_yi": 100.0 - i, "pct_change": 1.0, "lead_stock": "X"}
            for i in range(8)
        ]}}
        md = _render_p0(raw)
        assert "行业资金流入前列" in md
        assert "行业4" in md       # 前 5 名（行业0~4）保留
        assert "行业5" not in md   # 第 6 名起被裁
        # 撤离表删除后，低净额板块不再以任何形式出现
        assert "行业资金撤离前列" not in md

    def test_concept_inflow_capped_at_5_and_withdrawal_removed(self):
        raw = {"concept_moneyflow_ths": {"data": [
            {"name": f"概念{i}", "net_amount_yi": 100.0 - i, "pct_change": 1.0, "lead_stock": "X"}
            for i in range(8)
        ]}}
        md = _render_p0(raw)
        assert "概念板块资金流入前列" in md
        assert "概念4" in md
        assert "概念5" not in md
        assert "概念板块资金撤离前列" not in md

    def test_strongest_sectors_capped_at_5(self):
        raw = {"limit_cpt_list": {"data": [
            {"rank": i + 1, "name": f"强板块{i}", "up_nums": 10, "cons_nums": 3,
             "pct_chg": 3.0, "up_stat": "5天4板"}
            for i in range(8)
        ]}}
        md = _render_p0(raw)
        assert "最强板块" in md
        assert "强板块4" in md
        assert "强板块5" not in md

    def test_etf_capped_at_8(self):
        raw = {"etf_flow": [
            {"code": f"5100{i:02d}", "name": f"ETF{i}",
             "shares_change_billion": 100.0 - i, "total_shares_billion": 50.0}
            for i in range(12)
        ]}
        md = _render_p0(raw)
        assert "ETF 净申购" in md
        assert "ETF7" in md       # 按 |变动| 降序前 8（ETF0~7）
        assert "ETF8" not in md

    def test_exchange_stats_table_removed(self):
        """交易所市场统计摘录整表删除。"""
        raw = {"daily_info": {"data": [
            {"market": f"板块{i}", "amount": 1000.0, "vol": 1000} for i in range(5)
        ]}}
        md = _render_p0(raw)
        assert "交易所市场统计摘录" not in md
        # daily_info 已从 has_any 移除：仅有该数据时不应再生成空的「情绪与资金增强」章节
        assert "情绪与资金增强" not in md

    def test_sector_rhythm_drops_signal_column_and_caps_rows(self):
        """板块节奏表删除超宽「关键信号」列，且每类最多 8 行。"""
        from generators.report import _render_sector_rhythm
        lines: list[str] = []
        raw = {"sector_rhythm_industry": [
            {"name": f"节奏{i}", "rank_today": i + 1, "change_today": 2.0,
             "consecutive_in_top30": 3, "cumulative_pct_5d": 5.0,
             "phase": "发酵", "confidence": "中", "evidence": [f"独特证据{i}"]}
            for i in range(12)
        ]}
        _render_sector_rhythm(lines, raw, 9)
        md = "\n".join(lines)
        assert "板块节奏分析" in md
        assert "关键信号" not in md           # 超宽长文本列删除
        assert "独特证据0" not in md          # evidence 不再渲染
        assert "节奏7" in md                  # 前 8 行（节奏0~7）保留
        assert "节奏8" not in md              # 第 9 行起被裁

    def test_stock_info_compact_trims_counts_and_answer(self):
        """compact=True：互动易仅 1 条且答案截至 80 字，研报/新闻各最多 2 条。"""
        from generators.report import _render_stock_info_section
        info = {"000001.SZ": {
            "name": "测试股",
            "limit_prices": {"up_limit": 11.0, "down_limit": 9.0, "pre_close": 10.0},
            "investor_qa": [{"question": f"问题{i}", "answer": "答" * 200} for i in range(3)],
            "research_reports": [{"institution": f"机构{i}", "rating": "买入", "date": "20260328"} for i in range(3)],
            "news": [{"title": f"新闻{i}", "time": "10:00"} for i in range(3)],
        }}
        lines: list[str] = []
        _render_stock_info_section(lines, info, compact=True)
        md = "\n".join(lines)
        assert "盘前边界" not in md                       # 盘前导向噪音，盘后跳过
        assert "问题0" in md and "问题1" not in md       # 互动易仅 1 条
        assert "答" * 80 in md and "答" * 100 not in md   # 答案截至 80 字
        assert "机构1" in md and "机构2" not in md        # 研报 2 条
        assert "新闻1" in md and "新闻2" not in md        # 新闻 2 条

    def test_stock_info_default_unchanged_for_premarket(self):
        """compact 默认 False：盘前简报渲染条数/截断长度与改动前一致（3/3/3 + 150 字）。"""
        from generators.report import _render_stock_info_section
        info = {"000001.SZ": {
            "name": "测试股",
            "limit_prices": {"up_limit": 11.0, "down_limit": 9.0, "pre_close": 10.0},
            "investor_qa": [{"question": f"问题{i}", "answer": "答" * 200} for i in range(3)],
            "research_reports": [{"institution": f"机构{i}", "rating": "买入", "date": "20260328"} for i in range(3)],
            "news": [{"title": f"新闻{i}", "time": "10:00"} for i in range(3)],
        }}
        lines: list[str] = []
        _render_stock_info_section(lines, info)   # 不传 compact → 默认 False
        md = "\n".join(lines)
        assert "盘前边界" in md                            # 盘前简报仍渲染盘前边界
        assert "问题2" in md                              # 互动易 3 条
        assert "答" * 150 in md and "答" * 151 not in md  # 答案截至 150 字
        assert "机构2" in md                              # 研报 3 条
        assert "新闻2" in md                              # 新闻 3 条

    def test_block_trade_capped_at_5(self):
        """大宗交易按成交额只取前 5（原前 10）。"""
        with tempfile.TemporaryDirectory() as tmp:
            gen = ReportGenerator()
            gen.daily_dir = Path(tmp) / "daily"
            raw = {
                "date": "2026-03-28",
                "block_trade": {"data": [
                    {"code": f"00000{i}.SZ", "name": f"大宗{i}", "price": 10.0,
                     "amount": (100 - i) * 1e4, "buyer": "机构专用", "seller": "营业部"}
                    for i in range(8)
                ]},
            }
            md, _ = gen.generate_post_market("2026-03-28", raw)
        assert "大宗交易" in md
        assert "大宗4" in md       # 成交额前 5（大宗0~4）保留
        assert "大宗5" not in md   # 第 6 名起被裁

    def test_industry_gainers_capped_at_5(self):
        """板块排名「行业涨幅」只取前 5（原前 10），标题同步改为前5。"""
        with tempfile.TemporaryDirectory() as tmp:
            gen = ReportGenerator()
            gen.daily_dir = Path(tmp) / "daily"
            raw = {
                "date": "2026-03-28",
                "sector_industry": {"data": [
                    {"name": f"行业{i}", "change_pct": 9.0 - i, "volume_billion": 100, "top_stock": "X"}
                    for i in range(8)
                ], "bottom": []},
            }
            md, _ = gen.generate_post_market("2026-03-28", raw)
        assert "行业涨幅前5" in md
        assert "行业涨幅前10" not in md
        assert "行业4" in md
        assert "行业5" not in md

    def test_slimmed_full_report_size_guard(self):
        """重数据场景下整篇盘后报告体量护栏：防未来移除上限导致单条再度膨胀。

        用「各重段均喂满」的合成极端输入（非真实样本）做回归护栏：瘦身后约 5.2K 字符，
        未瘦身同等输入约 10.7K，移除任一行数/列上限都会顶破 8000。
        阈值 8000 同时高于真实样本量级（2026-05-22 实测约 7.1K），避免与真实数据语义打架。
        """
        with tempfile.TemporaryDirectory() as tmp:
            gen = ReportGenerator()
            gen.daily_dir = Path(tmp) / "daily"
            raw = {
                "date": "2026-03-28",
                "indices": {"shanghai": {"close": 3900, "change_pct": 0.5, "amount_billion": 8000}},
                "total_volume": {"total_billion": 12000},
                "limit_up": {"count": 60, "highest_board": 5},
                "limit_down": {"count": 5},
                "sector_industry": {"data": [
                    {"name": f"行业{i}", "change_pct": 3.0, "volume_billion": 100, "top_stock": "X"}
                    for i in range(12)
                ], "bottom": []},
                "sector_concept": {"data": []},
                "sector_fund_flow": {"data": [
                    {"name": f"流入{i}", "net_inflow_billion": 50.0, "change_pct": 3.0} for i in range(12)
                ]},
                "limit_step": {"data": [{"name": f"高标{i}", "nums": str(10 - i)} for i in range(12)]},
                "limit_cpt_list": {"data": [
                    {"rank": i + 1, "name": f"强{i}", "up_nums": 10, "cons_nums": 3,
                     "pct_chg": 3.0, "up_stat": "5天4板"} for i in range(12)
                ]},
                "sector_moneyflow_ths": {"data": [
                    {"name": f"行业流{i}", "net_amount_yi": 100.0 - i, "pct_change": 1.0, "lead_stock": "X"}
                    for i in range(12)
                ]},
                "concept_moneyflow_ths": {"data": [
                    {"name": f"概念流{i}", "net_amount_yi": 100.0 - i, "pct_change": 1.0, "lead_stock": "X"}
                    for i in range(12)
                ]},
                "etf_flow": [
                    {"code": f"5100{i:02d}", "name": f"ETF{i}",
                     "shares_change_billion": 100.0 - i, "total_shares_billion": 50.0}
                    for i in range(12)
                ],
                "daily_info": {"data": [{"market": f"板块{i}", "amount": 1000.0, "vol": 1000} for i in range(8)]},
                "dragon_tiger": {"data": [
                    {"name": f"龙{i}", "reason": "涨幅偏离", "net_amount": 1e8} for i in range(12)
                ]},
                "sector_rhythm_industry": [
                    {"name": f"节奏行业{i}", "rank_today": i + 1, "change_today": 2.0,
                     "consecutive_in_top30": 3, "cumulative_pct_5d": 5.0,
                     "phase": "发酵", "confidence": "中", "evidence": [f"证据{i}"]}
                    for i in range(20)
                ],
                "sector_rhythm_concept": [
                    {"name": f"节奏概念{i}", "rank_today": i + 1, "change_today": 2.0,
                     "consecutive_in_top30": 3, "cumulative_pct_5d": 5.0,
                     "phase": "发酵", "confidence": "中", "evidence": [f"证据{i}"]}
                    for i in range(20)
                ],
            }
            holdings = [
                {"name": f"持仓{i}", "code": f"00000{i}.SZ", "close": 10, "change_pct": 1.0,
                 "cost": 9, "pnl_pct": 11.1, "amount_billion": 1}
                for i in range(6)
            ]
            holdings_info = {
                f"00000{i}.SZ": {
                    "name": f"持仓{i}",
                    "investor_qa": [{"question": f"Q{j}", "answer": "答" * 200} for j in range(3)],
                    "research_reports": [{"institution": f"机构{j}", "rating": "买入", "date": "20260328"} for j in range(3)],
                    "news": [{"title": f"新闻{j}", "time": "10:00"} for j in range(3)],
                }
                for i in range(6)
            }
            md, _ = gen.generate_post_market(
                "2026-03-28", raw, holdings_data=holdings, holdings_info=holdings_info
            )

        assert len(md) < 8000, f"瘦身后整篇仍过大: {len(md)} 字符"


# =====================================================================
# T15. 连板排除 ST 股
#   连板天梯（limit_step，来自 tushare）会混入大量 ST 连板（ST 长期连板霸榜）；
#   连板梯队/连板数/最高连板改用 collector 已算好的 _ex_st 口径。
# =====================================================================

class TestConsecutiveBoardExcludeST:
    def test_limit_step_ladder_excludes_st(self):
        """连板天梯过滤 ST / 退市股：风险股不进榜，正常股保留。"""
        raw = {"limit_step": {"data": [
            {"name": "*ST闻泰", "nums": "9"},
            {"name": "ST海王", "nums": "7"},
            {"name": "国华退", "nums": "6"},
            {"name": "真龙头", "nums": "5"},
            {"name": "正常二板", "nums": "2"},
        ]}}
        md = _render_p0(raw)
        assert "连板天梯" in md
        assert "真龙头" in md
        assert "正常二板" in md
        assert "*ST闻泰" not in md
        assert "ST海王" not in md
        assert "国华退" not in md

    def test_limit_step_all_st_or_delisted_no_ladder_table(self):
        """连板天梯全为 ST / 退市股且无其它增强数据时：既不渲染子表，也不留空的章节标题。"""
        raw = {"limit_step": {"data": [
            {"name": "*ST闻泰", "nums": "9"},
            {"name": "ST海王", "nums": "7"},
            {"name": "国华退", "nums": "6"},
        ]}}
        md = _render_p0(raw)
        assert "连板天梯" not in md
        assert "情绪与资金增强" not in md   # 过滤后 ladder_rows 空 → has_any 不因 ST limit_step 触发空章节

    def test_consecutive_metrics_use_ex_st(self):
        """连板数/最高连板/连板梯队改用 _ex_st 口径，ST 不出现在梯队。"""
        with tempfile.TemporaryDirectory() as tmp:
            gen = ReportGenerator()
            gen.daily_dir = Path(tmp) / "daily"
            raw = {
                "date": "2026-03-28",
                "limit_up": {
                    "count": 20,
                    "consecutive_board_count": 10, "consecutive_board_count_ex_st": 7,
                    "highest_board": 9, "highest_board_ex_st": 5,
                    "board_ladder": {"9": ["*ST妖股"], "3": ["真龙头", "正常股"]},
                    "board_ladder_ex_st": {"5": ["真龙头"], "3": ["正常股"]},
                },
            }
            md, _ = gen.generate_post_market("2026-03-28", raw)
        assert "连板: 7)" in md            # ex-ST 连板数
        assert "最高连板: **5板**" in md     # ex-ST 最高连板
        assert "真龙头" in md
        assert "*ST妖股" not in md          # ST 不进连板梯队
        assert "9板" not in md              # 含 ST 的 9 板既不在梯队也不在最高连板

    def test_consecutive_metrics_fallback_when_no_ex_st(self):
        """老归档无 _ex_st 字段时回退到含 ST 值，不渲染出 None。"""
        with tempfile.TemporaryDirectory() as tmp:
            gen = ReportGenerator()
            gen.daily_dir = Path(tmp) / "daily"
            raw = {
                "date": "2026-03-28",
                "limit_up": {
                    "count": 5, "consecutive_board_count": 4, "highest_board": 3,
                    "board_ladder": {"3": ["甲股"], "2": ["乙股"]},
                },
            }
            md, _ = gen.generate_post_market("2026-03-28", raw)
        assert "连板: 4)" in md
        assert "最高连板: **3板**" in md
        assert "甲股" in md
        assert "None" not in md

    def test_empty_ex_st_ladder_does_not_fall_back_to_st(self):
        """当日连板全是 ST → board_ladder_ex_st 为空 dict：应不渲染梯队，
        绝不能 `or` 回退到含 ST 的 board_ladder（否则 ST 又冒出来）。"""
        with tempfile.TemporaryDirectory() as tmp:
            gen = ReportGenerator()
            gen.daily_dir = Path(tmp) / "daily"
            raw = {
                "date": "2026-03-28",
                "limit_up": {
                    "count": 3,
                    "consecutive_board_count": 2, "consecutive_board_count_ex_st": 0,
                    "highest_board": 5, "highest_board_ex_st": 0,
                    "board_ladder": {"5": ["*ST妖股"], "2": ["ST垃圾"]},
                    "board_ladder_ex_st": {},   # 已计算但无 ex-ST 连板
                },
            }
            md, _ = gen.generate_post_market("2026-03-28", raw)
        assert "连板梯队" not in md      # 空 ex-ST 梯队 → 不渲染
        assert "*ST妖股" not in md       # 绝不回退到含 ST 梯队
        assert "ST垃圾" not in md

    def test_auto_analysis_highest_board_uses_ex_st(self):
        """数据摘要「最高 N板」用 ex-ST 口径（ST 9 板不应顶替真实 3 板）。"""
        raw = {
            "limit_up": {"count": 20, "highest_board": 9, "highest_board_ex_st": 3,
                         "seal_rate_pct": 80.0},
            "limit_down": {"count": 2},
        }
        items = _generate_auto_analysis(raw)
        line = [s for s in items if "最高" in s]
        assert line and "最高 3板" in line[0]
        assert "9板" not in line[0]

    def test_limit_step_none_name_kept(self):
        """连板天梯行 name 为 None / 仅有 ts_code 时不被误过滤（is_st_stock 安全）。"""
        raw = {"limit_step": {"data": [
            {"name": None, "ts_code": "300999.SZ", "nums": "4"},
            {"name": "*ST坑", "nums": "9"},
        ]}}
        md = _render_p0(raw)
        assert "300999.SZ" in md       # 无 name 的正常行保留
        assert "*ST坑" not in md        # ST 仍被过滤
