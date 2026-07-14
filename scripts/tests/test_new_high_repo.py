from db.connection import get_connection
from db.migrate import migrate
from services.new_high import repo


REQUIRED_TABLES = {
    "daily_new_high_stats",
    "stock_adjusted_high_watermark",
    "trade_calendar",
}


def _insert_calendar(conn, entries):
    conn.executemany(
        "INSERT INTO trade_calendar (date, is_open) VALUES (?, ?)",
        entries,
    )
    conn.commit()


def test_save_and_get_daily_stats_round_trips_json(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    migrate(conn)
    record = {
        "date": "2026-07-08",
        "market_count": 3,
        "new_high_count": 2,
        "sector_summary": [{"industry": "半导体", "count": 2}],
        "stocks": [{"code": "688001.SH", "name": "样例", "industry": "半导体"}],
        "source": {"quote_source": "fake"},
    }
    repo.save_daily_stats(conn, record)

    loaded = repo.get_daily_stats(conn, "2026-07-08")
    assert loaded["date"] == "2026-07-08"
    assert loaded["new_high_count"] == 2
    assert loaded["sector_summary"][0]["industry"] == "半导体"
    assert loaded["stocks"][0]["code"] == "688001.SH"
    assert loaded["source"]["quote_source"] == "fake"
    conn.close()


def test_upsert_watermark_keeps_max_high(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    migrate(conn)
    repo.upsert_watermark(conn, {
        "code": "600001.SH",
        "name": "样例A",
        "industry": "银行",
        "max_adj_high": 10.0,
        "max_high_date": "2026-07-07",
        "max_raw_high": 9.5,
        "last_seen_date": "2026-07-07",
    })
    repo.upsert_watermark(conn, {
        "code": "600001.SH",
        "name": "样例A",
        "industry": "银行",
        "max_adj_high": 12.0,
        "max_high_date": "2026-07-08",
        "max_raw_high": 11.0,
        "last_seen_date": "2026-07-08",
    })

    watermarks = repo.get_watermarks(conn, ["600001.SH"])
    assert watermarks["600001.SH"]["max_adj_high"] == 12.0
    assert watermarks["600001.SH"]["max_high_date"] == "2026-07-08"
    conn.close()


def test_upsert_watermarks_batch_writes_multiple_rows(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    migrate(conn)

    repo.upsert_watermarks(conn, [
        {
            "code": "600001.SH",
            "name": "样例A",
            "industry": "银行",
            "max_adj_high": 10.0,
            "max_high_date": "2026-07-07",
            "max_raw_high": 9.5,
            "last_seen_date": "2026-07-07",
        },
        {
            "code": "600002.SH",
            "name": "样例B",
            "industry": "半导体",
            "max_adj_high": 20.0,
            "max_high_date": "2026-07-07",
            "max_raw_high": 19.5,
            "last_seen_date": "2026-07-07",
        },
    ])

    watermarks = repo.get_watermarks(conn, ["600001.SH", "600002.SH"])
    assert set(watermarks) == {"600001.SH", "600002.SH"}
    assert watermarks["600002.SH"]["industry"] == "半导体"
    conn.close()


def test_upsert_watermark_never_moves_last_seen_date_backwards(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    migrate(conn)
    repo.upsert_watermark(
        conn,
        {
            "code": "600001.SH",
            "name": "样例A",
            "industry": "银行",
            "max_adj_high": 15.0,
            "max_high_date": "2026-07-14",
            "max_raw_high": 15.0,
            "last_seen_date": "2026-07-14",
        },
    )
    repo.upsert_watermark(
        conn,
        {
            "code": "600001.SH",
            "name": "旧数据",
            "industry": "旧行业",
            "max_adj_high": 11.0,
            "max_high_date": "2026-07-13",
            "max_raw_high": 11.0,
            "last_seen_date": "2026-07-13",
        },
    )

    watermark = repo.get_watermarks(conn, ["600001.SH"])["600001.SH"]
    assert watermark["last_seen_date"] == "2026-07-14"
    assert watermark["max_adj_high"] == 15.0
    assert watermark["name"] == "样例A"
    conn.close()


def test_get_recent_stats_returns_ascending_window(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    migrate(conn)
    for date in ["2026-07-06", "2026-07-07", "2026-07-08"]:
        repo.save_daily_stats(conn, {
            "date": date,
            "market_count": 10,
            "new_high_count": 1,
            "sector_summary": [],
            "stocks": [],
            "source": {},
        })

    rows = repo.get_recent_stats(conn, "2026-07-08", 2)
    assert [row["date"] for row in rows] == ["2026-07-07", "2026-07-08"]
    conn.close()


def test_get_missing_required_tables_is_read_only(tmp_path):
    conn = get_connection(tmp_path / "bare.db")
    conn.execute("CREATE TABLE sentinel (id INTEGER PRIMARY KEY)")
    conn.commit()
    before = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }

    missing = repo.get_missing_required_tables(conn)

    after = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert set(missing) == REQUIRED_TABLES
    assert after == before
    conn.close()


def test_get_missing_required_tables_returns_empty_after_migrate(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    migrate(conn)

    assert repo.get_missing_required_tables(conn) == []
    conn.close()


def test_get_latest_stats_date_returns_max_date(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    migrate(conn)
    for date in ["2026-07-13", "2026-07-10", "2026-07-14"]:
        repo.save_daily_stats(conn, {
            "date": date,
            "market_count": 10,
            "new_high_count": 1,
            "sector_summary": [],
            "stocks": [],
            "source": {},
        })

    assert repo.get_latest_stats_date(conn) == "2026-07-14"
    conn.close()


def test_get_latest_watermark_date_uses_last_seen_date(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    migrate(conn)
    repo.upsert_watermarks(conn, [
        {
            "code": "600001.SH",
            "name": "样例A",
            "industry": "银行",
            "max_adj_high": 12.0,
            "max_high_date": "2026-07-10",
            "max_raw_high": 12.0,
            "last_seen_date": "2026-07-13",
        },
        {
            "code": "600002.SH",
            "name": "样例B",
            "industry": "半导体",
            "max_adj_high": 20.0,
            "max_high_date": "2026-07-11",
            "max_raw_high": 20.0,
            "last_seen_date": "2026-07-14",
        },
    ])

    assert repo.get_latest_watermark_date(conn) == "2026-07-14"
    conn.close()


def test_get_trade_calendar_rows_is_start_exclusive_end_inclusive(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    migrate(conn)
    _insert_calendar(conn, [
        ("2026-07-10", 1),
        ("2026-07-11", 0),
        ("2026-07-12", 0),
        ("2026-07-13", 1),
        ("2026-07-14", 1),
    ])

    rows = repo.get_trade_calendar_rows(conn, "2026-07-10", "2026-07-13")

    assert [(row["date"], row["is_open"]) for row in rows] == [
        ("2026-07-11", 0),
        ("2026-07-12", 0),
        ("2026-07-13", 1),
    ]
    conn.close()
