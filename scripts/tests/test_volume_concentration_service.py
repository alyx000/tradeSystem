"""volume_concentration service 编排单测(集成 collector+repo+trend+formatter,mock 外网)。"""
from __future__ import annotations

import json
import sqlite3

from db.schema import init_schema
from providers.base import DataResult
from services.volume_concentration import repo, service


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


class _FakeRegistry:
    def __init__(self, responses: dict):
        self.responses = responses

    def call(self, method_name: str, *args, **kwargs):
        resp = self.responses.get(method_name)
        if resp is None:
            return DataResult(data=None, source="stub", error=f"no_stub: {method_name}")
        return resp


def _full_registry():
    return _FakeRegistry({
        "get_stock_sw_industry_map": DataResult(
            data={"300750.SZ": {"name": "宁德时代", "sw_l2": "电池"}}, source="tushare:index_member_all"
        ),
        "get_stock_basic_batch": DataResult(data=[], source="tushare:stock_basic"),
        "get_market_volume": DataResult(data={"total_billion": 9000.0}, source="tushare:index_daily"),
    })


def _seed_daily_market(conn, date, stocks):
    conn.execute(
        "INSERT INTO daily_market (date, top_volume_stocks) VALUES (?, ?)",
        (date, json.dumps(stocks)),
    )
    conn.commit()


def test_run_daily_builds_saves_renders():
    conn = _conn()
    _seed_daily_market(conn, "2026-05-29", [{"code": "300750.SZ", "name": "", "amount_billion": 60.0}])

    md = service.run_daily(conn, _full_registry(), "2026-05-29")

    assert md is not None
    assert "2026-05-29" in md
    assert "电池" in md
    # 已落库
    assert repo.get_concentration(conn, "2026-05-29") is not None


def test_run_daily_none_and_no_write_when_no_data():
    conn = _conn()
    registry = _FakeRegistry({"get_top_volume_stocks": DataResult(data=[], source="tushare:daily")})

    md = service.run_daily(conn, registry, "2026-05-29")

    assert md is None  # 无数据不渲染
    assert repo.get_concentration(conn, "2026-05-29") is None  # 不写库


def test_run_daily_dry_run_does_not_persist():
    """dry-run 预览:仍渲染,但不写库(无副作用)。"""
    conn = _conn()
    _seed_daily_market(conn, "2026-05-29", [{"code": "300750.SZ", "name": "", "amount_billion": 60.0}])

    md = service.run_daily(conn, _full_registry(), "2026-05-29", persist=False)

    assert md is not None
    assert "2026-05-29" in md
    assert repo.get_concentration(conn, "2026-05-29") is None  # 未落库


def test_run_trend_reads_recent_and_renders():
    conn = _conn()
    for d, total in [("2026-05-28", 100.0), ("2026-05-29", 120.0)]:
        repo.save_concentration(conn, {
            "date": d, "top_n": 20, "total_amount_billion": total,
            "stocks": [], "sector_summary": [{"industry": "电池", "count": 1,
                       "amount_billion": total, "share_in_top_n": 1.0, "codes": []}],
            "source": {"industry_coverage": 1.0},
        })

    md = service.run_trend(conn, "2026-05-29", days=30)

    assert "集中度 CR3" in md   # 稳定渲染元素(热度趋势块在无显著变动时会省略)
    assert "2026-05-29" in md


def test_run_trend_empty_message():
    conn = _conn()
    md = service.run_trend(conn, "2026-05-29", days=30)
    assert "暂无" in md


def test_run_daily_refetch_bypasses_stale_db():
    """run_daily(refetch=True) → 即使库里有陈旧 top_volume_stocks,也用重拉数据落库(回填历史用)。"""
    conn = _conn()
    _seed_daily_market(conn, "2026-05-29", [{"code": "STALE.SZ", "name": "", "amount_billion": 9999.0}])
    registry = _FakeRegistry({
        "get_top_volume_stocks": DataResult(
            data=[{"code": "300750.SZ", "name": "", "amount_billion": 60.0}], source="tushare:daily"),
        "get_stock_sw_industry_map": DataResult(
            data={"300750.SZ": {"name": "宁德时代", "sw_l2": "电池"}}, source="tushare:index_member_all"),
        "get_stock_basic_batch": DataResult(data=[], source="tushare:stock_basic"),
        "get_market_volume": DataResult(data={"total_billion": 9000.0}, source="tushare:index_daily"),
    })

    md = service.run_daily(conn, registry, "2026-05-29", refetch=True)

    saved = repo.get_concentration(conn, "2026-05-29")
    assert saved["total_amount_billion"] == 60.0   # 重拉值,非陈旧 9999
    assert "电池" in md


def test_run_daily_dry_run_with_refetch_composes():
    """persist=False + refetch=True 组合(回填预览):不落库 + 用重拉数据(审查中-4)。"""
    conn = _conn()
    _seed_daily_market(conn, "2026-05-29", [{"code": "STALE.SZ", "name": "", "amount_billion": 9999.0}])
    registry = _FakeRegistry({
        "get_top_volume_stocks": DataResult(
            data=[{"code": "300750.SZ", "name": "", "amount_billion": 60.0}], source="tushare:daily"),
        "get_stock_sw_industry_map": DataResult(
            data={"300750.SZ": {"name": "宁德时代", "sw_l2": "电池"}}, source="tushare:index_member_all"),
        "get_stock_basic_batch": DataResult(data=[], source="tushare:stock_basic"),
        "get_market_volume": DataResult(data={"total_billion": 9000.0}, source="tushare:index_daily"),
    })

    md = service.run_daily(conn, registry, "2026-05-29", persist=False, refetch=True)

    assert md is not None
    assert "电池" in md                                          # 用重拉数据(非陈旧 STALE)
    assert repo.get_concentration(conn, "2026-05-29") is None   # 未落库


def test_run_daily_dry_run_caps_trend_window(tmp=None):
    """codex 中等:dry-run 拼入内存今日后,窗口须截到 trend_days,与真跑(先落库再 LIMIT)同窗。"""
    conn = _conn()
    for d, total in [("2026-05-26", 100.0), ("2026-05-27", 110.0), ("2026-05-28", 120.0)]:
        repo.save_concentration(conn, {
            "date": d, "top_n": 20, "total_amount_billion": total, "stocks": [],
            "sector_summary": [{"industry": "电池", "count": 1, "amount_billion": total,
                                "share_in_top_n": 1.0, "codes": []}],
            "source": {"industry_coverage": 1.0},
        })
    _seed_daily_market(conn, "2026-05-29", [{"code": "300750.SZ", "name": "", "amount_billion": 60.0}])

    md = service.run_daily(conn, _full_registry(), "2026-05-29", persist=False, trend_days=3)

    assert "近 3 交易日" in md       # 历史3 + 今日截到窗口 3
    assert "近 4 交易日" not in md   # 不溢出成 4
