from contextlib import closing
from datetime import datetime
import json

import pytest

from db.connection import get_db, get_readonly_connection
from db.migrate import migrate
from providers import margin_detail
from providers.base import DataResult
from services.ingest_service import IngestService
from services.margin_detail_refresh import (
    load_published_margin_detail, prefer_stored_margin_summary, prepare_margin_detail_for_post,
    refresh_previous_margin_detail,
)


FRIDAY = "2026-11-27"
MONDAY = "2026-11-30"
EVENING = datetime.fromisoformat("2026-11-30T20:00:00+08:00")


class Registry:
    def __init__(self):
        self.calls = []
        self.providers = [self]
        self.rows = []
        self.fail = False
        self.fetched_at = "2026-11-30T09:01:00+08:00"

    def supports(self, name):
        return name == "get_margin_detail"

    def call(self, name, day):
        assert name == "get_margin_detail"
        self.calls.append(day)
        if self.fail:
            return DataResult(None, margin_detail.SOURCE, error="source unavailable")
        result = margin_detail.fetch(lambda _, **params: self.rows if not params["offset"] else [], day)
        result.fetched_at = self.fetched_at
        return result


@pytest.fixture
def service(tmp_path):
    path = tmp_path / "trade.db"
    with get_db(path) as conn:
        migrate(conn)
        conn.executemany("INSERT INTO trade_calendar(date, exchange, is_open) VALUES (?,'SSE',?)",
                         [(FRIDAY, 1), ("2026-11-28", 0), ("2026-11-29", 0), (MONDAY, 1)])
    return IngestService(db_path=str(path), registry=Registry())


def original(service, *, rows=()):
    service.registry.rows = list(rows)
    service.registry.fetched_at = "2026-11-27T19:59:59+08:00"
    service.execute_interface("margin_detail", FRIDAY, input_by="fixture")
    service.registry.fetched_at = "2026-11-30T09:01:00+08:00"
    with get_db(service.db_path) as conn:
        conn.execute("UPDATE raw_interface_payloads SET inserted_at='2026-11-27 12:00:00'")
    service.registry.calls.clear()


def three_exchanges():
    return [dict(ts_code=code, trade_date="20261127", rzmre=100)
            for code in ("600000.SH", "000001.SZ", "920001.BJ")]


def recheck(service, now=EVENING):
    return refresh_previous_margin_detail(service, MONDAY, input_by="cmd_post", now=now)


@pytest.mark.parametrize("old_rows", [[], [dict(ts_code="510050.SH", trade_date="20261127", rzmre=120)]])
def test_pending_and_sh_only_rechecked_once_on_original_fact_date(service, old_rows):
    original(service, rows=old_rows)
    service.registry.rows = three_exchanges() + old_rows
    info = recheck(service)
    assert (info["status"], info["observed_date"], info["fact_date"]) == ("success", MONDAY, FRIDAY)
    with get_db(service.db_path) as conn:
        conn.execute("UPDATE raw_interface_payloads SET inserted_at='2026-11-30 01:02:00'")
        audit = conn.execute("SELECT biz_date,target_date,triggered_by,input_by FROM ingest_runs WHERE run_id=?",
                             (info["run_id"],)).fetchone()
        assert tuple(audit) == (FRIDAY, FRIDAY, "system", "cmd_post")
        assert conn.execute("SELECT COUNT(*) FROM raw_interface_payloads WHERE biz_date=?", (MONDAY,)).fetchone()[0] == 0
        rows, meta = load_published_margin_detail(conn, FRIDAY, as_of=EVENING)
        assert len(rows) == 3 + len(old_rows) and meta["source"] == margin_detail.SOURCE
    assert recheck(service)["status"] == "already_published"
    result = prepare_margin_detail_for_post(service.registry, MONDAY, db_path=service.db_path, now=EVENING)
    assert result["status"] == "published" and result["trade_date"] == FRIDAY
    assert result["stock_count"] == 3
    assert service.registry.calls == [FRIDAY]


@pytest.mark.parametrize("setup", ["no_receipt", "missing_calendar", "closed_today", "only_future_receipt"])
def test_recheck_never_bridges_gap_or_creates_absent_history(service, setup):
    if setup != "no_receipt":
        original(service)
    with get_db(service.db_path) as conn:
        if setup == "missing_calendar":
            conn.execute("DELETE FROM trade_calendar WHERE date='2026-11-29'")
        if setup == "closed_today":
            conn.execute("UPDATE trade_calendar SET is_open=0 WHERE date=?", (MONDAY,))
        if setup == "only_future_receipt":
            conn.execute("UPDATE raw_interface_payloads SET inserted_at='2026-11-30 12:01:00'")
    assert recheck(service)["status"] == "skipped"
    assert not service.registry.calls


