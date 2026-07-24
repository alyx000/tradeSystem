"""sector_crowding 测试：覆盖 schema / repo / analyzer / collector（后续阶段扩展 service/formatter）。"""
import sqlite3
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

from db.schema import init_schema
from services.sector_crowding import analyzer, collector, formatter, repo, service


def _sw_df(rows):
    import pandas as pd
    return pd.DataFrame(rows)


def _mk_provider(daily_rows, l1_codes=frozenset({"801080.SI"}), parent_map=None):
    p = MagicMock()
    p.pro.sw_daily.return_value = _sw_df(daily_rows)

    def _trade_cal(start_date=None, end_date=None, **_kwargs):
        start = date(
            int(start_date[:4]), int(start_date[4:6]), int(start_date[6:])
        )
        end = date(int(end_date[:4]), int(end_date[4:6]), int(end_date[6:]))
        rows = []
        cursor = start
        while cursor <= end:
            rows.append({
                "cal_date": cursor.strftime("%Y%m%d"),
                "is_open": 1 if cursor.weekday() < 5 else 0,
            })
            cursor += timedelta(days=1)
        return _sw_df(rows)

    p.pro.trade_cal.side_effect = _trade_cal
    p._ensure_sw_l1_codes.return_value = set(l1_codes)
    p._ensure_sw_l2_codes.return_value = {"801081.SI"}
    p._ensure_sw_l1_parent_map.return_value = parent_map or {}
    return p


ROWS = [
    {"ts_code": "801080.SI", "name": "电子", "close": 5000.0,
     "amount": 3000.0 * 10000.0, "trade_date": "20260717"},
    {"ts_code": "801081.SI", "name": "半导体", "close": 8000.0,
     "amount": 1500.0 * 10000.0, "trade_date": "20260717"},
]


@pytest.fixture()
def conn(tmp_path):
    c = sqlite3.connect(tmp_path / "t.db")
    c.row_factory = sqlite3.Row
    init_schema(c)
    yield c
    c.close()


@pytest.fixture(autouse=True)
def _small_catalog_floor(monkeypatch):
    """单元测试使用最小码表；生产基线完整性由专门用例覆盖。"""
    monkeypatch.setattr(collector, "SW2021_L2_CATALOG_COUNT_FLOOR", 1)
    monkeypatch.setattr(collector, "SW2021_L2_PUBLISHED_COUNT_FLOOR", 1)


def _rec(date, total=15000.0, sectors=None):
    return {
        "date": date,
        "market_total_billion": total,
        "sectors": sectors if sectors is not None else [
            {"code": "801080.SI", "name": "电子", "level": "L1",
             "close": 5000.0, "amount_billion": 3000.0, "share_pct": 20.0},
        ],
        "proxy": None,
        "meta": {"source": "tushare"},
    }


class TestSchema:
    def test_sector_crowding_daily_table_exists(self, conn):
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(sector_crowding_daily)")}
        assert {"date", "market_total_billion", "sectors_json", "proxy_json",
                "meta_json", "created_at", "updated_at"} <= cols

    def test_market_total_nullable(self, conn):
        conn.execute(
            "INSERT INTO sector_crowding_daily (date, sectors_json) VALUES ('2026-07-17', '[]')"
        )  # market_total_billion 缺省 NULL 不应报错


class TestRepo:
    def test_save_and_get_roundtrip(self, conn):
        repo.save_snapshot(conn, _rec("2026-07-17"))
        got = repo.get_snapshot(conn, "2026-07-17")
        assert got["sectors"][0]["code"] == "801080.SI"
        assert got["market_total_billion"] == 15000.0

    def test_upsert_idempotent_keeps_created_at(self, conn):
        repo.save_snapshot(conn, _rec("2026-07-17"))
        created = repo.get_snapshot(conn, "2026-07-17")["created_at"]
        repo.save_snapshot(conn, _rec("2026-07-17", total=16000.0))
        got = repo.get_snapshot(conn, "2026-07-17")
        assert got["market_total_billion"] == 16000.0
        assert got["created_at"] == created

    def test_market_total_none_persists_null(self, conn):
        repo.save_snapshot(conn, _rec("2026-07-17", total=None))
        assert repo.get_snapshot(conn, "2026-07-17")["market_total_billion"] is None

    def test_get_recent_ascending(self, conn):
        for d in ("2026-07-15", "2026-07-16", "2026-07-17"):
            repo.save_snapshot(conn, _rec(d))
        rows = repo.get_recent(conn, "2026-07-17", days=2)
        assert [r["date"] for r in rows] == ["2026-07-16", "2026-07-17"]
        assert all(r["meta"] is None for r in rows)

        rows_with_meta = repo.get_recent(
            conn,
            "2026-07-17",
            days=2,
            include_meta=True,
        )
        assert [r["meta"] for r in rows_with_meta] == [
            {"source": "tushare"},
            {"source": "tushare"},
        ]

    def test_missing_required_raises(self, conn):
        with pytest.raises(ValueError):
            repo.save_snapshot(conn, {"date": "2026-07-17"})  # 缺 sectors

    def test_empty_or_malformed_sectors_rejected(self, conn):
        # 空 sectors=数据源全失败,落库会伪装"正常无双高"(codex 门2 高)
        with pytest.raises(ValueError):
            repo.save_snapshot(conn, _rec("2026-07-17", sectors=[]))
        with pytest.raises(ValueError):
            repo.save_snapshot(conn, _rec("2026-07-17", sectors=["not-a-dict"]))
        with pytest.raises(ValueError):
            repo.save_snapshot(conn, _rec("2026-07-17", sectors=[{"name": "缺code"}]))
        # 非 str 身份字段(unhashable)会在读取侧 (level,code) dict 键上炸,写入即拒
        with pytest.raises(ValueError):
            repo.save_snapshot(conn, _rec("2026-07-17", sectors=[
                {"code": ["801080.SI"], "level": "L1"}]))
        with pytest.raises(ValueError):
            repo.save_snapshot(conn, _rec("2026-07-17", sectors=[
                {"code": "801080.SI", "level": " "}]))

    def test_insert_if_absent_never_overwrites(self, conn):
        # 回填专用写入口:已有行(含 proxy 的 daily 快照)绝不被覆盖(机制性防并发窗口)
        daily = _rec("2026-07-17")
        daily["proxy"] = {"moneyflow": [{"name": "电子", "net_amount_yi": 55.0}]}
        repo.save_snapshot(conn, daily)
        wrote = repo.insert_snapshot_if_absent(conn, _rec("2026-07-17", total=1.0))
        assert wrote is False
        got = repo.get_snapshot(conn, "2026-07-17")
        assert got["market_total_billion"] == 15000.0  # 原行保持
        assert got["proxy"] is not None                 # proxy 未丢
        assert repo.insert_snapshot_if_absent(conn, _rec("2026-07-16")) is True

    def test_save_snapshot_rejects_nan_at_write_boundary(self, conn):
        # 最后防线:采集层漏网的 NaN 在写边界炸掉,优于落成非标 JSON 毒化存储
        with pytest.raises(ValueError):
            repo.save_snapshot(conn, _rec("2026-07-17", sectors=[
                {"code": "801080.SI", "name": "电子", "level": "L1",
                 "close": float("nan"), "amount_billion": 1.0, "share_pct": 1.0}]))

    def test_get_recent_rejects_non_positive_days(self, conn):
        repo.save_snapshot(conn, _rec("2026-07-17"))
        for bad in (0, -1):
            with pytest.raises(ValueError):
                repo.get_recent(conn, "2026-07-17", days=bad)


