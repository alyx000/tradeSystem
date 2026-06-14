"""market-timing scanner 编排 + 落库单测（in-memory DB + fake registry）。

覆盖：底分型生命周期 forming→confirmed→invalid（跨日读上一行推进）、同日重跑 refreshed
幂等、斐波那契变盘点写入、市场级上下文（成交额分位/涨跌家数/跌停家数）、当日无数据跳过。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest

from db.schema import init_schema
from providers.base import DataResult
from services.market_timing import repo, scanner


# ── fixtures / helpers ──

@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    init_schema(c)
    yield c
    c.close()


class FakeRegistry:
    """按 code 返回预置日线；call_specific 同样命中（供 avg_price 路由）。"""

    def __init__(self, bars_by_code, limit_down=None, changes=None):
        self.bars_by_code = bars_by_code
        self.limit_down = limit_down
        self.changes = changes

    def _range(self, code, start, end):
        data = [b for b in self.bars_by_code.get(code, []) if start <= b["trade_date"] <= end]
        return DataResult(data=data, source=f"fake:{code}")

    def call(self, method, *args):
        if method == "get_index_daily_range":
            return self._range(*args)
        if method == "get_limit_down_list":
            if self.limit_down is None:
                return DataResult(data=None, source="fake", error="no data")
            return DataResult(data=self.limit_down, source="fake")
        if method == "get_market_daily_changes":
            if self.changes is None:
                return DataResult(data=None, source="fake", error="no data")
            return DataResult(data=self.changes, source="fake")
        return DataResult(data=None, source="fake", error="unsupported")

    def call_specific(self, name, method, *args):
        if method == "get_index_daily_range":
            return self._range(*args)
        return DataResult(data=None, source="fake", error="unsupported")


def _dates_ending(end_date: str, n: int) -> list[str]:
    base = datetime.strptime(end_date, "%Y-%m-%d")
    return [(base - timedelta(days=n - 1 - i)).strftime("%Y-%m-%d") for i in range(n)]


def _bars(end_date: str, ocv: list[tuple]) -> list[dict]:
    """ocv: [(open, close, vol[, amount]), ...] 升序，末根日期=end_date。high/low 由 o/c 推。"""
    dates = _dates_ending(end_date, len(ocv))
    out = []
    for d, row in zip(dates, ocv):
        o, c, v = row[0], row[1], row[2]
        amount = row[3] if len(row) > 3 else 0.0
        out.append({"trade_date": d, "open": o, "high": max(o, c), "low": min(o, c),
                    "close": c, "vol": v, "amount": amount})
    return out


# 13 根：idx9 结构低点；idx10 成型日；idx11 放量中阳确认日；idx12 跌破结构低点
_LIFECYCLE_OCV = [
    (100, 100, 1000), (100, 100, 1000), (100, 100, 1000), (100, 100, 1000),
    (100, 100, 1000), (100, 100, 1000), (100, 99, 1000), (99, 98, 1000),
    (98, 97, 1000), (97, 96, 1000), (97, 98, 1000), (98, 102, 2000), (100, 90, 1500),
]
_LC_DATES = _dates_ending("2026-06-13", 13)
_D_FORM = _LC_DATES[10]      # 2026-06-11 成型
_D_CONFIRM = _LC_DATES[11]   # 2026-06-12 确认
_D_INVALID = _LC_DATES[12]   # 2026-06-13 失效


def _lifecycle_registry():
    return FakeRegistry({"TEST.SH": _bars("2026-06-13", _LIFECYCLE_OCV)})


_IDX = [{"code": "TEST.SH", "name": "测试指数"}]


# ── 底分型生命周期 ──

def test_fractal_forms_then_confirms_then_invalidates(conn):
    reg = _lifecycle_registry()
    r1 = scanner.run_daily(conn, reg, _D_FORM, indices=_IDX)
    assert r1["signals"][0]["fractal_status"] == "forming"
    assert r1["signals"][0]["fractal_low_price"] == 96.0

    r2 = scanner.run_daily(conn, reg, _D_CONFIRM, indices=_IDX)
    assert r2["signals"][0]["fractal_status"] == "confirmed"
    assert r2["signals"][0]["fractal_confirm_date"] == _D_CONFIRM

    r3 = scanner.run_daily(conn, reg, _D_INVALID, indices=_IDX)
    assert r3["signals"][0]["fractal_status"] == "invalid"


def test_same_day_rerun_is_refreshed_not_duplicated(conn):
    reg = _lifecycle_registry()
    scanner.run_daily(conn, reg, _D_FORM, indices=_IDX)
    scanner.run_daily(conn, reg, _D_CONFIRM, indices=_IDX)
    scanner.run_daily(conn, reg, _D_CONFIRM, indices=_IDX)  # 重跑
    rows = repo.list_signals(conn, date=_D_CONFIRM)
    assert len(rows) == 1
    assert rows[0]["fractal_status"] == "confirmed"
    total = conn.execute("SELECT COUNT(*) FROM market_timing_signal").fetchone()[0]
    assert total == 2  # 仅 D_FORM + D_CONFIRM 两行，重跑未新增


def test_dry_run_does_not_persist(conn):
    reg = _lifecycle_registry()
    r = scanner.run_daily(conn, reg, _D_FORM, indices=_IDX, dry_run=True)
    assert r["signals"][0]["fractal_status"] == "forming"
    assert conn.execute("SELECT COUNT(*) FROM market_timing_signal").fetchone()[0] == 0


def test_pivot_override_date_not_in_window_raises(conn):
    reg = _lifecycle_registry()
    with pytest.raises(ValueError, match="不在"):
        scanner.run_daily(conn, reg, _D_CONFIRM, indices=_IDX, pivot_overrides={"TEST.SH": "2099-01-01"})


def test_pivot_override_unknown_index_raises(conn):
    reg = _lifecycle_registry()
    with pytest.raises(ValueError, match="不在本次扫描"):
        scanner.run_daily(conn, reg, _D_CONFIRM, indices=_IDX, pivot_overrides={"NOPE.SH": _D_FORM})


def test_pivot_override_hits_sets_manual_pivot(conn):
    reg = _lifecycle_registry()
    r = scanner.run_daily(conn, reg, _D_CONFIRM, indices=_IDX, pivot_overrides={"TEST.SH": _D_FORM})
    assert r["signals"][0]["swing_pivot_date"] == _D_FORM


def test_pivot_override_on_index_with_no_data_for_date_raises(conn):
    """被覆盖指数当日无数据 → 硬失败，不静默跳过（否则用户以为校准生效）。"""
    reg = _lifecycle_registry()  # TEST.SH 末日 2026-06-13
    with pytest.raises(ValueError, match="无数据"):
        scanner.run_daily(conn, reg, "2026-06-20", indices=_IDX, pivot_overrides={"TEST.SH": _D_FORM})


def test_confirm_recovers_when_forming_day_missing(conn):
    """成型日漏跑/跳过/dry_run（库中无成型行）→ 确认日仍能无状态推导出 confirmed。"""
    reg = _lifecycle_registry()
    r = scanner.run_daily(conn, reg, _D_CONFIRM, indices=_IDX)  # 直接跑确认日，无前置成型行
    assert r["signals"][0]["fractal_status"] == "confirmed"
    assert conn.execute("SELECT COUNT(*) FROM market_timing_signal").fetchone()[0] == 1


# ── 斐波那契变盘点 ──

def test_fib_hit_written_from_swing_pivot(conn):
    # 25 根：idx11 为峰值 swing 高点，到末根 idx24 共 13 个交易日 → 命中斐波那契 13
    prices = [100 + i * 2 for i in range(12)] + [122 - (i + 1) * 1.6 for i in range(13)]
    ocv = [(p, p, 1000) for p in prices]  # high=low=close=open，极值口径清晰
    reg = FakeRegistry({"PIV.SH": _bars("2026-06-13", ocv)})
    r = scanner.run_daily(conn, reg, "2026-06-13", indices=[{"code": "PIV.SH", "name": "峰值"}])
    sig = r["signals"][0]
    assert sig["swing_pivot_type"] == "high"
    assert sig["fib_day_count"] == 13
    assert sig["fib_hit"] == 13


# ── 当日点位（daily_market 未采集的指数：中证2000/平均股价 卡片用） ──

def test_today_quote_close_and_change_pct_written(conn):
    """末根 bar 收盘 + 对上一根求涨跌幅 → 写入 close/change_pct，并经 web_payload 暴露。"""
    from services.market_timing import web_payload
    ocv = [(100, 100, 1000)] * 11 + [(101, 102, 1000)]  # 上一根收 100，末根收 102 → +2%
    reg = FakeRegistry({"932000.CSI": _bars("2026-06-13", ocv)})
    r = scanner.run_daily(conn, reg, "2026-06-13", indices=[{"code": "932000.CSI", "name": "中证2000"}])
    sig = r["signals"][0]
    assert sig["close"] == 102
    assert sig["change_pct"] == pytest.approx(2.0)
    # 落库 → 只读查询 + 复盘网站 payload 均能取到
    assert repo.list_signals(conn, date="2026-06-13")[0]["close"] == 102
    payload_sig = web_payload.build_daily_payload(conn, "2026-06-13")["signals"][0]
    assert payload_sig["close"] == 102 and payload_sig["change_pct"] == pytest.approx(2.0)


def test_today_quote_change_pct_none_without_prior_bar(conn):
    """仅一根 bar（无上一根）→ change_pct 为 None，不臆造 0%。"""
    reg = FakeRegistry({"932000.CSI": _bars("2026-06-13", [(101, 102, 1000)])})
    r = scanner.run_daily(conn, reg, "2026-06-13", indices=[{"code": "932000.CSI", "name": "中证2000"}])
    sig = r["signals"][0]
    assert sig["close"] == 102
    assert sig["change_pct"] is None


# ── 市场级上下文 ──

def test_market_context_written(conn):
    flat = [(100, 100, 1000, 400000.0)] * 11
    # 今日成交额最低（地量）：上证+深成各 100000 千元 → 合计 200000 千元 = 2 亿
    sh = _bars("2026-06-13", flat + [(100, 100, 1000, 100000.0)])
    sz = _bars("2026-06-13", flat + [(100, 100, 1000, 100000.0)])
    reg = FakeRegistry(
        {"000001.SH": sh, "399001.SZ": sz},
        limit_down=[{"code": "a"}, {"code": "b"}, {"code": "c"}],
        changes={"advance": 4500, "decline": 900},
    )
    idx = [{"code": "000001.SH", "name": "上证综指"}, {"code": "399001.SZ", "name": "深证成指"}]
    r = scanner.run_daily(conn, reg, "2026-06-13", indices=idx)
    sig = r["signals"][0]
    assert sig["market_amount_yi"] == 2.0
    assert sig["amount_pctile_20d"] == pytest.approx(1 / 12, abs=0.01)  # 今日地量→低分位
    assert sig["limit_down_count"] == 3
    assert sig["advance"] == 4500 and sig["decline"] == 900
    assert r["resonance_count"] == 0  # 平盘无变盘点命中


def test_market_amount_none_when_one_side_missing(conn):
    """单侧指数缺当日数据 → 不把半截成交额当两市总额落库（fail-safe 为 None）。"""
    flat = [(100, 100, 1000, 400000.0)] * 11
    sh = _bars("2026-06-13", flat + [(100, 100, 1000, 100000.0)])
    sz = _bars("2026-06-10", flat + [(100, 100, 1000, 100000.0)])  # 深成末日 != 目标日 → 当日缺
    reg = FakeRegistry({"000001.SH": sh, "399001.SZ": sz})
    idx = [{"code": "000001.SH", "name": "上证综指"}, {"code": "399001.SZ", "name": "深证成指"}]
    r = scanner.run_daily(conn, reg, "2026-06-13", indices=idx)
    sh_sig = [s for s in r["signals"] if s["index_code"] == "000001.SH"][0]
    assert sh_sig["market_amount_yi"] is None
    assert sh_sig["amount_pctile_20d"] is None


# ── 跳过 ──

def test_index_skipped_when_no_data_for_date(conn):
    # 末根日期 != 目标日 → 当日无数据，跳过不写行
    reg = FakeRegistry({"TEST.SH": _bars("2026-06-10", _LIFECYCLE_OCV)})
    r = scanner.run_daily(conn, reg, "2026-06-13", indices=_IDX)
    assert r["signals"] == []
    assert len(r["skipped"]) == 1
    assert r["skipped"][0]["reason"] == "no_data_for_date"
    assert conn.execute("SELECT COUNT(*) FROM market_timing_signal").fetchone()[0] == 0


def test_init_schema_creates_market_timing_table(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(market_timing_signal)").fetchall()}
    assert {"trade_date", "index_code", "fractal_status", "fib_hit", "resonance_count",
            "amount_pctile_20d", "fractal_json"} <= cols
