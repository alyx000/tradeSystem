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


def _mv_result(sh: float, sz: float) -> DataResult:
    return DataResult(
        data={"shanghai_billion": sh, "shenzhen_billion": sz,
              "total_billion": round(sh + sz, 2)},
        source="tushare:index_daily",
    )


def test_market_total_guard_rejects_degraded_sz_leg():
    """守卫·比例地板：深市腿退化为成指口径(事故实测 8440/11924≈0.71<0.8) → 落 None 不落坏值。"""
    conn = _conn()
    registry = _FakeRegistry({"get_market_volume": _mv_result(11923.6, 8439.9)})

    total, source = collector._fetch_market_total(conn, registry, "2026-07-08")

    assert total is None and source is None


def test_market_total_guard_keeps_legal_extreme_low_volume():
    """守卫·反向用例：真实极端地量(合计仅 6600 亿但两腿比例正常) → 不误伤,照常返回。"""
    conn = _conn()
    registry = _FakeRegistry({"get_market_volume": _mv_result(3000.0, 3600.0)})

    total, source = collector._fetch_market_total(conn, registry, "2026-07-08")

    assert total == 6600.0
    assert source == "tushare:index_daily"


def test_market_total_guard_rejects_below_absolute_floor():
    """守卫·绝对地板：合计 < 3000 亿必为数据残缺 → 落 None。"""
    conn = _conn()
    registry = _FakeRegistry({"get_market_volume": _mv_result(1200.0, 1300.0)})

    total, _source = collector._fetch_market_total(conn, registry, "2026-07-08")

    assert total is None


def test_market_total_drop_warns_but_keeps(caplog):
    """守卫·环比骤降：较前值跌逾 45% 仅告警不拦截（真实地量与退化难机械区分）。"""
    import logging

    conn = _conn()
    conn.execute(
        """INSERT INTO daily_volume_concentration
           (date, top_n, total_amount_billion, market_total_billion, stocks_json, sector_summary_json)
           VALUES ('2026-07-07', 20, 100.0, 35000.0, '[]', '[]')"""
    )
    conn.commit()
    registry = _FakeRegistry({"get_market_volume": _mv_result(7000.0, 8000.0)})  # -57% 环比

    with caplog.at_level(logging.WARNING):
        total, _source = collector._fetch_market_total(conn, registry, "2026-07-08")

    assert total == 15000.0  # 保留
    assert any("骤降" in rec.message for rec in caplog.records)


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


def test_load_top20_refetch_flag_bypasses_db_cache():
    """refetch=True → 即使 daily_market 已落库(可能是陈旧 10× 值)也强制重拉 provider。"""
    conn = _conn()
    stale = [{"rank": 1, "code": "STALE.SZ", "amount_billion": 9999.0}]
    conn.execute(
        "INSERT INTO daily_market (date, top_volume_stocks) VALUES (?, ?)",
        ("2026-05-29", json.dumps(stale)),
    )
    conn.commit()
    fresh = [{"rank": 1, "code": "300750.SZ", "amount_billion": 60.0}]
    registry = _FakeRegistry({
        "get_top_volume_stocks": DataResult(data=fresh, source="tushare:daily"),
    })

    result = collector.load_top20(conn, registry, "2026-05-29", refetch=True)

    assert result == fresh                                              # 用重拉值,不用陈旧库值
    assert any(c[0] == "get_top_volume_stocks" for c in registry.calls)  # 强制触发重拉


def test_build_record_refetch_ignores_stale_db():
    """build_record(refetch=True) → 绕过库里陈旧 top_volume_stocks,用重拉数据组装。"""
    conn = _conn()
    conn.execute(
        "INSERT INTO daily_market (date, top_volume_stocks) VALUES (?, ?)",
        ("2026-05-29", json.dumps([{"rank": 1, "code": "STALE.SZ", "amount_billion": 9999.0}])),
    )
    conn.commit()
    registry = _FakeRegistry({
        "get_top_volume_stocks": DataResult(
            data=[{"rank": 1, "code": "300750.SZ", "name": "", "amount_billion": 60.0}], source="tushare:daily"),
        "get_stock_sw_industry_map": DataResult(
            data={"300750.SZ": {"name": "宁德时代", "sw_l2": "电池"}}, source="tushare:index_member_all"),
        "get_stock_basic_batch": DataResult(data=[], source="tushare:stock_basic"),
        "get_market_volume": DataResult(data={"total_billion": 9800.0}, source="tushare:index_daily"),
    })

    record = collector.build_record(conn, registry, "2026-05-29", refetch=True)

    codes = {s["code"] for s in record["stocks"]}
    assert codes == {"300750.SZ"}     # 用重拉数据
    assert "STALE.SZ" not in codes    # 没用陈旧库值


