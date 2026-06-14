"""L5: GET /api/trend-leaders 只读路由测试。

读 trend_leader_pool（active/exited），白名单 DTO：非价位列 + 归因 + signal_hits（Pass1 行=null）；
**不外泄 raw last_signal**（含 MA5/MA10/vol 等均线价位，违红线）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from fastapi.testclient import TestClient

from db.connection import get_connection
from db.migrate import migrate
from services.trend_leader import pool


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test_tl.db"
    conn = get_connection(path)
    migrate(conn)
    conn.close()
    return path


@pytest.fixture
def client(db_path, monkeypatch):
    monkeypatch.setattr("db.connection._DEFAULT_DB_PATH", db_path)
    from api.main import app
    return TestClient(app)


def _pass2_sig(*, near_dev=0.01, over_dev=0.04, is_yin=True, shrink=True,
               entry_trigger="涨停", branch_concepts=None):
    return {
        "shrink_pullback": {"is_yin": is_yin, "shrink": shrink, "insufficient_history": False},
        "near_ma5": {"ma5": 15.2, "deviation": near_dev, "insufficient_history": False},
        "overheat": {"ma5": 15.2, "deviation": over_dev, "insufficient_history": False},
        "trend": {"ma10": 14.9, "below_ma10": False, "insufficient_history": False},
        "entry_trigger": entry_trigger, "branch_concepts": branch_concepts or [],
    }


@pytest.fixture
def seeded(db_path):
    conn = get_connection(db_path)
    # active + 已 Pass2 维护（有信号）
    pool.record(conn, code="600552", name="凯盛科技", sw_l2="玻璃玻纤",
                first_limit_date="2026-06-09", date="2026-06-09",
                signal_json={"first_limit": {}, "entry_trigger": "涨停", "branch_concepts": ["CPO"]})
    pool.touch(conn, "600552", date="2026-06-10",
               signal_json=_pass2_sig(near_dev=0.01, over_dev=0.04, branch_concepts=["CPO"]))
    # active + 仅 Pass1（未维护）→ signal_hits=null
    pool.record(conn, code="605358", name="立昂微", sw_l2="半导体",
                first_limit_date="2026-06-10", date="2026-06-10",
                signal_json={"first_limit": {}, "entry_trigger": "双创15%加速", "branch_concepts": []})
    # exited
    pool.record(conn, code="000999", name="某退池股", sw_l2="软件开发",
                first_limit_date="2026-06-08", date="2026-06-08",
                signal_json={"first_limit": {}, "entry_trigger": "涨停", "branch_concepts": []})
    pool.mark_exited(conn, "000999", date="2026-06-11", reason="收盘跌破MA10")
    conn.commit()
    conn.close()


def test_empty_pool_returns_empty_list(client):
    r = client.get("/api/trend-leaders")
    assert r.status_code == 200
    assert r.json() == []


def test_lists_all_with_signal_hits(client, seeded):
    r = client.get("/api/trend-leaders")
    assert r.status_code == 200
    rows = {row["code"]: row for row in r.json()}
    assert set(rows) == {"600552", "605358", "000999"}
    # 每行都带 signal_hits 键
    assert all("signal_hits" in row for row in rows.values())


def test_pass2_row_signal_hits_reconstructed(client, seeded):
    rows = {row["code"]: row for row in client.get("/api/trend-leaders").json()}
    hits = rows["600552"]["signal_hits"]
    assert hits == {"shrink_pullback_buy": True, "near_ma5": True, "overheat": False}


def test_pass1_row_signal_hits_null(client, seeded):
    rows = {row["code"]: row for row in client.get("/api/trend-leaders").json()}
    assert rows["605358"]["signal_hits"] is None


def test_filter_active(client, seeded):
    rows = client.get("/api/trend-leaders?status=active").json()
    assert {r["code"] for r in rows} == {"600552", "605358"}


def test_filter_exited_has_reason(client, seeded):
    rows = client.get("/api/trend-leaders?status=exited").json()
    assert len(rows) == 1
    assert rows[0]["code"] == "000999"
    assert rows[0]["exit_reason"] == "收盘跌破MA10"


def test_invalid_status_rejected(client, seeded):
    r = client.get("/api/trend-leaders?status=bogus")
    assert r.status_code == 422


_FORBIDDEN_PRICE_KEYS = {"last_signal", "last_signal_json", "ma5", "ma10", "deviation",
                         "vol", "prev_vol", "ma5_vol", "trend", "neckline", "bottom1", "bottom2"}


def _assert_no_price_keys(node, path=""):
    """递归断言响应体任意层级都不含价位/量能明细键（红线 round-3：分支元素也要查）。"""
    if isinstance(node, dict):
        for k, v in node.items():
            assert k not in _FORBIDDEN_PRICE_KEYS, f"价位明细泄漏于 {path}.{k}"
            _assert_no_price_keys(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _assert_no_price_keys(v, f"{path}[{i}]")


def test_no_price_detail_leaked(client, seeded):
    """红线「不含价位」：响应任意层级不得含 raw last_signal 或均线价位/量能明细。"""
    for row in client.get("/api/trend-leaders").json():
        _assert_no_price_keys(row)
        sh = row["signal_hits"]
        # signal_hits 只含布尔 chip（near_ma5 是命中标记非价位），键名固定
        assert sh is None or set(sh) == {"shrink_pullback_buy", "near_ma5", "overheat"}


def test_branch_concepts_dict_elements_filtered(client, db_path):
    """branch_concepts 漂移成含 dict（夹带价位）时，DTO 只留 str 元素，红线不破。"""
    conn = get_connection(db_path)
    pool.record(conn, code="600003", name="脏分支股", sw_l2="券商",
                first_limit_date="2026-06-09", date="2026-06-09",
                signal_json={"first_limit": {}, "entry_trigger": "涨停",
                             "branch_concepts": ["CPO", {"ma5": 15.2, "trend": {"ma10": 14.9}}]})
    conn.commit()
    conn.close()
    row = {x["code"]: x for x in client.get("/api/trend-leaders").json()}["600003"]
    assert row["branch_concepts"] == ["CPO"]  # dict 元素被剔除
    _assert_no_price_keys(row)  # 递归确认无 ma5/ma10 泄漏


def test_non_price_attribution_surfaced(client, seeded):
    """非价位归因（触发方式 / 概念分支）从 last_signal 提到顶层，Pass1 行也有。"""
    rows = {row["code"]: row for row in client.get("/api/trend-leaders").json()}
    assert rows["600552"]["entry_trigger"] == "涨停"
    assert rows["600552"]["branch_concepts"] == ["CPO"]
    assert rows["605358"]["entry_trigger"] == "双创15%加速"  # Pass1 也带归因
    assert rows["605358"]["branch_concepts"] == []


def test_dirty_shape_row_does_not_break_endpoint(client, db_path):
    """脏值行（branch_concepts 非 list + 嵌套明细非 dict）不得 500 整个端点，归一降级。"""
    conn = get_connection(db_path)
    pool.record(conn, code="600002", name="脏形状股", sw_l2="电力",
                first_limit_date="2026-06-09", date="2026-06-09",
                signal_json={"first_limit": {}, "entry_trigger": "涨停", "branch_concepts": []})
    # 直接塞一条 truthy 非 dict 嵌套 + 非 list branch_concepts 的 last_signal（模拟脏值/漂移）
    pool.touch(conn, "600002", date="2026-06-10",
               signal_json={"shrink_pullback": True, "near_ma5": [], "overheat": "x",
                            "entry_trigger": "涨停", "branch_concepts": "CPO"})
    conn.commit()
    conn.close()
    r = client.get("/api/trend-leaders")
    assert r.status_code == 200  # 不 500
    row = {x["code"]: x for x in r.json()}["600002"]
    assert row["branch_concepts"] == []  # 非 list 归一为 []
    assert row["signal_hits"] == {"shrink_pullback_buy": False, "near_ma5": False, "overheat": False}


def test_nan_deviation_does_not_break_signal_hits(client, db_path):
    """脏值 NaN 偏离率不外泄、也不污染 signal_hits（abs(nan)<=阈值=False）。"""
    conn = get_connection(db_path)
    pool.record(conn, code="600001", name="脏值股", sw_l2="银行",
                first_limit_date="2026-06-09", date="2026-06-09",
                signal_json={"first_limit": {}, "entry_trigger": "涨停", "branch_concepts": []})
    pool.touch(conn, "600001", date="2026-06-10",
               signal_json=_pass2_sig(near_dev=float("nan"), over_dev=0.04))
    conn.commit()
    conn.close()
    row = {r["code"]: r for r in client.get("/api/trend-leaders").json()}["600001"]
    assert "last_signal" not in row
    assert row["signal_hits"]["near_ma5"] is False  # NaN 偏离率 → 未命中，无异常
