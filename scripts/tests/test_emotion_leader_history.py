from __future__ import annotations

import json
import sqlite3

from services.emotion_leader.history import discover_lifecycles, load_history


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE daily_market (date TEXT PRIMARY KEY, raw_data TEXT NOT NULL);
        CREATE TABLE trade_calendar (date TEXT PRIMARY KEY, is_open INTEGER NOT NULL);
        CREATE TABLE leader_tracking (
            id INTEGER PRIMARY KEY,
            stock_code TEXT,
            stock_name TEXT,
            sector TEXT,
            attribute_type TEXT,
            first_seen_date TEXT,
            last_seen_date TEXT,
            current_phase TEXT,
            is_active INTEGER
        );
        """
    )
    return conn


def _add_day(conn: sqlite3.Connection, date: str, stocks: list[dict], down: list[dict] | None = None) -> None:
    payload = {
        "raw_data": {
            "limit_up": {"stocks": stocks},
            "limit_down": {"stocks": down or []},
        }
    }
    conn.execute("INSERT INTO trade_calendar VALUES (?, 1)", (date,))
    conn.execute("INSERT INTO daily_market VALUES (?, ?)", (date, json.dumps(payload)))


def _stock(code: str, name: str, height: int, amount: float = 10.0) -> dict:
    return {
        "code": code,
        "name": name,
        "limit_times": height,
        "amount_billion": amount,
        "industry": "测试行业",
    }


def test_three_board_promotes_and_suspension_gap_keeps_chain() -> None:
    conn = _connection()
    _add_day(conn, "2026-07-20", [_stock("000001", "甲公司", 1)])
    _add_day(conn, "2026-07-21", [
        _stock("000001", "甲公司", 2, 1),
        _stock("000002", "乙公司", 5, 20),
        _stock("000003", "丙公司", 4, 15),
    ])
    _add_day(conn, "2026-07-22", [_stock("000001", "甲公司", 3)])
    _add_day(conn, "2026-07-23", [])
    _add_day(conn, "2026-07-24", [_stock("000001", "甲公司", 4)])

    history = load_history(conn, "2026-07-24")
    discovered = discover_lifecycles(conn, history)
    item = next(row for row in discovered["promoted"] if row["code"] == "000001.SZ")

    assert item["launch_date"] == "2026-07-20"
    assert item["candidate_date"] == "2026-07-21"
    assert item["promoted_date"] == "2026-07-22"
    assert item["max_height"] == 4
    assert item["run_count"] == 1


def test_height_front_can_promote_at_two_boards() -> None:
    conn = _connection()
    _add_day(conn, "2026-07-20", [_stock("300001", "丁公司", 1)])
    _add_day(conn, "2026-07-21", [_stock("300001", "丁公司", 2)])

    discovered = discover_lifecycles(conn, load_history(conn, "2026-07-21"))
    item = next(row for row in discovered["promoted"] if row["code"] == "300001.SZ")

    assert item["promoted_date"] == "2026-07-21"
    assert item["board_type"] == "20cm"


def test_height_breakthrough_links_the_leaders_launch_date() -> None:
    conn = _connection()
    for day in range(1, 22):
        stocks = []
        if day >= 18:
            stocks = [_stock("000001", "甲公司", day - 17)]
        _add_day(conn, f"2026-07-{day:02d}", stocks)

    discovered = discover_lifecycles(conn, load_history(conn, "2026-07-21"))
    event = discovered["height_breakthrough"]

    assert event["status"] == "triggered"
    assert event["source_status"] == "complete"
    assert event["lookback_open_days"] == 20
    assert event["previous_max_height"] == 3
    assert event["current_max_height"] == 4
    assert event["leaders"] == [
        {
            "code": "000001.SZ",
            "name": "甲公司",
            "launch_date": "2026-07-18",
            "launch_method": "limit_chain",
            "current_height": 4,
        }
    ]


def test_height_breakthrough_fails_closed_when_comparison_window_has_gap() -> None:
    conn = _connection()
    for day in range(1, 22):
        stocks = [_stock("000001", "甲公司", day - 17)] if day >= 18 else []
        _add_day(conn, f"2026-07-{day:02d}", stocks)
    conn.execute("DELETE FROM daily_market WHERE date = '2026-07-10'")

    discovered = discover_lifecycles(conn, load_history(conn, "2026-07-21"))
    event = discovered["height_breakthrough"]

    assert event["status"] == "missing_data"
    assert event["source_status"] == "partial"
    assert "缺1个开放日事实" in event["reason"]


def test_manual_confirmed_core_is_merged_without_limit_history() -> None:
    conn = _connection()
    _add_day(conn, "2026-07-21", [])
    conn.execute(
        """
        INSERT INTO leader_tracking
        (stock_code, stock_name, sector, attribute_type, first_seen_date,
         last_seen_date, current_phase, is_active)
        VALUES ('600001', '人工核心', '人工题材', '连板核心', '2026-07-18',
                '2026-07-21', '二波', 1)
        """
    )
    conn.execute(
        """
        INSERT INTO leader_tracking
        (stock_code, stock_name, sector, attribute_type, first_seen_date,
         last_seen_date, current_phase, is_active)
        VALUES ('600002', '未来核心', '未来题材', '连板核心', '2026-07-22',
                '2026-07-22', '单波', 1)
        """
    )

    discovered = discover_lifecycles(conn, load_history(conn, "2026-07-21"))
    item = next(row for row in discovered["promoted"] if row["code"] == "600001.SH")

    assert item["manual_confirmed"] is True
    assert item["manual_sector"] == "人工题材"
    assert item["manual_phase"] == "二波"
    assert item["launch_method"] == "manual_confirmed"
    assert all(row["code"] != "600002.SH" for row in discovered["promoted"])


def test_target_limit_source_failure_is_not_reported_as_empty_day() -> None:
    conn = _connection()
    conn.execute("INSERT INTO trade_calendar VALUES ('2026-07-21', 1)")
    conn.execute(
        "INSERT INTO daily_market VALUES (?, ?)",
        ("2026-07-21", json.dumps({"raw_data": {"limit_up": {"error": "timeout"}, "limit_down": {"stocks": []}}})),
    )

    history = load_history(conn, "2026-07-21")

    assert history["target_ok"] is False
    assert any("涨停来源失败" in error for error in history["errors"])




def test_subsequent_single_limit_refreshes_activity_without_changing_promotion():
    from services.emotion_leader.tracking import annotate
    conn = _connection()
    for day, height in [("2026-09-01", 1), ("2026-09-02", 2), ("2026-09-03", 3), ("2026-10-28", 1), ("2026-10-29", 0)]:
        _add_day(conn, day, [_stock("000001", "甲公司", height)] if height else [])
    discovered = discover_lifecycles(conn, load_history(conn, "2026-10-29"))
    item = discovered["promoted"][0]
    assert item["last_limit_up_date"] == "2026-10-28"
    assert item["launch_date"] == "2026-09-01" and item["max_height"] == 3 and item["run_count"] == 1
    assert annotate({**item,"metric_status":"ok","today_pct_chg":-5},"2026-10-29",["2026-10-28","2026-10-29"])["tracking_group"] == "current"


def test_manual_only_core_single_limit_survives_confirmation_expiry():
    from services.emotion_leader.tracking import annotate
    conn = _connection()
    conn.execute("INSERT INTO leader_tracking VALUES (1, '000001', '甲公司', '测试行业', '前排活跃', '2026-09-01', '2026-09-02', '', 1)")
    _add_day(conn, "2026-09-01", [])
    _add_day(conn, "2026-09-02", [])
    _add_day(conn, "2026-10-28", [_stock("000001", "甲公司", 1)])
    _add_day(conn, "2026-10-29", [])
    item = discover_lifecycles(conn, load_history(conn, "2026-10-29"))["promoted"][0]
    assert item["last_limit_up_date"] == "2026-10-28"
    assert item["launch_method"] == "manual_confirmed" and item["max_height"] == 0 and item["run_count"] == 0
    assert annotate({**item, "today_pct_chg": -4}, "2026-10-29", ["2026-10-28", "2026-10-29"])["tracking_group"] == "current"


def test_latest_manual_confirmation_wins_across_sector_attribute_and_code_alias():
    from services.emotion_leader.tracking import annotate
    for automatic in (False, True):
        conn = _connection()
        for day, height in [("2026-09-01", 1), ("2026-09-02", 2), ("2026-09-03", 3), ("2026-10-29", 0)]:
            _add_day(conn, day, [_stock("000001", "甲公司", height)] if automatic and height else [])
        conn.executemany("INSERT INTO leader_tracking VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)", [
            (1, "000001", "甲公司", "旧行业", "前排活跃", "2026-09-01", "2026-09-02", "旧阶段"),
            (2, "000001.SZ", "甲公司", "新行业", "连板核心", "2026-10-29", "2026-10-29", "新阶段"),
        ])
        promoted = discover_lifecycles(conn, load_history(conn, "2026-10-29"))["promoted"]
        assert len(promoted) == 1
        item = promoted[0]
        assert item["manual_last_seen_date"] == "2026-10-29"
        assert item["manual_sector"] == "新行业" and item["manual_phase"] == "新阶段"
        current = annotate({**item, "metric_status": "ok", "today_pct_chg": -4}, "2026-10-29", ["2026-10-29"])
        assert current["manual_priority_active"] is True and current["manual_age_open_days"] == 0
        assert current["tracking_group"] == "current"
        conn.close()
