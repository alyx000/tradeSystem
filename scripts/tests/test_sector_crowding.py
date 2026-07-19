"""sector_crowding 测试：覆盖 schema / repo / analyzer / collector（后续阶段扩展 service/formatter）。"""
import sqlite3
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
        amounts = {s["code"]: s["amount_billion"] for s in out["sectors"]}
        assert amounts["801080.SI"] == pytest.approx(3000.0)

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

    def test_run_daily_non_trading_day_guard(self, conn, monkeypatch):
        monkeypatch.setattr(service, "is_non_trading_day", lambda *a: True)
        md = service.run_daily(conn, _mk_registry_ok(), _mk_provider(ROWS), "2026-07-19")
        assert md is None
        assert repo.get_snapshot(conn, "2026-07-19") is None
        # dry-run 豁免守卫（与 sector_correlation 同语义）
        md2 = service.run_daily(conn, _mk_registry_ok(), _mk_provider(ROWS),
                                "2026-07-19", persist=False)
        assert md2 is not None

    def test_run_trend(self, conn):
        service.run_daily(conn, _mk_registry_ok(), _mk_provider(ROWS), "2026-07-17")
        md = service.run_trend(conn, "2026-07-17", "801080.SI", days=30)
        assert "2026-07-17" in md and "20.0%" in md


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
