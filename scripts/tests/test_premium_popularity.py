"""
PremiumCollector._collect_popularity_backfill 单元测试

验证三类人气股来源（A龙虎榜净买 / B连板股 / C成交额前10）
的筛选逻辑、去重合并与 T 日表现字段的计算。
"""
from typing import Optional
from unittest.mock import MagicMock

import pytest

from collectors.premium import PremiumCollector


# ──────────────────────────────────────────────────────────────────────────────
# Mock 工厂
# ──────────────────────────────────────────────────────────────────────────────

def _make_stock_daily_result(open_: float, close: float, pct_chg: float):
    r = MagicMock()
    r.success = True
    r.data = {"open": open_, "close": close, "pct_chg": pct_chg}
    return r


def _make_registry(daily_map: dict, top_volume_map: Optional[dict] = None):
    """daily_map: {code: (open, close, pct_chg)}, top_volume_map: {date: [items]}"""
    registry = MagicMock()
    top_volume_map = top_volume_map or {}

    def _call(method, *args, **kwargs):
        if method == "get_stock_daily":
            code = args[0]
            if code in daily_map:
                o, c, p = daily_map[code]
                return _make_stock_daily_result(o, c, p)
            r = MagicMock()
            r.success = False
            r.error = "no data"
            return r
        if method == "get_top_volume_stocks":
            date = args[0]
            r = MagicMock()
            data = top_volume_map.get(date)
            r.success = data is not None
            r.data = data
            r.error = "" if data is not None else "no top volume data"
            return r
        return MagicMock(success=False, error="unknown")

    registry.call = _call
    return registry


def _make_collector(daily_map: dict, top_volume_map: Optional[dict] = None) -> PremiumCollector:
    return PremiumCollector(_make_registry(daily_map, top_volume_map))


def _dragon_tiger_item(code, name, net_amount, close=10.0):
    return {"code": code, "name": name, "net_amount": net_amount, "close": close}


def _limit_up_item(code, name, close=10.0, limit_times=2):
    return {"code": code, "name": name, "close": close, "limit_times": limit_times}


def _volume_top_item(code, name, close=10.0, rank=1):
    return {"code": code, "name": name, "close": close, "rank": rank, "amount_billion": 50.0}


def _make_prev_data(
    *, dragon_tiger=None, limit_up_stocks=None, top_volume=None, include_top_volume=True
):
    """构造 T-1 的 prev_data 结构"""
    raw_data = {
        "dragon_tiger": {"data": dragon_tiger or []},
        "limit_up": {"stocks": limit_up_stocks or []},
    }
    if include_top_volume:
        raw_data["top_volume_stocks"] = top_volume or []
    return {"raw_data": raw_data}


# ──────────────────────────────────────────────────────────────────────────────
# 基础行为
# ──────────────────────────────────────────────────────────────────────────────

class TestPopularityBackfillBasic:
    def test_empty_sources_return_empty_list(self):
        collector = _make_collector({})
        prev_data = _make_prev_data()
        result = collector._collect_popularity_backfill(prev_data, "2026-04-01")
        assert result == []

    def test_returns_list(self):
        daily_map = {"000001.SZ": (11.0, 11.5, 5.0)}
        collector = _make_collector(daily_map)
        prev_data = _make_prev_data(
            dragon_tiger=[_dragon_tiger_item("000001.SZ", "平安银行", 1e6, 10.0)]
        )
        result = collector._collect_popularity_backfill(prev_data, "2026-04-01")
        assert isinstance(result, list)

    def test_entry_has_required_fields(self):
        daily_map = {"000001.SZ": (11.0, 11.5, 5.0)}
        collector = _make_collector(daily_map)
        prev_data = _make_prev_data(
            dragon_tiger=[_dragon_tiger_item("000001.SZ", "平安银行", 1e6, 10.0)]
        )
        result = collector._collect_popularity_backfill(prev_data, "2026-04-01")
        assert len(result) == 1
        entry = result[0]
        for field in ["code", "name", "source", "prev_close",
                      "t_open", "t_open_premium_pct",
                      "t_close", "t_close_change_pct",
                      "t_is_limit_up", "t_is_limit_down"]:
            assert field in entry, f"缺少字段: {field}"