class TestCollector:
    def test_fetch_sector_daily_native_l1(self):
        out = collector.fetch_sector_daily(_mk_provider(ROWS), "2026-07-17")
        levels = {s["code"]: s["level"] for s in out["sectors"]}
        assert levels == {"801080.SI": "L1", "801081.SI": "L2"}
        assert out["meta"]["l1_status"] == "native"
        assert out["meta"]["l2_expected_count"] == 1
        assert out["meta"]["l2_observed_count"] == 1
        assert out["meta"]["l2_expected_codes"] == ["801081.SI"]
        assert out["meta"]["l2_universe_complete"] is True
        amounts = {s["code"]: s["amount_billion"] for s in out["sectors"]}
        assert amounts["801080.SI"] == pytest.approx(3000.0)

    def test_fetch_sector_daily_rejects_partial_l2_universe(self):
        provider = _mk_provider(ROWS)
        provider._ensure_sw_l2_codes.return_value = {"801081.SI", "801102.SI"}

        assert collector.fetch_sector_daily(provider, "2026-07-17") is None

    def test_fetch_sector_daily_rejects_wrong_trade_date(self):
        provider = _mk_provider(ROWS)

        assert collector.fetch_sector_daily(provider, "2026-07-16") is None

    def test_fetch_sector_daily_rejects_partial_l2_catalog(self):
        provider = _mk_provider(ROWS)
        provider._ensure_sw_l2_codes.return_value = {"801081.SI", "801102.SI"}
        provider.pro.index_classify.return_value = _sw_df([
            {"index_code": "801081.SI", "is_pub": "1"},
        ])

        assert collector.fetch_sector_daily(provider, "2026-07-17") is None

    def test_fetch_sector_daily_rejects_stably_partial_l2_catalog(
        self,
        monkeypatch,
    ):
        provider = _mk_provider(ROWS)
        provider.pro.index_classify.return_value = _sw_df([
            {"index_code": "801081.SI", "is_pub": "1"},
        ])
        monkeypatch.setattr(
            collector,
            "SW2021_L2_CATALOG_COUNT_FLOOR",
            2,
        )

        assert collector.fetch_sector_daily(provider, "2026-07-17") is None

    def test_fetch_sector_daily_historical_excludes_code_not_yet_effective(self):
        provider = _mk_provider(ROWS)
        provider._ensure_sw_l2_codes.return_value = {"801081.SI", "801102.SI"}
        provider.pro.index_classify.return_value = _sw_df([
            {"index_code": "801081.SI", "is_pub": "1"},
            {"index_code": "801102.SI", "is_pub": "1"},
        ])

        def _sw(ts_code=None, trade_date=None, start_date=None, **_kwargs):
            if trade_date:
                return _sw_df([
                    {**row, "trade_date": "20200102"} for row in ROWS
                ])
            if ts_code == "801102.SI" and start_date > "20200102":
                return _sw_df([{
                    "trade_date": "20210104",
                    "close": 1.0,
                    "amount": 1.0,
                }])
            return _sw_df([])

        provider.pro.sw_daily.side_effect = _sw

        out = collector.fetch_sector_daily(provider, "2020-01-02")

        assert out is not None
        assert out["meta"]["l2_expected_codes"] == ["801081.SI"]

    def test_fetch_sector_daily_historical_rejects_active_code_gap(self):
        provider = _mk_provider(ROWS)
        provider._ensure_sw_l2_codes.return_value = {"801081.SI", "801102.SI"}
        provider.pro.index_classify.return_value = _sw_df([
            {"index_code": "801081.SI", "is_pub": "1"},
            {"index_code": "801102.SI", "is_pub": "1"},
        ])

        def _sw(ts_code=None, trade_date=None, start_date=None, **_kwargs):
            if trade_date:
                return _sw_df([
                    {**row, "trade_date": "20200102"} for row in ROWS
                ])
            if ts_code == "801102.SI":
                return _sw_df([{
                    "trade_date": "20191231" if start_date < "20200102" else "20200103",
                    "close": 1.0,
                    "amount": 1.0,
                }])
            return _sw_df([])

        provider.pro.sw_daily.side_effect = _sw

        assert collector.fetch_sector_daily(provider, "2020-01-02") is None

    def test_fetch_sector_daily_excludes_retired_catalog_code(self):
        provider = _mk_provider(ROWS)
        provider._ensure_sw_l2_codes.return_value = {"801011.SI", "801081.SI"}
        provider.pro.index_classify.return_value = _sw_df([
            {"index_code": "801011.SI", "is_pub": "0"},
            {"index_code": "801081.SI", "is_pub": "1"},
        ])

        def _sw(ts_code=None, trade_date=None, end_date=None, **_kwargs):
            if trade_date:
                return _sw_df(ROWS)
            if ts_code == "801011.SI" and end_date < "20260717":
                return _sw_df([{
                    "trade_date": "20211231",
                    "close": 1.0,
                    "amount": 1.0,
                }])
            return _sw_df([])

        provider.pro.sw_daily.side_effect = _sw

        out = collector.fetch_sector_daily(provider, "2026-07-17")

        assert out is not None
        assert out["meta"]["l2_expected_codes"] == ["801081.SI"]

    def test_fetch_sector_daily_rejects_missing_l2_code_table(self):
        provider = _mk_provider(ROWS)
        provider._ensure_sw_l2_codes.return_value = set()

        assert collector.fetch_sector_daily(provider, "2026-07-17") is None

    def test_fetch_sector_daily_filters_unknown_levels(self):
        # sw_daily 含 L3/申万50 等特殊指数,不在 L1/L2 码表内必须过滤(防混级双计)
        rows = ROWS + [{"ts_code": "801001.SI", "name": "申万50", "close": 1.0,
                        "amount": 5.0, "trade_date": "20260717"}]
        out = collector.fetch_sector_daily(_mk_provider(rows), "2026-07-17")
        assert {s["code"] for s in out["sectors"]} == {"801080.SI", "801081.SI"}

    def test_fetch_sector_daily_l1_missing_no_synthesis(self):
        # sw_daily 只有 L2 行、parent_map 为空 → 禁止合成,l1_status=missing
        out = collector.fetch_sector_daily(
            _mk_provider([ROWS[1]], l1_codes=frozenset(), parent_map={}), "2026-07-17")
        assert out["meta"]["l1_status"] == "missing"
        assert all(s["level"] == "L2" for s in out["sectors"])

    def test_fetch_sector_daily_l1_synthesized_when_map_ok(self):
        out = collector.fetch_sector_daily(
            _mk_provider([ROWS[1]], l1_codes=frozenset(),
                         parent_map={"801081.SI": "801080.SI"}), "2026-07-17")
        assert out["meta"]["l1_status"] == "synthesized"
        l1 = [s for s in out["sectors"] if s["level"] == "L1"]
        assert l1 and l1[0]["code"] == "801080.SI" and l1[0]["close"] is None
        assert l1[0]["amount_billion"] == pytest.approx(1500.0)

    def test_fetch_market_total_guard_floor(self, conn):
        registry = MagicMock()
        registry.call.return_value = MagicMock(
            success=True, data={"total_billion": 1000.0,
                                "shanghai_billion": 500.0, "shenzhen_billion": 500.0},
            source="tushare")
        total, src = collector.fetch_market_total(conn, registry, "2026-07-17")
        assert total is None  # 低于绝对地板 3000 亿

    def test_fetch_market_total_ratio_guard(self, conn):
        registry = MagicMock()
        registry.call.return_value = MagicMock(
            success=True, data={"total_billion": 20000.0,
                                "shanghai_billion": 12000.0, "shenzhen_billion": 8000.0},
            source="tushare")
        total, _ = collector.fetch_market_total(conn, registry, "2026-07-17")
        assert total is None  # 深/沪腿比 8/12 < 0.8 疑口径退化

    def test_fetch_proxy_partial_failure(self):
        registry = MagicMock()

        def _call(cap, *a, **kw):
            if cap == "get_sector_moneyflow_ths":
                return MagicMock(success=True, source="tushare:moneyflow_ind_ths",
                                 data=[{"name": "电子", "net_amount_yi": 55.0}])
            return MagicMock(success=False, data=None, error="down", source="x")

        registry.call.side_effect = _call
        out = collector.fetch_proxy(registry, "2026-07-17")
        assert out["moneyflow"][0]["net_amount_yi"] == 55.0
        assert out["etf"] is None and out["margin"] is None
        assert out["errors"]  # 失败路有记录

    def test_normalize_moneyflow_akshare_field_shape(self):
        # akshare get_sector_fund_flow 源字段为 net_inflow_billion → 归一为 net_amount_yi
        out = collector._normalize_moneyflow(
            [{"name": "电子", "net_inflow_billion": 30.5},
             {"name": "脏值", "net_inflow_billion": "bad"}])
        assert out == [{"name": "电子", "net_amount_yi": 30.5}]

    def test_normalize_moneyflow_rejects_nan_inf(self):
        # float("nan") 不抛异常但毒化排序比较并落成非标 JSON(门1 review 角度A 高)
        out = collector._normalize_moneyflow(
            [{"name": "A", "net_amount_yi": float("nan")},
             {"name": "B", "net_amount_yi": float("inf")},
             {"name": "C", "net_amount_yi": 1.0}])
        assert out == [{"name": "C", "net_amount_yi": 1.0}]

    def test_fetch_market_total_rejects_nan(self, conn):
        # NaN 的 < 比较恒 False 会穿透三段守卫(降级链"成功但含脏值"事故同型)
        registry = MagicMock()
        registry.call.return_value = MagicMock(
            success=True, data={"total_billion": float("nan"),
                                "shanghai_billion": 7000.0, "shenzhen_billion": 8000.0},
            source="tushare")
        total, _ = collector.fetch_market_total(conn, registry, "2026-07-17")
        assert total is None

    def test_sector_daily_scrubs_nan_amount_and_close(self):
        rows = [dict(ROWS[0], amount=float("nan"), close=float("nan")), ROWS[1]]
        out = collector.fetch_sector_daily(_mk_provider(rows), "2026-07-17")
        sec = {s["code"]: s for s in out["sectors"]}
        assert sec["801080.SI"]["amount_billion"] is None
        assert sec["801080.SI"]["close"] is None

    def test_synthesize_l1_skips_nan_amount(self):
        # NaN 参与加总会把整个合成 L1 毒成 NaN(单行脏值放大为整行业缺席)
        l2 = [{"code": "801081.SI", "level": "L2", "amount_billion": float("nan")},
              {"code": "801082.SI", "level": "L2", "amount_billion": 100.0}]
        pm = {"801081.SI": "801080.SI", "801082.SI": "801080.SI"}
        out = collector.synthesize_l1(l2, pm)
        assert out[0]["amount_billion"] == pytest.approx(100.0)

    def test_fetch_proxy_normalizes_etf_and_margin_nan(self):
        # ETF/两融代理与 moneyflow 对称归一:NaN 不得落库/渲染(codex 门2 中)
        registry = MagicMock()

        def _call(cap, *a, **kw):
            if cap == "get_etf_flow":
                return MagicMock(success=True, source="tushare:fund_share", data=[
                    {"code": "512480", "name": "半导体ETF",
                     "total_shares_billion": float("nan"),
                     "shares_change_billion": 2.0}])
            if cap == "get_margin_data":
                return MagicMock(success=True, source="tushare:margin", data={
                    "trade_date": "2026-07-17", "total_rzrqye_yi": float("nan")})
            return MagicMock(success=False, data=None, error="down", source="x")

        registry.call.side_effect = _call
        out = collector.fetch_proxy(registry, "2026-07-17")
        assert out["etf"][0]["total_shares_billion"] is None
        assert out["etf"][0]["shares_change_billion"] == 2.0
        assert out["margin"] is None  # 主值 NaN → 整体置 None

    def test_clean_margin_whitelists_and_nulls_nested_nan(self):
        # 白名单重建:嵌套 NaN 字段置 None、未消费字段(exchanges)不透传
        out = collector._clean_margin({
            "trade_date": "2026-07-17", "total_rzrqye_yi": 19000.0,
            "total_rzye_yi": float("nan"), "total_rqye_yi": 100.0,
            "exchanges": [{"rzye_yi": float("nan")}]})
        assert out["total_rzye_yi"] is None
        assert out["total_rqye_yi"] == 100.0
        assert "exchanges" not in out

    def test_backfill_empty_fetch_aborts(self, conn):
        # 空返回型源故障(全码空 DataFrame 不抛异常)不得伪装成"成功 0 写入"
        p = _mk_provider(ROWS)
        p.pro.sw_daily.side_effect = lambda **kw: _sw_df([])
        with pytest.raises(RuntimeError):
            service.run_backfill(conn, _mk_registry_ok(), p, "2026-07-16", "2026-07-16")

    def test_fetch_code_history_skips_malformed_trade_date(self):
        # pandas 缺值整列 int→float64:str() 出 "20200101.0"/"nan" → 畸形日期键必须跳行
        p = MagicMock()
        p.pro.sw_daily.return_value = _sw_df(
            [{"ts_code": "801080.SI", "trade_date": "20200101.0", "close": 1.0, "amount": 10000.0},
             {"ts_code": "801080.SI", "trade_date": "nan", "close": 1.0, "amount": 10000.0},
             {"ts_code": "801080.SI", "trade_date": "20200102", "close": 1.0, "amount": 10000.0}])
        bars = collector.fetch_code_history(p, "801080.SI", "2020-01-01", "2020-12-31")
        assert [b["date"] for b in bars] == ["2020-01-02"]

    def test_fetch_code_history_rejects_date_outside_requested_chunk(self):
        p = MagicMock()
        p.pro.sw_daily.return_value = _sw_df([
            {"trade_date": "20210104", "close": 1.0, "amount": 10000.0},
        ])

        with pytest.raises(RuntimeError, match="请求分片外日期"):
            collector.fetch_code_history(
                p,
                "801080.SI",
                "2020-01-01",
                "2020-12-31",
            )

    @pytest.mark.parametrize("row_code", [None, "", "801081.SI"])
    def test_fetch_code_history_requires_matching_ts_code(self, row_code):
        p = MagicMock()
        p.pro.sw_daily.return_value = _sw_df([{
            "ts_code": row_code,
            "trade_date": "20200102",
            "close": 1.0,
            "amount": 10000.0,
        }])

        with pytest.raises(RuntimeError, match="缺失或不匹配代码行"):
            collector.fetch_code_history(
                p,
                "801080.SI",
                "2020-01-01",
                "2020-12-31",
            )

    def test_history_probe_retries_transient_empty(self):
        p = MagicMock()
        p.pro.sw_daily.side_effect = [
            _sw_df([]),
            _sw_df([]),
            _sw_df([{
                "trade_date": "20200102",
                "close": 1.0,
                "amount": 10000.0,
            }]),
        ]

        assert collector._probe_history_exists(
            p, "801081.SI", "2020-01-01", "2020-01-03"
        ) is True
        assert p.pro.sw_daily.call_count == 3

    @pytest.mark.parametrize(
        "responses",
        [
            [TimeoutError("one"), TimeoutError("two"), _sw_df([])],
            [TimeoutError("one"), _sw_df([]), _sw_df([])],
        ],
    )
    def test_history_probe_does_not_treat_mixed_errors_as_stable_empty(
        self,
        responses,
        monkeypatch,
    ):
        monkeypatch.setattr(collector, "CODE_FETCH_RETRY_SLEEP_SECONDS", 0.0)
        p = MagicMock()
        p.pro.sw_daily.side_effect = responses

        with pytest.raises(TimeoutError):
            collector._probe_history_exists(
                p,
                "801081.SI",
                "2020-01-01",
                "2020-01-03",
            )


