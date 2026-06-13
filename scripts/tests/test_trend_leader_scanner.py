"""趋势主升漏斗 scanner 端到端单测（Stage 2c），mock registry 隔离外网。

漏斗：涨停列表 → 映射申万二级 → ∩ 主线(Top-K ∪ sectors) → 拉OHLCV → 检测 → 入池/维护/退池。
入池门槛 = 主线∩ + 首次涨停加速 + 缓涨（**不含贴MA5**：涨停日必远离 MA5，贴MA5 是在池后回踩信号）。
池内唯一身份 = 裸码：scanner 用 bare 请求行情并入池，故 bars_by_code 与 summary 均以裸码为键。
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from db.schema import init_schema
from services.trend_leader import pool, scanner


class _R:
    """轻量 DataResult 替身。"""
    def __init__(self, data, success=True):
        self.data = data
        self.success = success


class FakeRegistry:
    def __init__(self, limit_stocks, sw_map, bars_by_code, market_changes=None):
        self._lu = limit_stocks
        self._sw = sw_map
        self._bars = bars_by_code
        self._mc = market_changes or []  # 全市场日涨跌（双创@15% 候选源）；默认空=无额外候选

    def call(self, name, *args):
        if name == "get_limit_up_list":
            return _R({"stocks": self._lu})
        if name == "get_stock_sw_industry_map":
            return _R(self._sw)
        if name == "get_stock_daily_range":
            return _R(self._bars.get(args[0], []))
        if name == "get_market_daily_changes":
            return _R(self._mc)
        return _R(None, success=False)


def _mk_bars(closes, pcts=None, vols=None, opens=None):
    n = len(closes)
    pcts = pcts or [0.0] * n
    vols = vols or [1000.0] * n
    opens = opens or list(closes)
    return [
        {"trade_date": f"2026-06-{i + 1:02d}", "open": opens[i], "high": max(opens[i], closes[i]),
         "low": min(opens[i], closes[i]), "close": closes[i], "pre_close": closes[i - 1] if i else closes[i],
         "vol": vols[i], "amount": closes[i] * vols[i], "pct_chg": pcts[i]}
        for i in range(n)
    ]


def _leader_bars():
    """20 根：前 19 缓涨(10.0→11.0, 无涨停) + 今日涨停(+10%→12.1)。满足首次涨停+缓涨。"""
    closes = [round(10.0 + i * (1.0 / 18), 4) for i in range(19)] + [12.1]
    pcts = [0.5] * 19 + [10.0]
    return _mk_bars(closes, pcts=pcts)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row  # vc_repo 按列名访问；与生产 db.connection 一致
    init_schema(c)
    return c


def _seed_concentration(conn, date, industries):
    summary = [{"industry": ind, "amount_billion": 100.0 - i} for i, ind in enumerate(industries)]
    conn.execute(
        "INSERT INTO daily_volume_concentration "
        "(date, top_n, total_amount_billion, stocks_json, sector_summary_json) VALUES (?,?,?,?,?)",
        (date, 20, 1000.0, "[]", json.dumps(summary, ensure_ascii=False)))
    conn.commit()


def test_funnel_enters_main_line_first_limit_leader(conn):
    _seed_concentration(conn, "2026-06-09", ["玻璃玻纤", "半导体"])
    reg = FakeRegistry(
        limit_stocks=[
            {"code": "600552.SH", "name": "凯盛科技", "pct_chg": 10.0},
            {"code": "000001.SZ", "name": "平安银行", "pct_chg": 10.0},  # 非主线
        ],
        sw_map={
            "600552.SH": {"name": "凯盛科技", "sw_l2": "玻璃玻纤"},
            "000001.SZ": {"name": "平安银行", "sw_l2": "股份制银行Ⅱ"},
        },
        bars_by_code={"600552": _leader_bars(), "000001": _leader_bars()},  # scanner 用裸码请求
    )
    summary = scanner.run_daily(conn, reg, "2026-06-09")
    assert summary["candidates"] == 1          # 只有玻璃玻纤的凯盛进候选（平安非主线被滤）
    assert "600552" in summary["entered"]
    assert pool.get_active(conn, "600552") is not None
    assert pool.get_active(conn, "000001") is None


def test_funnel_skips_when_detectors_fail(conn):
    """主线∩涨停 但不满足首次涨停/缓涨（历史含涨停）→ 不入池。"""
    _seed_concentration(conn, "2026-06-09", ["玻璃玻纤"])
    bars = _leader_bars()
    bars[5]["pct_chg"] = 10.0  # 历史窗口里早有涨停 → 非首次
    reg = FakeRegistry(
        limit_stocks=[{"code": "600552.SH", "name": "凯盛科技", "pct_chg": 10.0}],
        sw_map={"600552.SH": {"name": "凯盛科技", "sw_l2": "玻璃玻纤"}},
        bars_by_code={"600552": bars},
    )
    summary = scanner.run_daily(conn, reg, "2026-06-09")
    assert summary["candidates"] == 1
    assert summary["entered"] == []
    assert pool.get_active(conn, "600552") is None


def test_funnel_matches_bare_code_against_suffixed_sw_map(conn):
    """AkShare 降级回裸码 600552，sw_map 是 ts_code 键 600552.SH → 仍须匹配到主线。"""
    _seed_concentration(conn, "2026-06-09", ["玻璃玻纤"])
    reg = FakeRegistry(
        limit_stocks=[{"code": "600552", "name": "凯盛科技", "pct_chg": 10.0}],  # 裸码
        sw_map={"600552.SH": {"name": "凯盛科技", "sw_l2": "玻璃玻纤"}},          # 后缀键
        bars_by_code={"600552": _leader_bars()},
    )
    summary = scanner.run_daily(conn, reg, "2026-06-09")
    assert summary["candidates"] == 1
    assert "600552" in summary["entered"]


def test_funnel_identity_dedup_bare_then_suffixed_one_active(conn):
    """同一逻辑股先裸码入池、后 ts_code 再命中 → 仅一条 active 行（身份归一，防重复 active）。"""
    _seed_concentration(conn, "2026-06-09", ["玻璃玻纤"])
    reg1 = FakeRegistry(
        limit_stocks=[{"code": "600552", "name": "凯盛科技", "pct_chg": 10.0}],
        sw_map={"600552.SH": {"name": "凯盛科技", "sw_l2": "玻璃玻纤"}},
        bars_by_code={"600552": _leader_bars()},
    )
    scanner.run_daily(conn, reg1, "2026-06-09")
    _seed_concentration(conn, "2026-06-10", ["玻璃玻纤"])
    reg2 = FakeRegistry(
        limit_stocks=[{"code": "600552.SH", "name": "凯盛科技", "pct_chg": 10.0}],  # 换 ts_code 源
        sw_map={"600552.SH": {"name": "凯盛科技", "sw_l2": "玻璃玻纤"}},
        bars_by_code={"600552": _leader_bars()},
    )
    scanner.run_daily(conn, reg2, "2026-06-10")
    actives = pool.list_pool(conn, status="active")
    assert len(actives) == 1 and actives[0]["code"] == "600552"


def test_funnel_skips_touch_on_data_failure(conn):
    """在池股今日拉不到行情(失败/空) → 不 touch 推进、不退池，记 data_errors。"""
    pool.record(conn, code="600552", name="凯盛科技", sw_l2="玻璃玻纤",
                first_limit_date="2026-06-09", date="2026-06-09")
    _seed_concentration(conn, "2026-06-10", ["玻璃玻纤"])
    reg = FakeRegistry(limit_stocks=[], sw_map={}, bars_by_code={})  # 600552 拉不到 → []
    summary = scanner.run_daily(conn, reg, "2026-06-10")
    a = pool.get_active(conn, "600552")
    assert a["days_in_pool"] == 1 and a["last_seen_date"] == "2026-06-09"  # 未推进
    assert "600552" in summary["data_errors"]
    assert "600552" not in summary["exited"]                              # 数据缺失不退池


def test_funnel_records_source_errors_on_discovery_failure(conn):
    """涨停榜/申万映射 provider 失败 → 记 source_errors，不伪装成「今日无候选」。"""
    _seed_concentration(conn, "2026-06-09", ["玻璃玻纤"])

    class FailReg:
        def call(self, name, *a):
            return _R(None, success=False)

    summary = scanner.run_daily(conn, FailReg(), "2026-06-09")
    assert "limit_up" in summary["source_errors"]
    assert "sw_map" in summary["source_errors"]
    assert summary["candidates"] == 0  # sw 不可用 → 跳过 Pass1，但 source_errors 可区分


def test_funnel_exits_active_on_trend_break(conn):
    """在池股今日不涨停 → 走维护；跌破 MA10 → 退池。"""
    pool.record(conn, code="600552", name="凯盛科技", sw_l2="玻璃玻纤",
                first_limit_date="2026-06-09", date="2026-06-09")
    _seed_concentration(conn, "2026-06-12", ["玻璃玻纤"])
    reg = FakeRegistry(
        limit_stocks=[],  # 今日无涨停
        sw_map={},
        bars_by_code={"600552": _mk_bars([10] * 9 + [8])},  # 跌破 MA10
    )
    summary = scanner.run_daily(conn, reg, "2026-06-12")
    assert "600552" in summary["exited"]
    assert pool.get_active(conn, "600552") is None


def test_funnel_no_exit_report_on_stale_backfill(conn):
    """已 last_seen=06-10 后补跑更早的 06-09：mark_exited no-op → 不报 exited、仍 active。"""
    pool.record(conn, code="600552", name="凯盛科技", sw_l2="玻璃玻纤",
                first_limit_date="2026-06-10", date="2026-06-10")
    _seed_concentration(conn, "2026-06-09", ["玻璃玻纤"])
    reg = FakeRegistry(limit_stocks=[], sw_map={},
                       bars_by_code={"600552": _mk_bars([10] * 9 + [8])})  # 旧日 broken bars
    summary = scanner.run_daily(conn, reg, "2026-06-09")
    assert "600552" not in summary["exited"]
    assert pool.get_active(conn, "600552") is not None


def test_funnel_touches_active_without_break(conn):
    """在池股今日不涨停、未破趋势 → touch 保活并记信号。"""
    pool.record(conn, code="600552", name="凯盛科技", sw_l2="玻璃玻纤",
                first_limit_date="2026-06-09", date="2026-06-09")
    _seed_concentration(conn, "2026-06-10", ["玻璃玻纤"])
    reg = FakeRegistry(
        limit_stocks=[],
        sw_map={},
        bars_by_code={"600552": _mk_bars([10] * 10)},  # 平走，未破 MA10
    )
    summary = scanner.run_daily(conn, reg, "2026-06-10")
    assert "600552" not in summary["exited"]
    a = pool.get_active(conn, "600552")
    assert a is not None and a["days_in_pool"] == 2


# ---- GAP A: 双创@15% 加速候选（鞠磊「20cm 涨15%+」，不必全涨停）----

def test_funnel_enters_dual_board_15pct_via_market_changes(conn):
    """双创@16%（非全涨停、不在涨停榜）经 get_market_daily_changes 入候选并入池。"""
    _seed_concentration(conn, "2026-06-09", ["半导体"])
    reg = FakeRegistry(
        limit_stocks=[],                                   # 不在涨停榜（未到 20%）
        sw_map={"688512.SH": {"name": "慧智微", "sw_l2": "半导体"}},
        bars_by_code={"688512": _leader_bars()},
        market_changes=[{"ts_code": "688512.SH", "name": "慧智微", "pct_chg": 16.0}],
    )
    summary = scanner.run_daily(conn, reg, "2026-06-09")
    assert summary["candidates"] == 1
    assert "688512" in summary["entered"]
    assert pool.get_active(conn, "688512") is not None


def test_funnel_dual_board_below_15pct_not_candidate(conn):
    """双创@14%（< 15% 加速阈值）不进候选。"""
    _seed_concentration(conn, "2026-06-09", ["半导体"])
    reg = FakeRegistry(
        limit_stocks=[],
        sw_map={"688512.SH": {"name": "慧智微", "sw_l2": "半导体"}},
        bars_by_code={"688512": _leader_bars()},
        market_changes=[{"ts_code": "688512.SH", "name": "慧智微", "pct_chg": 14.0}],
    )
    summary = scanner.run_daily(conn, reg, "2026-06-09")
    assert summary["candidates"] == 0
    assert pool.get_active(conn, "688512") is None


def test_funnel_main_board_15pct_not_added_by_dual_rule(conn):
    """主板@15%（非涨停）不因双创规则进候选——主板只认涨停榜。"""
    _seed_concentration(conn, "2026-06-09", ["半导体"])
    reg = FakeRegistry(
        limit_stocks=[],
        sw_map={"600519.SH": {"name": "某主板", "sw_l2": "半导体"}},
        bars_by_code={"600519": _leader_bars()},
        market_changes=[{"ts_code": "600519.SH", "name": "某主板", "pct_chg": 15.0}],
    )
    summary = scanner.run_daily(conn, reg, "2026-06-09")
    assert summary["candidates"] == 0
    assert pool.get_active(conn, "600519") is None


def test_funnel_market_changes_failure_degrades_to_limit_only(conn):
    """get_market_daily_changes 失败 → 记 source_errors，降级仅涨停源（不中断）。"""
    _seed_concentration(conn, "2026-06-09", ["玻璃玻纤"])

    class PartialReg(FakeRegistry):
        def call(self, name, *a):
            if name == "get_market_daily_changes":
                return _R(None, success=False)
            return super().call(name, *a)

    reg = PartialReg(
        limit_stocks=[{"code": "600552.SH", "name": "凯盛科技", "pct_chg": 10.0}],
        sw_map={"600552.SH": {"name": "凯盛科技", "sw_l2": "玻璃玻纤"}},
        bars_by_code={"600552": _leader_bars()},
    )
    summary = scanner.run_daily(conn, reg, "2026-06-09")
    assert "market_changes" in summary["source_errors"]
    assert "600552" in summary["entered"]              # 涨停源仍正常入池
