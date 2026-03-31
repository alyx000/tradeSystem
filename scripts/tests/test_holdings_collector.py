"""HoldingsCollector 单元测试：load / save / add / remove / collect_data / announcements / summary / enrich / info"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from collectors.holdings import HoldingsCollector, collect_info_for_stocks
from providers.base import DataResult


def _mock_registry(daily_map=None, ann_map=None, ma_map=None, sector_data=None, info_map=None):
    daily_map = daily_map or {}
    ann_map = ann_map or {}
    ma_map = ma_map or {}
    info_map = info_map or {}
    reg = MagicMock()

    def _call(method, *a, **kw):
        if method == "get_stock_daily":
            code = a[0]
            if code in daily_map:
                return DataResult(data=dict(daily_map[code]), source="mock")
            return DataResult(data=None, source="mock", error="no data")
        if method == "get_stock_announcements":
            code = a[0]
            if code in ann_map:
                return DataResult(data=ann_map[code], source="mock")
            return DataResult(data=None, source="mock", error="no ann")
        if method == "get_stock_ma":
            code = a[0]
            if code in ma_map:
                return DataResult(data=ma_map[code], source="mock")
            return DataResult(data=None, source="mock", error="no ma")
        if method == "get_sector_rankings":
            if sector_data is not None:
                return DataResult(data=sector_data, source="mock")
            return DataResult(data=None, source="mock", error="no sector")
        if method in ("get_stock_news", "get_investor_qa", "get_research_reports"):
            code = a[0]
            key = f"{method}:{code}"
            if key in info_map:
                return DataResult(data=info_map[key], source="mock")
            return DataResult(data=None, source="mock", error="no info")
        return DataResult(data=None, source="mock", error="unsupported")

    reg.call.side_effect = _call
    return reg


class TestLoadSave:
    def test_load_empty(self, tmp_path):
        hc = HoldingsCollector()
        hc.holdings_file = tmp_path / "h.yaml"
        result = hc.load()
        assert result == []

    def test_save_and_reload(self, tmp_path):
        hc = HoldingsCollector()
        hc.holdings_file = tmp_path / "h.yaml"
        hc._holdings = [{"code": "600000.SH", "name": "浦发银行", "shares": 100, "cost": 10.0, "sector": "银行"}]
        hc.save()

        hc2 = HoldingsCollector()
        hc2.holdings_file = tmp_path / "h.yaml"
        loaded = hc2.load()
        assert len(loaded) == 1
        assert loaded[0]["code"] == "600000.SH"


class TestAddRemove:
    def test_add_new(self, tmp_path):
        hc = HoldingsCollector()
        hc.holdings_file = tmp_path / "h.yaml"
        hc._holdings = []
        hc.add_stock({"code": "600000.SH", "name": "浦发银行", "shares": 100, "cost": 10.0})
        assert len(hc._holdings) == 1

    def test_add_existing_updates(self, tmp_path):
        hc = HoldingsCollector()
        hc.holdings_file = tmp_path / "h.yaml"
        hc._holdings = [{"code": "600000.SH", "name": "浦发银行", "shares": 100, "cost": 10.0}]
        hc.add_stock({"code": "600000.SH", "name": "浦发银行", "shares": 200, "cost": 11.0})
        assert len(hc._holdings) == 1
        assert hc._holdings[0]["shares"] == 200
        assert hc._holdings[0]["cost"] == 11.0

    def test_remove(self, tmp_path):
        hc = HoldingsCollector()
        hc.holdings_file = tmp_path / "h.yaml"
        hc._holdings = [{"code": "600000.SH", "name": "浦发"}, {"code": "300750.SZ", "name": "宁德"}]
        hc.remove_stock("600000.SH")
        assert len(hc._holdings) == 1
        assert hc._holdings[0]["code"] == "300750.SZ"

    def test_get_codes_and_names(self, tmp_path):
        hc = HoldingsCollector()
        hc.holdings_file = tmp_path / "h.yaml"
        hc._holdings = [{"code": "600000.SH", "name": "浦发"}, {"code": "300750.SZ", "name": "宁德"}]
        assert hc.get_codes() == ["600000.SH", "300750.SZ"]
        assert hc.get_names() == ["浦发", "宁德"]


class TestCollectHoldingsData:
    def test_no_registry(self, tmp_path):
        hc = HoldingsCollector(registry=None)
        hc.holdings_file = tmp_path / "h.yaml"
        hc._holdings = [{"code": "600000.SH", "name": "浦发"}]
        assert hc.collect_holdings_data("2026-03-30") == []

    def test_basic_collect(self, tmp_path):
        daily = {
            "600000.SH": {"code": "600000.SH", "close": 12.0, "change_pct": 2.0, "volume": 100000, "amount_billion": 1.5, "turnover_rate": 3.0, "amplitude_pct": 4.0},
        }
        reg = _mock_registry(daily_map=daily)
        hc = HoldingsCollector(registry=reg)
        hc.holdings_file = tmp_path / "h.yaml"
        hc._holdings = [{"code": "600000.SH", "name": "浦发银行", "shares": 100, "cost": 10.0, "sector": "银行"}]

        result = hc.collect_holdings_data("2026-03-30")
        assert len(result) == 1
        assert result[0]["close"] == 12.0
        assert result[0]["name"] == "浦发银行"
        assert result[0]["sector"] == "银行"
        assert result[0]["pnl_pct"] == 20.0  # (12-10)/10*100

    def test_failed_source(self, tmp_path):
        reg = _mock_registry()
        hc = HoldingsCollector(registry=reg)
        hc.holdings_file = tmp_path / "h.yaml"
        hc._holdings = [{"code": "999999.SH", "name": "不存在"}]

        result = hc.collect_holdings_data("2026-03-30")
        assert len(result) == 1
        assert "error" in result[0]

    def test_pnl_summary(self, tmp_path):
        daily = {
            "600000.SH": {"code": "600000.SH", "close": 12.0, "change_pct": 2.0, "volume": 100, "amount_billion": 1.5, "turnover_rate": 3.0, "amplitude_pct": 4.0},
            "300750.SZ": {"code": "300750.SZ", "close": 250.0, "change_pct": -1.0, "volume": 200, "amount_billion": 5.0, "turnover_rate": 2.0, "amplitude_pct": 3.0},
        }
        reg = _mock_registry(daily_map=daily)
        hc = HoldingsCollector(registry=reg)
        hc.holdings_file = tmp_path / "h.yaml"
        hc._holdings = [
            {"code": "600000.SH", "name": "浦发", "shares": 1000, "cost": 10.0, "sector": "银行"},
            {"code": "300750.SZ", "name": "宁德", "shares": 100, "cost": 200.0, "sector": "电池"},
        ]

        result = hc.collect_holdings_data("2026-03-30")
        summary = hc.compute_summary(result)
        assert summary["total_stocks"] == 2
        assert summary["total_market_value"] > 0
        assert "total_pnl_pct" in summary


class TestCollectAnnouncements:
    def test_no_registry(self, tmp_path):
        hc = HoldingsCollector(registry=None)
        hc.holdings_file = tmp_path / "h.yaml"
        hc._holdings = [{"code": "600000.SH", "name": "浦发"}]
        assert hc.collect_holdings_announcements("2026-03-28", "2026-03-30") == {}

    def test_basic_announcements(self, tmp_path):
        ann = {"600000.SH": [{"title": "公告A", "ann_date": "20260330"}]}
        reg = _mock_registry(ann_map=ann)
        hc = HoldingsCollector(registry=reg)
        hc.holdings_file = tmp_path / "h.yaml"
        hc._holdings = [{"code": "600000.SH", "name": "浦发"}]

        result = hc.collect_holdings_announcements("2026-03-28", "2026-03-30")
        assert "600000.SH" in result
        assert result["600000.SH"]["announcements"][0]["title"] == "公告A"


class TestEnrichWithMa:
    def test_adds_ma_fields(self, tmp_path):
        ma = {"600000.SH": {"ma5": 11.5, "ma10": 11.0, "ma20": 10.5, "volume_ma5": 90000.0}}
        reg = _mock_registry(ma_map=ma)
        hc = HoldingsCollector(registry=reg)
        hc.holdings_file = tmp_path / "h.yaml"
        data = [{"code": "600000.SH", "name": "浦发", "close": 12.0, "volume": 100000}]
        result = hc.enrich_with_ma(data, "2026-03-30")
        assert result[0]["ma5"] == 11.5
        assert result[0]["ma20"] == 10.5
        assert result[0]["volume_vs_ma5"] == "以上"

    def test_sector_rankings_called_once(self, tmp_path):
        ma = {
            "600000.SH": {"ma5": 11.5},
            "300750.SZ": {"ma5": 230.0},
        }
        sector_data = {
            "top": [{"name": "银行", "change_pct": 1.2}, {"name": "电池", "change_pct": -0.5}],
            "bottom": [],
        }
        reg = _mock_registry(ma_map=ma, sector_data=sector_data)
        hc = HoldingsCollector(registry=reg)
        hc.holdings_file = tmp_path / "h.yaml"
        data = [
            {"code": "600000.SH", "name": "浦发", "close": 12.0, "sector": "银行"},
            {"code": "300750.SZ", "name": "宁德", "close": 250.0, "sector": "电池"},
        ]
        result = hc.enrich_with_ma(data, "2026-03-30")
        assert result[0]["sector_change_pct"] == 1.2
        assert result[1]["sector_change_pct"] == -0.5
        sector_calls = [c for c in reg.call.call_args_list if c[0][0] == "get_sector_rankings"]
        assert len(sector_calls) == 1

    def test_skips_error_items(self, tmp_path):
        reg = _mock_registry()
        hc = HoldingsCollector(registry=reg)
        hc.holdings_file = tmp_path / "h.yaml"
        data = [{"code": "999.SH", "name": "ERR", "error": "no data"}]
        result = hc.enrich_with_ma(data, "2026-03-30")
        assert "ma5" not in result[0]

    def test_no_registry(self, tmp_path):
        hc = HoldingsCollector(registry=None)
        hc.holdings_file = tmp_path / "h.yaml"
        data = [{"code": "600000.SH", "name": "浦发"}]
        assert hc.enrich_with_ma(data, "2026-03-30") is data


class TestCollectInfoForStocks:
    def test_basic(self):
        info = {
            "get_stock_news:600519.SH": [{"title": "新闻1", "time": "09:00"}],
            "get_investor_qa:600519.SH": [{"question": "Q1", "answer": "A1", "date": "2026-03-25"}],
            "get_research_reports:600519.SH": [{"institution": "中信", "rating": "买入", "title": "报告", "date": "2026-03-20"}],
        }
        reg = _mock_registry(info_map=info)
        result = collect_info_for_stocks(reg, [("600519.SH", "贵州茅台")], "2026-03-27")
        assert "600519.SH" in result
        assert result["600519.SH"]["news"][0]["title"] == "新闻1"
        assert result["600519.SH"]["investor_qa"][0]["question"] == "Q1"
        assert result["600519.SH"]["research_reports"][0]["institution"] == "中信"

    def test_empty_stock_skipped(self):
        reg = _mock_registry()
        result = collect_info_for_stocks(reg, [("", "空"), ("  ", "空白")], "2026-03-27")
        assert result == {}

    def test_no_registry(self):
        result = collect_info_for_stocks(None, [("600519.SH", "茅台")], "2026-03-27")
        assert result == {}

    def test_stock_with_no_info_excluded(self):
        reg = _mock_registry(info_map={})
        result = collect_info_for_stocks(reg, [("600519.SH", "茅台")], "2026-03-27")
        assert result == {}