def _view(double_high=False, l1_status="native"):
    sec = {"code": "801080.SI", "name": "电子", "level": "L1", "close": 5000.0,
           "amount_billion": 7050.0, "share_pct": 47.0, "share_pctile": 99.0,
           "gain_5d": 8.0, "gain_20d": 70.0, "gain_60d": 120.0, "gain_pctile_20d": 99.0}
    return {"date": "2026-07-17", "market_total_billion": 15000.0,
            "sectors": [sec], "double_high": [sec] if double_high else [],
            "meta": {"l1_status": l1_status}}


class TestFormatter:
    def test_report_contains_extreme_marker_and_naming(self):
        md = formatter.format_report(_view())
        assert "全行业交易拥挤度" in md
        assert "🔴" in md            # 47% ≥ 40 极值区
        assert "电子" in md

    def test_report_double_high_section(self):
        md = formatter.format_report(_view(double_high=True))
        assert "双高拥挤" in md and "801080.SI" not in md.split("双高拥挤")[0]

    def test_report_sector_labels_keep_fact_and_judgment_boundaries(self):
        view = _view()
        view["sector_labels"] = {
            "status": "success",
            "definitions": {
                "half_year_ma_window": 89,
                "year_ma_window": 144,
                "resonance_lookback_days": 5,
                "resonance_breakout_window": 30,
                "window_unit": "trading_snapshot_days",
            },
            "summary": {
                "total_l2": 1,
                "above_half_year_ma": 1,
                "above_year_ma": 1,
                "recent_resonance": 1,
                "year_and_resonance": 1,
            },
            "items": [{
                "code": "801081.SI",
                "name": "半导体",
                "above_half_year_ma": True,
                "above_year_ma": True,
                "recent_price_volume_resonance": True,
                "last_resonance": {"date": "2026-07-17"},
            }],
        }

        md = formatter.format_report(view)

        assert "板块趋势标签（申万二级）" in md
        assert "年线上 [事实]" in md
        assert "半年线上 [事实]" in md
        assert "MA89" in md
        assert "近期价量共振 [判断]" in md
        assert "MA144" in md
        assert "最近5个交易快照日" in md
        assert "此前30个交易快照日高点" in md
        assert "半导体(801081.SI)" in md
        assert "最近共振 2026-07-17" in md

    def test_report_sector_labels_do_not_present_unknown_as_no_hit(self):
        view = _view()
        view["sector_labels"] = {
            "status": "success",
            "definitions": analyzer.sector_label_definitions(),
            "summary": {
                "total_l2": 2,
                "above_half_year_ma": 0,
                "above_year_ma": 0,
                "recent_resonance": 0,
                "year_and_resonance": 0,
                "half_year_ma_insufficient": 1,
                "year_ma_insufficient": 2,
                "resonance_insufficient": 1,
            },
            "items": [
                {
                    "code": "801081.SI",
                    "name": "半导体",
                    "above_half_year_ma": None,
                    "above_year_ma": None,
                    "recent_price_volume_resonance": None,
                },
                {
                    "code": "801102.SI",
                    "name": "通信设备",
                    "above_half_year_ma": False,
                    "above_year_ma": None,
                    "recent_price_volume_resonance": False,
                },
            ],
        }

        md = formatter.format_report(view)

        assert "暂无已确认命中" in md
        assert "半年线数据不足 1 个" in md
        assert "年线数据不足 2 个" in md
        assert "共振数据不足 1 个" in md
        assert "无命中标签板块" not in md

    def test_report_sector_labels_warns_when_l2_coverage_is_partial(self):
        view = _view()
        view["sector_labels"] = {
            "status": "partial",
            "definitions": analyzer.sector_label_definitions(),
            "summary": {
                "total_l2": 2,
                "missing_l2_count": 1,
                "above_half_year_ma": 1,
                "above_year_ma": 1,
                "recent_resonance": 0,
                "year_and_resonance": 0,
                "half_year_ma_insufficient": 1,
                "year_ma_insufficient": 1,
                "resonance_insufficient": 1,
            },
            "items": [{
                "code": "801081.SI",
                "name": "半导体",
                "above_half_year_ma": True,
                "above_year_ma": True,
                "recent_price_volume_resonance": False,
            }, {
                "code": "801102.SI",
                "name": "通信设备",
                "above_half_year_ma": None,
                "above_year_ma": None,
                "recent_price_volume_resonance": None,
            }],
        }

        md = formatter.format_report(view)

        assert "当日申万二级覆盖不完整：缺失 1 个板块" in md
        assert "半导体(801081.SI)" in md

    def test_report_sector_labels_reports_full_l2_missing_count(self):
        view = _view()
        view["sector_labels"] = {
            "status": "missing_l2",
            "definitions": analyzer.sector_label_definitions(),
            "summary": {
                "total_l2": 2,
                "missing_l2_count": 2,
                "above_half_year_ma": 0,
                "above_year_ma": 0,
                "recent_resonance": 0,
                "year_and_resonance": 0,
                "half_year_ma_insufficient": 2,
                "year_ma_insufficient": 2,
                "resonance_insufficient": 2,
            },
            "items": [],
        }

        md = formatter.format_report(view)

        assert "当日快照完全缺少申万二级" in md
        assert "保留预期板块清单 2 个" in md

    def test_report_proxy_disclaimer_always_present(self):
        md = formatter.format_report({**_view(), "proxy": {
            "moneyflow": [{"name": "电子", "net_amount_yi": 55.0}],
            "moneyflow_source": "tushare:moneyflow_ind_ths",
            "etf": None, "margin": None, "errors": []}})
        assert "非公募持仓真值" in md

    def test_report_l1_missing_no_fake_rows(self):
        v = _view(l1_status="missing")
        v["sectors"][0]["level"] = "L2"
        md = formatter.format_report(v)
        assert "L1 数据缺失" in md

    def test_report_etf_share_jump_flagged(self):
        # 单次份额变动超存量 30% → 疑拆分,必须标「勿直读」(spec 事故级用例 6)
        md = formatter.format_report({**_view(), "proxy": {
            "moneyflow": None, "moneyflow_source": None,
            "etf": [{"code": "512480", "name": "半导体ETF",
                     "total_shares_billion": 100.0, "shares_change_billion": 40.0}],
            "margin": None, "errors": []}})
        assert "勿直读" in md
        md2 = formatter.format_report({**_view(), "proxy": {
            "moneyflow": None, "moneyflow_source": None,
            "etf": [{"code": "512480", "name": "半导体ETF",
                     "total_shares_billion": 100.0, "shares_change_billion": 2.0}],
            "margin": None, "errors": []}})
        assert "勿直读" not in md2

    def test_report_market_total_missing_note(self):
        v = _view()
        v["market_total_billion"] = None
        md = formatter.format_report(v)
        assert "两市总成交额缺失" in md

    def test_report_margin_stale_note(self):
        md = formatter.format_report({**_view(), "proxy": {
            "moneyflow": None, "moneyflow_source": None, "etf": None,
            "margin": {"trade_date": "2026-07-16", "total_rzrqye_yi": 19000.0},
            "errors": []}})
        assert "数据日 2026-07-16" in md and "两融" in md

    def test_report_margin_missing_key_does_not_crash(self):
        # proxy 原样持久化:缺键脏数据落库后 run_report 不得永炸
        md = formatter.format_report({**_view(), "proxy": {
            "moneyflow": None, "moneyflow_source": None, "etf": None,
            "margin": {"trade_date": "2026-07-16"}, "errors": []}})
        assert "两融" not in md  # 缺值跳行,不渲染坏行

    def test_report_fact_judgment_labels(self):
        md = formatter.format_report(_view())
        assert "[事实]" in md and "[判断]" in md and "不构成买卖建议" in md


