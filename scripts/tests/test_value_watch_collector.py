"""value-watch collector：sw_daily 直连 + get_stock_daily_range + 持仓装载。

fail-closed 契约：空返回/异常 → None；恰 2000 行疑似镜像静默截断 → raise；
持仓身份缺失 → insufficient_identity 标记；代码统一 canonical ts_code。
"""
from __future__ import annotations

import unittest.mock as mock

import pandas as pd
import pytest

from db import queries as Q
from db.connection import get_connection
from db.schema import init_schema
from services.value_watch import collector


# ── fetch_sw_index_series ──────────────────────────────────────

def _provider_with(df):
    p = mock.MagicMock()
    p.pro.sw_daily.return_value = df
    return p


def test_sw_series_normalized_ascending():
    df = pd.DataFrame({"trade_date": ["20260721", "20260720"], "close": [110.0, 100.0]})
    out = collector.fetch_sw_index_series(_provider_with(df), "801780.SI",
                                          "2026-07-01", "2026-07-21")
    assert out == [{"date": "2026-07-20", "close": 100.0},
                   {"date": "2026-07-21", "close": 110.0}]


def test_sw_series_empty_or_error_returns_none():
    assert collector.fetch_sw_index_series(_provider_with(pd.DataFrame()), "X", "a", "b") is None
    p = mock.MagicMock()
    p.pro.sw_daily.side_effect = RuntimeError("down")
    assert collector.fetch_sw_index_series(p, "X", "a", "b") is None


def test_sw_series_exact_2000_rows_raises():
    df = pd.DataFrame({"trade_date": [f"2026{i:04d}" for i in range(2000)],
                       "close": [1.0] * 2000})
    with pytest.raises(RuntimeError):
        collector.fetch_sw_index_series(_provider_with(df), "X", "a", "b")


def test_sw_series_nan_close_dropped():
    df = pd.DataFrame({"trade_date": ["20260720", "20260721"], "close": [float("nan"), 110.0]})
    out = collector.fetch_sw_index_series(_provider_with(df), "X", "a", "b")
    assert out == [{"date": "2026-07-21", "close": 110.0}]


# ── fetch_stock_series ─────────────────────────────────────────

class _Registry:
    def __init__(self, data, success=True, error=""):
        self._r = mock.MagicMock(success=success, data=data, error=error)

    def call(self, cap, *a, **k):
        assert cap == "get_stock_daily_range"
        return self._r


def test_stock_series_maps_fields():
    data = [{"trade_date": "2026-07-20", "close": 9.0, "vol": 100.0},
            {"trade_date": "2026-07-21", "close": 9.5, "vol": None}]
    out = collector.fetch_stock_series(_Registry(data), "601939.SH", "a", "b")
    assert out == [{"date": "2026-07-20", "close": 9.0, "volume": 100.0},
                   {"date": "2026-07-21", "close": 9.5, "volume": None}]


def test_stock_series_failure_returns_none():
    assert collector.fetch_stock_series(_Registry(None, success=False, error="x"),
                                        "601939.SH", "a", "b") is None
    assert collector.fetch_stock_series(_Registry([]), "601939.SH", "a", "b") is None


def test_stock_series_rows_missing_close_dropped():
    data = [{"trade_date": "2026-07-20", "close": None, "vol": 1.0},
            {"trade_date": "2026-07-21", "close": 9.5, "vol": 1.0}]
    out = collector.fetch_stock_series(_Registry(data), "601939.SH", "a", "b")
    assert [r["date"] for r in out] == ["2026-07-21"]


# ── load_ladder_positions ──────────────────────────────────────

@pytest.fixture
def conn(tmp_path):
    c = get_connection(tmp_path / "vw_col.db")
    init_schema(c)
    yield c
    c.close()


