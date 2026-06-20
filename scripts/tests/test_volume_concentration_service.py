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

    def initialize_all(self):  # CLI 路径调用,no-op
        pass

    def call(self, method_name: str, *args, **kwargs):
        resp = self.responses.get(method_name)
        if callable(resp):  # 支持按入参动态返回(如 get_stock_daily_range 逐 code)
            return resp(*args, **kwargs)
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


# ──────────────────────────────────────────────────────────────
# 区间涨幅排名:run_daily 接线 + build_sector_gain_ranking_payload
# ──────────────────────────────────────────────────────────────

def _gain_registry():
    """top50 + sw 映射 + 个股区间日线(单调上行)全 stub。

    bars 末根 trade_date = 请求的 end(满足 enrich_interval_gains 的"最新一根=榜单日"诚实守卫)。
    """
    import datetime as _dt
    closes = [round(10.0 + 0.1 * i, 3) for i in range(25)]

    def _range(code, start, end):
        base = _dt.date.fromisoformat(end)
        bars = [
            {"trade_date": (base - _dt.timedelta(days=(len(closes) - 1 - i))).isoformat(), "close": c}
            for i, c in enumerate(closes)
        ]
        return DataResult(data=bars, source="tushare:daily")

    return _FakeRegistry({
        "get_top_volume_stocks": DataResult(
            data=[{"rank": 1, "code": "300750.SZ", "name": "", "amount_billion": 60.0},
                  {"rank": 2, "code": "600519.SH", "name": "", "amount_billion": 40.0}],
            source="tushare:daily"),
        "get_stock_sw_industry_map": DataResult(
            data={"300750.SZ": {"name": "宁德时代", "sw_l2": "电池"},
                  "600519.SH": {"name": "贵州茅台", "sw_l2": "白酒Ⅱ"}},
            source="tushare:index_member_all"),
        "get_stock_basic_batch": DataResult(data=[], source="tushare:stock_basic"),
        "get_market_volume": DataResult(data={"total_billion": 9000.0}, source="tushare:index_daily"),
        "get_stock_daily_range": _range,
    })


def test_run_daily_persists_gain_universe():
    conn = _conn()
    # build_record 走 read-through(daily_market 已 seed);build_gain_universe 走 get_top_volume_stocks
    _seed_daily_market(conn, "2026-05-29", [{"code": "300750.SZ", "name": "", "amount_billion": 60.0}])

    md = service.run_daily(conn, _gain_registry(), "2026-05-29")

    assert "### 📈 板块区间涨幅排名(成交额前50)" in md
    rec = repo.get_concentration(conn, "2026-05-29")
    codes = {s["code"] for s in rec["gain_universe"]}
    assert codes == {"300750.SZ", "600519.SH"}  # top50 原始集已落库


def test_build_sector_gain_ranking_payload_from_db():
    conn = _conn()
    _seed_daily_market(conn, "2026-05-29", [{"code": "300750.SZ", "name": "", "amount_billion": 60.0}])
    service.run_daily(conn, _gain_registry(), "2026-05-29")

    payload = service.build_sector_gain_ranking_payload(conn, "2026-05-29")

    assert payload["date"] == "2026-05-29"
    assert set(payload["rankings"].keys()) == {"5d", "10d", "20d"}
    inds = {s["industry"] for s in payload["rankings"]["5d"]}
    assert "电池" in inds and "白酒Ⅱ" in inds


def test_build_sector_gain_ranking_payload_empty_shell_when_no_record():
    conn = _conn()
    payload = service.build_sector_gain_ranking_payload(conn, "2099-01-01")
    empty = {"5d": [], "10d": [], "20d": []}
    assert payload == {"date": "2099-01-01", "rankings": empty, "concept_rankings": empty}


