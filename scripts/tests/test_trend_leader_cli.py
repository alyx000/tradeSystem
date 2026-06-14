"""趋势主升 CLI handler 行为单测（Stage 3）。

覆盖 smoke parse 之外的运行语义：
- --dry-run：内存副本跑，**不落真实池**、不推送；
- --no-push：落池但不推送；
- 裸 daily：落池 + 推送；
- pool --json：结构化输出；
- --sectors 非法 JSON → 退出。
mock registry + monkeypatch get_connection 指向临时库，隔离外网与真实库。
"""
from __future__ import annotations

import argparse
import json

import pytest

import main as main_module
from cli import trend_leader as tl
from db.connection import get_connection
from db.schema import init_schema
from services.trend_leader import pool


class _R:
    def __init__(self, data, success=True):
        self.data = data
        self.success = success


class FakeRegistry:
    def __init__(self, limit_stocks, sw_map, bars_by_code):
        self._lu, self._sw, self._bars = limit_stocks, sw_map, bars_by_code

    def initialize_all(self):
        pass

    def call(self, name, *args):
        if name == "get_limit_up_list":
            return _R({"stocks": self._lu})
        if name == "get_stock_sw_industry_map":
            return _R(self._sw)
        if name == "get_stock_daily_range":
            return _R(self._bars.get(args[0], []))
        return _R(None, success=False)


def _mk_bars(closes, pcts):
    n = len(closes)
    return [
        {"trade_date": f"2026-06-{i + 1:02d}", "open": closes[i], "high": closes[i],
         "low": closes[i], "close": closes[i], "pre_close": closes[i - 1] if i else closes[i],
         "vol": 1000.0, "amount": closes[i] * 1000.0, "pct_chg": pcts[i]}
        for i in range(n)
    ]


def _leader_bars():
    closes = [round(10.0 + i * (1.0 / 18), 4) for i in range(19)] + [12.1]
    pcts = [0.5] * 19 + [10.0]
    return _mk_bars(closes, pcts)


def _leader_registry():
    return FakeRegistry(
        limit_stocks=[{"code": "600552.SH", "name": "凯盛科技", "pct_chg": 10.0}],
        sw_map={"600552.SH": {"name": "凯盛科技", "sw_l2": "玻璃玻纤"}},
        bars_by_code={"600552": _leader_bars()},
    )


@pytest.fixture
def tmp_db(tmp_path):
    db = str(tmp_path / "trade.db")
    conn = get_connection(db)
    init_schema(conn)
    conn.execute(
        "INSERT INTO daily_volume_concentration "
        "(date, top_n, total_amount_billion, stocks_json, sector_summary_json) VALUES (?,?,?,?,?)",
        ("2026-06-12", 20, 1000.0, "[]",
         json.dumps([{"industry": "玻璃玻纤", "amount_billion": 100.0}], ensure_ascii=False)))
    conn.commit()
    conn.close()
    return db


@pytest.fixture
def wired(tmp_db, monkeypatch):
    """接线：setup_providers→FakeRegistry，get_connection→临时库，push→记账。"""
    reg = _leader_registry()
    monkeypatch.setattr(main_module, "setup_providers", lambda config: reg)
    monkeypatch.setattr(tl, "get_connection", lambda *a, **k: get_connection(tmp_db))
    pushes = []
    monkeypatch.setattr(tl, "_push_to_dingtalk", lambda title, md: pushes.append((title, md)))
    return {"db": tmp_db, "pushes": pushes}


def _daily_args(**over):
    base = dict(date="2026-06-12", sectors=None, top_k=5, dry_run=False, no_push=False,
                main_line="l2", top_concepts=8)
    base.update(over)
    return argparse.Namespace(**base)


def test_daily_dry_run_does_not_persist(wired, capsys):
    tl._run_daily({}, _daily_args(dry_run=True))
    out = capsys.readouterr().out
    assert "趋势主升观察清单" in out
    assert wired["pushes"] == []                       # 未推送
    conn = get_connection(wired["db"])
    try:
        assert pool.get_active(conn, "600552") is None  # 内存副本跑，真实库无落池
    finally:
        conn.close()


def test_daily_no_push_persists_without_push(wired, capsys):
    tl._run_daily({}, _daily_args(no_push=True))
    assert wired["pushes"] == []                        # 未推送
    conn = get_connection(wired["db"])
    try:
        assert pool.get_active(conn, "600552") is not None  # 已落池
    finally:
        conn.close()


def test_daily_persists_and_pushes(wired, capsys):
    tl._run_daily({}, _daily_args())
    assert len(wired["pushes"]) == 1                    # 推送一次
    assert "趋势主升观察清单 · 2026-06-12" == wired["pushes"][0][0]
    conn = get_connection(wired["db"])
    try:
        assert pool.get_active(conn, "600552") is not None
    finally:
        conn.close()


def test_pool_json_output(wired, capsys):
    conn = get_connection(wired["db"])
    pool.record(conn, code="600552", name="凯盛科技", sw_l2="玻璃玻纤",
                first_limit_date="2026-06-12", date="2026-06-12")
    conn.close()
    tl._run_pool({}, argparse.Namespace(status="active", json=True))
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data[0]["code"] == "600552"
    assert "last_signal_json" not in data[0]            # 去掉冗余原始串


def test_parse_sectors_invalid_json_exits():
    with pytest.raises(SystemExit):
        tl._parse_sectors("{not json")


def test_parse_sectors_valid_list():
    assert tl._parse_sectors('["半导体","电池"]') == ["半导体", "电池"]


def test_daily_same_day_rerun_still_shows_entry(wired, capsys):
    """端到端：同日重跑（模拟推送失败重试），第二次报告仍须显示首次发现的新入池。"""
    tl._run_daily({}, _daily_args(no_push=True))   # 首次：600552 入池
    capsys.readouterr()                             # 清掉第一次输出
    tl._run_daily({}, _daily_args(no_push=True))   # 同日重跑：600552→refreshed
    out = capsys.readouterr().out
    assert "600552" in out
    # 必须落在「今日新入池」段，而非仅出现在在池信号
    head = out.split("## 在池信号")[0]
    assert "600552" in head


def test_daily_top_k_rejects_non_positive():
    """--top-k 0/负数必须 argparse 退出(2)，避免 [:top_k] 切出异常主线池后落池+推送。"""
    parser = main_module.build_parser()
    for bad in ["0", "-1", "abc"]:
        with pytest.raises(SystemExit):
            parser.parse_args(["trend-leader", "daily", "--top-k", bad])
