"""volume_concentration collector 单测:三级降级打标 / read-through / record 组装。"""
from __future__ import annotations

import json
import sqlite3

from db.schema import init_schema
from providers.base import DataResult
from services.volume_concentration import collector


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


class _FakeRegistry:
    """按方法名返回预置 DataResult;记录调用。"""

    def __init__(self, responses: dict):
        self.responses = responses
        self.calls: list = []

    def call(self, method_name: str, *args, **kwargs):
        self.calls.append((method_name, args))
        resp = self.responses.get(method_name)
        if callable(resp):
            return resp(*args, **kwargs)
        if resp is None:
            return DataResult(data=None, source="stub", error=f"no_stub: {method_name}")
        return resp


def test_label_three_level_fallback():
    """三级降级:① 申万命中→sw_l2+name ② 缺成分→stock_basic 兜 name+未分类 ③ 仍缺→name 留空+未分类。"""
    stocks = [
        {"code": "300750.SZ", "name": "", "amount_billion": 50.0},   # 申万命中
        {"code": "688635.SH", "name": "", "amount_billion": 30.0},   # 缺成分,stock_basic 有 name
        {"code": "999999.SZ", "name": "", "amount_billion": 10.0},   # 缺成分 + stock_basic 也无
    ]
    sw_map = {"300750.SZ": {"name": "宁德时代", "sw_l2": "电池"}}
    registry = _FakeRegistry({
        "get_stock_sw_industry_map": DataResult(data=sw_map, source="tushare:index_member_all"),
        "get_stock_basic_batch": DataResult(
            data=[{"ts_code": "688635.SH", "name": "C长进"}], source="tushare:stock_basic"
        ),
    })

    result = collector.label_industries(stocks, registry)
    labeled = {s["code"]: s for s in result["stocks"]}

    # Level 1:申万命中
    assert labeled["300750.SZ"]["industry"] == "电池"
    assert labeled["300750.SZ"]["name"] == "宁德时代"  # name 回填
    # Level 2:缺成分,stock_basic 兜 name
    assert labeled["688635.SH"]["industry"] == "未分类"
    assert labeled["688635.SH"]["name"] == "C长进"
    # Level 3:缺成分 + stock_basic 无 → name 留空
    assert labeled["999999.SZ"]["industry"] == "未分类"
    assert labeled["999999.SZ"]["name"] == ""

    # stock_basic 只对缺成分的批量查一次
    basic_calls = [c for c in registry.calls if c[0] == "get_stock_basic_batch"]
    assert len(basic_calls) == 1
    assert set(basic_calls[0][1][0]) == {"688635.SH", "999999.SZ"}


def test_label_sw_source_failure_all_unclassified():
    """申万源整挂(success=False)→ 全部「未分类」,name 退 stock_basic,source 标失败。"""
    stocks = [{"code": "300750.SZ", "name": "", "amount_billion": 50.0}]
    registry = _FakeRegistry({
        "get_stock_sw_industry_map": DataResult(data=None, source="tushare", error="index_member_all_failed: boom"),
        "get_stock_basic_batch": DataResult(
            data=[{"ts_code": "300750.SZ", "name": "宁德时代"}], source="tushare:stock_basic"
        ),
    })

    result = collector.label_industries(stocks, registry)

    assert result["stocks"][0]["industry"] == "未分类"
    assert result["stocks"][0]["name"] == "宁德时代"  # stock_basic 兜底
    assert "sw_failed" in result["industry_source"]


def test_load_top20_read_through_hit_no_refetch():
    """daily_market 已落库 → 读库优先解析 JSON,零重拉。"""
    conn = _conn()
    stocks = [{"rank": 1, "code": "300750.SZ", "amount_billion": 50.0}]
    conn.execute(
        "INSERT INTO daily_market (date, top_volume_stocks) VALUES (?, ?)",
        ("2026-05-29", json.dumps(stocks)),
    )
    conn.commit()
    registry = _FakeRegistry({})  # 无 get_top_volume_stocks 桩

    result = collector.load_top20(conn, registry, "2026-05-29")

    assert result == stocks
    assert not any(c[0] == "get_top_volume_stocks" for c in registry.calls)  # 零重拉


