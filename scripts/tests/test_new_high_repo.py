from db.connection import get_connection
from db.migrate import migrate
from services.new_high import repo


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