@pytest.mark.parametrize("at", ["2026-11-29T20:00:00+08:00", "2026-11-30T00:29:59+00:00", "2026-11-30T09:30:00"])
def test_observed_date_and_disclosure_clock(service, at):
    original(service)
    assert recheck(service, now=datetime.fromisoformat(at))["status"] == "skipped"
    assert not service.registry.calls


def test_source_failure_retains_previous_raw_and_isolated_from_post(service):
    original(service)
    service.registry.fail = True
    assert recheck(service)["status"] == "failed"
    with closing(get_readonly_connection(service.db_path)) as conn:
        row = conn.execute("SELECT biz_date,status,row_count FROM raw_interface_payloads").fetchone()
        assert tuple(row) == (FRIDAY, "empty", 0)


def test_prepare_keeps_verified_partial_if_storage_does_not_admit_new_response(service):
    original(service, rows=[dict(ts_code="510050.SH", trade_date="20261127", rzmre=120)])
    service.registry.rows = three_exchanges()  # 刷新漏掉原来已披露的同日证券，原始库保留原回执。
    result = prepare_margin_detail_for_post(service.registry, MONDAY, db_path=service.db_path, now=EVENING)
    assert result["status"] == "partial" and result["exchanges"] == {"SH": 1}
    assert result["latest_attempt"]["status"] == "published"
    assert "未取代" in result["latest_attempt"]["reason"]
    assert service.registry.calls == [FRIDAY]


def test_prepare_reads_again_after_request_commit(service, monkeypatch):
    original(service)
    service.registry.rows = three_exchanges()
    # 模拟读取/发请求期间跨过一秒。数据库以秒存UTC，只有第二个时刻可见新记录。
    clocks = iter([datetime.fromisoformat("2026-11-30T08:55:00+08:00"), EVENING])

    class Clock(datetime):
        @classmethod
        def now(cls, tz):
            return next(clocks).astimezone(tz)

    observed = IngestService.execute_interface

    def commit_at_noon(self, *args, **kwargs):
        result = observed(self, *args, **kwargs)
        with get_db(self.db_path) as conn:
            conn.execute("UPDATE raw_interface_payloads SET inserted_at='2026-11-30 01:02:00'")
        return result

    monkeypatch.setattr("services.margin_detail_refresh.datetime", Clock)
    monkeypatch.setattr(IngestService, "execute_interface", commit_at_noon)
    report = prepare_margin_detail_for_post(service.registry, MONDAY, db_path=service.db_path)
    assert report["trade_date"] == FRIDAY and report["status"] == "published"


@pytest.mark.parametrize(("previous_rows", "fail", "outcome", "attempt"), [
    ([], False, "pending", "pending"), ([], True, "source_failed", "source_failed"),
    (three_exchanges()[:1], True, "partial", "source_failed"),
    (three_exchanges()[:1], False, "partial", "pending"),
])
def test_pending_partial_and_source_failure_remain_visible(service, previous_rows, fail, outcome, attempt):
    original(service, rows=previous_rows)
    service.registry.fail = fail
    service.registry.rows = []
    result = prepare_margin_detail_for_post(service.registry, MONDAY, db_path=service.db_path, now=EVENING)
    assert result["requested_date"] == FRIDAY and result["status"] == outcome
    assert result["latest_attempt"]["status"] == attempt and result["latest_attempt"]["reason"]
    assert result["latest_attempt"]["observed_date"] == MONDAY and result["latest_attempt"]["run_id"]
    if previous_rows:
        assert result["count"] == 1 and result["exchanges"] == {"SH": 1}
        assert result["trade_date"] == FRIDAY
    else:
        assert result["count"] is None and result["trade_date"] is None and result["reason"]


def test_legacy_partial_after_failure_remains_unverified(service):
    original(service, rows=three_exchanges()[:1])
    with get_db(service.db_path) as conn:
        conn.execute("UPDATE raw_interface_payloads SET status='success', "
                     "source_meta_json=json_remove(source_meta_json,'$.note')")
    service.registry.fail = True
    result = prepare_margin_detail_for_post(service.registry, MONDAY, db_path=service.db_path, now=EVENING)
    assert result["status"] == "source_failed" and result["count"] is None


def failed_original(service):
    service.registry.fail = True
    service.execute_interface("margin_detail", FRIDAY, input_by="fixture")
    service.registry.fail = False
    service.registry.calls.clear()
    with get_db(service.db_path) as conn:
        # 这类旧运行的结束时刻无时区，通过同一次错误行的 SQLite UTC 取得时刻交叉验证。
        conn.execute("UPDATE ingest_runs SET started_at='2026-11-28T02:45:00', finished_at='2026-11-28T02:46:06.672'")
        conn.execute("UPDATE ingest_errors SET created_at='2026-11-27 18:46:06'")


