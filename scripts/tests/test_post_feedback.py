"""回填两端一致性、并发独立字段、进程中断与旧归档只读兼容。"""
import json

import pytest

from db import queries as Q
from db.connection import get_db
from db.dual_write import reconcile_daily_market, retry_pending
from services import post_feedback as feedback
from utils.post_archive import atomic_write_archive, read_archive

SOURCE = "2026-11-20"
OUTCOME = "2026-11-23"


def blocks():
    dates = {"prev_date": SOURCE, "trade_date": OUTCOME, "computed_at": "first"}
    return {"premium_backfill": {**dates, "first_board": {"count": 1}},
            "promotion_backfill": {**dates, "first_to_second": {"base": 1}},
            "popularity_backfill": [{"code": "600000.SH"}]}


@pytest.fixture
def archive(api_client, tmp_path, monkeypatch):
    client, database = api_client
    path = tmp_path / "daily" / SOURCE / "post-market.yaml"
    path.parent.mkdir(parents=True)
    monkeypatch.setattr("api.routes.crud._REPO_ROOT", tmp_path)
    original = {"date": SOURCE, "raw_data": {"limit_up": {"stocks": [{"code": "600000.SH"}]}}}
    atomic_write_archive(path, original)
    with get_db(database) as conn:
        Q.upsert_trade_calendar(conn, [{"date": f"2026-11-{day}", "is_open": int(day in (20, 23))}
                                       for day in (20, 21, 22, 23)])
        Q.upsert_daily_market(conn, {"date": SOURCE, "total_amount": 123,
                                     "raw_data": {**original, "only_in_db": True}})
    return client, database, path, original


def test_dual_write_and_repeat_preserve_unrelated_fields(archive):
    client, db, path, original = archive
    atomic_write_archive(path, {**original, "only_in_file": True})
    feedback.publish_feedback(path, original, blocks(), db)
    first_stat = path.stat()
    repeat = blocks()
    repeat["premium_backfill"]["computed_at"] = "later"
    repeat.pop("promotion_backfill")
    assert feedback.publish_feedback(path, original, repeat, db)["computed_at"] == "first"
    assert path.stat().st_mtime_ns == first_stat.st_mtime_ns
    assert read_archive(path)["only_in_file"] and read_archive(path)["promotion_backfill"] == blocks()["promotion_backfill"]
    with get_db(db) as conn:
        row = Q.get_daily_market(conn, SOURCE)
        stored = json.loads(row["raw_data"])
        assert row["total_amount"] == 123 and stored["only_in_db"]
        assert all(stored[k] == v for k, v in blocks().items())
    assert not reconcile_daily_market(db, path.parent.parent)


def test_atomic_failure_replay_without_provider(archive, monkeypatch):
    _, db, path, original = archive
    def interrupted(*args):
        raise OSError("replace failed")

    with monkeypatch.context() as context:
        context.setattr(feedback, "atomic_write_archive", interrupted)
        with pytest.raises(OSError, match="replace failed"):
            feedback.publish_feedback(path, original, blocks(), db)
    assert read_archive(path) == original
    with get_db(db) as conn:
        assert "premium_backfill" not in json.loads(Q.get_daily_market(conn, SOURCE)["raw_data"])
    monkeypatch.setattr("db.dual_write.PENDING_WRITES_PATH", path.parent / "test-pending.json")
    assert retry_pending(db, daily_dir=path.parent.parent) == (1, 0)
    assert retry_pending(db, daily_dir=path.parent.parent) == (0, 0)
    assert read_archive(path)["premium_backfill"] == blocks()["premium_backfill"]