def test_run_daily_gain_universe_failure_does_not_block_concentration():
    """区间涨幅取数抛错(脏 bar:含非 dict 行)→ fail-closed 降级 gain_universe=[],
    但既有 Top20 集中度仍正常落库 + 渲染(可选排名特性绝不拖垮主日报)。"""
    conn = _conn()
    _seed_daily_market(conn, "2026-05-29", [{"code": "300750.SZ", "name": "", "amount_billion": 60.0}])
    registry = _FakeRegistry({
        "get_top_volume_stocks": DataResult(
            data=[{"rank": 1, "code": "300750.SZ", "name": "", "amount_billion": 60.0}], source="tushare:daily"),
        "get_stock_sw_industry_map": DataResult(
            data={"300750.SZ": {"name": "宁德时代", "sw_l2": "电池"}}, source="tushare:index_member_all"),
        "get_stock_basic_batch": DataResult(data=[], source="tushare:stock_basic"),
        "get_market_volume": DataResult(data={"total_billion": 9000.0}, source="tushare:index_daily"),
        # 脏 bar：含非 dict 行 → enrich_interval_gains 的 b.get 抛 AttributeError，模拟 provider 脏数据
        "get_stock_daily_range": DataResult(
            data=[{"trade_date": "2026-05-29", "close": 10.0}, "garbage"], source="tushare:daily"),
    })

    md = service.run_daily(conn, registry, "2026-05-29")

    assert md is not None
    assert "电池" in md                                       # 集中度日报照常渲染
    rec = repo.get_concentration(conn, "2026-05-29")
    assert rec is not None                                    # 基础集中度仍落库
    assert rec["gain_universe"] == []                         # 区间涨幅 fail-closed 降级为空
    assert "板块区间涨幅排名" not in md                        # 排名段缺省(降级，不阻断)


def test_run_daily_failed_gain_rerun_preserves_existing_ranking():
    """同日先成功落库 gain_universe,后 top50 取数失败重跑 → 保留前次榜单,不被空覆盖
    (codex 高:Top20 read-through 成功但 top50 provider 抖动的重跑不得抹掉已算榜单;幂等)。"""
    conn = _conn()
    _seed_daily_market(conn, "2026-05-29", [{"code": "300750.SZ", "name": "", "amount_billion": 60.0}])

    # 第一次:成功落库
    service.run_daily(conn, _gain_registry(), "2026-05-29")
    before = repo.get_concentration(conn, "2026-05-29")["gain_universe"]
    assert before  # 非空,已成功落库

    # 第二次:top50 取数失败(get_top_volume_stocks 返空)→ build_gain_universe 返 []
    fail_reg = _FakeRegistry({
        "get_top_volume_stocks": DataResult(data=[], source="tushare:daily"),  # top50 失败
        "get_stock_sw_industry_map": DataResult(
            data={"300750.SZ": {"name": "宁德时代", "sw_l2": "电池"}}, source="tushare:index_member_all"),
        "get_stock_basic_batch": DataResult(data=[], source="tushare:stock_basic"),
        "get_market_volume": DataResult(data={"total_billion": 9000.0}, source="tushare:index_daily"),
    })
    service.run_daily(conn, fail_reg, "2026-05-29")
    after = repo.get_concentration(conn, "2026-05-29")["gain_universe"]

    assert after == before  # 前次榜单被保留,未被降级空覆盖


def test_run_daily_all_range_failure_rerun_preserves_ranking():
    """同日先成功,后 top50 成功但全部 get_stock_daily_range 失败(非空但 gain 全 None)重跑:
    无可用覆盖 → 视为降级,保留前次榜单不抹(codex 高:'非空全 None' 也不得覆盖有效榜单)。"""
    conn = _conn()
    _seed_daily_market(conn, "2026-05-29", [{"code": "300750.SZ", "name": "", "amount_billion": 60.0}])

    service.run_daily(conn, _gain_registry(), "2026-05-29")  # 第一次成功
    before = repo.get_concentration(conn, "2026-05-29")["gain_universe"]
    assert before and service._has_gain_coverage(before)

    # 第二次:top50 成功(2 只),但 get_stock_daily_range 全失败 → universe 非空但 gain 全 None
    degraded_reg = _FakeRegistry({
        "get_top_volume_stocks": DataResult(
            data=[{"rank": 1, "code": "300750.SZ", "name": "", "amount_billion": 60.0},
                  {"rank": 2, "code": "600519.SH", "name": "", "amount_billion": 40.0}],
            source="tushare:daily"),
        "get_stock_sw_industry_map": DataResult(
            data={"300750.SZ": {"name": "宁德时代", "sw_l2": "电池"},
                  "600519.SH": {"name": "贵州茅台", "sw_l2": "白酒Ⅱ"}}, source="tushare:index_member_all"),
        "get_stock_basic_batch": DataResult(data=[], source="tushare:stock_basic"),
        "get_market_volume": DataResult(data={"total_billion": 9000.0}, source="tushare:index_daily"),
        "get_stock_daily_range": DataResult(data=None, source="tushare", error="boom"),  # 区间日线全失败
    })
    service.run_daily(conn, degraded_reg, "2026-05-29")
    after = repo.get_concentration(conn, "2026-05-29")["gain_universe"]

    assert after == before  # 非空全 None 不算成功 → 保留前次有效榜单