def test_verified_failed_attempt_even_without_raw_is_retried_for_its_fact_day(service):
    failed_original(service)
    service.registry.rows = three_exchanges()[:1]
    result = prepare_margin_detail_for_post(service.registry, MONDAY, db_path=service.db_path, now=EVENING)
    assert result["status"] == "partial" and result["trade_date"] == FRIDAY
    assert result["latest_attempt"]["status"] == "partial"
    assert service.registry.calls == [FRIDAY]


@pytest.mark.parametrize("sql", [
    "UPDATE ingest_runs SET finished_at=NULL",
    "UPDATE ingest_runs SET target_date='2026-11-26'",
    "UPDATE ingest_runs SET stage='pre_core'",
    "UPDATE ingest_runs SET params_json='{}'",
    "UPDATE ingest_runs SET finished_at='2026-12-01T18:00:00+08:00', started_at='2026-12-01T17:59:59+08:00'",
    "UPDATE ingest_runs SET finished_at='2026-11-27T14:59:00+08:00', started_at='2026-11-27T14:58:00+08:00'",
    "DELETE FROM ingest_errors",  # 仅有无时区旧日志，无法确认取得时点。
])
def test_misaligned_failed_attempt_is_not_a_refresh_anchor(service, sql):
    failed_original(service)
    with get_db(service.db_path) as conn:
        conn.execute(sql)
    assert recheck(service)["status"] == "skipped"
    assert not service.registry.calls


@pytest.mark.parametrize("change", ["future_db", "future_fetch", "naive_fetch", "preclose_fetch", "bad_hash", "wrong_source"])
def test_archive_must_be_published_and_visible_at_observation(service, change):
    original(service, rows=three_exchanges())
    with get_db(service.db_path) as conn:
        field = conn.execute("SELECT source_meta_json FROM raw_interface_payloads").fetchone()[0]
        meta = json.loads(field)
        if change == "future_db":
            conn.execute("UPDATE raw_interface_payloads SET inserted_at='2026-11-30 12:00:01'")
        elif change == "bad_hash":
            conn.execute("UPDATE raw_interface_payloads SET payload_hash='corrupt'")
        elif change == "wrong_source":
            meta["source"] = "tushare:margin"
        else:
            meta["fetched_at"] = {"future_fetch": "2026-12-01T01:00:00+08:00",
                                  "naive_fetch": "2026-11-30T09:00:00",
                                  "preclose_fetch": "2026-11-27T14:59:59+08:00"}[change]
        conn.execute("UPDATE raw_interface_payloads SET source_meta_json=?", (json.dumps(meta),))
        assert load_published_margin_detail(conn, FRIDAY, as_of=EVENING) is None


@pytest.mark.parametrize("change", ["pending", "source_failed", "partial", "missing_field", "replacement_code"])
def test_report_keeps_same_day_canonical_on_degradation(service, change):
    good = three_exchanges()
    good[0]["rzye"] = 130
    original(service, rows=good)
    current = [dict(row) for row in good]
    if change == "pending":
        current = []
    elif change == "partial":
        current.pop()
    elif change == "missing_field":
        current[0].pop("rzye")
    elif change == "replacement_code":
        current[0]["ts_code"] = "600001.SH"
    if change == "source_failed":
        block = {"status": "source_failed", "requested_date": FRIDAY}
    else:
        result = margin_detail.fetch(lambda _, **p: [] if p["offset"] else current, FRIDAY)
        current = result.data
        block = margin_detail.summary(current, result.to_dict(), FRIDAY)
    report = prefer_stored_margin_summary(block, FRIDAY, rows=current, db_path=service.db_path, now=EVENING)
    assert (report["status"], report["trade_date"], report["count"]) == ("published", FRIDAY, 3)
    assert report["fetched_at"] == "2026-11-27T19:59:59+08:00"
    assert report["latest_attempt"]["status"] == block["status"]
    assert report["latest_attempt"]["requested_date"] == FRIDAY


def test_report_uses_newer_valid_superset_or_no_visible_canonical(service):
    original(service, rows=three_exchanges())
    current = three_exchanges() + [dict(ts_code="000002.SZ", trade_date="20261127", rzmre=140)]
    result = margin_detail.fetch(lambda _, **p: [] if p["offset"] else current, FRIDAY)
    block = margin_detail.summary(result.data, result.to_dict(), FRIDAY)
    assert prefer_stored_margin_summary(block, FRIDAY, rows=result.data, db_path=service.db_path, now=EVENING) is block
    with get_db(service.db_path) as conn:
        conn.execute("UPDATE raw_interface_payloads SET inserted_at='2026-11-29 10:00:00'")
    failed = {"status": "source_failed", "requested_date": FRIDAY}
    assert prefer_stored_margin_summary(failed, FRIDAY, db_path=service.db_path, now=EVENING) is failed
