"""趋势主升 概念分支主线（GAP B）单测：申万二级 ∪ 同花顺概念 Top-M。

鞠磊「主线或其分支」——分支=同花顺概念（资金净流入 Top-M）。默认 main_line="hybrid" 时
主线门放宽为 sw_l2∈主线二级 OR 个股概念∩主线概念，并可由 LLM 过滤明显非窄分支概念；
main_line="l2" 保留纯二级口径，main_line="l2+concept" 保留不经 LLM 的机械概念分支口径。
概念成员极噪（通用概念遍地），靠资金热度 Top-M 自然滤垃圾概念；mock 隔离外网。
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from db.schema import init_schema
from services.trend_leader import constants as C
from services.trend_leader import pool, scanner
from services.volume_concentration.aggregator import UNCLASSIFIED


class _R:
    def __init__(self, data, success=True):
        self.data, self.success = data, success


class FakeRegistry:
    def __init__(self, *, limit_stocks=None, sw_map=None, bars_by_code=None,
                 market_changes=None, concept_flow=None, ths_member=None):
        self._lu = limit_stocks or []
        self._sw = sw_map or {}
        self._bars = bars_by_code or {}
        self._mc = market_changes or []
        self._cf = concept_flow if concept_flow is not None else []
        self._tm = ths_member if ths_member is not None else []
        self.calls = []

    def call(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        if name == "get_limit_up_list":
            return _R({"stocks": self._lu})
        if name == "get_stock_sw_industry_map":
            return _R(self._sw)
        if name == "get_stock_daily_range":
            return _R(self._bars.get(args[0], []))
        if name == "get_market_daily_changes":
            return _R(self._mc)
        if name == "get_concept_moneyflow_ths":
            return _R(self._cf)
        if name == "get_ths_member":
            return _R(self._tm)
        return _R(None, success=False)


def _mk_bars(closes, pcts=None):
    n = len(closes)
    pcts = pcts or [0.0] * n
    return [
        {"trade_date": f"2026-06-{i + 1:02d}", "open": closes[i], "high": closes[i],
         "low": closes[i], "close": closes[i], "pre_close": closes[i - 1] if i else closes[i],
         "vol": 1000.0, "amount": closes[i] * 1000.0, "pct_chg": pcts[i]}
        for i in range(n)
    ]


def _leader_bars():
    closes = [round(10.0 + i * (1.0 / 18), 4) for i in range(19)] + [12.1]
    return _mk_bars(closes, pcts=[0.5] * 19 + [10.0])


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_schema(c)
    return c


def _seed_concentration(conn, date, industries):
    summary = [{"industry": ind, "amount_billion": 100.0 - i} for i, ind in enumerate(industries)]
    conn.execute(
        "INSERT INTO daily_volume_concentration "
        "(date, top_n, total_amount_billion, stocks_json, sector_summary_json) VALUES (?,?,?,?,?)",
        (date, 20, 1000.0, "[]", json.dumps(summary, ensure_ascii=False)))
    conn.commit()


# ---- _main_concepts ----

def test_main_concepts_ranks_by_net_amount():
    reg = FakeRegistry(concept_flow=[
        {"name": "PCB概念", "net_amount": 50.0},
        {"name": "融资融券", "net_amount": 5.0},
        {"name": "CPO", "net_amount": 80.0},
        {"name": "PET铜箔", "net_amount": 30.0},
    ])
    mc = {"PCB概念": 50, "融资融券": 40, "CPO": 30, "PET铜箔": 25}  # 均≤cap 且>0，本例只测排序
    got, ok, cov = scanner._main_concepts(reg, "2026-06-09", 2, mc)
    assert ok is True and cov is True         # 入选窗口内概念成员充足，无覆盖缺失
    assert got == {"CPO", "PCB概念"}          # net_amount Top-2，融资融券/PET铜箔 出局


def test_main_concepts_excludes_container_by_member_cap():
    """容器概念（成员数>cap，如融资融券3845）即便净流入最高也被成员数闸挡掉，不当窄分支。"""
    reg = FakeRegistry(concept_flow=[
        {"name": "融资融券", "net_amount": 999.0},   # 净流入最高
        {"name": "CPO", "net_amount": 50.0},
        {"name": "PCB概念", "net_amount": 40.0},
    ])
    mc = {"融资融券": 3845, "CPO": 195, "PCB概念": 216}
    got, ok, cov = scanner._main_concepts(reg, "2026-06-09", 2, mc)
    assert ok is True
    assert cov is True                        # 容器(>cap)被剔不算覆盖缺失
    assert got == {"CPO", "PCB概念"}
    assert "融资融券" not in got               # 成员数 3845 > 300 → 出局，不因净流入第一入选


def test_main_concepts_excludes_unknown_size_concept():
    """概念净流入靠前但 ths_member 无成员（成员数=0）→ 排除，且 coverage_ok=False（疑似部分覆盖缺失）。"""
    reg = FakeRegistry(concept_flow=[
        {"name": "幽灵概念", "net_amount": 99.0},   # 净流入最高却无成员 → 覆盖缺失信号
        {"name": "CPO", "net_amount": 10.0},
    ])
    got, ok, cov = scanner._main_concepts(reg, "2026-06-09", 5, {"CPO": 195})
    assert got == {"CPO"}                      # 幽灵概念成员数 0，出局
    assert cov is False                        # 入选窗口内热概念无成员 → 标覆盖缺失


def test_main_concepts_failure_returns_false():
    class Fail:
        def call(self, name, *a):
            return _R(None, success=False)
    got, ok, cov = scanner._main_concepts(Fail(), "2026-06-09", 5, {})
    assert got == set() and ok is False and cov is True  # 取数失败由 ok 表达，coverage 无意义→True


# ---- _stock_concept_map ----

def test_stock_concept_map_builds_reverse():
    reg = FakeRegistry(ths_member=[
        {"con_code": "002859.SZ", "index_name": "PCB概念"},
        {"con_code": "002859.SZ", "index_name": "消费电子概念"},
        {"con_code": "300750.SZ", "index_name": "锂电池概念"},
    ])
    m, mc, ok = scanner._stock_concept_map(reg, "2026-06-09")
    assert ok is True
    assert m["002859"] == {"PCB概念", "消费电子概念"}
    assert m["300750"] == {"锂电池概念"}
    assert mc["PCB概念"] == 1 and mc["消费电子概念"] == 1 and mc["锂电池概念"] == 1  # 成员数计数


# ---- 概念分支主线集成 ----

def _branch_reg(sw_l2_of_target):
    """目标股 301xxx 涨停、其申万二级=sw_l2_of_target、概念含 PCB概念（当日资金热）。"""
    return FakeRegistry(
        limit_stocks=[{"code": "301628.SZ", "name": "强达电路", "pct_chg": 20.0}],
        sw_map={"301628.SZ": {"name": "强达电路", "sw_l2": sw_l2_of_target}},
        bars_by_code={"301628": _leader_bars()},
        concept_flow=[{"name": "PCB概念", "net_amount": 99.0}],
        ths_member=[{"con_code": "301628.SZ", "index_name": "PCB概念"}],
    )


def test_default_hybrid_enters_concept_branch(conn):
    """默认 hybrid：不指定 main_line 也启用同花顺概念分支；无 LLM runner 时退化为确定性 l2+concept。"""
    _seed_concentration(conn, "2026-06-09", ["半导体"])
    reg = _branch_reg("其他电子Ⅱ")
    summary = scanner.run_daily(conn, reg, "2026-06-09", top_concepts=5)
    assert summary["main_line"] == "hybrid"
    assert "301628" in summary["entered"]


def test_concept_mode_fetches_only_ranked_concept_window(conn, monkeypatch):
    """trend-leader 只按资金流前排概念查询成员，不再触发 provider 全量 ths_member 扫描。"""
    monkeypatch.setattr(C, "CONCEPT_PREFETCH_MIN", 2)
    monkeypatch.setattr(C, "CONCEPT_PREFETCH_MULTIPLIER", 1)
    _seed_concentration(conn, "2026-06-09", ["半导体"])
    reg = FakeRegistry(
        limit_stocks=[{"code": "301628.SZ", "name": "强达电路", "pct_chg": 20.0}],
        sw_map={"301628.SZ": {"name": "强达电路", "sw_l2": "其他电子Ⅱ"}},
        bars_by_code={"301628": _leader_bars()},
        concept_flow=[
            {"name": "PCB概念", "net_amount": 200.0},
            {"name": "液冷服务器", "net_amount": 100.0},
            {"name": "低位概念", "net_amount": 50.0},
        ],
        ths_member=[{"con_code": "301628.SZ", "index_name": "PCB概念"}],
    )

    summary = scanner.run_daily(conn, reg, "2026-06-09", main_line="l2+concept", top_concepts=1)

    ths_calls = [c for c in reg.calls if c[0] == "get_ths_member"]
    assert ths_calls == [
        ("get_ths_member", ("2026-06-09",), {"concept_names": ["PCB概念", "液冷服务器"]})
    ]
    assert "301628" in summary["entered"]


def test_concept_mode_enters_branch_stock(conn):
    """sw_l2 不在主线二级，但概念∈主线概念 → l2+concept 入候选并入池。"""
    _seed_concentration(conn, "2026-06-09", ["半导体"])     # 主线二级=半导体
    reg = _branch_reg("其他电子Ⅱ")                          # 目标二级不在主线
    summary = scanner.run_daily(conn, reg, "2026-06-09", main_line="l2+concept", top_concepts=5)
    assert summary["candidates"] == 1
    assert "301628" in summary["entered"]
    assert pool.get_active(conn, "301628") is not None


def test_hybrid_llm_filters_defensive_concept_without_denying_l2(conn):
    """LLM 只过滤概念分支，不否决申万 Top-K：防御篮子分支被挡，二级主线票仍入池。"""
    _seed_concentration(conn, "2026-06-09", ["半导体"])
    reg = FakeRegistry(
        limit_stocks=[
            {"code": "301628.SZ", "name": "强达电路", "pct_chg": 20.0},
            {"code": "301999.SZ", "name": "防御篮子票", "pct_chg": 20.0},
            {"code": "688512.SH", "name": "慧智微", "pct_chg": 20.0},
        ],
        sw_map={
            "301628.SZ": {"name": "强达电路", "sw_l2": "其他电子Ⅱ"},
            "301999.SZ": {"name": "防御篮子票", "sw_l2": "其他电子Ⅱ"},
            "688512.SH": {"name": "慧智微", "sw_l2": "半导体"},
        },
        bars_by_code={
            "301628": _leader_bars(),
            "301999": _leader_bars(),
            "688512": _leader_bars(),
        },
        concept_flow=[
            {"name": "PCB概念", "net_amount": 200.0},
            {"name": "高股息精选", "net_amount": 150.0},
        ],
        ths_member=[
            {"con_code": "301628.SZ", "index_name": "PCB概念"},
            {"con_code": "301999.SZ", "index_name": "高股息精选"},
        ],
    )

    def llm_runner(prompt, payload):
        assert "只筛选主线概念" in prompt
        assert set(payload["main_concepts"]) == {"PCB概念", "高股息精选"}
        return {
            "accepted_concepts": ["PCB概念"],
            "rejected": [{"name": "高股息精选", "reason": "防御篮子"}],
        }

    summary = scanner.run_daily(
        conn, reg, "2026-06-09", main_line="hybrid", top_concepts=5,
        mainline_llm_runner=llm_runner)
    assert "301628" in summary["entered"]      # LLM 接受的概念分支
    assert "301999" not in summary["entered"]  # LLM 过滤的防御篮子分支
    assert "688512" in summary["entered"]      # 申万二级 Top-K 不被 LLM 否决
    assert summary["main_concepts"] == ["PCB概念"]
    assert summary["mainline_llm"]["status"] == "ok"


def test_hybrid_llm_bad_output_falls_back_to_mechanical_concepts(conn):
    """LLM 不可用/解析失败时降级到确定性 l2+concept，不中断扫描。"""
    _seed_concentration(conn, "2026-06-09", ["半导体"])
    reg = _branch_reg("其他电子Ⅱ")

    def bad_runner(prompt, payload):
        return {"accepted_concepts": ["输入外概念"]}

    summary = scanner.run_daily(
        conn, reg, "2026-06-09", main_line="hybrid", top_concepts=5,
        mainline_llm_runner=bad_runner)
    assert "301628" in summary["entered"]
    assert summary["mainline_llm"]["status"] == "fallback"
    assert "mainline_llm" in summary["source_errors"]


def test_l2_mode_ignores_concept_branch(conn):
    """默认 l2：概念分支不生效 → 二级不在主线的票不入候选。"""
    _seed_concentration(conn, "2026-06-09", ["半导体"])
    reg = _branch_reg("其他电子Ⅱ")
    summary = scanner.run_daily(conn, reg, "2026-06-09", main_line="l2")  # 默认口径
    assert summary["candidates"] == 0
    assert pool.get_active(conn, "301628") is None


def test_concept_mode_still_takes_l2_hits(conn):
    """l2+concept 下，二级命中的票照常入（概念是并集不是替换）。"""
    _seed_concentration(conn, "2026-06-09", ["半导体"])
    reg = _branch_reg("半导体")                              # 目标二级就在主线
    summary = scanner.run_daily(conn, reg, "2026-06-09", main_line="l2+concept", top_concepts=5)
    assert "301628" in summary["entered"]


def test_concept_branch_records_matched_concept(conn):
    """经概念分支入池 → signal_json 记命中的概念，供报告标注分支来源。"""
    _seed_concentration(conn, "2026-06-09", ["半导体"])
    reg = _branch_reg("其他电子Ⅱ")
    scanner.run_daily(conn, reg, "2026-06-09", main_line="l2+concept", top_concepts=5)
    sig = pool.get_active(conn, "301628")["last_signal"]
    assert "PCB概念" in (sig.get("branch_concepts") or [])


def test_concept_mode_container_concept_filtered(conn, monkeypatch):
    """run_daily 端到端：容器概念（成员数>cap）即便资金最热也不构成分支，只靠它命中的票不入候选。

    monkeypatch cap=1 避免在测试里塞 300+ 行成员；语义同生产（cap=300 挡融资融券3845）。
    """
    monkeypatch.setattr(C, "CONCEPT_MAX_MEMBERS", 1)
    _seed_concentration(conn, "2026-06-09", ["半导体"])         # 主线二级=半导体
    reg = FakeRegistry(
        limit_stocks=[{"code": "301628.SZ", "name": "强达电路", "pct_chg": 20.0}],
        sw_map={"301628.SZ": {"name": "强达电路", "sw_l2": "其他电子Ⅱ"}},  # 二级不在主线
        bars_by_code={"301628": _leader_bars()},
        concept_flow=[{"name": "大容器", "net_amount": 99.0}],
        ths_member=[{"con_code": "301628.SZ", "index_name": "大容器"},
                    {"con_code": "999999.SZ", "index_name": "大容器"}],  # 成员数 2 > cap 1
    )
    summary = scanner.run_daily(conn, reg, "2026-06-09", main_line="l2+concept", top_concepts=5)
    assert summary["candidates"] == 0           # 容器被挡 → 无分支 → 二级不在主线 → 不入候选
    assert pool.get_active(conn, "301628") is None


def test_concept_mode_enters_branch_when_sw_map_down(conn):
    """门2 codex M1：sw_map 失败但概念源健康 → concept 模式应仍靠概念分支放行涨停票（sw_l2=未分类），
    不被与概念分支无关的 sw 故障静默全挡（concept 分支门不依赖 sw_map）。"""
    _seed_concentration(conn, "2026-06-09", ["半导体"])

    class SwDownReg(FakeRegistry):
        def call(self, name, *a, **k):
            if name == "get_stock_sw_industry_map":
                return _R(None, success=False)
            return super().call(name, *a, **k)

    reg = SwDownReg(
        limit_stocks=[{"code": "301628.SZ", "name": "强达电路", "pct_chg": 20.0}],
        bars_by_code={"301628": _leader_bars()},
        concept_flow=[{"name": "PCB概念", "net_amount": 99.0}],
        ths_member=[{"con_code": "301628.SZ", "index_name": "PCB概念"}],
    )
    summary = scanner.run_daily(conn, reg, "2026-06-09", main_line="l2+concept", top_concepts=5)
    assert "sw_map" in summary["source_errors"]        # sw 故障仍记账
    assert "301628" in summary["entered"]              # 概念分支仍放行，不被 sw 故障静默全挡
    rec = pool.get_active(conn, "301628")
    assert rec["sw_l2"] == UNCLASSIFIED                # sw 缺失 → 未分类
    assert "PCB概念" in (rec["last_signal"].get("branch_concepts") or [])


def test_l2_mode_sw_down_still_blocks(conn):
    """对照：l2 模式 sw_map 失败仍应全挡（无概念分支兜底，无法判二级主线）——M1 修复不能泄漏到 l2。"""
    _seed_concentration(conn, "2026-06-09", ["半导体"])

    class SwDownReg(FakeRegistry):
        def call(self, name, *a, **k):
            if name == "get_stock_sw_industry_map":
                return _R(None, success=False)
            return super().call(name, *a, **k)

    reg = SwDownReg(limit_stocks=[{"code": "301628.SZ", "name": "强达电路", "pct_chg": 20.0}],
                    bars_by_code={"301628": _leader_bars()})
    summary = scanner.run_daily(conn, reg, "2026-06-09", main_line="l2")
    assert summary["candidates"] == 0
    assert pool.get_active(conn, "301628") is None


def test_concept_mode_empty_ths_member_flags_coverage(conn):
    """门2 codex M2：ths_member 返回成功但空（部分/空覆盖）→ 概念分支静默失效，须记 concept_coverage
    警示，不能伪装成「今日无概念候选」。二级命中票仍降级 l2 入池。"""
    _seed_concentration(conn, "2026-06-09", ["半导体"])
    reg = FakeRegistry(
        limit_stocks=[{"code": "688512.SH", "name": "慧智微", "pct_chg": 20.0}],
        sw_map={"688512.SH": {"name": "慧智微", "sw_l2": "半导体"}},
        bars_by_code={"688512": _leader_bars()},
        concept_flow=[{"name": "PCB概念", "net_amount": 99.0}],
        ths_member=[],                                  # success 但空 → 覆盖缺失
    )
    summary = scanner.run_daily(conn, reg, "2026-06-09", main_line="l2+concept", top_concepts=5)
    assert "concept_coverage" in summary["source_errors"]  # 显式记账，运营可辨「链路降级」非「无候选」
    assert "688512" in summary["entered"]              # 二级命中票仍入（降级 l2）


def test_concept_mode_partial_ths_coverage_flags(conn):
    """门2 codex M2'：ths_member 部分截断——净流入最高的热概念缺成员、另一概念有成员。
    main_concepts 仍非空，但热分支被静默漏，须记 concept_coverage（不能无声）。"""
    _seed_concentration(conn, "2026-06-09", ["半导体"])
    reg = FakeRegistry(
        limit_stocks=[{"code": "301628.SZ", "name": "强达电路", "pct_chg": 20.0}],
        sw_map={"301628.SZ": {"name": "强达电路", "sw_l2": "其他电子Ⅱ"}},
        bars_by_code={"301628": _leader_bars()},
        # 「液冷服务器」净流入最高但 ths_member 没有它的成员（部分截断）；「PCB概念」有成员。
        concept_flow=[{"name": "液冷服务器", "net_amount": 200.0},
                      {"name": "PCB概念", "net_amount": 50.0}],
        ths_member=[{"con_code": "301628.SZ", "index_name": "PCB概念"}],
    )
    summary = scanner.run_daily(conn, reg, "2026-06-09", main_line="l2+concept", top_concepts=5)
    assert "concept_coverage" in summary["source_errors"]  # 热概念液冷无成员 → 部分覆盖缺失记账
    assert "301628" in summary["entered"]                  # PCB概念有成员，301628 仍经分支入池


def test_concept_branch_attribution_survives_maintenance(conn):
    """门2 codex M1''：概念分支入池票经次日 Pass2 维护后，last_signal.branch_concepts 不被丢——
    否则 sw 故障期入池的「未分类」票维护一过就无法解释为何入池。"""
    _seed_concentration(conn, "2026-06-09", ["半导体"])
    scanner.run_daily(conn, _branch_reg("其他电子Ⅱ"), "2026-06-09",
                      main_line="l2+concept", top_concepts=5)
    assert "PCB概念" in (pool.get_active(conn, "301628")["last_signal"].get("branch_concepts") or [])

    # 次日：301628 不再是候选（无涨停/双创）→ 落入 Pass2 维护（趋势未破，touch 重写 last_signal）。
    _seed_concentration(conn, "2026-06-10", ["半导体"])
    reg_day2 = FakeRegistry(
        limit_stocks=[], sw_map={"301628.SZ": {"name": "强达电路", "sw_l2": "其他电子Ⅱ"}},
        bars_by_code={"301628": _leader_bars()},
        concept_flow=[{"name": "PCB概念", "net_amount": 99.0}],
        ths_member=[{"con_code": "301628.SZ", "index_name": "PCB概念"}],
    )
    scanner.run_daily(conn, reg_day2, "2026-06-10", main_line="l2+concept", top_concepts=5)
    rec = pool.get_active(conn, "301628")
    assert rec is not None                                  # 未退池
    assert "PCB概念" in (rec["last_signal"].get("branch_concepts") or [])  # 维护后分支归因仍在


def test_concept_source_failure_degrades(conn):
    """概念源失败（l2+concept 模式）→ 记 source_errors，二级命中票仍正常入池。"""
    _seed_concentration(conn, "2026-06-09", ["玻璃玻纤"])

    class PartialReg(FakeRegistry):
        def call(self, name, *a, **k):
            if name == "get_concept_moneyflow_ths":
                return _R(None, success=False)
            return super().call(name, *a, **k)

    reg = PartialReg(
        limit_stocks=[{"code": "600552.SH", "name": "凯盛科技", "pct_chg": 10.0}],
        sw_map={"600552.SH": {"name": "凯盛科技", "sw_l2": "玻璃玻纤"}},
        bars_by_code={"600552": _leader_bars()},
    )
    summary = scanner.run_daily(conn, reg, "2026-06-09", main_line="l2+concept")
    assert "concept_flow" in summary["source_errors"]
    assert "600552" in summary["entered"]      # 二级命中票不受概念源失败影响