def test_run_daily_all_unclassified_rerun_preserves_ranking():
    """同日先成功,后申万映射失败(价格成功但全『未分类』→ 派生空榜单)重跑:
    以最终排名为判据,无可见榜单 → 视为降级,保留前次榜单不抹(codex 高:申万映射降级也不得删榜单)。"""
    conn = _conn()
    _seed_daily_market(conn, "2026-05-29", [{"code": "300750.SZ", "name": "", "amount_billion": 60.0}])

    service.run_daily(conn, _gain_registry(), "2026-05-29")  # 第一次成功(有分类榜单)
    before = repo.get_concentration(conn, "2026-05-29")["gain_universe"]
    assert before and service._has_gain_coverage(before)

    import datetime as _dt
    cl = [round(10.0 + 0.1 * i, 3) for i in range(25)]
    base = _dt.date(2026, 5, 29)
    bars = [{"trade_date": (base - _dt.timedelta(days=(len(cl) - 1 - i))).isoformat(), "close": c}
            for i, c in enumerate(cl)]
    # 申万映射失败(空 map) + stock_basic 空 → 全『未分类』；但价格区间成功 → gain 有值
    unclassified_reg = _FakeRegistry({
        "get_top_volume_stocks": DataResult(
            data=[{"rank": 1, "code": "300750.SZ", "name": "宁德时代", "amount_billion": 60.0}],
            source="tushare:daily"),
        "get_stock_sw_industry_map": DataResult(data={}, source="tushare:index_member_all"),  # 映射降级
        "get_stock_basic_batch": DataResult(data=[], source="tushare:stock_basic"),
        "get_market_volume": DataResult(data={"total_billion": 9000.0}, source="tushare:index_daily"),
        "get_stock_daily_range": DataResult(data=bars, source="tushare:daily"),  # 价格成功
    })
    service.run_daily(conn, unclassified_reg, "2026-05-29")
    after = repo.get_concentration(conn, "2026-05-29")["gain_universe"]

    assert after == before  # 全『未分类』派生空榜单 → 不算成功 → 保留前次有效榜单


def test_volume_watch_cli_migrates_v33_db_before_save(tmp_path, monkeypatch):
    """v33 老库(daily_volume_concentration 无 gain_universe_json)经 volume-watch CLI 路径:
    CLI 连库后 migrate 补列 → save_concentration 不崩(部署兼容回归;codex 高)。"""
    import argparse
    from db.connection import get_connection
    from db.migrate import migrate
    import db.connection as dbconn
    from cli import volume_watch

    db = tmp_path / "v33.db"
    conn = get_connection(db)
    migrate(conn)
    # 退回 v33 形态：重建无 gain_universe_json 列的同名表 + 降版本号 + seed top_volume_stocks
    conn.execute("DROP TABLE daily_volume_concentration")
    conn.execute(
        "CREATE TABLE daily_volume_concentration ("
        "date TEXT PRIMARY KEY, top_n INTEGER NOT NULL DEFAULT 20, "
        "total_amount_billion REAL NOT NULL, market_total_billion REAL, "
        "stocks_json TEXT NOT NULL, sector_summary_json TEXT NOT NULL, source_json TEXT, "
        "created_at TEXT, updated_at TEXT)"
    )
    conn.execute(
        "INSERT INTO daily_market (date, top_volume_stocks) VALUES ('2026-05-29', ?)",
        (json.dumps([{"code": "300750.SZ", "name": "", "amount_billion": 60.0}]),),
    )
    conn.execute("PRAGMA user_version = 33")
    conn.commit()
    conn.close()

    monkeypatch.setattr(dbconn, "_DEFAULT_DB_PATH", db)
    monkeypatch.setattr("main.setup_providers", lambda config: _gain_registry())
    monkeypatch.setattr(volume_watch, "_push_to_dingtalk", lambda *a, **k: None)

    args = argparse.Namespace(date="2026-05-29", dry_run=False, refetch=False)
    volume_watch._run_daily({}, args)  # v33 老库不应抛 OperationalError(no column gain_universe_json)

    conn2 = get_connection(db)
    cols = {r[1] for r in conn2.execute("PRAGMA table_info(daily_volume_concentration)").fetchall()}
    assert "gain_universe_json" in cols  # CLI migrate 已补列
    row = conn2.execute(
        "SELECT total_amount_billion FROM daily_volume_concentration WHERE date='2026-05-29'"
    ).fetchone()
    assert row is not None and row[0] == 60.0  # 集中度成功落库(未崩)
    conn2.close()