def _mk_registry_ok():
    registry = MagicMock()

    def _call(cap, *a, **kw):
        if cap == "get_market_volume":
            return MagicMock(success=True, source="tushare",
                             data={"total_billion": 15000.0, "shanghai_billion": 7000.0,
                                   "shenzhen_billion": 8000.0})
        if cap == "get_sector_moneyflow_ths":
            return MagicMock(success=True, source="tushare:moneyflow_ind_ths",
                             data=[{"name": "电子", "net_amount_yi": 55.0}])
        return MagicMock(success=False, data=None, error="down", source="x")

    registry.call.side_effect = _call
    return registry


class TestService:
    def test_run_daily_persists_and_renders(self, conn):
        md = service.run_daily(conn, _mk_registry_ok(), _mk_provider(ROWS), "2026-07-17")
        assert md is not None and "全行业交易拥挤度" in md
        snap = repo.get_snapshot(conn, "2026-07-17")
        assert snap is not None
        sec = {s["code"]: s for s in snap["sectors"]}
        assert sec["801080.SI"]["share_pct"] == pytest.approx(20.0)
        # 派生指标不落库
        assert "share_pctile" not in sec["801080.SI"]
        # 报告含代理段(get_snapshot 覆盖当日全量,proxy 不得静默丢失)
        assert "主力净流入" in md

    def test_run_daily_dry_run_no_persist(self, conn):
        md = service.run_daily(conn, _mk_registry_ok(), _mk_provider(ROWS),
                               "2026-07-17", persist=False)
        assert md is not None
        assert repo.get_snapshot(conn, "2026-07-17") is None

    def test_run_daily_dry_run_uses_fresh_record_over_stale_row(self, conn):
        # 库里已有旧行(share_pct=20)时,dry-run 必须展示本次 fresh 采集而非陈旧库行
        service.run_daily(conn, _mk_registry_ok(), _mk_provider(ROWS), "2026-07-17")
        rows2 = [dict(ROWS[0], amount=6000.0 * 10000.0), ROWS[1]]
        md = service.run_daily(conn, _mk_registry_ok(), _mk_provider(rows2),
                               "2026-07-17", persist=False)
        assert "40.0%" in md  # 6000/15000,而非旧值 20%

    def test_run_daily_no_sectors_returns_none(self, conn):
        p = _mk_provider([])
        p.pro.sw_daily.return_value = _sw_df([])
        assert service.run_daily(conn, _mk_registry_ok(), p, "2026-07-17") is None

    def test_run_report_missing_date(self, conn):
        assert "无拥挤度快照" in service.run_report(conn, "2026-01-01")

    def test_run_report_includes_proxy_and_meta(self, conn):
        # get_recent 精简列契约:report 必须用 get_snapshot 覆盖当日,代理段/meta 不丢
        service.run_daily(conn, _mk_registry_ok(), _mk_provider(ROWS), "2026-07-17")
        md = service.run_report(conn, "2026-07-17")
        assert "主力净流入" in md

    def test_label_payload_prefers_recent_trusted_universe_from_repo(self, conn):
        keep = {
            "code": "801081.SI", "name": "半导体", "level": "L2",
            "close": 100.0, "amount_billion": 10.0, "share_pct": 1.0,
        }
        old = {
            "code": "801999.SI", "name": "旧分类", "level": "L2",
            "close": 80.0, "amount_billion": 6.0, "share_pct": 0.6,
        }
        new = {
            "code": "801102.SI", "name": "通信设备", "level": "L2",
            "close": 88.0, "amount_billion": 8.0, "share_pct": 0.8,
        }
        repo.save_snapshot(
            conn,
            _rec("2026-07-15", sectors=[keep, old]),
        )
        trusted = _rec("2026-07-16", sectors=[keep, new])
        trusted["meta"] = {
            "l2_expected_codes": ["801081.SI", "801102.SI"],
            "l2_expected_count": 2,
            "l2_observed_count": 2,
            "l2_universe_complete": True,
        }
        repo.save_snapshot(conn, trusted)
        repo.save_snapshot(
            conn,
            _rec("2026-07-17", sectors=[keep]),
        )

        payload = service.build_sector_labels_payload(conn, "2026-07-17")

        assert payload["status"] == "partial"
        assert payload["summary"]["missing_l2_count"] == 1
        assert {item["code"] for item in payload["items"]} == {
            "801081.SI",
            "801102.SI",
        }

    def test_report_uses_recent_trusted_universe_from_repo(
        self,
        conn,
        monkeypatch,
    ):
        keep = {
            "code": "801081.SI", "name": "半导体", "level": "L2",
            "close": 100.0, "amount_billion": 10.0, "share_pct": 1.0,
        }
        old = {
            "code": "801999.SI", "name": "旧分类", "level": "L2",
            "close": 80.0, "amount_billion": 6.0, "share_pct": 0.6,
        }
        new = {
            "code": "801102.SI", "name": "通信设备", "level": "L2",
            "close": 88.0, "amount_billion": 8.0, "share_pct": 0.8,
        }
        repo.save_snapshot(conn, _rec("2026-07-15", sectors=[keep, old]))
        trusted = _rec("2026-07-16", sectors=[keep, new])
        trusted["meta"] = {
            "l2_expected_codes": ["801081.SI", "801102.SI"],
            "l2_expected_count": 2,
            "l2_observed_count": 2,
            "l2_universe_complete": True,
        }
        repo.save_snapshot(conn, trusted)
        repo.save_snapshot(conn, _rec("2026-07-17", sectors=[keep]))
        captured = {}

        def _capture(view):
            captured.update(view)
            return "ok"

        monkeypatch.setattr(service.formatter, "format_report", _capture)

        assert service.run_report(conn, "2026-07-17") == "ok"
        assert {
            item["code"] for item in captured["sector_labels"]["items"]
        } == {"801081.SI", "801102.SI"}

    def test_run_daily_non_trading_day_guard(self, conn, monkeypatch):
        monkeypatch.setattr(service, "is_non_trading_day", lambda *a: True)
        md = service.run_daily(conn, _mk_registry_ok(), _mk_provider(ROWS), "2026-07-19")
        assert md is None
        assert repo.get_snapshot(conn, "2026-07-19") is None
        # dry-run 豁免守卫（与 sector_correlation 同语义）
        rows_19 = [{**row, "trade_date": "20260719"} for row in ROWS]
        md2 = service.run_daily(conn, _mk_registry_ok(), _mk_provider(rows_19),
                                "2026-07-19", persist=False)
        assert md2 is not None

    def test_run_trend(self, conn):
        service.run_daily(conn, _mk_registry_ok(), _mk_provider(ROWS), "2026-07-17")
        md = service.run_trend(conn, "2026-07-17", "801080.SI", days=30)
        assert "2026-07-17" in md and "20.0%" in md