# ──────────────────────────────────────────────────────────────────────────────
# 来源 A：龙虎榜净买
# ──────────────────────────────────────────────────────────────────────────────

class TestSourceDragonTiger:
    def test_net_buy_included(self):
        daily_map = {"000001.SZ": (11.0, 11.5, 5.0)}
        collector = _make_collector(daily_map)
        prev_data = _make_prev_data(
            dragon_tiger=[_dragon_tiger_item("000001.SZ", "平安银行", 5e6, 10.0)]
        )
        result = collector._collect_popularity_backfill(prev_data, "2026-04-01")
        assert len(result) == 1
        assert "dragon_tiger" in result[0]["source"]

    def test_net_sell_excluded(self):
        collector = _make_collector({"000001.SZ": (11.0, 11.5, 5.0)})
        prev_data = _make_prev_data(
            dragon_tiger=[_dragon_tiger_item("000001.SZ", "平安银行", -5e6, 10.0)]
        )
        result = collector._collect_popularity_backfill(prev_data, "2026-04-01")
        assert result == []

    def test_zero_net_excluded(self):
        collector = _make_collector({"000001.SZ": (11.0, 11.5, 5.0)})
        prev_data = _make_prev_data(
            dragon_tiger=[_dragon_tiger_item("000001.SZ", "平安银行", 0, 10.0)]
        )
        result = collector._collect_popularity_backfill(prev_data, "2026-04-01")
        assert result == []

    def test_empty_dragon_tiger_no_error(self):
        collector = _make_collector({})
        prev_data = _make_prev_data(dragon_tiger=[])
        result = collector._collect_popularity_backfill(prev_data, "2026-04-01")
        assert result == []


# ──────────────────────────────────────────────────────────────────────────────
# 来源 B：连板股（limit_times >= 2）
# ──────────────────────────────────────────────────────────────────────────────

class TestSourceConsecutive:
    def test_consecutive_2plus_included(self):
        daily_map = {"300750.SZ": (45.0, 44.0, 2.0)}
        collector = _make_collector(daily_map)
        prev_data = _make_prev_data(
            limit_up_stocks=[_limit_up_item("300750.SZ", "宁德时代", 40.0, limit_times=2)]
        )
        result = collector._collect_popularity_backfill(prev_data, "2026-04-01")
        assert len(result) == 1
        assert "consecutive" in result[0]["source"]

    def test_first_board_excluded(self):
        collector = _make_collector({"300750.SZ": (45.0, 44.0, 2.0)})
        prev_data = _make_prev_data(
            limit_up_stocks=[_limit_up_item("300750.SZ", "宁德时代", 40.0, limit_times=1)]
        )
        result = collector._collect_popularity_backfill(prev_data, "2026-04-01")
        assert result == []


# ──────────────────────────────────────────────────────────────────────────────
# 来源 C：成交额前10
# ──────────────────────────────────────────────────────────────────────────────

class TestSourceVolumeTop10:
    def test_top10_included(self):
        daily_map = {"600519.SH": (1900.0, 1920.0, 1.05)}
        collector = _make_collector(daily_map)
        top10 = [_volume_top_item("600519.SH", "贵州茅台", 1800.0, rank=1)]
        prev_data = _make_prev_data(top_volume=top10)
        result = collector._collect_popularity_backfill(prev_data, "2026-04-01")
        assert len(result) == 1
        assert "volume_top10" in result[0]["source"]

    def test_only_first_10_taken(self):
        """top_volume_stocks 超过10条时只取前10"""
        stocks = [
            _volume_top_item(f"00000{i}.SZ", f"股票{i}", 10.0, rank=i)
            for i in range(1, 16)
        ]
        daily_map = {s["code"]: (11.0, 11.5, 5.0) for s in stocks[:10]}
        collector = _make_collector(daily_map)
        prev_data = _make_prev_data(top_volume=stocks)
        result = collector._collect_popularity_backfill(prev_data, "2026-04-01")
        assert len(result) <= 10

    def test_fallback_fetch_when_prev_yaml_missing_top_volume(self):
        """历史 T-1 YAML 没有 top_volume_stocks 时，回退拉取 prev_date 的成交额前10"""
        code = "600519.SH"
        daily_map = {code: (1900.0, 1920.0, 1.05)}
        top_volume_map = {
            "2026-03-31": [_volume_top_item(code, "贵州茅台", 1800.0, rank=1)],
        }
        collector = _make_collector(daily_map, top_volume_map)
        prev_data = _make_prev_data(include_top_volume=False)
        result = collector._collect_popularity_backfill(
            prev_data, "2026-04-01", "2026-03-31"
        )
        assert len(result) == 1
        assert "volume_top10" in result[0]["source"]