# ──────────────────────────────────────────────────────────────
# 题材榜（同花顺概念）接线 + fail-closed + 概念维度幂等
# ──────────────────────────────────────────────────────────────

def _ths_rows():
    """两只票各挂 CPO（窄概念，2 成员）。"""
    return [
        {"con_code": "300750.SZ", "index_name": "共封装光学(CPO)"},
        {"con_code": "600519.SH", "index_name": "共封装光学(CPO)"},
    ]


def _add_hot_concepts(reg):
    """让 ths_member + 资金 moneyflow 都成功(CPO 为热概念)。"""
    reg.responses["get_ths_member"] = DataResult(data=_ths_rows(), source="tushare:ths_member")
    reg.responses["get_concept_moneyflow_ths"] = DataResult(
        data=[{"name": "共封装光学(CPO)", "net_amount": 100.0}], source="tushare")
    return reg


def test_run_daily_persists_concepts_and_payload_has_concept_rankings():
    conn = _conn()
    _seed_daily_market(conn, "2026-05-29", [{"code": "300750.SZ", "name": "", "amount_billion": 60.0}])
    reg = _add_hot_concepts(_gain_registry())

    md = service.run_daily(conn, reg, "2026-05-29")

    assert "题材区间涨幅排名" in md                       # 题材榜段进了报告
    rec = repo.get_concentration(conn, "2026-05-29")
    assert all("concepts" in s for s in rec["gain_universe"])   # 概念已落库
    payload = service.build_sector_gain_ranking_payload(conn, "2026-05-29")
    assert "共封装光学(CPO)" in {s["industry"] for s in payload["concept_rankings"]["5d"]}


def test_run_daily_concept_failure_does_not_block_sector_ranking():
    """ths_member 取数失败 → 题材榜空,但申万榜 + 主日报照常。"""
    conn = _conn()
    _seed_daily_market(conn, "2026-05-29", [{"code": "300750.SZ", "name": "", "amount_billion": 60.0}])
    reg = _gain_registry()
    reg.responses["get_ths_member"] = DataResult(data=None, source="tushare", error="boom")

    md = service.run_daily(conn, reg, "2026-05-29")

    assert "板块区间涨幅排名" in md            # 申万榜在
    assert "题材区间涨幅排名" not in md        # 题材榜降级缺省
    rec = repo.get_concentration(conn, "2026-05-29")
    assert rec["gain_universe"][0]["concepts"] == []   # 概念空（不影响 gain）


def test_run_daily_concept_failure_rerun_preserves_concepts():
    """同日先成功落库题材,后 ths_member 失败重跑 → 按码回填既有 concepts,不抹题材榜（幂等）。"""
    conn = _conn()
    _seed_daily_market(conn, "2026-05-29", [{"code": "300750.SZ", "name": "", "amount_billion": 60.0}])
    ok_reg = _add_hot_concepts(_gain_registry())
    service.run_daily(conn, ok_reg, "2026-05-29")
    before = repo.get_concentration(conn, "2026-05-29")["gain_universe"]
    assert any(s["concepts"] for s in before)   # 已落题材

    # 第二次：gains 仍成功，但 ths_member 失败
    fail_reg = _gain_registry()
    fail_reg.responses["get_ths_member"] = DataResult(data=None, source="tushare", error="boom")
    service.run_daily(conn, fail_reg, "2026-05-29")
    after = repo.get_concentration(conn, "2026-05-29")["gain_universe"]

    after_concepts = {s["code"]: s["concepts"] for s in after}
    before_concepts = {s["code"]: s["concepts"] for s in before}
    assert after_concepts == before_concepts   # 既有题材被回填保留，未被抹空