# ──────────────────────────────────────────────────────────────
# 区间涨幅 universe(成交额前50 → 5/10/20 日涨幅)
# ──────────────────────────────────────────────────────────────

def _bars(closes: list[float], end: str = "2026-05-29") -> list[dict]:
    """升序 close 序列 → get_stock_daily_range 形态 bars，最后一根 trade_date = end。

    enrich_interval_gains 要求"最新一根 = 榜单日"才算涨幅，故 bars 必须带 trade_date 且末根=end。
    中间日期用自然日回推（仅末根需精确，gain 按位置算）。
    """
    from datetime import datetime, timedelta
    base = datetime.strptime(end, "%Y-%m-%d")
    n = len(closes)
    return [
        {"trade_date": (base - timedelta(days=(n - 1 - i))).strftime("%Y-%m-%d"), "close": c}
        for i, c in enumerate(closes)
    ]


def test_interval_gain_basic():
    # closes[-1]=11, closes[-6]=10 → 5日 +10%；closes[-11]=8 → 10日 +37.5%
    closes = [8.0, 8.0, 8.0, 8.0, 8.0, 10.0, 10.0, 10.0, 10.0, 10.0, 11.0]
    assert collector._interval_gain(closes, 5) == 10.0
    assert collector._interval_gain(closes, 10) == 37.5


def test_interval_gain_insufficient_history_none():
    closes = [10.0, 10.5, 11.0]  # 仅 3 根
    assert collector._interval_gain(closes, 5) is None


def test_interval_gain_nonpositive_base_none():
    closes = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]  # base=closes[-6]=0
    assert collector._interval_gain(closes, 5) is None


def test_interval_gain_non_finite_returns_none():
    """NaN/Inf/非数值 base 或 last → None(防脏值污染榜单,codex 中等)。"""
    nan_base = [float("nan")] + [10.0] * 5   # len6,base=closes[-6]=nan
    assert collector._interval_gain(nan_base, 5) is None
    inf_last = [10.0] * 6 + [float("inf")]   # last=inf
    assert collector._interval_gain(inf_last, 5) is None
    str_close = ["abc"] + [10.0] * 5         # len6,base 非数值,不得抛 TypeError
    assert collector._interval_gain(str_close, 5) is None


def test_enrich_interval_gains_non_finite_last_close_all_none():
    """榜单日 close 为 NaN/Inf(脏值)→ 不冒充当日,全 None(不写非有限 gain)。"""
    closes = [round(10.0 + 0.1 * i, 3) for i in range(25)]
    closes[-1] = float("inf")  # 末根(榜单日)close 非有限
    stocks = [{"code": "X.SZ", "name": "x", "industry": "电子"}]
    registry = _FakeRegistry({
        "get_stock_daily_range": DataResult(data=_bars(closes), source="tushare:daily"),
    })

    out = collector.enrich_interval_gains(stocks, "2026-05-29", registry)

    for key in ("gain_5d", "gain_10d", "gain_20d"):
        assert out[0][key] is None


def test_enrich_interval_gains_attaches_fields():
    # 25 根:close 全程从 10 线性到 13，足够 5/10/20 日
    closes = [round(10.0 + 0.125 * i, 3) for i in range(25)]
    stocks = [{"code": "600519.SH", "name": "甲", "industry": "白酒"}]
    registry = _FakeRegistry({
        "get_stock_daily_range": DataResult(data=_bars(closes), source="tushare:daily"),
    })

    out = collector.enrich_interval_gains(stocks, "2026-05-29", registry)

    assert out[0]["code"] == "600519.SH"
    for key in ("gain_5d", "gain_10d", "gain_20d"):
        assert isinstance(out[0][key], float)
    # 单调上行 → 三档均为正
    assert out[0]["gain_5d"] > 0 and out[0]["gain_20d"] > 0


def test_enrich_interval_gains_partial_history():
    # 仅 8 根 → 5日有值、10/20 日 None
    closes = [10.0 + 0.1 * i for i in range(8)]
    stocks = [{"code": "688001.SH", "name": "次新", "industry": "半导体"}]
    registry = _FakeRegistry({
        "get_stock_daily_range": DataResult(data=_bars(closes), source="tushare:daily"),
    })

    out = collector.enrich_interval_gains(stocks, "2026-05-29", registry)

    assert out[0]["gain_5d"] is not None
    assert out[0]["gain_10d"] is None
    assert out[0]["gain_20d"] is None


