"""build_holding_signals 涨跌停价来源单测。

验证盘前持仓信号的涨跌停价由「前一交易日 daily_basic 收盘价 × 板块比例」实时算出，
不再读取陈旧 stk_limit 快照；并验证派生风控旗不再因陈旧限价误报。
"""
from __future__ import annotations

import hashlib
import json

import pytest

from db.connection import get_connection
from db.schema import init_schema
from services.holding_signals import build_holding_signals


def _insert_raw_payload(conn, interface_name, biz_date, rows, status="success"):
    payload_json = json.dumps({"interface_name": interface_name, "rows": rows}, ensure_ascii=False)
    conn.execute(
        """
        INSERT INTO raw_interface_payloads
        (interface_name, provider, stage, biz_date, target_date, raw_table, dedupe_key,
         payload_json, payload_hash, row_count, status, params_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            interface_name, "tushare", "post_core", biz_date, biz_date,
            f"raw_{interface_name}", f"{interface_name}:{biz_date}",
            payload_json, hashlib.sha256(payload_json.encode()).hexdigest(),
            len(rows), status, "{}",
        ),
    )
    conn.commit()


@pytest.fixture
def conn(tmp_path):
    c = get_connection(tmp_path / "test.db")
    init_schema(c)
    yield c
    c.close()


class TestLimitPriceSource:
    def test_computed_from_daily_basic_not_stale_stk_limit(self, conn):
        # 陈旧 stk_limit（4-03 快照，错值）
        _insert_raw_payload(conn, "stk_limit", "2026-04-03",
                            [{"ts_code": "002008.SZ", "up_limit": 67.88, "down_limit": 55.54}])
        # 当前 daily_basic（前一交易日真实收盘）
        _insert_raw_payload(conn, "daily_basic", "2026-05-21",
                            [{"ts_code": "002008.SZ", "close": 146.28}])
        holdings = [{"stock_code": "002008.SZ", "stock_name": "大族激光",
                     "entry_price": 142.47, "current_price": 146.0, "sector": "PCB 设备"}]

        out = build_holding_signals(conn, "2026-05-22", holdings=holdings)
        snap = out["items"][0]["price_snapshot"]

        assert snap["up_limit"] == 160.91    # 146.28 × 1.1，非陈旧 67.88
        assert snap["down_limit"] == 131.65  # 146.28 × 0.9，非陈旧 55.54
        assert snap["pre_close"] == 146.28

    def test_no_false_near_limit_down_flag(self, conn):
        # 陈旧 stk_limit down_limit 偏高 → 旧逻辑会误报「临近跌停价」
        _insert_raw_payload(conn, "stk_limit", "2026-04-03",
                            [{"ts_code": "002027.SZ", "up_limit": 7.15, "down_limit": 5.85}])
        _insert_raw_payload(conn, "daily_basic", "2026-05-21",
                            [{"ts_code": "002027.SZ", "close": 6.0}])  # 正确 down_limit = 5.40
        holdings = [{"stock_code": "002027.SZ", "stock_name": "分众传媒",
                     "entry_price": 7.414, "current_price": 5.58}]

        out = build_holding_signals(conn, "2026-05-22", holdings=holdings)
        item = out["items"][0]
        labels = {f["label"] for f in item["risk_flags"]}

        assert item["price_snapshot"]["down_limit"] == 5.40
        assert "临近跌停价" not in labels  # 5.58 > 5.40×1.01，不应触发

    def test_uses_prev_trade_day_close_not_query_day(self, conn):
        # D 与 D-1 的 daily_basic 同时存在：限价基准必须取 D-1 收盘（前收），不能取 D 当日收盘。
        _insert_raw_payload(conn, "daily_basic", "2026-05-21",
                            [{"ts_code": "002008.SZ", "close": 146.28}])  # D-1，正确基准
        _insert_raw_payload(conn, "daily_basic", "2026-05-22",
                            [{"ts_code": "002008.SZ", "close": 200.0}])   # D 当日，不应被用作基准
        holdings = [{"stock_code": "002008.SZ", "stock_name": "大族激光",
                     "entry_price": 142.47, "current_price": 146.0}]

        out = build_holding_signals(conn, "2026-05-22", holdings=holdings)
        snap = out["items"][0]["price_snapshot"]

        assert snap["pre_close"] == 146.28      # 取 D-1，非 D 当日 200.0
        assert snap["up_limit"] == 160.91       # 146.28 × 1.1，而非 200 × 1.1 = 220.0

    def test_main_board_st_uses_authoritative_st_set(self, conn):
        # 名称不含 "ST" 但在 stock_st 权威名单内 → 主板按 5% 算限价（验证 st_set 接入）。
        _insert_raw_payload(conn, "daily_basic", "2026-05-21",
                            [{"ts_code": "600123.SH", "close": 10.0}])
        _insert_raw_payload(conn, "stock_st", "2026-05-22",
                            [{"ts_code": "600123.SH", "name": "某公司"}])
        holdings = [{"stock_code": "600123.SH", "stock_name": "某公司",
                     "entry_price": 12.0, "current_price": 10.0}]

        out = build_holding_signals(conn, "2026-05-22", holdings=holdings)
        snap = out["items"][0]["price_snapshot"]

        assert snap["up_limit"] == 10.5    # 10.0 × 1.05（主板 ST 5%）
        assert snap["down_limit"] == 9.5

    def test_main_board_st_name_fallback_when_st_set_empty(self, conn):
        # stock_st 未落库（st_set 为空）→ 退回名称启发式：主板名称含 *ST → 5%，不被静默判成 10%。
        _insert_raw_payload(conn, "daily_basic", "2026-05-21",
                            [{"ts_code": "600145.SH", "close": 10.0}])
        holdings = [{"stock_code": "600145.SH", "stock_name": "*ST美都",
                     "entry_price": 12.0, "current_price": 10.0}]

        out = build_holding_signals(conn, "2026-05-22", holdings=holdings)
        snap = out["items"][0]["price_snapshot"]

        assert snap["up_limit"] == 10.5    # 10.0 × 1.05（名称兜底识别 ST → 主板 5%）
        assert snap["down_limit"] == 9.5

    def test_etf_limit_is_none(self, conn):
        _insert_raw_payload(conn, "daily_basic", "2026-05-21",
                            [{"ts_code": "159516.SZ", "close": 1.28}])
        holdings = [{"stock_code": "159516.SZ", "stock_name": "半导设备",
                     "entry_price": 1.2776, "current_price": 1.3}]

        out = build_holding_signals(conn, "2026-05-22", holdings=holdings)
        snap = out["items"][0]["price_snapshot"]

        assert snap["up_limit"] is None
        assert snap["down_limit"] is None