def test_load_positions_canonical_key_and_identity(conn):
    Q.upsert_holding(conn, stock_code="600900", stock_name="长江电力",   # 无后缀
                     entry_price=25.0, entry_date="2026-06-01",
                     status="active", input_by="manual", thesis_id=7)
    Q.upsert_holding(conn, stock_code="601939.SH", stock_name="建设银行",  # 缺 entry_date
                     entry_price=9.0, status="active", input_by="manual")
    conn.execute("UPDATE holdings SET entry_date=NULL WHERE stock_code='601939.SH'")
    conn.commit()
    Q.upsert_holding(conn, stock_code="600519.SH", stock_name="贵州茅台",  # 不在清单
                     entry_price=1500.0, entry_date="2026-06-01",
                     status="active", input_by="manual")

    rows = collector.load_ladder_positions(conn)
    by_code = {r["code"]: r for r in rows}
    assert set(by_code) == {"600900.SH", "601939.SH"}   # 茅台不在 LADDER_CODES
    cy = by_code["600900.SH"]
    # 键 = canonical:holding_id(id 周期唯一;entry_date 可修正故不进键,收尾门 med)
    assert cy["position_key"] == f"600900.SH:{cy['holding_id']}"
    assert cy["insufficient_identity"] is False
    assert cy["thesis_id"] == 7
    ccb = by_code["601939.SH"]
    assert ccb["insufficient_identity"] is True           # 缺 entry_date
    assert ccb["position_key"] is None


def test_load_positions_thesis_backfill_does_not_change_key(conn):
    """spec 回归:补录 thesis_id 不换键不重推(thesis_id 仅报告展示,不进键;
    upsert 复用同 active 行 → holding_id 不变 → 键稳定)。"""
    Q.upsert_holding(conn, stock_code="601398.SH", stock_name="工商银行",
                     entry_price=5.0, entry_date="2026-06-01",
                     status="active", input_by="manual")
    k1 = collector.load_ladder_positions(conn)[0]["position_key"]
    Q.upsert_holding(conn, stock_code="601398.SH", stock_name="工商银行",
                     status="active", input_by="manual", thesis_id=42)
    k2 = collector.load_ladder_positions(conn)[0]["position_key"]
    assert k1 == k2 and k1.startswith("601398.SH:")


def test_entry_date_correction_does_not_change_key(conn):
    """收尾门 med:entry_date 经 PUT/CLI 修正 → 键不变(不重推旧档位),仅档位窗口重算。"""
    Q.upsert_holding(conn, stock_code="601988.SH", stock_name="中国银行",
                     entry_price=5.0, entry_date="2026-06-01",
                     status="active", input_by="manual")
    k1 = collector.load_ladder_positions(conn)[0]["position_key"]
    Q.upsert_holding(conn, stock_code="601988.SH", stock_name="中国银行",
                     entry_date="2026-06-02", status="active", input_by="manual")
    k2 = collector.load_ladder_positions(conn)[0]["position_key"]
    assert k1 == k2


def test_same_day_close_reopen_gets_new_key(conn):
    """门2 G3 round2 high:同日平仓再开仓 → 新行新 holding_id → 新键,
    新持仓的阶梯提醒不被旧持仓账本静默压制。"""
    Q.upsert_holding(conn, stock_code="601939.SH", stock_name="建设银行",
                     entry_price=9.0, entry_date="2026-07-22",
                     status="active", input_by="manual")
    k1 = collector.load_ladder_positions(conn)[0]["position_key"]
    Q.close_active_holdings_by_code(conn, "601939.SH", input_by="manual")
    Q.upsert_holding(conn, stock_code="601939.SH", stock_name="建设银行",
                     entry_price=9.2, entry_date="2026-07-22",   # 同日重开同 entry_date
                     status="active", input_by="manual")
    rows = collector.load_ladder_positions(conn)
    assert len(rows) == 1   # closed 行不进池
    k2 = rows[0]["position_key"]
    assert k1 != k2


def test_load_positions_entry_price_change_does_not_change_key(conn):
    """spec 回归:成本修正不换键(entry_price 不进键,只重算档位)。"""
    Q.upsert_holding(conn, stock_code="601288.SH", stock_name="农业银行",
                     entry_price=3.0, entry_date="2026-06-01",
                     status="active", input_by="manual")
    k1 = collector.load_ladder_positions(conn)[0]["position_key"]
    Q.upsert_holding(conn, stock_code="601288.SH", stock_name="农业银行",
                     entry_price=3.5, status="active", input_by="manual")
    k2 = collector.load_ladder_positions(conn)[0]["position_key"]
    assert k1 == k2