def test_enrich_interval_gains_stale_last_bar_all_none():
    """最新一根 bar 早于榜单日(停牌/数据延迟)→ 不冒充当日,全 None(红线诚实)。"""
    closes = [round(10.0 + 0.1 * i, 3) for i in range(25)]
    stocks = [{"code": "600519.SH", "name": "甲", "industry": "白酒"}]
    registry = _FakeRegistry({
        # 数据止于 05-28，但请求榜单日是 05-29 → 末根 != end_date
        "get_stock_daily_range": DataResult(data=_bars(closes, end="2026-05-28"), source="tushare:daily"),
    })

    out = collector.enrich_interval_gains(stocks, "2026-05-29", registry)

    assert out[0]["gain_5d"] is None
    assert out[0]["gain_10d"] is None
    assert out[0]["gain_20d"] is None


def test_enrich_interval_gains_range_failure_all_none():
    stocks = [{"code": "X.SZ", "name": "x", "industry": "电子"}]
    registry = _FakeRegistry({
        "get_stock_daily_range": DataResult(data=None, source="tushare", error="boom"),
    })

    out = collector.enrich_interval_gains(stocks, "2026-05-29", registry)

    assert out[0]["gain_5d"] is None and out[0]["gain_20d"] is None


def test_build_gain_universe_top50_labeled_and_enriched():
    closes = [round(10.0 + 0.1 * i, 3) for i in range(25)]

    def _range(code, start, end):
        return DataResult(data=_bars(closes), source="tushare:daily")

    registry = _FakeRegistry({
        "get_top_volume_stocks": DataResult(
            data=[{"rank": 1, "code": "300750.SZ", "name": "", "amount_billion": 60.0},
                  {"rank": 2, "code": "999999.SZ", "name": "", "amount_billion": 40.0}],
            source="tushare:daily"),
        "get_stock_sw_industry_map": DataResult(
            data={"300750.SZ": {"name": "宁德时代", "sw_l2": "电池"}}, source="tushare:index_member_all"),
        "get_stock_basic_batch": DataResult(data=[], source="tushare:stock_basic"),
        "get_stock_daily_range": _range,
    })

    universe = collector.build_gain_universe(registry, "2026-05-29")

    assert len(universe) == 2
    by_code = {s["code"]: s for s in universe}
    assert by_code["300750.SZ"]["industry"] == "电池"
    assert by_code["999999.SZ"]["industry"] == "未分类"
    assert isinstance(by_code["300750.SZ"]["gain_5d"], float)
    # 取 top50 口径
    tv_call = next(c for c in registry.calls if c[0] == "get_top_volume_stocks")
    assert tv_call[1][1] == collector.TOP_VOLUME_UNIVERSE_N


def test_build_gain_universe_empty_when_no_top_volume():
    registry = _FakeRegistry({
        "get_top_volume_stocks": DataResult(data=[], source="tushare:daily"),
    })
    assert collector.build_gain_universe(registry, "2026-05-29") == []


# ──────────────────────────────────────────────────────────────
# 题材标签 enrich_concepts（同花顺概念，容器过滤）
# ──────────────────────────────────────────────────────────────

def test_enrich_concepts_attaches_filtered_concepts():
    universe = [
        {"code": "300570.SZ", "name": "太辰光", "industry": "通信设备", "gain_5d": 5.0},
        {"code": "688498.SH", "name": "源杰科技", "industry": "半导体", "gain_5d": 3.0},
    ]
    ths_rows = [
        {"con_code": "300570.SZ", "index_name": "共封装光学(CPO)"},
        {"con_code": "300570.SZ", "index_name": "融资融券"},          # 容器(>300)将被剔
        {"con_code": "688498.SH", "index_name": "共封装光学(CPO)"},
    ]
    # 制造融资融券 >300 成员：补 301 个不同票挂该概念
    for i in range(301):
        ths_rows.append({"con_code": f"{600000+i}.SH", "index_name": "融资融券"})
    registry = _FakeRegistry({
        "get_ths_member": DataResult(data=ths_rows, source="tushare:ths_member"),
        "get_concept_moneyflow_ths": DataResult(
            data=[{"name": "共封装光学(CPO)", "net_amount": 100.0}], source="tushare"),  # CPO 为热概念
    })

    out, ok = collector.enrich_concepts(universe, registry, "2026-06-15")

    assert ok is True
    by = {s["code"]: s for s in out}
    assert by["300570.SZ"]["concepts"] == ["共封装光学(CPO)"]   # 容器融资融券被剔
    assert by["688498.SH"]["concepts"] == ["共封装光学(CPO)"]


