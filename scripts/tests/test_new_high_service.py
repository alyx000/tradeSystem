import threading

import pytest

from db.connection import get_connection
from db.migrate import migrate
from providers.base import DataResult
from services.new_high import constants as C
from services.new_high import repo, service


@pytest.fixture(autouse=True)
def _use_small_market_floor_for_unit_fixtures(monkeypatch):
    monkeypatch.setattr(C, "MIN_MARKET_COUNT", 1, raising=False)


class FakeRegistry:
    def __init__(self, *, fail_quote_dates=None):
        self.calls = []
        self.fail_quote_dates = set(fail_quote_dates or [])

    def call(self, name, *args):
        self.calls.append((name, args))
        if name == "get_market_daily_quotes":
            if args[0] in self.fail_quote_dates:
                return DataResult(data=[], source="fake:daily", error="source unavailable")
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


class BlockingRegistry(FakeRegistry):
    def __init__(self, provider_entered, release_provider):
        super().__init__()
        self.provider_entered = provider_entered
        self.release_provider = release_provider

    def call(self, name, *args):
        if name == "get_market_daily_quotes":
            self.provider_entered.set()
            if not self.release_provider.wait(timeout=5):
                raise TimeoutError("test did not release blocking provider")
        return super().call(name, *args)


def _seed_stats(conn, date):
    repo.save_daily_stats(conn, {
        "date": date,
        "market_count": 2,
        "new_high_count": 0,
        "sector_summary": [],
        "stocks": [],
        "source": {"quote_source": "seed"},
    })


def _seed_watermark(conn, date):
    repo.upsert_watermark(conn, {
        "code": "600001.SH",
        "name": "A",
        "industry": "半导体",
        "max_adj_high": 10.0,
        "max_high_date": date,
        "max_raw_high": 10.0,
        "last_seen_date": date,
    })


def _seed_baseline(conn, date):
    _seed_stats(conn, date)
    _seed_watermark(conn, date)
    conn.commit()


def _insert_calendar(conn, entries):
    conn.executemany(
        "INSERT INTO trade_calendar (date, is_open) VALUES (?, ?)",
        entries,
    )
    conn.commit()


def _quote_dates(registry):
    return [
        args[0]
        for name, args in registry.calls
        if name == "get_market_daily_quotes"
    ]


def _industry_map(rows):
    return {
        (row.get("code") or row.get("ts_code")): {
            "sw_l2": "测试行业",
            "name": row.get("name") or "测试股票",
        }
        for row in rows
        if row.get("code") or row.get("ts_code")
    }


def _assert_due_summary_shape(summary):
    assert {
        "status",
        "processed_dates",
        "failed_date",
        "target_record",
        "failure_detail",
    } <= set(summary)


