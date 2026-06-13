"""趋势主升漏斗 scanner 端到端单测（Stage 2c），mock registry 隔离外网。

漏斗：涨停列表 → 映射申万二级 → ∩ 主线(Top-K ∪ sectors) → 拉OHLCV → 检测 → 入池/维护/退池。
入池门槛 = 主线∩ + 首次涨停加速 + 缓涨（**不含贴MA5**：涨停日必远离 MA5，贴MA5 是在池后回踩信号）。
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
    def __init__(self, limit_stocks, sw_map, bars_by_code):
        self._lu = limit_stocks
        self._sw = sw_map
        self._bars = bars_by_code

    def call(self, name, *args):
        if name == "get_limit_up_list":
            return _R({"stocks": self._lu})
        if name == "get_stock_sw_industry_map":
            return _R(self._sw)
        if name == "get_stock_daily_range":
            return _R(self._bars.get(args[0], []))
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
        bars_by_code={"600552.SH": _leader_bars(), "000001.SZ": _leader_bars()},
    )
    summary = scanner.run_daily(conn, reg, "2026-06-09")
    assert summary["candidates"] == 1          # 只有玻璃玻纤的凯盛进候选（平安非主线被滤）
    assert "600552.SH" in summary["entered"]
    assert pool.get_active(conn, "600552.SH") is not None
    assert pool.get_active(conn, "000001.SZ") is None


def test_funnel_skips_when_detectors_fail(conn):
    """主线∩涨停 但不满足首次涨停/缓涨（历史含涨停）→ 不入池。"""
    _seed_concentration(conn, "2026-06-09", ["玻璃玻纤"])
    bars = _leader_bars()
    bars[5]["pct_chg"] = 10.0  # 历史窗口里早有涨停 → 非首次
    reg = FakeRegistry(
        limit_stocks=[{"code": "600552.SH", "name": "凯盛科技", "pct_chg": 10.0}],
        sw_map={"600552.SH": {"name": "凯盛科技", "sw_l2": "玻璃玻纤"}},
        bars_by_code={"600552.SH": bars},
    )
    summary = scanner.run_daily(conn, reg, "2026-06-09")
    assert summary["candidates"] == 1
    assert summary["entered"] == []
    assert pool.get_active(conn, "600552.SH") is None


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


def test_funnel_skips_touch_on_data_failure(conn):
    """在池股今日拉不到行情(失败/空) → 不 touch 推进、不退池，记 data_errors。"""
    pool.record(conn, code="600552.SH", name="凯盛科技", sw_l2="玻璃玻纤",
                first_limit_date="2026-06-09", date="2026-06-09")
    _seed_concentration(conn, "2026-06-10", ["玻璃玻纤"])
    reg = FakeRegistry(limit_stocks=[], sw_map={}, bars_by_code={})  # 600552.SH 拉不到 → []
    summary = scanner.run_daily(conn, reg, "2026-06-10")
    a = pool.get_active(conn, "600552.SH")
    assert a["days_in_pool"] == 1 and a["last_seen_date"] == "2026-06-09"  # 未推进
    assert "600552.SH" in summary["data_errors"]
    assert "600552.SH" not in summary["exited"]                            # 数据缺失不退池


def test_funnel_exits_active_on_trend_break(conn):
    """在池股今日不涨停 → 走维护；跌破 MA10 → 退池。"""
    pool.record(conn, code="600552.SH", name="凯盛科技", sw_l2="玻璃玻纤",
                first_limit_date="2026-06-09", date="2026-06-09")
    _seed_concentration(conn, "2026-06-12", ["玻璃玻纤"])
    reg = FakeRegistry(
        limit_stocks=[],  # 今日无涨停
        sw_map={},
        bars_by_code={"600552.SH": _mk_bars([10] * 9 + [8])},  # 跌破 MA10
    )
    summary = scanner.run_daily(conn, reg, "2026-06-12")
    assert "600552.SH" in summary["exited"]
    assert pool.get_active(conn, "600552.SH") is None


def test_funnel_touches_active_without_break(conn):
    """在池股今日不涨停、未破趋势 → touch 保活并记信号。"""
    pool.record(conn, code="600552.SH", name="凯盛科技", sw_l2="玻璃玻纤",
                first_limit_date="2026-06-09", date="2026-06-09")
    _seed_concentration(conn, "2026-06-10", ["玻璃玻纤"])
    reg = FakeRegistry(
        limit_stocks=[],
        sw_map={},
        bars_by_code={"600552.SH": _mk_bars([10] * 10)},  # 平走，未破 MA10
    )
    summary = scanner.run_daily(conn, reg, "2026-06-10")
    assert "600552.SH" not in summary["exited"]
    a = pool.get_active(conn, "600552.SH")
    assert a is not None and a["days_in_pool"] == 2