def test_enrich_concepts_intersects_hot_only():
    """题材榜降噪:只保留资金 Top-M 热概念,非热概念(光纤概念)被剔。"""
    universe = [{"code": "300570.SZ", "name": "太辰光", "industry": "通信设备", "gain_5d": 5.0}]
    ths_rows = [
        {"con_code": "300570.SZ", "index_name": "共封装光学(CPO)"},
        {"con_code": "300570.SZ", "index_name": "光纤概念"},
        {"con_code": "688498.SH", "index_name": "共封装光学(CPO)"},   # 让 CPO 成员数 ≥1
        {"con_code": "999999.SZ", "index_name": "光纤概念"},
    ]
    registry = _FakeRegistry({
        "get_ths_member": DataResult(data=ths_rows, source="tushare:ths_member"),
        # 只有 CPO 在资金 Top-1 → 光纤概念虽是窄概念但非热,被剔
        "get_concept_moneyflow_ths": DataResult(
            data=[{"name": "共封装光学(CPO)", "net_amount": 100.0},
                  {"name": "光纤概念", "net_amount": 5.0}], source="tushare"),
    })

    out, ok = collector.enrich_concepts(universe, registry, "2026-06-15", top_m=1)

    assert ok is True
    assert out[0]["concepts"] == ["共封装光学(CPO)"]  # 光纤概念非 Top-1 热概念 → 剔


def test_enrich_concepts_ths_failure_all_empty():
    universe = [{"code": "300570.SZ", "name": "太辰光", "industry": "通信设备", "gain_5d": 5.0}]
    registry = _FakeRegistry({
        "get_ths_member": DataResult(data=None, source="tushare", error="boom"),
    })
    out, ok = collector.enrich_concepts(universe, registry, "2026-06-15")
    assert ok is False           # ths_member 失败 → concept_ok=False
    assert out[0]["concepts"] == []      # 空,不影响 gain/industry
    assert out[0]["gain_5d"] == 5.0


def test_enrich_concepts_moneyflow_failure_all_empty():
    """ths_member 成功但资金 moneyflow 失败 → 题材榜降级空(由上层幂等保留既有)。"""
    universe = [{"code": "300570.SZ", "name": "太辰光", "industry": "通信设备", "gain_5d": 5.0}]
    registry = _FakeRegistry({
        "get_ths_member": DataResult(
            data=[{"con_code": "300570.SZ", "index_name": "共封装光学(CPO)"}], source="tushare:ths_member"),
        "get_concept_moneyflow_ths": DataResult(data=None, source="tushare", error="boom"),
    })
    out, ok = collector.enrich_concepts(universe, registry, "2026-06-15")
    assert ok is False           # moneyflow 失败 → concept_ok=False
    assert out[0]["concepts"] == []


def test_enrich_concepts_partial_coverage_degrades_to_empty():
    """ths_member 疑似部分截断(资金热概念里有成员数=0 → hot_concepts coverage_ok=False)→
    即便有残留可出榜概念,也全 [](由上层幂等保留旧完整榜;codex 高:部分覆盖不冒充成功)。"""
    universe = [{"code": "300570.SZ", "name": "太辰光", "industry": "通信设备", "gain_5d": 5.0}]
    ths_rows = [
        {"con_code": "300570.SZ", "index_name": "共封装光学(CPO)"},   # 有成员
        {"con_code": "688498.SH", "index_name": "共封装光学(CPO)"},
        # 「光纤概念」在 moneyflow 热榜但 ths_member 里无成员 → member_count=0 → coverage_ok=False
    ]
    registry = _FakeRegistry({
        "get_ths_member": DataResult(data=ths_rows, source="tushare:ths_member"),
        "get_concept_moneyflow_ths": DataResult(
            data=[{"name": "光纤概念", "net_amount": 200.0},          # 最热但 ths_member 无成员→截断信号
                  {"name": "共封装光学(CPO)", "net_amount": 100.0}], source="tushare"),
    })

    out, ok = collector.enrich_concepts(universe, registry, "2026-06-15", top_m=5)

    assert ok is False           # coverage_ok=False → concept_ok=False
    assert out[0]["concepts"] == []   # 全降级,不写残缺榜


def test_enrich_concepts_empty_moneyflow_degraded():
    """资金 moneyflow 成功但返空表(交易日静默零行)→ 无热概念 → concept_ok=False(降级,由上层保留旧)。"""
    universe = [{"code": "300570.SZ", "name": "太辰光", "industry": "通信设备", "gain_5d": 5.0}]
    registry = _FakeRegistry({
        "get_ths_member": DataResult(
            data=[{"con_code": "300570.SZ", "index_name": "共封装光学(CPO)"}], source="tushare:ths_member"),
        "get_concept_moneyflow_ths": DataResult(data=[], source="tushare"),   # 空表(非报错)
    })
    out, ok = collector.enrich_concepts(universe, registry, "2026-06-15")
    assert ok is False           # 空 moneyflow → bool(hot)=False → concept_ok=False
    assert out[0]["concepts"] == []