def test_read_compatibility_and_observation_cutoff(archive):
    client, db, path, original = archive
    atomic_write_archive(path, {**original, **blocks()})
    before = path.read_bytes()
    url = f"/api/post-market/{SOURCE}"
    complete = client.get(url).json()
    assert all(complete[k] == v for k, v in blocks().items())
    assert complete["t1_feedback_dates"] == {"source_trade_date": SOURCE, "outcome_trade_date": OUTCOME}
    assert not set(blocks()).intersection(client.get(url, params={"as_of": SOURCE}).json())
    assert client.get(url, params={"as_of": "2026-11-19"}).json()["available"] is False
    assert path.read_bytes() == before
    diffs = reconcile_daily_market(db, path.parent.parent)
    assert {d["field"] for d in diffs} == set(blocks())
    assert all(d["yaml_outcome"] == OUTCOME and not d["db_outcome"] for d in diffs)


def test_invalid_date_or_replaced_source_never_mixed(archive):
    client, db, path, original = archive
    other = {**original, "raw_data": {"limit_up": {"stocks": []}}}
    atomic_write_archive(path, {**other, **blocks()})
    with pytest.raises(ValueError, match="源行情已变更"):
        feedback.publish_feedback(path, original, blocks(), db)
    assert "premium_backfill" not in client.get(f"/api/post-market/{SOURCE}").json()
    updates = blocks()
    updates["premium_backfill"]["trade_date"] = "2026-11-22"
    atomic_write_archive(path, original)
    with pytest.raises(ValueError, match="相邻开放日"):
        feedback.publish_feedback(path, original, updates, db)
    assert read_archive(path) == original


def test_partial_prices_and_failed_list_preserve_confirmed_feedback(archive, monkeypatch):
    """实际经过 collector 的来源失败、部分覆盖、完整空涨停名单三种返回形状。"""
    from collectors import premium
    from providers.base import DataResult
    _, db, path, original = archive
    monkeypatch.setattr(premium, "DAILY_DIR", path.parent.parent)
    raw = {"limit_up": {"stocks": [
        {"code": "600001.SH", "close": 10, "pct_chg": 10, "limit_times": 1},
        {"code": "600002.SH", "close": 20, "pct_chg": 10, "limit_times": 2}]},
        "top_volume_stocks": [{"code": "600001.SH", "close": 10}, {"code": "600002.SH", "close": 20}]}
    original = {**original, "raw_data": raw}
    atomic_write_archive(path, original)
    with get_db(db) as conn:
        conn.execute("UPDATE daily_market SET raw_data=? WHERE date=?", (json.dumps(original), SOURCE))
    class Registry:
        quotes = set()
        limit = None

        def call(self, method, *args):
            if method == "get_stock_daily" and args[0] in self.quotes:
                return DataResult({"open": 11, "close": 11}, "mock")
            if method == "get_limit_up_list" and self.limit is not None:
                return DataResult(self.limit, "mock")
            return DataResult(None, "mock", error="unavailable")
    registry = Registry()
    collector = premium.PremiumCollector(registry)
    first = collector.collect(OUTCOME, SOURCE)
    assert first["_coverage"]["status"] == "source_failed" and first["first_board"]["count"] is None
    assert "promotion_backfill" not in read_archive(path)
    registry.quotes = {"600001.SH", "600002.SH"}
    registry.limit = {"stocks": [], "count": 0}
    good = collector.collect(OUTCOME, SOURCE)
    stored = read_archive(path)
    assert good["first_board"]["count"] == 1 and good["third_board"]["count"] == 0
    assert stored["promotion_backfill"]["first_to_second"]["promoted"] == 0
    assert stored["popularity_backfill_coverage"]["status"] == "complete"
    assert len(stored["popularity_backfill"]) == 2
    registry.quotes = {"600002.SH"}
    registry.limit = {"error": "upstream", "count": 0, "stocks": []}
    assert collector.collect(OUTCOME, SOURCE) == good
    assert read_archive(path) == stored
    registry.quotes.clear()
    assert collector.collect(OUTCOME, SOURCE) == good
    assert read_archive(path) == stored