def test_run_daily_persists_stats_and_updates_watermarks(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    migrate(conn)
    _seed_baseline(conn, "2026-07-07")

    record = service.run_daily(conn, FakeRegistry(), "2026-07-08", persist=True, top_n=10)

    assert record["new_high_count"] == 1
    conn.close()

    check = get_connection(db_path)
    assert repo.get_daily_stats(check, "2026-07-08")["new_high_count"] == 1
    watermark = repo.get_watermarks(check, ["600001.SH"])["600001.SH"]
    assert watermark["max_high_date"] == "2026-07-08"
    assert watermark["last_seen_date"] == "2026-07-08"
    check.close()


def test_run_daily_rolls_back_watermark_when_save_daily_stats_raises(
    tmp_path,
    monkeypatch,
):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    migrate(conn)
    _seed_baseline(conn, "2026-07-10")

    def _raise_on_stats(_conn, _record):
        raise RuntimeError("daily stats write failed")

    monkeypatch.setattr(repo, "save_daily_stats", _raise_on_stats)

    with pytest.raises(RuntimeError, match="daily stats write failed"):
        service.run_daily(
            conn,
            FakeRegistry(),
            "2026-07-13",
            persist=True,
            top_n=10,
        )
    conn.close()

    check = get_connection(db_path)
    watermark = repo.get_watermarks(check, ["600001.SH"])["600001.SH"]
    assert watermark["max_adj_high"] == 10.0
    assert watermark["last_seen_date"] == "2026-07-10"
    assert repo.get_daily_stats(check, "2026-07-13") is None
    check.close()


def test_run_daily_dry_run_does_not_persist(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    migrate(conn)

    record = service.run_daily(conn, FakeRegistry(), "2026-07-08", persist=False, top_n=10)

    assert record["date"] == "2026-07-08"
    assert repo.get_daily_stats(conn, "2026-07-08") is None
    conn.close()


def test_run_daily_rejects_jointly_truncated_bootstrap_snapshot(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(C, "MIN_MARKET_COUNT", 3, raising=False)
    conn = get_connection(tmp_path / "test.db")
    migrate(conn)

    record = service.run_daily(
        conn,
        FakeRegistry(),
        "2026-07-08",
        persist=True,
        top_n=10,
        expected_tail_date=None,
    )

    assert record["status"] == "coverage_failed"
    assert "market_count_below_absolute_minimum" in record["source"]["coverage_violations"]
    assert repo.get_daily_stats(conn, "2026-07-08") is None
    assert repo.get_latest_watermark_date(conn) is None
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


@pytest.mark.parametrize("industry_data", [None, {}], ids=["failed", "empty"])
def test_run_daily_rejects_unavailable_industry_source_without_advancing_tail(
    tmp_path,
    industry_data,
):
    class IndustryFailedRegistry(FakeRegistry):
        def call(self, name, *args):
            if name == "get_stock_sw_industry_map":
                return DataResult(
                    data=industry_data,
                    source="fake:sw",
                    error="industry unavailable" if industry_data is None else "",
                )
            return super().call(name, *args)

    conn = get_connection(tmp_path / "test.db")
    migrate(conn)
    _seed_baseline(conn, "2026-07-10")

    record = service.run_daily(
        conn,
        IndustryFailedRegistry(),
        "2026-07-13",
        persist=True,
        top_n=10,
        expected_tail_date="2026-07-10",
    )

    assert record["status"] == "source_failed"
    assert record["source"]["failed_source"] == "get_stock_sw_industry_map"
    assert repo.get_daily_stats(conn, "2026-07-13") is None
    assert repo.get_latest_stats_date(conn) == "2026-07-10"
    assert repo.get_latest_watermark_date(conn) == "2026-07-10"
    conn.close()


def test_run_daily_rejects_severely_truncated_industry_map(tmp_path):
    quote_rows = [
        {"code": f"{index:06d}.SZ", "high": 10.0}
        for index in range(1, 101)
    ]
    adj_rows = [
        {"ts_code": row["code"], "adj_factor": 1.0}
        for row in quote_rows
    ]

    class PartialIndustryRegistry:
        def call(self, name, *args):
            if name == "get_market_daily_quotes":
                return DataResult(data=quote_rows, source="fake:daily")
            if name == "get_adj_factor":
                return DataResult(data=adj_rows, source="fake:adj")
            if name == "get_stock_sw_industry_map":
                return DataResult(
                    data={quote_rows[0]["code"]: {"sw_l2": "测试行业"}},
                    source="fake:partial-sw",
                )
            return DataResult(data=None, source="fake", error=f"unexpected {name}")

    conn = get_connection(tmp_path / "test.db")
    migrate(conn)

    record = service.run_daily(
        conn,
        PartialIndustryRegistry(),
        "2026-07-13",
        persist=True,
        top_n=10,
        expected_tail_date=None,
    )

    assert record["status"] == "coverage_failed"
    assert record["source"]["industry_coverage"] == pytest.approx(0.01)
    assert "industry_coverage_below_minimum" in record["source"]["coverage_violations"]
    assert repo.get_daily_stats(conn, "2026-07-13") is None
    assert repo.get_latest_watermark_date(conn) is None
    conn.close()


@pytest.mark.parametrize(
    ("quote_rows", "adj_rows"),
    [
        (
            [{"code": "600001.SH", "name": "A", "high": 11.0}],
            [{"ts_code": "600999.SH", "adj_factor": 1.1}],
        ),
        (
            [{"code": "600001.SH", "name": "A", "high": None}],
            [{"ts_code": "600001.SH", "adj_factor": 1.1}],
        ),
    ],
    ids=["quote-adj-codes-do-not-overlap", "all-joined-rows-invalid"],
)
def test_run_daily_rejects_nonempty_inputs_when_no_valid_joined_rows(
    tmp_path,
    quote_rows,
    adj_rows,
):
    class NoValidRowsRegistry:
        def call(self, name, *args):
            if name == "get_market_daily_quotes":
                return DataResult(data=quote_rows, source="fake:daily")
            if name == "get_adj_factor":
                return DataResult(data=adj_rows, source="fake:adj")
            if name == "get_stock_sw_industry_map":
                return DataResult(data=_industry_map(quote_rows), source="fake:sw")
            return DataResult(data=None, source="fake", error=f"unexpected {name}")

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    migrate(conn)
    _seed_baseline(conn, "2026-07-10")

    record = service.run_daily(
        conn,
        NoValidRowsRegistry(),
        "2026-07-13",
        persist=True,
        top_n=10,
    )

    assert record["status"] == "coverage_failed"
    assert repo.get_daily_stats(conn, "2026-07-13") is None
    assert repo.get_latest_stats_date(conn) == "2026-07-10"
    assert repo.get_latest_watermark_date(conn) == "2026-07-10"
    conn.close()


def test_run_daily_rejects_nonzero_but_severely_partial_market_snapshot(tmp_path):
    class PartialRegistry(FakeRegistry):
        def call(self, name, *args):
            if name == "get_market_daily_quotes":
                return DataResult(
                    data=[{
                        "code": "600001.SH",
                        "ts_code": "600001.SH",
                        "name": "A",
                        "high": 11.0,
                    }],
                    source="fake:partial-daily",
                )
            if name == "get_adj_factor":
                return DataResult(
                    data=[{"ts_code": "600001.SH", "adj_factor": 1.1}],
                    source="fake:partial-adj",
                )
            return super().call(name, *args)

    conn = get_connection(tmp_path / "test.db")
    migrate(conn)
    _seed_baseline(conn, "2026-07-10")

    record = service.run_daily(
        conn,
        PartialRegistry(),
        "2026-07-13",
        persist=True,
        top_n=10,
        expected_tail_date="2026-07-10",
    )

    assert record["status"] == "coverage_failed"
    assert record["source"]["failed_source"] == "coverage_guard"
    assert record["source"]["previous_market_count"] == 2
    assert record["source"]["market_count"] == 1
    assert repo.get_daily_stats(conn, "2026-07-13") is None
    assert repo.get_latest_stats_date(conn) == "2026-07-10"
    assert repo.get_latest_watermark_date(conn) == "2026-07-10"
    conn.close()


def test_run_daily_rejects_partial_snapshot_without_previous_stats_via_adj_universe(
    tmp_path,
):
    class PartialBootstrapRegistry:
        def call(self, name, *args):
            if name == "get_market_daily_quotes":
                return DataResult(
                    data=[{"code": "600001.SH", "high": 11.0}],
                    source="fake:partial-daily",
                )
            if name == "get_adj_factor":
                return DataResult(
                    data=[
                        {"ts_code": "600001.SH", "adj_factor": 1.1},
                        {"ts_code": "600002.SH", "adj_factor": 1.0},
                    ],
                    source="fake:full-adj",
                )
            if name == "get_stock_sw_industry_map":
                return DataResult(
                    data=_industry_map([{"code": "600001.SH", "name": "A"}]),
                    source="fake:sw",
                )
            return DataResult(data=None, source="fake", error=f"unexpected {name}")

    conn = get_connection(tmp_path / "test.db")
    migrate(conn)

    record = service.run_daily(
        conn,
        PartialBootstrapRegistry(),
        "2026-07-13",
        persist=True,
        top_n=10,
        expected_tail_date=None,
    )

    assert record["status"] == "coverage_failed"
    assert record["source"]["failed_source"] == "coverage_guard"
    assert record["source"]["adj_universe_coverage"] == 0.5
    assert repo.get_daily_stats(conn, "2026-07-13") is None
    assert repo.get_latest_watermark_date(conn) is None
    conn.close()


def test_run_daily_rejects_duplicate_quote_codes(tmp_path):
    class DuplicateRegistry:
        def call(self, name, *args):
            if name == "get_market_daily_quotes":
                return DataResult(
                    data=[
                        {"code": "600001.SH", "high": 11.0},
                        {"code": "600001.SH", "high": 11.0},
                    ],
                    source="fake:duplicate-daily",
                )
            if name == "get_adj_factor":
                return DataResult(
                    data=[{"ts_code": "600001.SH", "adj_factor": 1.1}],
                    source="fake:adj",
                )
            if name == "get_stock_sw_industry_map":
                return DataResult(
                    data=_industry_map([{"code": "600001.SH", "name": "A"}]),
                    source="fake:sw",
                )
            return DataResult(data=None, source="fake", error=f"unexpected {name}")

    conn = get_connection(tmp_path / "test.db")
    migrate(conn)

    record = service.run_daily(
        conn,
        DuplicateRegistry(),
        "2026-07-13",
        persist=True,
        top_n=10,
        expected_tail_date=None,
    )

    assert record["status"] == "coverage_failed"
    assert record["source"]["failed_source"] == "coverage_guard"
    assert record["source"]["duplicate_quote_count"] == 1
    assert repo.get_daily_stats(conn, "2026-07-13") is None
    conn.close()


@pytest.mark.parametrize(
    ("current_count", "expected_status"),
    [
        (98, "ok"),
        (97, "coverage_failed"),
        (102, "ok"),
        (103, "coverage_failed"),
    ],
    ids=["lower-bound", "below-lower", "upper-bound", "above-upper"],
)
def test_run_daily_enforces_calibrated_previous_market_count_band(
    tmp_path,
    current_count,
    expected_status,
):
    rows = [
        {"code": f"{index:06d}.SZ", "high": 10.0}
        for index in range(1, current_count + 1)
    ]
    adj = [
        {"ts_code": row["code"], "adj_factor": 1.0}
        for row in rows
    ]

    class CountRegistry:
        def call(self, name, *args):
            if name == "get_market_daily_quotes":
                return DataResult(data=rows, source="fake:daily")
            if name == "get_adj_factor":
                return DataResult(data=adj, source="fake:adj")
            if name == "get_stock_sw_industry_map":
                return DataResult(data=_industry_map(rows), source="fake:sw")
            return DataResult(data=None, source="fake", error=f"unexpected {name}")

    conn = get_connection(tmp_path / f"{current_count}.db")
    migrate(conn)
    repo.save_daily_stats(
        conn,
        {
            "date": "2026-07-10",
            "market_count": 100,
            "new_high_count": 0,
            "sector_summary": [],
            "stocks": [],
            "source": {"quote_source": "seed"},
        },
    )
    _seed_watermark(conn, "2026-07-10")
    conn.commit()

    record = service.run_daily(
        conn,
        CountRegistry(),
        "2026-07-13",
        persist=True,
        top_n=10,
        expected_tail_date="2026-07-10",
    )

    assert record["status"] == expected_status
    if expected_status == "ok":
        assert repo.get_daily_stats(conn, "2026-07-13")["market_count"] == current_count
    else:
        assert repo.get_daily_stats(conn, "2026-07-13") is None
        assert repo.get_latest_stats_date(conn) == "2026-07-10"
        assert repo.get_latest_watermark_date(conn) == "2026-07-10"
    conn.close()


def test_run_daily_requires_every_unique_quote_to_have_valid_adjusted_high(tmp_path):
    quote_rows = [
        {"code": f"{index:06d}.SZ", "high": 10.0}
        for index in range(1, 101)
    ]
    quote_rows[-1]["high"] = None
    adj_rows = [
        {"ts_code": row["code"], "adj_factor": 1.0}
        for row in quote_rows
    ]

    class InvalidJoinRegistry:
        def call(self, name, *args):
            if name == "get_market_daily_quotes":
                return DataResult(data=quote_rows, source="fake:daily")
            if name == "get_adj_factor":
                return DataResult(data=adj_rows, source="fake:adj")
            if name == "get_stock_sw_industry_map":
                return DataResult(data=_industry_map(quote_rows), source="fake:sw")
            return DataResult(data=None, source="fake", error=f"unexpected {name}")

    conn = get_connection(tmp_path / "test.db")
    migrate(conn)

    record = service.run_daily(
        conn,
        InvalidJoinRegistry(),
        "2026-07-13",
        persist=True,
        top_n=10,
        expected_tail_date=None,
    )

    assert record["status"] == "coverage_failed"
    assert record["source"]["valid_join_count"] == 99
    assert record["source"]["quote_unique_count"] == 100
    assert repo.get_daily_stats(conn, "2026-07-13") is None
    conn.close()


def test_backfill_processes_dates_in_order(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    migrate(conn)
    _insert_calendar(
        conn,
        [("2026-07-07", 1), ("2026-07-08", 1)],
    )
    calls = []

    class Registry(FakeRegistry):
        def call(self, name, *args):
            if name == "get_market_daily_quotes":
                calls.append(args[0])
            return super().call(name, *args)

    summary = service.run_backfill(
        conn,
        Registry(),
        ["2026-07-08", "2026-07-07"],
        top_n=10,
    )

    assert calls == ["2026-07-07", "2026-07-08"]
    assert summary["processed_count"] == 2
    assert repo.get_daily_stats(conn, "2026-07-08") is not None
    conn.close()


def test_backfill_rejects_requested_range_that_skips_open_days_after_tail(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    migrate(conn)
    _seed_baseline(conn, "2026-07-10")
    _insert_calendar(
        conn,
        [
            ("2026-07-11", 0),
            ("2026-07-12", 0),
            ("2026-07-13", 1),
            ("2026-07-14", 1),
            ("2026-07-15", 1),
            ("2026-07-16", 1),
            ("2026-07-17", 1),
            ("2026-07-18", 0),
            ("2026-07-19", 0),
            ("2026-07-20", 1),
            ("2026-07-21", 1),
        ],
    )
    registry = FakeRegistry()

    summary = service.run_backfill(
        conn,
        registry,
        ["2026-07-20", "2026-07-21"],
        top_n=10,
    )

    assert summary["status"] == "historical_gap"
    assert summary["failed_date"] == "2026-07-13"
    assert summary["failure_detail"]["stats_end"] == "2026-07-10"
    assert registry.calls == []
    assert repo.get_daily_stats(conn, "2026-07-20") is None
    assert repo.get_latest_stats_date(conn) == "2026-07-10"
    assert repo.get_latest_watermark_date(conn) == "2026-07-10"
    conn.close()


def test_backfill_rejects_sparse_open_dates_when_building_empty_baseline(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    migrate(conn)
    _insert_calendar(
        conn,
        [
            ("2026-07-07", 1),
            ("2026-07-08", 1),
            ("2026-07-09", 1),
        ],
    )
    registry = FakeRegistry()

    summary = service.run_backfill(
        conn,
        registry,
        ["2026-07-07", "2026-07-09"],
        top_n=10,
    )

    assert summary["status"] == "historical_gap"
    assert summary["failed_date"] == "2026-07-08"
    assert summary["failure_detail"]["missing_open_date"] == "2026-07-08"
    assert registry.calls == []
    assert repo.get_latest_stats_date(conn) is None
    assert repo.get_latest_watermark_date(conn) is None
    conn.close()


def test_run_backfill_requires_complete_calendar_without_provider_calls(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    migrate(conn)
    registry = FakeRegistry()

    summary = service.run_backfill(
        conn,
        registry,
        ["2026-07-07", "2026-07-08"],
        top_n=10,
    )

    assert summary["status"] == "calendar_missing"
    assert summary["failed_date"] == "2026-07-07"
    assert registry.calls == []
    assert repo.get_daily_stats(conn, "2026-07-07") is None
    conn.close()


def test_run_due_dates_skips_closed_weekend_and_processes_open_target(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    migrate(conn)
    _seed_baseline(conn, "2026-07-10")
    _insert_calendar(conn, [
        ("2026-07-11", 0),
        ("2026-07-12", 0),
        ("2026-07-13", 1),
    ])
    registry = FakeRegistry()

    summary = service.run_due_dates(conn, registry, "2026-07-13", top_n=10)

    _assert_due_summary_shape(summary)
    assert summary["status"] == "ok"
    assert summary["processed_dates"] == ["2026-07-13"]
    assert summary["failed_date"] is None
    assert summary["target_record"]["date"] == "2026-07-13"
    assert _quote_dates(registry) == ["2026-07-13"]
    conn.close()


def test_run_due_dates_processes_multiple_open_days_in_ascending_order(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    migrate(conn)
    _seed_baseline(conn, "2026-07-10")
    _insert_calendar(conn, [
        ("2026-07-11", 0),
        ("2026-07-12", 0),
        ("2026-07-13", 1),
        ("2026-07-14", 1),
    ])
    registry = FakeRegistry()

    summary = service.run_due_dates(conn, registry, "2026-07-14", top_n=10)

    assert summary["status"] == "ok"
    assert summary["processed_dates"] == ["2026-07-13", "2026-07-14"]
    assert _quote_dates(registry) == ["2026-07-13", "2026-07-14"]
    conn.close()


def test_run_due_dates_stops_on_failure_and_retry_resumes_from_failed_date(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    migrate(conn)
    _seed_baseline(conn, "2026-07-13")
    _insert_calendar(conn, [
        ("2026-07-14", 1),
        ("2026-07-15", 1),
        ("2026-07-16", 1),
    ])
    failing_registry = FakeRegistry(fail_quote_dates={"2026-07-15"})

    first = service.run_due_dates(
        conn,
        failing_registry,
        "2026-07-16",
        top_n=10,
    )

    _assert_due_summary_shape(first)
    assert first["status"] == "source_failed"
    assert first["processed_dates"] == ["2026-07-14"]
    assert first["failed_date"] == "2026-07-15"
    assert first["target_record"] is None
    assert first["failure_detail"]["failed_source"] == "get_market_daily_quotes"
    assert first["failure_detail"]["error"] == "source unavailable"
    assert _quote_dates(failing_registry) == ["2026-07-14", "2026-07-15"]
    conn.close()

    check = get_connection(db_path)
    assert repo.get_daily_stats(check, "2026-07-14") is not None
    watermark = repo.get_watermarks(check, ["600001.SH"])["600001.SH"]
    assert watermark["last_seen_date"] == "2026-07-14"
    assert repo.get_latest_stats_date(check) == "2026-07-14"
    assert repo.get_latest_watermark_date(check) == "2026-07-14"
    check.close()

    retry_registry = FakeRegistry()
    retry_conn = get_connection(db_path)
    second = service.run_due_dates(
        retry_conn,
        retry_registry,
        "2026-07-16",
        top_n=10,
    )

    assert second["status"] == "ok"
    assert second["processed_dates"] == ["2026-07-15", "2026-07-16"]
    assert second["failed_date"] is None
    assert second["target_record"]["date"] == "2026-07-16"
    assert _quote_dates(retry_registry) == ["2026-07-15", "2026-07-16"]
    retry_conn.close()

    final = get_connection(db_path)
    assert repo.get_daily_stats(final, "2026-07-15") is not None
    assert repo.get_daily_stats(final, "2026-07-16") is not None
    assert repo.get_latest_watermark_date(final) == "2026-07-16"
    final.close()


@pytest.mark.parametrize(
    "missing_table",
    ["daily_new_high_stats", "stock_adjusted_high_watermark", "trade_calendar"],
)
def test_run_due_dates_returns_schema_missing_when_one_required_table_is_absent(
    tmp_path,
    missing_table,
):
    conn = get_connection(tmp_path / "test.db")
    migrate(conn)
    conn.execute(f'DROP TABLE "{missing_table}"')
    conn.commit()
    registry = FakeRegistry()

    summary = service.run_due_dates(conn, registry, "2026-07-13", top_n=10)

    _assert_due_summary_shape(summary)
    assert summary["status"] == "schema_missing"
    assert summary["processed_dates"] == []
    assert summary["failed_date"] == "2026-07-13"
    assert summary["failure_detail"] == {"missing_tables": [missing_table]}
    assert registry.calls == []
    conn.close()


@pytest.mark.parametrize(
    ("seed_stats", "seed_watermark"),
    [(False, False), (False, True), (True, False)],
    ids=["both-missing", "stats-missing", "watermark-missing"],
)
def test_run_due_dates_returns_baseline_missing_when_any_baseline_side_is_absent(
    tmp_path,
    seed_stats,
    seed_watermark,
):
    conn = get_connection(tmp_path / "test.db")
    migrate(conn)
    if seed_stats:
        _seed_stats(conn, "2026-07-10")
    if seed_watermark:
        _seed_watermark(conn, "2026-07-10")
    conn.commit()
    registry = FakeRegistry()

    summary = service.run_due_dates(conn, registry, "2026-07-13", top_n=10)

    _assert_due_summary_shape(summary)
    assert summary["status"] == "baseline_missing"
    assert summary["processed_dates"] == []
    assert summary["failed_date"] == "2026-07-13"
    assert registry.calls == []
    conn.close()


def test_run_due_dates_returns_baseline_mismatch_without_provider_calls(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    migrate(conn)
    _seed_stats(conn, "2026-07-13")
    _seed_watermark(conn, "2026-07-12")
    conn.commit()
    registry = FakeRegistry()

    summary = service.run_due_dates(conn, registry, "2026-07-14", top_n=10)

    _assert_due_summary_shape(summary)
    assert summary["status"] == "baseline_mismatch"
    assert summary["processed_dates"] == []
    assert summary["failed_date"] == "2026-07-14"
    assert summary["failure_detail"] == {
        "stats_end": "2026-07-13",
        "watermark_end": "2026-07-12",
    }
    assert registry.calls == []
    conn.close()


def test_run_due_dates_returns_calendar_missing_when_target_row_is_absent(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    migrate(conn)
    _seed_baseline(conn, "2026-07-10")
    _insert_calendar(conn, [
        ("2026-07-11", 0),
        ("2026-07-12", 0),
    ])
    registry = FakeRegistry()

    summary = service.run_due_dates(conn, registry, "2026-07-13", top_n=10)

    _assert_due_summary_shape(summary)
    assert summary["status"] == "calendar_missing"
    assert summary["failed_date"] == "2026-07-13"
    assert summary["processed_dates"] == []
    assert registry.calls == []
    conn.close()


def test_run_due_dates_returns_calendar_missing_when_intermediate_row_is_absent(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    migrate(conn)
    _seed_baseline(conn, "2026-07-10")
    _insert_calendar(conn, [
        ("2026-07-11", 0),
        ("2026-07-13", 1),
    ])
    registry = FakeRegistry()

    summary = service.run_due_dates(conn, registry, "2026-07-13", top_n=10)

    assert summary["status"] == "calendar_missing"
    assert summary["failed_date"] == "2026-07-12"
    assert summary["processed_dates"] == []
    assert registry.calls == []
    conn.close()


def test_run_due_dates_returns_historical_gap_when_target_row_is_missing_behind_tail(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    migrate(conn)
    _seed_stats(conn, "2026-07-10")
    _seed_stats(conn, "2026-07-14")
    _seed_watermark(conn, "2026-07-14")
    conn.commit()
    registry = FakeRegistry()

    summary = service.run_due_dates(conn, registry, "2026-07-13", top_n=10)

    _assert_due_summary_shape(summary)
    assert summary["status"] == "historical_gap"
    assert summary["failed_date"] == "2026-07-13"
    assert summary["processed_dates"] == []
    assert registry.calls == []
    conn.close()


def test_run_due_dates_returns_non_trading_day_without_provider_calls(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    migrate(conn)
    _seed_baseline(conn, "2026-07-10")
    _insert_calendar(conn, [
        ("2026-07-11", 0),
        ("2026-07-12", 0),
        ("2026-07-13", 0),
    ])
    registry = FakeRegistry()

    summary = service.run_due_dates(conn, registry, "2026-07-13", top_n=10)

    _assert_due_summary_shape(summary)
    assert summary["status"] == "non_trading_day"
    assert summary["processed_dates"] == []
    assert registry.calls == []
    conn.close()


def test_run_due_dates_catches_up_open_prefix_before_closed_target(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    migrate(conn)
    _seed_baseline(conn, "2026-07-09")
    _insert_calendar(
        conn,
        [
            ("2026-07-10", 1),
            ("2026-07-11", 0),
        ],
    )
    registry = FakeRegistry()

    summary = service.run_due_dates(conn, registry, "2026-07-11", top_n=10)

    assert summary["status"] == "non_trading_day"
    assert summary["processed_dates"] == ["2026-07-10"]
    assert summary["failed_date"] == "2026-07-11"
    assert _quote_dates(registry) == ["2026-07-10"]
    assert repo.get_daily_stats(conn, "2026-07-10") is not None
    assert repo.get_latest_stats_date(conn) == "2026-07-10"
    assert repo.get_latest_watermark_date(conn) == "2026-07-10"
    conn.close()


def test_run_due_dates_returns_already_complete_without_provider_calls(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    migrate(conn)
    _seed_baseline(conn, "2026-07-13")
    registry = FakeRegistry()

    summary = service.run_due_dates(conn, registry, "2026-07-13", top_n=10)

    _assert_due_summary_shape(summary)
    assert summary["status"] == "already_complete"
    assert summary["processed_dates"] == []
    assert summary["failed_date"] is None
    assert summary["target_record"]["date"] == "2026-07-13"
    assert registry.calls == []
    conn.close()


def test_run_due_dates_concurrent_retry_does_not_overwrite_canonical_target(tmp_path):
    db_path = tmp_path / "test.db"
    seed = get_connection(db_path)
    migrate(seed)
    _seed_baseline(seed, "2026-07-10")
    _insert_calendar(seed, [
        ("2026-07-11", 0),
        ("2026-07-12", 0),
        ("2026-07-13", 1),
    ])
    seed.close()

    provider_entered = threading.Event()
    release_provider = threading.Event()
    outcome = {}

    def _run_blocked_call():
        conn = get_connection(db_path)
        try:
            outcome["summary"] = service.run_due_dates(
                conn,
                BlockingRegistry(provider_entered, release_provider),
                "2026-07-13",
                top_n=10,
            )
        except BaseException as exc:  # surface worker failures in the test thread
            outcome["error"] = exc
        finally:
            conn.close()

    worker = threading.Thread(target=_run_blocked_call, daemon=True)
    worker.start()
    try:
        assert provider_entered.wait(timeout=5), "blocked call did not reach provider"

        winner_conn = get_connection(db_path)
        try:
            winner = service.run_due_dates(
                winner_conn,
                FakeRegistry(),
                "2026-07-13",
                top_n=10,
            )
        finally:
            winner_conn.close()
        assert winner["status"] == "ok"
        assert winner["target_record"]["new_high_count"] == 1
    finally:
        release_provider.set()
        worker.join(timeout=5)

    assert not worker.is_alive(), "blocked call left a worker thread running"
    if "error" in outcome:
        raise outcome["error"]
    assert outcome["summary"]["status"] == "already_complete"

    check = get_connection(db_path)
    try:
        assert repo.get_daily_stats(check, "2026-07-13")["new_high_count"] == 1
    finally:
        check.close()


def test_manual_daily_does_not_overwrite_target_completed_while_provider_waits(tmp_path):
    db_path = tmp_path / "test.db"
    seed = get_connection(db_path)
    migrate(seed)
    _seed_baseline(seed, "2026-07-10")
    _insert_calendar(seed, [
        ("2026-07-11", 0),
        ("2026-07-12", 0),
        ("2026-07-13", 1),
    ])
    seed.close()

    provider_entered = threading.Event()
    release_provider = threading.Event()
    outcome = {}

    def _run_blocked_manual_daily():
        conn = get_connection(db_path)
        try:
            outcome["record"] = service.run_daily(
                conn,
                BlockingRegistry(provider_entered, release_provider),
                "2026-07-13",
                persist=True,
                top_n=10,
            )
        except BaseException as exc:
            outcome["error"] = exc
        finally:
            conn.close()

    worker = threading.Thread(target=_run_blocked_manual_daily, daemon=True)
    worker.start()
    try:
        assert provider_entered.wait(timeout=5), "manual daily did not reach provider"
        winner_conn = get_connection(db_path)
        try:
            winner = service.run_due_dates(
                winner_conn,
                FakeRegistry(),
                "2026-07-13",
                top_n=10,
            )
        finally:
            winner_conn.close()
        assert winner["status"] == "ok"
        assert winner["target_record"]["new_high_count"] == 1
    finally:
        release_provider.set()
        worker.join(timeout=5)

    assert not worker.is_alive(), "manual daily left a worker thread running"
    if "error" in outcome:
        raise outcome["error"]
    assert outcome["record"]["status"] == "already_complete"

    check = get_connection(db_path)
    try:
        assert repo.get_daily_stats(check, "2026-07-13")["new_high_count"] == 1
    finally:
        check.close()


def test_sequential_manual_daily_reuses_existing_canonical_target(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    migrate(conn)
    _seed_baseline(conn, "2026-07-10")
    first = service.run_daily(
        conn,
        FakeRegistry(),
        "2026-07-13",
        persist=True,
        top_n=10,
    )
    rerun_registry = FakeRegistry()
    second = service.run_daily(
        conn,
        rerun_registry,
        "2026-07-13",
        persist=True,
        top_n=10,
    )

    assert first["status"] == "ok"
    assert second["status"] == "already_complete"
    assert second["new_high_count"] == 1
    assert _quote_dates(rerun_registry) == []
    assert repo.get_daily_stats(conn, "2026-07-13")["new_high_count"] == 1
    assert repo.get_watermarks(conn, ["600001.SH"])["600001.SH"]["max_adj_high"] == 12.1
    conn.close()


def test_run_backfill_rerun_preserves_canonical_results_without_provider_calls(
    tmp_path,
):
    conn = get_connection(tmp_path / "test.db")
    migrate(conn)
    _insert_calendar(
        conn,
        [("2026-07-07", 1), ("2026-07-08", 1)],
    )
    first = service.run_backfill(
        conn,
        FakeRegistry(),
        ["2026-07-07", "2026-07-08"],
        top_n=10,
    )
    canonical_stats = {
        date: repo.get_daily_stats(conn, date)
        for date in ("2026-07-07", "2026-07-08")
    }
    canonical_watermarks = repo.get_watermarks(
        conn,
        ["600001.SH", "600002.SH"],
    )
    rerun_registry = FakeRegistry()

    second = service.run_backfill(
        conn,
        rerun_registry,
        ["2026-07-07", "2026-07-08"],
        top_n=10,
    )

    assert first["processed_count"] == 2
    assert second["processed_count"] == 0
    assert second["already_complete_count"] == 2
    assert rerun_registry.calls == []
    assert {
        date: repo.get_daily_stats(conn, date)
        for date in ("2026-07-07", "2026-07-08")
    } == canonical_stats
    assert repo.get_watermarks(
        conn,
        ["600001.SH", "600002.SH"],
    ) == canonical_watermarks
    conn.close()


def test_run_backfill_skips_closed_dates_and_stops_on_open_day_source_failure(
    tmp_path,
):
    conn = get_connection(tmp_path / "test.db")
    migrate(conn)
    _insert_calendar(
        conn,
        [
            ("2026-07-11", 0),
            ("2026-07-12", 0),
            ("2026-07-13", 1),
            ("2026-07-14", 1),
        ],
    )
    registry = FakeRegistry(fail_quote_dates={"2026-07-13"})

    summary = service.run_backfill(
        conn,
        registry,
        ["2026-07-11", "2026-07-12", "2026-07-13", "2026-07-14"],
        top_n=10,
    )

    assert summary["status"] == "source_failed"
    assert summary["failed_date"] == "2026-07-13"
    assert summary["closed_dates"] == ["2026-07-11", "2026-07-12"]
    assert summary["processed_dates"] == []
    assert summary["skipped"][0]["date"] == "2026-07-13"
    assert summary["skipped"][0]["status"] == "source_failed"
    assert _quote_dates(registry) == ["2026-07-13"]
    assert repo.get_daily_stats(conn, "2026-07-14") is None
    conn.close()


def test_run_daily_rejects_write_when_tail_changes_to_another_date_during_collection(
    tmp_path,
):
    db_path = tmp_path / "test.db"
    seed = get_connection(db_path)
    migrate(seed)
    _seed_baseline(seed, "2026-07-10")
    seed.close()

    provider_entered = threading.Event()
    release_provider = threading.Event()
    outcome = {}

    def _run_blocked_daily():
        conn = get_connection(db_path)
        try:
            outcome["record"] = service.run_daily(
                conn,
                BlockingRegistry(provider_entered, release_provider),
                "2026-07-13",
                persist=True,
                top_n=10,
                expected_tail_date="2026-07-10",
            )
        except BaseException as exc:
            outcome["error"] = exc
        finally:
            conn.close()

    worker = threading.Thread(target=_run_blocked_daily, daemon=True)
    worker.start()
    try:
        assert provider_entered.wait(timeout=5), "blocked call did not reach provider"
        winner = get_connection(db_path)
        try:
            repo.save_daily_stats(
                winner,
                {
                    "date": "2026-07-14",
                    "market_count": 2,
                    "new_high_count": 0,
                    "sector_summary": [],
                    "stocks": [],
                    "source": {"quote_source": "concurrent-winner"},
                },
            )
            repo.upsert_watermark(
                winner,
                {
                    "code": "600001.SH",
                    "name": "A",
                    "industry": "半导体",
                    "max_adj_high": 15.0,
                    "max_high_date": "2026-07-14",
                    "max_raw_high": 15.0,
                    "last_seen_date": "2026-07-14",
                },
            )
            winner.commit()
        finally:
            winner.close()
    finally:
        release_provider.set()
        worker.join(timeout=5)

    assert not worker.is_alive(), "blocked call left a worker thread running"
    if "error" in outcome:
        raise outcome["error"]
    assert outcome["record"]["status"] == "tail_changed"

    check = get_connection(db_path)
    try:
        assert repo.get_daily_stats(check, "2026-07-13") is None
        assert repo.get_latest_stats_date(check) == "2026-07-14"
        assert repo.get_latest_watermark_date(check) == "2026-07-14"
        assert (
            repo.get_watermarks(check, ["600001.SH"])["600001.SH"][
                "max_adj_high"
            ]
            == 15.0
        )
    finally:
        check.close()


def test_run_due_dates_begins_immediate_before_target_recheck_and_watermark_read(
    tmp_path,
):
    conn = get_connection(tmp_path / "test.db")
    migrate(conn)
    _seed_baseline(conn, "2026-07-10")
    _insert_calendar(conn, [
        ("2026-07-11", 0),
        ("2026-07-12", 0),
        ("2026-07-13", 1),
    ])
    trace = []
    conn.set_trace_callback(trace.append)

    summary = service.run_due_dates(
        conn,
        FakeRegistry(),
        "2026-07-13",
        top_n=10,
    )

    assert summary["status"] == "ok"
    normalized = [" ".join(statement.upper().split()) for statement in trace]
    begin_index = next(
        index
        for index, statement in enumerate(normalized)
        if statement.startswith("BEGIN IMMEDIATE")
    )
    target_recheck_index = next(
        index
        for index, statement in enumerate(normalized)
        if index > begin_index
        and statement.startswith("SELECT")
        and "FROM DAILY_NEW_HIGH_STATS" in statement
    )
    watermark_read_index = next(
        index
        for index, statement in enumerate(normalized)
        if index > begin_index
        and statement.startswith("SELECT")
        and "FROM STOCK_ADJUSTED_HIGH_WATERMARK" in statement
    )
    assert begin_index < target_recheck_index
    assert begin_index < watermark_read_index
    conn.close()
