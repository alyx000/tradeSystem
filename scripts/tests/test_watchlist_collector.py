"""WatchlistCollector 核心方法单元测试：collect / _collect_tier1 / _collect_tier2 / _collect_tier3 / _check_alerts / blacklist"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from collectors.watchlist import WatchlistCollector, _get_limit_pct, ALERT_THRESHOLD
from providers.base import DataResult


# ---------------------------------------------------------------------------
# _get_limit_pct
# ---------------------------------------------------------------------------

class TestGetLimitPct:
    def test_main_board_sh(self):
        assert _get_limit_pct("600000.SH") == 10.0

    def test_main_board_sz(self):
        assert _get_limit_pct("000001.SZ") == 10.0

    def test_chinext(self):
        assert _get_limit_pct("300750.SZ") == 20.0
        assert _get_limit_pct("301001.SZ") == 20.0

    def test_star(self):
        assert _get_limit_pct("688041.SH") == 20.0
        assert _get_limit_pct("689009.SH") == 20.0

    def test_bse(self):
        assert _get_limit_pct("830799.BJ") == 30.0
        assert _get_limit_pct("430047.BJ") == 30.0

    def test_st(self):
        assert _get_limit_pct("600000.SH", "ST测试") == 5.0
        assert _get_limit_pct("300750.SZ", "*ST某某") == 5.0


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _make_wl_yaml(stocks_t1=None, stocks_t2=None, stocks_t3=None, blacklist=None):
    data = {
        "tier1_core": stocks_t1 or [],
        "tier2_watch": stocks_t2 or [],
        "tier3_sector_leaders": stocks_t3 or [],
        "blacklist": blacklist or [],
    }
    return data


def _mock_registry(daily_map: dict | None = None):
    """daily_map: {code: {close, change_pct, turnover_rate, ...}}"""
    daily_map = daily_map or {}
    reg = MagicMock()

    def _call(method, *a, **kw):
        if method == "get_stock_daily":
            code = a[0]
            if code in daily_map:
                return DataResult(data=daily_map[code], source="mock")
            return DataResult(data=None, source="mock", error="no data")
        return DataResult(data=None, source="mock", error="unsupported")

    reg.call.side_effect = _call
    return reg


# ---------------------------------------------------------------------------
# _check_alerts
# ---------------------------------------------------------------------------

class TestCheckAlerts:
    def test_limit_up_main_board(self):
        col = WatchlistCollector(None)
        stock = {"stock_code": "600000.SH", "stock_name": "浦发银行", "target_price": 0, "stop_loss": 0}
        alerts = col._check_alerts(stock, close=10.0, change_pct=9.95)
        assert len(alerts) == 1
        assert alerts[0]["type"] == "limit_up"

    def test_no_limit_up_at_9_for_main_board(self):
        col = WatchlistCollector(None)
        stock = {"stock_code": "600000.SH", "stock_name": "浦发银行", "target_price": 0, "stop_loss": 0}
        alerts = col._check_alerts(stock, close=10.0, change_pct=9.0)
        types = [a["type"] for a in alerts]
        assert "limit_up" not in types

    def test_limit_up_chinext_at_19_9(self):
        col = WatchlistCollector(None)
        stock = {"stock_code": "300750.SZ", "stock_name": "宁德时代", "target_price": 0, "stop_loss": 0}
        alerts = col._check_alerts(stock, close=250.0, change_pct=19.9)
        assert len(alerts) == 1
        assert alerts[0]["type"] == "limit_up"

    def test_no_limit_up_chinext_at_15(self):
        col = WatchlistCollector(None)
        stock = {"stock_code": "300750.SZ", "stock_name": "宁德时代", "target_price": 0, "stop_loss": 0}
        alerts = col._check_alerts(stock, close=250.0, change_pct=15.0)
        types = [a["type"] for a in alerts]
        assert "limit_up" not in types

    def test_limit_up_st(self):
        col = WatchlistCollector(None)
        stock = {"stock_code": "600000.SH", "stock_name": "*ST测试", "target_price": 0, "stop_loss": 0}
        alerts = col._check_alerts(stock, close=3.0, change_pct=4.96)
        assert len(alerts) == 1
        assert alerts[0]["type"] == "limit_up"

    def test_limit_down_bse(self):
        col = WatchlistCollector(None)
        stock = {"stock_code": "830799.BJ", "stock_name": "北交所票", "target_price": 0, "stop_loss": 0}
        alerts = col._check_alerts(stock, close=5.0, change_pct=-29.8)
        assert len(alerts) == 1
        assert alerts[0]["type"] == "limit_down"

    def test_near_target(self):
        col = WatchlistCollector(None)
        stock = {"stock_code": "600000.SH", "stock_name": "浦发银行", "target_price": 10.0, "stop_loss": 0}
        alerts = col._check_alerts(stock, close=9.85, change_pct=1.0)
        assert any(a["type"] == "near_target" for a in alerts)

    def test_no_near_target_far(self):
        col = WatchlistCollector(None)
        stock = {"stock_code": "600000.SH", "stock_name": "浦发银行", "target_price": 10.0, "stop_loss": 0}
        alerts = col._check_alerts(stock, close=9.0, change_pct=1.0)
        assert not any(a["type"] == "near_target" for a in alerts)

    def test_near_stop_loss(self):
        col = WatchlistCollector(None)
        stock = {"stock_code": "600000.SH", "stock_name": "浦发银行", "target_price": 0, "stop_loss": 8.0}
        alerts = col._check_alerts(stock, close=8.1, change_pct=-2.0)
        assert any(a["type"] == "near_stop_loss" for a in alerts)


# ---------------------------------------------------------------------------
# blacklist
# ---------------------------------------------------------------------------

class TestBlacklist:
    def test_blacklisted_stock_skipped(self, tmp_path, monkeypatch):
        wl_file = tmp_path / "watchlist.yaml"
        data = _make_wl_yaml(
            stocks_t1=[{"stock_code": "000001.SZ", "stock_name": "A"}],
            blacklist=[{"stock_code": "000001.SZ", "stock_name": "A", "reason": "banned", "until": "2099-01-01"}],
        )
        wl_file.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")
        monkeypatch.setattr("collectors.watchlist.WATCHLIST_FILE", wl_file)

        reg = _mock_registry({"000001.SZ": {"close": 10, "change_pct": 1, "turnover_rate": 2}})
        col = WatchlistCollector(reg)
        result = col.collect("2026-03-30")
        assert len(result["tier1"]) == 0

    def test_expired_blacklist_not_skipped(self, tmp_path, monkeypatch):
        wl_file = tmp_path / "watchlist.yaml"
        data = _make_wl_yaml(
            stocks_t1=[{"stock_code": "000001.SZ", "stock_name": "A"}],
            blacklist=[{"stock_code": "000001.SZ", "stock_name": "A", "reason": "old", "until": "2020-01-01"}],
        )
        wl_file.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")
        monkeypatch.setattr("collectors.watchlist.WATCHLIST_FILE", wl_file)

        reg = _mock_registry({"000001.SZ": {"close": 10, "change_pct": 1, "turnover_rate": 2}})
        col = WatchlistCollector(reg)
        result = col.collect("2026-03-30")
        assert len(result["tier1"]) == 1

    def test_blacklist_applies_to_tier2(self, tmp_path, monkeypatch):
        wl_file = tmp_path / "watchlist.yaml"
        data = _make_wl_yaml(
            stocks_t2=[{"stock_code": "300750.SZ", "stock_name": "B"}],
            blacklist=[{"stock_code": "300750.SZ", "stock_name": "B", "reason": "bad", "until": ""}],
        )
        wl_file.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")
        monkeypatch.setattr("collectors.watchlist.WATCHLIST_FILE", wl_file)

        reg = _mock_registry({"300750.SZ": {"close": 200, "change_pct": 3, "turnover_rate": 2}})
        col = WatchlistCollector(reg)
        result = col.collect("2026-03-30")
        assert len(result["tier2"]) == 0


# ---------------------------------------------------------------------------
# collect / tier1 / tier2 / tier3
# ---------------------------------------------------------------------------

class TestCollect:
    def test_tier1_updates_yaml_fields(self, tmp_path, monkeypatch):
        wl_file = tmp_path / "watchlist.yaml"
        data = _make_wl_yaml(
            stocks_t1=[{"stock_code": "600000.SH", "stock_name": "浦发银行", "target_price": 0, "stop_loss": 0}],
        )
        wl_file.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")
        monkeypatch.setattr("collectors.watchlist.WATCHLIST_FILE", wl_file)

        reg = _mock_registry({"600000.SH": {"close": 12.5, "change_pct": 4.0, "turnover_rate": 6.0}})
        col = WatchlistCollector(reg)
        result = col.collect("2026-03-30")

        assert len(result["tier1"]) == 1
        entry = result["tier1"][0]
        assert entry["close"] == 12.5
        assert entry["status"] == "走强"
        assert entry["vol_status"] == "放量"

        saved = yaml.safe_load(wl_file.read_text(encoding="utf-8"))
        assert saved["tier1_core"][0]["current_price"] == 12.5
        assert saved["tier1_core"][0]["current_status"] == "走强"

    def test_tier2_updates_yaml_fields(self, tmp_path, monkeypatch):
        wl_file = tmp_path / "watchlist.yaml"
        data = _make_wl_yaml(
            stocks_t2=[{"stock_code": "300750.SZ", "stock_name": "宁德时代"}],
        )
        wl_file.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")
        monkeypatch.setattr("collectors.watchlist.WATCHLIST_FILE", wl_file)

        reg = _mock_registry({"300750.SZ": {"close": 220.0, "change_pct": 2.5, "turnover_rate": 3.0}})
        col = WatchlistCollector(reg)
        result = col.collect("2026-03-30")

        assert len(result["tier2"]) == 1
        entry = result["tier2"][0]
        assert entry["close"] == 220.0

        saved = yaml.safe_load(wl_file.read_text(encoding="utf-8"))
        assert saved["tier2_watch"][0]["current_price"] == 220.0
        assert saved["tier2_watch"][0]["current_change_pct"] == 2.5

    def test_tier2_surge_alert_main_board(self, tmp_path, monkeypatch):
        """主板 10cm 异动阈值 = 10*0.5 = 5%，7% 触发"""
        wl_file = tmp_path / "watchlist.yaml"
        data = _make_wl_yaml(
            stocks_t2=[{"stock_code": "600000.SH", "stock_name": "浦发银行"}],
        )
        wl_file.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")
        monkeypatch.setattr("collectors.watchlist.WATCHLIST_FILE", wl_file)

        reg = _mock_registry({"600000.SH": {"close": 12.0, "change_pct": 7.0, "turnover_rate": 5.0}})
        col = WatchlistCollector(reg)
        result = col.collect("2026-03-30")
        assert len(result["alerts"]) == 1
        assert result["alerts"][0]["type"] == "tier2_surge"

    def test_tier2_no_surge_chinext_at_7(self, tmp_path, monkeypatch):
        """创业板 20cm 异动阈值 = 20*0.5 = 10%，7% 不触发"""
        wl_file = tmp_path / "watchlist.yaml"
        data = _make_wl_yaml(
            stocks_t2=[{"stock_code": "300750.SZ", "stock_name": "宁德时代"}],
        )
        wl_file.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")
        monkeypatch.setattr("collectors.watchlist.WATCHLIST_FILE", wl_file)

        reg = _mock_registry({"300750.SZ": {"close": 250.0, "change_pct": 7.0, "turnover_rate": 5.0}})
        col = WatchlistCollector(reg)
        result = col.collect("2026-03-30")
        assert len(result["alerts"]) == 0

    def test_tier2_surge_chinext_at_12(self, tmp_path, monkeypatch):
        """创业板 20cm 异动阈值 = 10%，12% 触发"""
        wl_file = tmp_path / "watchlist.yaml"
        data = _make_wl_yaml(
            stocks_t2=[{"stock_code": "300750.SZ", "stock_name": "宁德时代"}],
        )
        wl_file.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")
        monkeypatch.setattr("collectors.watchlist.WATCHLIST_FILE", wl_file)

        reg = _mock_registry({"300750.SZ": {"close": 250.0, "change_pct": 12.0, "turnover_rate": 5.0}})
        col = WatchlistCollector(reg)
        result = col.collect("2026-03-30")
        assert len(result["alerts"]) == 1
        assert result["alerts"][0]["type"] == "tier2_surge"

    def test_tier3_collects_leader(self, tmp_path, monkeypatch):
        wl_file = tmp_path / "watchlist.yaml"
        data = _make_wl_yaml(
            stocks_t3=[{
                "sector": "AI算力",
                "leader_stock": "688041.SH 海光信息",
                "leader_type": "趋势龙",
                "status": "active",
                "since_date": "2026-03-01",
                "successor": "",
            }],
        )
        wl_file.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")
        monkeypatch.setattr("collectors.watchlist.WATCHLIST_FILE", wl_file)

        reg = _mock_registry({"688041.SH": {"close": 100.0, "change_pct": 3.5, "turnover_rate": 4.0}})
        col = WatchlistCollector(reg)
        result = col.collect("2026-03-30")

        assert len(result["tier3"]) == 1
        t3 = result["tier3"][0]
        assert t3["code"] == "688041.SH"
        assert t3["sector"] == "AI算力"
        assert t3["close"] == 100.0

    def test_empty_codes_skipped(self, tmp_path, monkeypatch):
        wl_file = tmp_path / "watchlist.yaml"
        data = _make_wl_yaml(
            stocks_t1=[{"stock_code": "", "stock_name": ""}],
            stocks_t2=[{"stock_code": "  ", "stock_name": ""}],
        )
        wl_file.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")
        monkeypatch.setattr("collectors.watchlist.WATCHLIST_FILE", wl_file)

        reg = _mock_registry()
        col = WatchlistCollector(reg)
        result = col.collect("2026-03-30")
        assert result["tier1"] == []
        assert result["tier2"] == []

    def test_failed_data_source(self, tmp_path, monkeypatch):
        wl_file = tmp_path / "watchlist.yaml"
        data = _make_wl_yaml(
            stocks_t1=[{"stock_code": "999999.SH", "stock_name": "不存在"}],
        )
        wl_file.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")
        monkeypatch.setattr("collectors.watchlist.WATCHLIST_FILE", wl_file)

        reg = _mock_registry()
        col = WatchlistCollector(reg)
        result = col.collect("2026-03-30")
        assert len(result["tier1"]) == 1
        assert "error" in result["tier1"][0]


# ---------------------------------------------------------------------------
# format_report
# ---------------------------------------------------------------------------

class TestFormatReport:
    def test_includes_all_sections(self):
        col = WatchlistCollector(None)
        result = {
            "date": "2026-03-30",
            "tier1": [{"code": "600000.SH", "name": "浦发", "close": 10.0, "change_pct": 1.0, "status": "震荡", "vol_status": "正常", "alerts": []}],
            "tier2": [{"code": "300750.SZ", "name": "宁德", "close": 200.0, "change_pct": 6.0, "alert": "异动"}],
            "tier3": [{"code": "688041.SH", "name": "海光", "sector": "AI", "close": 100.0, "change_pct": 3.0}],
            "alerts": [{"message": "测试提醒"}],
        }
        report = col.format_report(result)
        assert "核心关注" in report
        assert "观察池" in report
        assert "板块龙头" in report
        assert "提醒" in report
        assert "浦发" in report
        assert "[AI]" in report