class TestBackfill:
    def test_truncation_raises(self):
        p = MagicMock()
        p.pro.sw_daily.return_value = _sw_df(
            [{"trade_date": "20200101", "close": 1.0, "amount": 1.0}] * 2000)
        with pytest.raises(collector.BackfillTruncationError):
            collector.fetch_code_history(p, "801080.SI", "2019-01-01", "2026-07-17")

    def test_fetch_code_history_chunks_and_sorts(self):
        p = MagicMock()
        calls = []

        def _sw(ts_code=None, start_date=None, end_date=None):
            calls.append((start_date, end_date))
            return _sw_df([{
                "ts_code": ts_code,
                "trade_date": start_date,
                "close": 1.0,
                "amount": 10000.0,
            }])

        p.pro.sw_daily.side_effect = _sw
        bars = collector.fetch_code_history(p, "801080.SI", "2019-01-01", "2026-07-17")
        # 4 年分片:2019-2022 / 2023-2026,无缝无叠
        assert calls == [("20190101", "20221231"), ("20230101", "20260717")]
        assert [b["date"] for b in bars] == ["2019-01-01", "2023-01-01"]
        assert bars[0]["amount_billion"] == pytest.approx(1.0)

    def test_backfill_writes_whole_day_once(self, conn):
        p = _mk_provider(ROWS)

        def _sw(ts_code=None, start_date=None, end_date=None, trade_date=None):
            rows = [r for r in ROWS if r["ts_code"] == ts_code]
            return _sw_df([{**r, "trade_date": "20260716"} for r in rows])

        p.pro.sw_daily.side_effect = _sw
        stats = service.run_backfill(conn, _mk_registry_ok(), p,
                                     "2026-07-16", "2026-07-16")
        assert stats["dates_written"] == 1
        snap = repo.get_snapshot(conn, "2026-07-16")
        # 两个码在同一行里 → 没有按码互相覆盖
        assert {s["code"] for s in snap["sectors"]} == {"801080.SI", "801081.SI"}
        assert snap["meta"]["backfilled"] is True
        assert snap["meta"]["l2_expected_codes"] == ["801081.SI"]
        assert snap["meta"]["l2_universe_complete"] is True
        sec = {s["code"]: s for s in snap["sectors"]}
        assert sec["801080.SI"]["share_pct"] == pytest.approx(20.0)

    def test_backfill_single_l2_empty_fails_closed(self, conn):
        provider = _mk_provider(ROWS)
        provider._ensure_sw_l2_codes.return_value = {"801081.SI", "801102.SI"}

        def _sw(ts_code=None, **_kwargs):
            if ts_code == "801102.SI":
                return _sw_df([])
            rows = [row for row in ROWS if row["ts_code"] == ts_code]
            return _sw_df([{**row, "trade_date": "20260716"} for row in rows])

        provider.pro.sw_daily.side_effect = _sw

        with pytest.raises(RuntimeError):
            service.run_backfill(
                conn,
                _mk_registry_ok(),
                provider,
                "2026-07-16",
                "2026-07-16",
            )
        assert repo.get_snapshot(conn, "2026-07-16") is None

    def test_backfill_l2_empty_outside_effective_range_is_not_failure(self, conn):
        provider = _mk_provider(ROWS)
        provider._ensure_sw_l2_codes.return_value = {"801081.SI", "801102.SI"}

        def _sw(ts_code=None, start_date=None, end_date=None, **_kwargs):
            if ts_code == "801080.SI":
                return _sw_df([])
            if ts_code == "801081.SI":
                return _sw_df([{
                    **ROWS[1],
                    "trade_date": "20200102",
                }])
            # 新码在目标区间后才有行情：主区间空，区间外探针非空。
            if start_date and start_date > "20200102":
                return _sw_df([{
                    "ts_code": "801102.SI",
                    "name": "通信设备",
                    "close": 88.0,
                    "amount": 8.0 * 10000.0,
                    "trade_date": "20210104",
                }])
            return _sw_df([])

        provider.pro.sw_daily.side_effect = _sw

        stats = service.run_backfill(
            conn,
            _mk_registry_ok(),
            provider,
            "2020-01-02",
            "2020-01-02",
        )

        assert stats["dates_written"] == 1
        snap = repo.get_snapshot(conn, "2020-01-02")
        assert snap["meta"]["l2_expected_codes"] == ["801081.SI"]

    def test_backfill_retired_l2_empty_after_effective_range_is_not_failure(self, conn):
        provider = _mk_provider(ROWS)
        provider._ensure_sw_l2_codes.return_value = {"801011.SI", "801081.SI"}
        provider.pro.index_classify.return_value = _sw_df([
            {"index_code": "801011.SI", "industry_name": "林业Ⅱ", "is_pub": "0"},
            {"index_code": "801081.SI", "industry_name": "半导体", "is_pub": "1"},
        ])

        def _sw(ts_code=None, start_date=None, end_date=None, **_kwargs):
            if ts_code == "801080.SI":
                return _sw_df([])
            if ts_code == "801081.SI":
                return _sw_df([{**ROWS[1], "trade_date": "20260716"}])
            if ts_code == "801011.SI" and end_date < "20260716":
                return _sw_df([{
                    "trade_date": "20211231",
                    "close": 1.0,
                    "amount": 1.0,
                }])
            return _sw_df([])

        provider.pro.sw_daily.side_effect = _sw

        stats = service.run_backfill(
            conn,
            _mk_registry_ok(),
            provider,
            "2026-07-16",
            "2026-07-16",
        )

        assert stats["dates_written"] == 1
        snap = repo.get_snapshot(conn, "2026-07-16")
        assert snap["meta"]["l2_expected_codes"] == ["801081.SI"]

    def test_backfill_rejects_trailing_active_l2_gap(self, conn):
        provider = _mk_provider(ROWS)
        provider._ensure_sw_l2_codes.return_value = {"801081.SI", "801102.SI"}
        provider.pro.index_classify.return_value = _sw_df([
            {"index_code": "801081.SI", "industry_name": "半导体", "is_pub": "1"},
            {"index_code": "801102.SI", "industry_name": "通信设备", "is_pub": "1"},
        ])

        def _bars(code, dates):
            return _sw_df([{
                "ts_code": code,
                "trade_date": d.replace("-", ""),
                "close": 1.0,
                "amount": 10000.0,
            } for d in dates])

        def _sw(ts_code=None, start_date=None, **_kwargs):
            if ts_code == "801080.SI":
                return _sw_df([])
            if ts_code == "801081.SI":
                return _bars(ts_code, ["2020-01-02", "2020-01-03", "2020-01-06"])
            if ts_code == "801102.SI":
                if start_date and start_date >= "20200104":
                    return _bars(ts_code, ["2020-01-06"])
                return _bars(ts_code, ["2020-01-02", "2020-01-03"])
            return _sw_df([])

        provider.pro.sw_daily.side_effect = _sw

        with pytest.raises(RuntimeError, match="采集失败"):
            service.run_backfill(
                conn,
                _mk_registry_ok(),
                provider,
                "2020-01-02",
                "2020-01-06",
            )
        assert repo.get_snapshot(conn, "2020-01-02") is None

    @pytest.mark.parametrize(
        "missing_date",
        ["2020-01-02", "2020-01-03", "2020-01-06"],
    )
    def test_backfill_rejects_l2_universe_wide_calendar_gap(
        self,
        conn,
        missing_date,
    ):
        provider = _mk_provider(ROWS)
        provider._ensure_sw_l2_codes.return_value = {"801081.SI", "801102.SI"}
        provider.pro.index_classify.return_value = _sw_df([
            {"index_code": "801081.SI", "industry_name": "半导体", "is_pub": "1"},
            {"index_code": "801102.SI", "industry_name": "通信设备", "is_pub": "1"},
        ])
        target_dates = ["2020-01-02", "2020-01-03", "2020-01-06"]

        def _rows_for(code, start_date, end_date):
            history_dates = ["2019-12-31"] + [
                d for d in target_dates if d != missing_date
            ]
            return _sw_df([{
                "ts_code": code,
                "trade_date": d.replace("-", ""),
                "close": 1.0,
                "amount": 10000.0,
            } for d in history_dates
                if start_date <= d.replace("-", "") <= end_date])

        def _sw(ts_code=None, start_date=None, end_date=None, **_kwargs):
            if ts_code == "801080.SI":
                return _sw_df([{
                    "ts_code": ts_code,
                    "trade_date": d.replace("-", ""),
                    "close": 1.0,
                    "amount": 10000.0,
                } for d in target_dates
                    if start_date <= d.replace("-", "") <= end_date])
            if ts_code in {"801081.SI", "801102.SI"}:
                return _rows_for(ts_code, start_date, end_date)
            return _sw_df([])

        provider.pro.sw_daily.side_effect = _sw

        with pytest.raises(RuntimeError, match="采集失败"):
            service.run_backfill(
                conn,
                _mk_registry_ok(),
                provider,
                "2020-01-02",
                "2020-01-06",
            )
        assert repo.get_snapshot(conn, "2020-01-02") is None

    def test_backfill_skips_existing_daily_rows(self, conn):
        repo.save_snapshot(conn, _rec("2026-07-16"))
        p = _mk_provider(ROWS)
        p.pro.sw_daily.side_effect = lambda ts_code=None, **kw: _sw_df(
            [{**row, "trade_date": "20260716"}
             for row in ROWS if row["ts_code"] == ts_code])
        stats = service.run_backfill(conn, _mk_registry_ok(), p,
                                     "2026-07-16", "2026-07-16")
        assert stats["dates_skipped"] == 1
        # 原行未被覆盖
        assert repo.get_snapshot(conn, "2026-07-16")["market_total_billion"] == 15000.0

    def test_backfill_synthesizes_l1_when_native_missing(self, conn):
        # Explore review 中1:码表有 L1 但 sw_daily 无 L1 行(历史数据缺口)时回填也要合成,
        # 否则 L1 永无历史分位。注意与"码表为空=拉取失败必须中止"区分。
        p = _mk_provider([ROWS[1]], l1_codes={"801080.SI"},
                         parent_map={"801081.SI": "801080.SI"})

        def _sw(ts_code=None, **kw):
            if ts_code == "801080.SI":
                return _sw_df([])  # L1 码正常返回但无行情行(非失败)
            return _sw_df([{**ROWS[1], "trade_date": "20260716"}])

        p.pro.sw_daily.side_effect = _sw
        stats = service.run_backfill(conn, _mk_registry_ok(), p,
                                     "2026-07-16", "2026-07-16")
        assert stats["dates_written"] == 1
        snap = repo.get_snapshot(conn, "2026-07-16")
        assert snap["meta"]["l1_status"] == "synthesized"
        l1 = [s for s in snap["sectors"] if s["level"] == "L1"]
        assert l1 and l1[0]["code"] == "801080.SI"

    def test_backfill_empty_code_table_aborts(self, conn):
        # 码表空集=拉取失败:静默写半截历史会被 existing 集合锁死,必须整体中止
        p = _mk_provider(ROWS, l1_codes=frozenset())
        p._ensure_sw_l2_codes.return_value = set()
        with pytest.raises(RuntimeError):
            service.run_backfill(conn, _mk_registry_ok(), p, "2026-07-16", "2026-07-16")

    def test_backfill_rejects_non_iso_dates(self, conn):
        # YYYYMMDD 入参会让 BETWEEN 跳过判定恒空集 → daily 行被静默覆盖
        with pytest.raises(ValueError):
            service.run_backfill(conn, _mk_registry_ok(), _mk_provider(ROWS),
                                 "20190101", "2026-07-16")

    def test_backfill_code_failure_fails_closed_then_recovers(self, conn):
        # fail-closed(codex 门2 高):部分码失败 → 整体中止不落库;源恢复后重跑可完整自愈
        p = _mk_provider(ROWS)

        def _sw_broken(ts_code=None, **kw):
            if ts_code == "801080.SI":
                raise RuntimeError("boom")
            return _sw_df([{**ROWS[1], "trade_date": "20260716"}])

        p.pro.sw_daily.side_effect = _sw_broken
        with pytest.raises(RuntimeError):
            service.run_backfill(conn, _mk_registry_ok(), p, "2026-07-16", "2026-07-16")
        assert repo.get_snapshot(conn, "2026-07-16") is None  # 未落半截数据

        p2 = _mk_provider(ROWS)

        def _sw_ok(ts_code=None, **kw):
            rows = [r for r in ROWS if r["ts_code"] == ts_code]
            return _sw_df([{**r, "trade_date": "20260716"} for r in rows])

        p2.pro.sw_daily.side_effect = _sw_ok
        stats = service.run_backfill(conn, _mk_registry_ok(), p2,
                                     "2026-07-16", "2026-07-16")
        assert stats["dates_written"] == 1
        snap = repo.get_snapshot(conn, "2026-07-16")
        assert {s["code"] for s in snap["sectors"]} == {"801080.SI", "801081.SI"}

    def test_code_fetch_retries_transient_failure(self, monkeypatch):
        # 瞬时超时重试吸收(330 请求随机掉 1 个曾两轮各废 15 分钟);穷尽才抛
        monkeypatch.setattr(collector, "CODE_FETCH_RETRY_SLEEP_SECONDS", 0.0)
        p = MagicMock()
        calls = {"n": 0}

        def _flaky(**kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise TimeoutError("read timed out")
            return _sw_df([{
                "ts_code": kw.get("ts_code"),
                "trade_date": "20200102",
                "close": 1.0,
                "amount": 10000.0,
            }])

        p.pro.sw_daily.side_effect = _flaky
        bars = collector._fetch_code_history_with_retry(p, "801080.SI",
                                                        "2020-01-01", "2020-12-31")
        assert len(bars) == 1 and calls["n"] == 2

        p.pro.sw_daily.side_effect = TimeoutError("always down")
        with pytest.raises(TimeoutError):
            collector._fetch_code_history_with_retry(p, "801080.SI",
                                                     "2020-01-01", "2020-12-31")

    def test_backfill_counts_null_market_total(self, conn):
        # 总额守卫失败日落 NULL 属设计语义,但必须显式计数(消除覆盖率假信心)
        registry = MagicMock()

        def _call(cap, *a, **kw):
            return MagicMock(success=False, data=None, error="down", source="x")

        registry.call.side_effect = _call
        p = _mk_provider(ROWS)
        p.pro.sw_daily.side_effect = lambda ts_code=None, **kw: _sw_df(
            [{**r, "trade_date": "20260716"} for r in ROWS if r["ts_code"] == ts_code])
        stats = service.run_backfill(conn, registry, p, "2026-07-16", "2026-07-16")
        assert stats["dates_null_total"] == 1
        snap = repo.get_snapshot(conn, "2026-07-16")
        assert snap["market_total_billion"] is None
        assert all(s["share_pct"] is None for s in snap["sectors"])


class TestMigrateEnsure:
    def test_ensure_rebuilds_missing_table_on_v40_db(self, conn):
        # 存量库版本门跳过 init_schema → 表缺失;版本无关兜底必须补建(内存库≠真实库)
        from db.migrate import _ensure_sector_crowding_daily

        conn.execute("DROP TABLE sector_crowding_daily")
        _ensure_sector_crowding_daily(conn)
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name='sector_crowding_daily'"
        ).fetchone() is not None
        _ensure_sector_crowding_daily(conn)  # 健康库幂等