def test_fresh_verified_source_supersedes_old_intent(archive, monkeypatch):
    _, db, path, original = archive
    with monkeypatch.context() as patch:
        patch.setattr(feedback, "atomic_write_archive", lambda *a: (_ for _ in ()).throw(OSError()))
        with pytest.raises(OSError):
            feedback.publish_feedback(path, original, blocks(), db)
    revised = {"date": SOURCE, "raw_data": {"limit_up": {"stocks": [{"code": "600999.SH"}]}}}
    atomic_write_archive(path, revised)
    with pytest.raises(ValueError, match="两端回填源行情不一致"):
        feedback.publish_feedback(path, revised, blocks(), db)
    pending = path.with_name(feedback._PENDING)
    assert json.loads(pending.read_bytes())["source_sha256"] == feedback.source_digest(original)
    with get_db(db) as conn:
        conn.execute("UPDATE daily_market SET raw_data=? WHERE date=?", (json.dumps(revised), SOURCE))
    latest = blocks()
    latest["premium_backfill"]["first_board"]["count"] = 4
    feedback.publish_feedback(path, revised, latest, db)
    assert not pending.exists()
    assert read_archive(path)["premium_backfill"]["first_board"]["count"] == 4


def test_old_general_retry_rereads_latest_under_date_lock(archive, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from db import dual_write
    from utils.post_archive import archive_lock

    _, db, path, original = archive
    monkeypatch.setattr(dual_write, "PENDING_WRITES_PATH", path.parent / "test-pending.json")
    dual_write.record_pending("daily_market", {"date": SOURCE, "raw_data": original}, "old")
    started = threading.Event()
    def retry():
        started.set()
        return retry_pending(db, daily_dir=path.parent.parent)
    with ThreadPoolExecutor() as executor:
        with archive_lock(path):
            job = executor.submit(retry)
            assert started.wait(timeout=2)
            assert not job.done()
            atomic_write_archive(path, {**original, **blocks()})
        assert job.result(timeout=5) == (1, 0)
    with get_db(db) as conn:
        result = json.loads(Q.get_daily_market(conn, SOURCE)["raw_data"])
    assert all(result[k] == v for k, v in blocks().items())

def test_missing_previous_close_is_unknown_and_partial_popularity_keeps_open(archive, monkeypatch):
    from collectors import premium
    from providers.base import DataResult
    _, db, path, original = archive
    monkeypatch.setattr(premium, "DAILY_DIR", path.parent.parent)
    class NoQuotes:
        def call(self, *args):
            return DataResult(None, "mock", error="no quote")
    result = premium.PremiumCollector(NoQuotes()).collect(OUTCOME, SOURCE)
    assert result["_coverage"]["status"] == "source_failed"
    assert result["_coverage"]["expected_codes"] == ["600000.SH"]
    assert result["first_board"]["count"] is None
    updates = blocks()
    updates["popularity_backfill"][0].update(t_open=10, t_open_premium_pct=1, t_is_limit_up=True, t_is_limit_down=False)
    feedback.publish_feedback(path, original, updates, db)
    updates["popularity_backfill"][0].update(t_open=None, t_open_premium_pct=None, t_close=11, t_is_limit_up=None, t_is_limit_down=None)
    feedback.publish_feedback(path, original, updates, db)
    assert read_archive(path)["popularity_backfill"][0] == {
        "code": "600000.SH", "t_open": 10, "t_open_premium_pct": 1, "t_close": 11,
        "t_is_limit_up": True, "t_is_limit_down": False}



def test_dated_stock_quote_key_cannot_switch_day_or_exchange():
    from collectors.premium import _quote_on
    from providers.base import DataResult

    good = {"ts_code": "920001.BJ", "trade_date": "20261123", "open": 10.1}
    assert _quote_on(DataResult(good, "test"), "920001", OUTCOME) == good
    assert not _quote_on(DataResult(good, "test"), "920001.SH", OUTCOME)
    assert not _quote_on(DataResult(good, "test"), "920001", SOURCE)