# ──────────────────────────────────────────────────────────────────────────────
# 去重与多标签合并
# ──────────────────────────────────────────────────────────────────────────────

class TestDeduplication:
    def test_same_stock_multi_source_merged(self):
        """同一只股票同时出现在龙虎榜和连板，应合并为一条 entry，source 含两个标签"""
        code = "000001.SZ"
        daily_map = {code: (11.0, 11.5, 5.0)}
        collector = _make_collector(daily_map)
        prev_data = _make_prev_data(
            dragon_tiger=[_dragon_tiger_item(code, "平安银行", 1e6, 10.0)],
            limit_up_stocks=[_limit_up_item(code, "平安银行", 10.0, limit_times=2)],
        )
        result = collector._collect_popularity_backfill(prev_data, "2026-04-01")
        assert len(result) == 1
        assert "dragon_tiger" in result[0]["source"]
        assert "consecutive" in result[0]["source"]

    def test_triple_source_merged(self):
        """同一只股票同时出现在三个来源"""
        code = "600519.SH"
        daily_map = {code: (1900.0, 1920.0, 1.05)}
        collector = _make_collector(daily_map)
        prev_data = _make_prev_data(
            dragon_tiger=[_dragon_tiger_item(code, "茅台", 1e8, 1800.0)],
            limit_up_stocks=[_limit_up_item(code, "茅台", 1800.0, limit_times=3)],
            top_volume=[_volume_top_item(code, "茅台", 1800.0, rank=1)],
        )
        result = collector._collect_popularity_backfill(prev_data, "2026-04-01")
        assert len(result) == 1
        assert len(result[0]["source"]) == 3


# ──────────────────────────────────────────────────────────────────────────────
# 字段计算
# ──────────────────────────────────────────────────────────────────────────────

