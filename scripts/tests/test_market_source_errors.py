"""盘前数据源错误文案归一化单测（collectors/market._normalize_source_error）。

网络/外部源不可达类失败（yfinance→Yahoo、akshare 远端瞬断、tushare 不支持国际指数）
应统一成可读提示，而非把 RemoteDisconnected / 'NoneType' subscriptable 等隐晦栈信息直接抛给用户。
"""
from __future__ import annotations

import pytest

import hashlib
import json

from collectors.market import _normalize_source_error, apply_margin_db_fallback
from db.connection import get_connection
from db.schema import init_schema

_UNREACHABLE_PREFIX = "数据源暂不可达"


class TestNormalizeSourceError:
    @pytest.mark.parametrize("raw", [
        "所有数据源均失败: akshare: ('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))",
        "所有数据源均失败: akshare: 'NoneType' object is not subscriptable",
        "所有数据源均失败: tushare: tushare 未支持的国际指数键: kospi; akshare: AkShare/yfinance 均未获取到亚太指数: kospi",
        "未找到 VIX 数据（yfinance 不可用）",
        "yfinance 不可用: No module named yfinance",
    ])
    def test_network_failures_normalized(self, raw):
        out = _normalize_source_error(raw)
        assert out.startswith(_UNREACHABLE_PREFIX)
        # 原始原因保留作后缀，便于排障
        assert ":" in out or "：" in out

    def test_non_network_error_unchanged(self):
        raw = "无融资融券汇总数据: 2026-05-21"
        assert _normalize_source_error(raw) == raw

    def test_none_and_empty_unchanged(self):
        assert _normalize_source_error(None) is None
        assert _normalize_source_error("") == ""


def _insert_margin_payload(conn, biz_date, summary):
    pj = json.dumps({"interface_name": "margin", "rows": [summary]}, ensure_ascii=False)
    conn.execute(
        """
        INSERT INTO raw_interface_payloads
        (interface_name, provider, stage, biz_date, target_date, raw_table, dedupe_key,
         payload_json, payload_hash, row_count, status, params_json)
        VALUES ('margin','tushare','post_extended',?,?,'raw_margin',?,?,?,1,'success','{}')
        """,
        (biz_date, biz_date, f"margin:{biz_date}", pj, hashlib.sha256(pj.encode()).hexdigest()),
    )
    conn.commit()


class TestMarginDbFallback:
    def _conn(self, tmp_path):
        c = get_connection(tmp_path / "m.db")
        init_schema(c)
        return c

    def test_fallback_fills_latest_with_as_of(self, tmp_path):
        conn = self._conn(tmp_path)
        _insert_margin_payload(conn, "2026-04-30", {
            "trade_date": "2026-04-30", "total_rzrqye_yi": 18500.0,
            "total_rzye_yi": 18000.0, "total_rqye_yi": 500.0, "exchanges": [],
        })
        market_data = {"margin_data": {"error": "无融资融券汇总数据: 2026-05-21"}}

        apply_margin_db_fallback(conn, market_data, "2026-05-22")
        md = market_data["margin_data"]

        assert "error" not in md
        assert md["as_of"] == "2026-04-30"
        assert md["stale"] is True
        assert md["total_rzrqye_yi"] == 18500.0
        conn.close()

    def test_no_fallback_when_margin_ok(self, tmp_path):
        conn = self._conn(tmp_path)
        _insert_margin_payload(conn, "2026-04-30", {"trade_date": "2026-04-30"})
        market_data = {"margin_data": {"trade_date": "2026-05-21", "total_rzrqye_yi": 19000.0}}

        apply_margin_db_fallback(conn, market_data, "2026-05-22")
        # 实时已成功 → 不动它，不打 stale 标
        assert market_data["margin_data"]["total_rzrqye_yi"] == 19000.0
        assert "stale" not in market_data["margin_data"]
        conn.close()

    def test_no_db_data_keeps_error(self, tmp_path):
        conn = self._conn(tmp_path)
        market_data = {"margin_data": {"error": "无融资融券汇总数据: 2026-05-21"}}

        apply_margin_db_fallback(conn, market_data, "2026-05-22")
        # DB 也没有 → 保留原 error
        assert "error" in market_data["margin_data"]
        conn.close()

    def test_fallback_uses_strictly_prior_not_query_day(self, tmp_path):
        # D 与 D-1 都在库：回退取严格早于 D 的那条（融资融券盘前语义是上一交易日）。
        conn = self._conn(tmp_path)
        _insert_margin_payload(conn, "2026-05-21", {"trade_date": "2026-05-21", "total_rzrqye_yi": 100.0, "exchanges": []})
        _insert_margin_payload(conn, "2026-05-22", {"trade_date": "2026-05-22", "total_rzrqye_yi": 999.0, "exchanges": []})
        market_data = {"margin_data": {"error": "无数据"}}

        apply_margin_db_fallback(conn, market_data, "2026-05-22")
        assert market_data["margin_data"]["as_of"] == "2026-05-21"   # 非当日 999 那条
        assert market_data["margin_data"]["total_rzrqye_yi"] == 100.0
        conn.close()

    def test_malformed_row_keeps_error(self, tmp_path):
        # 畸形回退行（缺 trade_date / 全部汇总字段缺失）→ 不回填，保留原 error 便于排障。
        conn = self._conn(tmp_path)
        _insert_margin_payload(conn, "2026-04-30", {"foo": "bar"})
        market_data = {"margin_data": {"error": "无融资融券汇总数据: 2026-05-21"}}

        apply_margin_db_fallback(conn, market_data, "2026-05-22")
        assert "error" in market_data["margin_data"]
        assert "stale" not in market_data["margin_data"]
        conn.close()