def test_run_daily_concept_enrich_exception_does_not_lose_sector_ranking(monkeypatch):
    """enrich_concepts 抛异常(非预期)→ 申万 universe 不被清空,申万榜+主日报照常落库渲染。"""
    conn = _conn()
    _seed_daily_market(conn, "2026-05-29", [{"code": "300750.SZ", "name": "", "amount_billion": 60.0}])
    reg = _gain_registry()

    def _boom(*a, **k):
        raise RuntimeError("concept boom")
    monkeypatch.setattr("services.volume_concentration.collector.enrich_concepts", _boom)

    md = service.run_daily(conn, reg, "2026-05-29")

    assert "板块区间涨幅排名" in md           # 申万榜未被概念异常拖垮
    rec = repo.get_concentration(conn, "2026-05-29")
    assert service._has_gain_coverage(rec["gain_universe"])     # 申万 universe 完整落库
    assert all(s["concepts"] == [] for s in rec["gain_universe"])  # 概念降级为空


def test_run_daily_healthy_empty_concepts_not_backfilled():
    """同日先成功落 CPO(2 票),后概念链路成功(concept_ok=True)但当日 CPO 只剩单票(题材榜空·健康空)
    → 不回填旧 concepts;之前挂 CPO 的 600519 这次如实置空,不被旧 stale 题材污染(codex 高:反向污染)。"""
    conn = _conn()
    _seed_daily_market(conn, "2026-05-29", [{"code": "300750.SZ", "name": "", "amount_billion": 60.0}])

    ok_reg = _add_hot_concepts(_gain_registry())   # CPO 2 成员(300750+600519)→ 出榜
    service.run_daily(conn, ok_reg, "2026-05-29")
    before = {s["code"]: s["concepts"] for s in repo.get_concentration(conn, "2026-05-29")["gain_universe"]}
    assert before["600519.SH"] == ["共封装光学(CPO)"]   # 第一次两票都挂 CPO

    # 第二次:concept_ok=True(ths+moneyflow 都成功),但 CPO 只 300750 挂(单票<2 成员→题材榜空·健康)
    healthy_reg = _gain_registry()
    healthy_reg.responses["get_ths_member"] = DataResult(
        data=[{"con_code": "300750.SZ", "index_name": "共封装光学(CPO)"}], source="tushare:ths_member")
    healthy_reg.responses["get_concept_moneyflow_ths"] = DataResult(
        data=[{"name": "共封装光学(CPO)", "net_amount": 100.0}], source="tushare")
    service.run_daily(conn, healthy_reg, "2026-05-29")
    after = {s["code"]: s["concepts"] for s in repo.get_concentration(conn, "2026-05-29")["gain_universe"]}

    assert after["600519.SH"] == []                    # 健康空 → 如实置空,未回填旧 CPO
    assert after["300750.SZ"] == ["共封装光学(CPO)"]   # 当前真有 CPO 的票仍如实打标


def test_run_daily_empty_moneyflow_rerun_preserves_concepts():
    """同日先成功落 CPO,后 ths+gains 成功但资金 moneyflow 返空表(静默降级)→ concept_ok=False
    → 按码回填既有 concepts,不抹题材榜(codex 高:空成功响应不冒充健康空)。"""
    conn = _conn()
    _seed_daily_market(conn, "2026-05-29", [{"code": "300750.SZ", "name": "", "amount_billion": 60.0}])
    service.run_daily(conn, _add_hot_concepts(_gain_registry()), "2026-05-29")
    before = {s["code"]: s["concepts"] for s in repo.get_concentration(conn, "2026-05-29")["gain_universe"]}
    assert before["600519.SH"] == ["共封装光学(CPO)"]

    empty_mf_reg = _gain_registry()
    empty_mf_reg.responses["get_ths_member"] = DataResult(data=_ths_rows(), source="tushare:ths_member")
    empty_mf_reg.responses["get_concept_moneyflow_ths"] = DataResult(data=[], source="tushare")  # 空表
    service.run_daily(conn, empty_mf_reg, "2026-05-29")
    after = {s["code"]: s["concepts"] for s in repo.get_concentration(conn, "2026-05-29")["gain_universe"]}

    assert after == before   # 空 moneyflow=降级 → 回填保留既有 CPO,未被抹空