class TestAnalyzer:
    def test_share_pct_basic_and_none_denominator(self):
        assert analyzer.compute_share_pct(3000.0, 15000.0) == 20.0
        assert analyzer.compute_share_pct(3000.0, None) is None
        assert analyzer.compute_share_pct(3000.0, 0) is None

    def test_interval_gain_requires_last_bar_on_end_date(self):
        bars = [(f"2026-07-{d:02d}", 100.0 + i) for i, d in enumerate(range(1, 12))]
        assert analyzer.interval_gain(bars, 5, "2026-07-11") == pytest.approx(
            (110.0 / 105.0 - 1) * 100, abs=0.01)
        # 末根不是目标日（陈旧数据）→ None
        assert analyzer.interval_gain(bars, 5, "2026-07-12") is None

    def test_interval_gain_insufficient_history(self):
        bars = [("2026-07-10", 100.0), ("2026-07-11", 101.0)]
        assert analyzer.interval_gain(bars, 5, "2026-07-11") is None

    def test_rolling_percentile_threshold(self):
        hist = [float(i) for i in range(59)]          # 59 + current = 60 → 达标
        assert analyzer.rolling_percentile(hist, 100.0) == 100.0
        assert analyzer.rolling_percentile(hist[:10], 100.0) is None  # 样本不足

    def _records(self, n_days=100, share=5.0, spike_last=False):
        # n_days=100:20日涨幅历史序列=80个样本 ≥ MIN_PCTILE_SAMPLES=60,分位可算
        recs = []
        for i in range(n_days):
            d = f"2026-{4 + i // 30:02d}-{i % 30 + 1:02d}"
            s = share if not (spike_last and i == n_days - 1) else 45.0
            recs.append({
                "date": d, "market_total_billion": 15000.0,
                "sectors": [{"code": "801080.SI", "name": "电子", "level": "L1",
                             # 平走+末日跳涨:历史20日涨幅≈0,末日70% → 斜率分位=100
                             "close": 170.0 if (spike_last and i == n_days - 1) else 100.0,
                             "amount_billion": 150.0 * s, "share_pct": s}],
                "proxy": None, "meta": None,
            })
        return recs

    def _label_records(self, n_days=233, *, resonance_index=None):
        start = date(2025, 1, 1)
        records = []
        for i in range(n_days):
            close = 100.0
            amount = 10.0
            if resonance_index == i:
                close = 101.0
                amount = 11.0
            records.append({
                "date": (start + timedelta(days=i)).isoformat(),
                "market_total_billion": 15000.0,
                "sectors": [{
                    "code": "801081.SI",
                    "name": "半导体",
                    "level": "L2",
                    "close": close,
                    "amount_billion": amount,
                    "share_pct": 1.0,
                }],
                "proxy": None,
                "meta": None,
            })
        return records

    def test_build_view_double_high(self):
        recs = self._records(spike_last=True)
        view = analyzer.build_view(recs, recs[-1]["date"])
        sec = view["sectors"][0]
        assert sec["share_pctile"] is not None and sec["share_pctile"] >= 90
        assert [s["code"] for s in view["double_high"]] == ["801080.SI"]

    def test_build_view_rejects_date_mismatch(self):
        recs = self._records()
        assert analyzer.build_view(recs, "2026-12-31") is None

    def test_build_view_no_l1_l2_crosstalk(self):
        # 同名不同 level 的 code 分开算分位：L2 历史不足时 L2 分位为 None，但 L1 正常
        recs = self._records()
        for r in recs[-5:]:
            r["sectors"].append({"code": "801081.SI", "name": "半导体", "level": "L2",
                                 "close": 50.0, "amount_billion": 300.0, "share_pct": 2.0})
        view = analyzer.build_view(recs, recs[-1]["date"])
        l2 = [s for s in view["sectors"] if s["level"] == "L2"][0]
        assert l2["share_pctile"] is None  # 样本 5 < 60

    def test_zero_variance_series_not_double_high(self):
        # 恒定死板块:share 与涨幅历史全等值 → 分位 None,绝不能按 100 误判双高
        recs = self._records()  # share 恒 5.0、close 恒 100.0
        view = analyzer.build_view(recs, recs[-1]["date"])
        sec = view["sectors"][0]
        assert sec["share_pctile"] is None
        assert sec["gain_pctile_20d"] is None
        assert view["double_high"] == []

    def test_rolling_percentile_skips_non_finite(self):
        # 公开 API 防线:NaN/None 混入 history 不得崩溃、不得稀释分母
        hist = [float(i) for i in range(59)] + [float("nan"), None]
        assert analyzer.rolling_percentile(hist, 100.0) == 100.0

    def test_duplicate_sector_rows_deduped(self):
        # 同快照同 (level,code) 重复行:保留末条,双高不重复计入
        recs = self._records(spike_last=True)
        dup = dict(recs[-1]["sectors"][0])
        recs[-1]["sectors"] = [dup, recs[-1]["sectors"][0]]
        view = analyzer.build_view(recs, recs[-1]["date"])
        assert len([s for s in view["sectors"] if s["code"] == "801080.SI"]) == 1
        assert len(view["double_high"]) == 1

    def test_sector_labels_ma233_and_same_day_price_volume_resonance(self):
        recs = self._label_records(resonance_index=232)

        labels = analyzer.build_sector_labels(recs, recs[-1]["date"])

        item = labels["items"][0]
        assert item["above_half_year_ma"] is True
        assert item["half_year_ma"] == pytest.approx((143 * 100.0 + 101.0) / 144, abs=0.01)
        assert item["above_year_ma"] is True
        assert item["year_ma"] == pytest.approx((232 * 100.0 + 101.0) / 233, abs=0.01)
        assert item["recent_price_volume_resonance"] is True
        assert item["resonance_age_snapshot_days"] == 0
        assert item["last_resonance"] == {
            "date": recs[-1]["date"],
            "close": 101.0,
            "prior_close_high": 100.0,
            "amount_billion": 11.0,
            "prior_amount_high_billion": 10.0,
        }
        assert labels["summary"]["year_and_resonance"] == 1

    def test_sector_labels_half_year_can_be_above_while_year_is_not(self):
        recs = self._label_records()
        for record in recs[:89]:
            record["sectors"][0]["close"] = 200.0
        recs[-1]["sectors"][0]["close"] = 101.0
        recs[-1]["sectors"][0]["amount_billion"] = 11.0

        labels = analyzer.build_sector_labels(recs, recs[-1]["date"])
        item = labels["items"][0]

        assert item["above_half_year_ma"] is True
        assert item["above_year_ma"] is False
        assert item["recent_price_volume_resonance"] is True
        assert labels["summary"]["year_and_resonance"] == 0

    def test_sector_labels_144_snapshots_only_resolve_half_year(self):
        recs = self._label_records(n_days=144, resonance_index=143)

        item = analyzer.build_sector_labels(recs, recs[-1]["date"])["items"][0]

        assert item["above_half_year_ma"] is True
        assert item["above_year_ma"] is None

    def test_sector_labels_half_year_is_unknown_when_one_window_close_is_missing(self):
        recs = self._label_records()
        recs[-50]["sectors"][0]["close"] = None

        item = analyzer.build_sector_labels(recs, recs[-1]["date"])["items"][0]

        assert item["half_year_ma"] is None
        assert item["above_half_year_ma"] is None

    def test_sector_labels_preserve_precision_for_strict_ma_comparison(self):
        recs = self._label_records()
        recs[0]["sectors"][0]["close"] = 99.99

        item = analyzer.build_sector_labels(recs, recs[-1]["date"])["items"][0]

        assert item["above_year_ma"] is True
        assert item["close"] > item["year_ma"]
        assert item["year_ma"] == pytest.approx(
            (99.99 + 232 * 100.0) / 233,
            abs=1e-12,
        )
        assert round(item["year_ma"], 2) == item["close"]

    def test_sector_labels_windows_follow_available_trading_snapshots(self):
        recs = self._label_records(n_days=234, resonance_index=233)
        del recs[100]  # 中间自然日无整批快照；剩余 233 个快照仍构成完整 MA 窗口。

        item = analyzer.build_sector_labels(recs, recs[-1]["date"])["items"][0]

        assert item["above_year_ma"] is True
        assert item["recent_price_volume_resonance"] is True
        assert item["resonance_age_snapshot_days"] == 0

    def test_sector_labels_require_close_and_amount_breakout_on_same_day(self):
        recs = self._label_records()
        recs[-1]["sectors"][0]["close"] = 101.0
        recs[-1]["sectors"][0]["amount_billion"] = 10.0

        item = analyzer.build_sector_labels(recs, recs[-1]["date"])["items"][0]

        assert item["above_year_ma"] is True
        assert item["recent_price_volume_resonance"] is False
        assert item["last_resonance"] is None

    def test_sector_labels_resonance_expires_after_ten_snapshot_days(self):
        recs = self._label_records(n_days=244, resonance_index=233)

        item = analyzer.build_sector_labels(recs, recs[-1]["date"])["items"][0]

        assert item["recent_price_volume_resonance"] is False
        assert item["last_resonance"] is None

    def test_sector_labels_resonance_age_nine_is_still_recent(self):
        recs = self._label_records(n_days=243, resonance_index=233)

        item = analyzer.build_sector_labels(recs, recs[-1]["date"])["items"][0]

        assert item["recent_price_volume_resonance"] is True
        assert item["resonance_age_snapshot_days"] == 9

    def test_sector_labels_preserve_precision_for_resonance_evidence(self):
        recs = self._label_records()
        for record in recs:
            record["sectors"][0]["close"] = 100.0001
            record["sectors"][0]["amount_billion"] = 10.0001
        recs[-1]["sectors"][0]["close"] = 100.0002
        recs[-1]["sectors"][0]["amount_billion"] = 10.0002

        item = analyzer.build_sector_labels(recs, recs[-1]["date"])["items"][0]
        event = item["last_resonance"]

        assert item["recent_price_volume_resonance"] is True
        assert event["close"] == 100.0002
        assert event["prior_close_high"] == 100.0001
        assert event["amount_billion"] == 10.0002
        assert event["prior_amount_high_billion"] == 10.0001

    def test_sector_labels_reject_price_and_amount_breakouts_on_different_days(self):
        recs = self._label_records()
        recs[-2]["sectors"][0]["close"] = 101.0
        recs[-1]["sectors"][0]["amount_billion"] = 11.0

        item = analyzer.build_sector_labels(recs, recs[-1]["date"])["items"][0]

        assert item["recent_price_volume_resonance"] is False

    def test_sector_labels_partial_recent_window_is_unknown_without_hit(self):
        recs = self._label_records(n_days=25)

        item = analyzer.build_sector_labels(recs, recs[-1]["date"])["items"][0]

        assert item["recent_price_volume_resonance"] is None

    def test_sector_labels_equal_to_mas_is_not_above(self):
        recs = self._label_records()

        item = analyzer.build_sector_labels(recs, recs[-1]["date"])["items"][0]

        assert item["year_ma"] == 100.0
        assert item["half_year_ma"] == 100.0
        assert item["above_half_year_ma"] is False
        assert item["above_year_ma"] is False

    @pytest.mark.parametrize(
        ("n_days", "close", "ma_key", "position_key"),
        [
            (144, 1000.05, "half_year_ma", "above_half_year_ma"),
            (233, 1000.04, "year_ma", "above_year_ma"),
        ],
    )
    def test_sector_labels_decimal_equal_to_ma_is_not_above(
        self,
        n_days,
        close,
        ma_key,
        position_key,
    ):
        recs = self._label_records(n_days=n_days)
        for record in recs:
            record["sectors"][0]["close"] = close

        item = analyzer.build_sector_labels(recs, recs[-1]["date"])["items"][0]

        assert item[ma_key] == close
        assert item[position_key] is False

    def test_sector_labels_only_emit_l2(self):
        recs = self._label_records()
        for record in recs:
            record["sectors"].append({
                "code": "801080.SI",
                "name": "电子",
                "level": "L1",
                "close": 999.0,
                "amount_billion": 999.0,
                "share_pct": 9.0,
            })

        labels = analyzer.build_sector_labels(recs, recs[-1]["date"])

        assert [item["code"] for item in labels["items"]] == ["801081.SI"]

    def test_sector_labels_partial_target_keeps_previous_l2_as_unknown(self):
        recs = self._label_records()
        for record in recs:
            record["sectors"].append({
                "code": "801102.SI",
                "name": "通信设备",
                "level": "L2",
                "close": 88.0,
                "amount_billion": 8.0,
                "share_pct": 0.8,
            })
        recs[-1]["sectors"] = [
            sector
            for sector in recs[-1]["sectors"]
            if sector["code"] != "801102.SI"
        ]

        labels = analyzer.build_sector_labels(recs, recs[-1]["date"])

        assert labels["available"] is True
        assert labels["status"] == "partial"
        assert labels["summary"]["total_l2"] == 2
        assert labels["summary"]["missing_l2_count"] == 1
        missing = next(
            item for item in labels["items"] if item["code"] == "801102.SI"
        )
        assert missing["present_on_target"] is False
        assert missing["close"] is None
        assert missing["amount_billion"] is None
        assert missing["half_year_ma"] is None
        assert missing["above_half_year_ma"] is None
        assert missing["year_ma"] is None
        assert missing["above_year_ma"] is None
        assert missing["recent_price_volume_resonance"] is None

    def test_sector_labels_consecutive_partial_does_not_shrink_legacy_universe(self):
        recs = self._label_records(n_days=3)
        for record in recs:
            record["sectors"].append({
                "code": "801102.SI",
                "name": "通信设备",
                "level": "L2",
                "close": 88.0,
                "amount_billion": 8.0,
                "share_pct": 0.8,
            })
        for record in recs[-2:]:
            record["sectors"] = [
                sector
                for sector in record["sectors"]
                if sector["code"] != "801102.SI"
            ]

        labels = analyzer.build_sector_labels(recs, recs[-1]["date"])

        assert labels["status"] == "partial"
        assert labels["summary"]["total_l2"] == 2
        assert labels["summary"]["missing_l2_count"] == 1
        missing = next(
            item for item in labels["items"] if item["code"] == "801102.SI"
        )
        assert missing["name"] == "通信设备"
        assert missing["present_on_target"] is False

    def test_sector_labels_consecutive_full_missing_keeps_legacy_universe(self):
        recs = self._label_records(n_days=3)
        for record in recs:
            record["sectors"].append({
                "code": "801102.SI",
                "name": "通信设备",
                "level": "L2",
                "close": 88.0,
                "amount_billion": 8.0,
                "share_pct": 0.8,
            })
        for record in recs[-2:]:
            record["sectors"] = [{
                "code": "801080.SI",
                "name": "电子",
                "level": "L1",
                "close": 5000.0,
                "amount_billion": 3000.0,
                "share_pct": 20.0,
            }]

        labels = analyzer.build_sector_labels(recs, recs[-1]["date"])

        assert labels["available"] is False
        assert labels["status"] == "missing_l2"
        assert labels["summary"]["total_l2"] == 2
        assert labels["summary"]["missing_l2_count"] == 2
        assert all(item["present_on_target"] is False for item in labels["items"])

    def test_sector_labels_fallback_prefers_latest_trusted_prior_universe(self):
        recs = self._label_records(n_days=3)
        recs[0]["sectors"].append({
            "code": "801999.SI",
            "name": "旧分类",
            "level": "L2",
            "close": 80.0,
            "amount_billion": 6.0,
            "share_pct": 0.6,
        })
        recs[1]["sectors"].append({
            "code": "801102.SI",
            "name": "通信设备",
            "level": "L2",
            "close": 88.0,
            "amount_billion": 8.0,
            "share_pct": 0.8,
        })
        recs[1]["meta"] = {
            "l2_expected_codes": ["801081.SI", "801102.SI"],
            "l2_expected_count": 2,
            "l2_observed_count": 2,
            "l2_universe_complete": True,
        }

        labels = analyzer.build_sector_labels(recs, recs[-1]["date"])

        assert labels["status"] == "partial"
        assert {item["code"] for item in labels["items"]} == {
            "801081.SI",
            "801102.SI",
        }
        assert all(item["code"] != "801999.SI" for item in labels["items"])

    def test_sector_labels_trusted_target_universe_allows_legitimate_code_switch(self):
        recs = self._label_records()
        for record in recs:
            record["sectors"].append({
                "code": "801999.SI",
                "name": "旧分类",
                "level": "L2",
                "close": 80.0,
                "amount_billion": 6.0,
                "share_pct": 0.6,
            })
        recs[-1]["sectors"] = [
            sector
            for sector in recs[-1]["sectors"]
            if sector["code"] != "801999.SI"
        ]
        recs[-1]["sectors"].append({
            "code": "801102.SI",
            "name": "通信设备",
            "level": "L2",
            "close": 88.0,
            "amount_billion": 8.0,
            "share_pct": 0.8,
        })
        recs[-1]["meta"] = {
            "l2_expected_codes": ["801081.SI", "801102.SI"],
            "l2_expected_count": 2,
            "l2_observed_count": 2,
            "l2_universe_complete": True,
        }

        labels = analyzer.build_sector_labels(recs, recs[-1]["date"])

        assert labels["status"] == "success"
        assert labels["summary"]["total_l2"] == 2
        assert labels["summary"]["missing_l2_count"] == 0
        assert {item["code"] for item in labels["items"]} == {
            "801081.SI",
            "801102.SI",
        }

    def test_sector_labels_rejects_empty_trusted_universe_metadata(self):
        recs = self._label_records()
        for record in recs[:-1]:
            record["sectors"].append({
                "code": "801102.SI",
                "name": "通信设备",
                "level": "L2",
                "close": 88.0,
                "amount_billion": 8.0,
                "share_pct": 0.8,
            })
        recs[-1]["meta"] = {
            "l2_expected_codes": [],
            "l2_expected_count": 0,
            "l2_observed_count": 0,
            "l2_universe_complete": True,
        }

        labels = analyzer.build_sector_labels(recs, recs[-1]["date"])

        assert labels["status"] == "partial"
        assert labels["summary"]["total_l2"] == 2
        assert labels["summary"]["missing_l2_count"] == 1

    def test_sector_labels_rejects_observed_count_mismatch_in_metadata(self):
        recs = self._label_records()
        for record in recs[:-1]:
            record["sectors"].append({
                "code": "801999.SI",
                "name": "旧分类",
                "level": "L2",
                "close": 80.0,
                "amount_billion": 6.0,
                "share_pct": 0.6,
            })
        recs[-1]["sectors"].append({
            "code": "801102.SI",
            "name": "通信设备",
            "level": "L2",
            "close": 88.0,
            "amount_billion": 8.0,
            "share_pct": 0.8,
        })
        recs[-1]["meta"] = {
            "l2_expected_codes": ["801081.SI", "801102.SI"],
            "l2_expected_count": 2,
            "l2_observed_count": 1,
            "l2_universe_complete": True,
        }

        labels = analyzer.build_sector_labels(recs, recs[-1]["date"])

        assert labels["status"] == "partial"
        assert labels["summary"]["total_l2"] == 3
        assert labels["summary"]["missing_l2_count"] == 1
        assert any(item["code"] == "801999.SI" for item in labels["items"])

    @pytest.mark.parametrize(
        "meta",
        [
            "not-a-dict",
            {
                "l2_expected_codes": ["801081.SI", "801081.SI"],
                "l2_expected_count": 1,
                "l2_observed_count": 1,
                "l2_universe_complete": True,
            },
            {
                "l2_expected_codes": ["801081.SI", 123],
                "l2_expected_count": 1,
                "l2_observed_count": 1,
                "l2_universe_complete": True,
            },
        ],
    )
    def test_sector_labels_rejects_malformed_trusted_metadata(self, meta):
        record = self._label_records(n_days=1)[0]
        record["meta"] = meta

        assert analyzer._trusted_expected_l2_codes(
            record,
            {"801081.SI"},
        ) is None

    def test_sector_labels_full_l2_missing_keeps_previous_universe_unknown(self):
        recs = self._label_records()
        for record in recs:
            record["sectors"].append({
                "code": "801102.SI",
                "name": "通信设备",
                "level": "L2",
                "close": 88.0,
                "amount_billion": 8.0,
                "share_pct": 0.8,
            })
        recs[-1]["sectors"] = [{
            "code": "801080.SI",
            "name": "电子",
            "level": "L1",
            "close": 5000.0,
            "amount_billion": 3000.0,
            "share_pct": 20.0,
        }]

        labels = analyzer.build_sector_labels(recs, recs[-1]["date"])

        assert labels["available"] is False
        assert labels["status"] == "missing_l2"
        assert labels["summary"]["total_l2"] == 2
        assert labels["summary"]["missing_l2_count"] == 2
        assert labels["summary"]["half_year_ma_insufficient"] == 2
        assert labels["summary"]["year_ma_insufficient"] == 2
        assert labels["summary"]["resonance_insufficient"] == 2
        assert all(item["present_on_target"] is False for item in labels["items"])
        assert all(item["above_half_year_ma"] is None for item in labels["items"])
        assert all(item["above_year_ma"] is None for item in labels["items"])
        assert all(
            item["recent_price_volume_resonance"] is None
            for item in labels["items"]
        )

    def test_sector_labels_mark_insufficient_history_unknown(self):
        recs = self._label_records(n_days=19)

        labels = analyzer.build_sector_labels(recs, recs[-1]["date"])
        item = labels["items"][0]

        assert item["half_year_ma"] is None
        assert item["above_half_year_ma"] is None
        assert item["year_ma"] is None
        assert item["above_year_ma"] is None
        assert item["recent_price_volume_resonance"] is None
        assert labels["summary"]["half_year_ma_insufficient"] == 1
        assert labels["summary"]["year_ma_insufficient"] == 1
        assert labels["summary"]["resonance_insufficient"] == 1

    def test_sector_labels_reject_stale_target_date(self):
        recs = self._label_records()
        assert analyzer.build_sector_labels(recs, "2099-01-01") is None