class TestFieldCalculation:
    def test_open_premium_calculation(self):
        """t_open_premium_pct = (t_open - prev_close) / prev_close * 100"""
        code = "000001.SZ"
        daily_map = {code: (11.0, 10.8, 4.0)}
        collector = _make_collector(daily_map)
        prev_data = _make_prev_data(
            dragon_tiger=[_dragon_tiger_item(code, "平安银行", 1e6, 10.0)]
        )
        result = collector._collect_popularity_backfill(prev_data, "2026-04-01")
        assert len(result) == 1
        prem = result[0]["t_open_premium_pct"]
        assert prem == pytest.approx((11.0 - 10.0) / 10.0 * 100, abs=0.01)

    def test_close_change_calculation(self):
        """t_close_change_pct = (t_close - prev_close) / prev_close * 100"""
        code = "000001.SZ"
        daily_map = {code: (11.0, 10.5, 5.0)}
        collector = _make_collector(daily_map)
        prev_data = _make_prev_data(
            dragon_tiger=[_dragon_tiger_item(code, "平安银行", 1e6, 10.0)]
        )
        result = collector._collect_popularity_backfill(prev_data, "2026-04-01")
        assert len(result) == 1
        assert result[0]["t_close_change_pct"] == pytest.approx(5.0, abs=0.01)

    def test_limit_up_flag(self):
        code = "000001.SZ"
        daily_map = {code: (11.0, 11.0, 10.0)}
        collector = _make_collector(daily_map)
        prev_data = _make_prev_data(
            dragon_tiger=[_dragon_tiger_item(code, "平安银行", 1e6, 10.0)]
        )
        result = collector._collect_popularity_backfill(prev_data, "2026-04-01")
        assert result[0]["t_is_limit_up"] is True
        assert result[0]["t_is_limit_down"] is False

    def test_limit_down_flag(self):
        code = "000001.SZ"
        daily_map = {code: (9.0, 9.0, -10.0)}
        collector = _make_collector(daily_map)
        prev_data = _make_prev_data(
            dragon_tiger=[_dragon_tiger_item(code, "平安银行", 1e6, 10.0)]
        )
        result = collector._collect_popularity_backfill(prev_data, "2026-04-01")
        assert result[0]["t_is_limit_down"] is True
        assert result[0]["t_is_limit_up"] is False

    def test_api_failure_skips_stock(self):
        """个股 T 日行情获取失败时，该股被跳过，不报异常"""
        collector = _make_collector({})
        prev_data = _make_prev_data(
            dragon_tiger=[_dragon_tiger_item("999999.SZ", "虚构股", 1e6, 10.0)]
        )
        result = collector._collect_popularity_backfill(prev_data, "2026-04-01")
        assert result == []

    def test_t_close_zero_skips_stock(self):
        """T 日收盘价为 0（数据获取失败/停牌）时，该股应被跳过而非产生脏数据"""
        code = "000001.SZ"
        daily_map = {code: (11.0, 0, 0.0)}  # close=0 表示数据失败
        collector = _make_collector(daily_map)
        prev_data = _make_prev_data(
            dragon_tiger=[_dragon_tiger_item(code, "平安银行", 1e6, 10.0)]
        )
        result = collector._collect_popularity_backfill(prev_data, "2026-04-01")
        assert result == [], "收盘价为0的条目应被跳过"

    def test_t_open_zero_premium_is_none(self):
        """T 日开盘价为 0 时，t_open 和 t_open_premium_pct 均应为 None，但条目保留"""
        code = "000001.SZ"
        daily_map = {code: (0, 10.5, 5.0)}  # open=0，close 有效
        collector = _make_collector(daily_map)
        prev_data = _make_prev_data(
            dragon_tiger=[_dragon_tiger_item(code, "平安银行", 1e6, 10.0)]
        )
        result = collector._collect_popularity_backfill(prev_data, "2026-04-01")
        assert len(result) == 1, "close 有效时条目应保留"
        assert result[0]["t_open"] is None
        assert result[0]["t_open_premium_pct"] is None
        assert result[0]["t_close"] == pytest.approx(10.5)

    def test_string_zero_prices_treated_as_missing(self):
        """字符串 '0' / '0.0' 也应视为无效价格，而不是 float 后继续参与计算"""
        code = "000001.SZ"
        daily_map = {code: ("0", "0.0", 0.0)}
        collector = _make_collector(daily_map)
        prev_data = _make_prev_data(
            dragon_tiger=[_dragon_tiger_item(code, "平安银行", 1e6, 10.0)]
        )
        result = collector._collect_popularity_backfill(prev_data, "2026-04-01")
        assert result == []

    def test_pct_chg_none_limit_flags_false(self):
        """pct_chg 缺失时，涨跌停标志均应为 False"""
        code = "000001.SZ"
        # pct_chg=None 的场景：mock 中返回 pct_chg=None
        registry = MagicMock()
        r = MagicMock()
        r.success = True
        r.data = {"open": 11.0, "close": 10.5, "pct_chg": None}
        registry.call = lambda method, *args, **kwargs: r
        collector = PremiumCollector(registry)
        prev_data = _make_prev_data(
            dragon_tiger=[_dragon_tiger_item(code, "平安银行", 1e6, 10.0)]
        )
        result = collector._collect_popularity_backfill(prev_data, "2026-04-01")
        assert len(result) == 1
        assert result[0]["t_is_limit_up"] is False
        assert result[0]["t_is_limit_down"] is False
