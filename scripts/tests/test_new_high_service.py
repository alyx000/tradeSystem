from db.connection import get_connection
from db.migrate import migrate
from providers.base import DataResult
from services.new_high import repo, service


class FakeRegistry:
    def __init__(self):
        self.calls = []

    def call(self, name, *args):
        self.calls.append((name, args))
        if name == "get_market_daily_quotes":
            return DataResult(data=[
                {
                    "code": "600001.SH",
                    "ts_code": "600001.SH",
                    "name": "A",
                    "high": 11.0,
                    "pct_chg": 5.0,
                    "amount": 1000,
                },
                {
                    "code": "600002.SH",
                    "ts_code": "600002.SH",
                    "name": "B",
                    "high": 8.0,
                    "pct_chg": 1.0,
                    "amount": 500,
                },
            ], source="fake:daily")
        if name == "get_adj_factor":
            return DataResult(data=[
                {"ts_code": "600001.SH", "adj_factor": 1.1},
                {"ts_code": "600002.SH", "adj_factor": 1.0},
            ], source="fake:adj")
        if name == "get_stock_sw_industry_map":
            return DataResult(data={
                "600001.SH": {"sw_l2": "半导体", "name": "A"},
                "600002.SH": {"sw_l2": "银行", "name": "B"},
            }, source="fake:sw")
        return DataResult(data=None, source="fake", error=f"unexpected {name}")


def test_run_daily_persists_stats_and_updates_watermarks(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    migrate(conn)
    repo.upsert_watermark(conn, {
        "code": "600001.SH",
        "name": "A",
        "industry": "半导体",
        "max_adj_high": 10.0,
        "max_high_date": "2026-07-07",
        "max_raw_high": 10.0,
        "last_seen_date": "2026-07-07",
    })

    record = service.run_daily(conn, FakeRegistry(), "2026-07-08", persist=True, top_n=10)

    assert record["new_high_count"] == 1
    assert repo.get_daily_stats(conn, "2026-07-08")["new_high_count"] == 1
    assert repo.get_watermarks(conn, ["600001.SH"])["600001.SH"]["max_high_date"] == "2026-07-08"
    conn.close()


def test_run_daily_dry_run_does_not_persist(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    migrate(conn)

    record = service.run_daily(conn, FakeRegistry(), "2026-07-08", persist=False, top_n=10)

    assert record["date"] == "2026-07-08"
    assert repo.get_daily_stats(conn, "2026-07-08") is None
    conn.close()


def test_run_daily_source_failed_does_not_persist_normal_stats(tmp_path):
    class EmptyRegistry(FakeRegistry):
        def call(self, name, *args):
            if name == "get_market_daily_quotes":
                return DataResult(data=[], source="fake:daily")
            return super().call(name, *args)

    conn = get_connection(tmp_path / "test.db")
    migrate(conn)

    record = service.run_daily(conn, EmptyRegistry(), "2026-07-08", persist=True, top_n=10)

    assert record["status"] == "source_failed"
    assert repo.get_daily_stats(conn, "2026-07-08") is None
    conn.close()


def test_backfill_processes_dates_in_order(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    migrate(conn)
    calls = []

    class Registry(FakeRegistry):
        def call(self, name, *args):
            if name == "get_market_daily_quotes":
                calls.append(args[0])
            return super().call(name, *args)

    summary = service.run_backfill(conn, Registry(), ["2026-07-08", "2026-07-07"], persist=True, top_n=10)

    assert calls == ["2026-07-07", "2026-07-08"]
    assert summary["processed_count"] == 2
    assert repo.get_daily_stats(conn, "2026-07-08") is not None
    conn.close()