def test_load_top20_refetch_when_db_missing():
    """daily_market 无当日数据 → 重拉 get_top_volume_stocks 自愈。"""
    conn = _conn()
    refetched = [{"rank": 1, "code": "600519.SH", "amount_billion": 80.0}]
    registry = _FakeRegistry({
        "get_top_volume_stocks": DataResult(data=refetched, source="tushare:daily"),
    })

    result = collector.load_top20(conn, registry, "2026-05-29")

    assert result == refetched
    assert any(c[0] == "get_top_volume_stocks" for c in registry.calls)  # 触发重拉


def test_build_record_assembles_full_snapshot():
    """编排:read-through→打标→market_total 单拉→聚合→组装完整 record。"""
    conn = _conn()
    top20 = [
        {"rank": 1, "code": "300750.SZ", "name": "", "amount_billion": 60.0},
        {"rank": 2, "code": "688635.SH", "name": "", "amount_billion": 40.0},
    ]
    conn.execute(
        "INSERT INTO daily_market (date, top_volume_stocks) VALUES (?, ?)",
        ("2026-05-29", json.dumps(top20)),
    )
    conn.commit()
    registry = _FakeRegistry({
        "get_stock_sw_industry_map": DataResult(
            data={"300750.SZ": {"name": "宁德时代", "sw_l2": "电池"}}, source="tushare:index_member_all"
        ),
        "get_stock_basic_batch": DataResult(
            data=[{"ts_code": "688635.SH", "name": "C长进"}], source="tushare:stock_basic"
        ),
        "get_market_volume": DataResult(data={"total_billion": 9800.0}, source="tushare:index_daily"),
    })

    record = collector.build_record(conn, registry, "2026-05-29")

    assert record["date"] == "2026-05-29"
    assert record["top_n"] == 2
    assert record["total_amount_billion"] == 100.0
    assert record["market_total_billion"] == 9800.0
    by_code = {s["code"]: s for s in record["stocks"]}
    assert by_code["300750.SZ"]["industry"] == "电池"
    assert by_code["688635.SH"]["industry"] == "未分类"
    assert by_code["688635.SH"]["name"] == "C长进"
    inds = {s["industry"] for s in record["sector_summary"]}
    assert "电池" in inds and "未分类" in inds
    assert record["source"]["industry_coverage"] == 0.5  # 2 只 1 只归类
    assert record["source"]["market_total_source"] == "tushare:index_daily"


def test_build_record_none_when_no_data():
    """读库空 + 重拉也空 → 返 None(无数据不写库)。"""
    conn = _conn()
    registry = _FakeRegistry({
        "get_top_volume_stocks": DataResult(data=[], source="tushare:daily"),
    })

    assert collector.build_record(conn, registry, "2026-05-29") is None


def test_build_record_market_total_none_on_failure():
    """market_total 取数失败 → market_total_billion=None,不阻断(占比报告层略过)。"""
    conn = _conn()
    conn.execute(
        "INSERT INTO daily_market (date, top_volume_stocks) VALUES (?, ?)",
        ("2026-05-29", json.dumps([{"code": "300750.SZ", "amount_billion": 60.0}])),
    )
    conn.commit()
    registry = _FakeRegistry({
        "get_stock_sw_industry_map": DataResult(data={}, source="tushare:index_member_all"),
        "get_stock_basic_batch": DataResult(data=[], source="tushare:stock_basic"),
        "get_market_volume": DataResult(data=None, source="tushare", error="无成交额数据"),
    })

    record = collector.build_record(conn, registry, "2026-05-29")

    assert record["market_total_billion"] is None
    assert record["source"]["market_total_source"] is None
