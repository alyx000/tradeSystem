from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from services.intraday_summary.service import run


TZ = ZoneInfo("Asia/Shanghai")
STOCK_CODES = [f"{index:06d}.SZ" for index in range(1, 4001)]
INDEX_CODES = {"000001.SH", "399001.SZ", "399006.SZ", "000688.SH"}


class _Result:
    def __init__(self, data=None, error=None, source="fake"):
        self.data = data
        self.error = error
        self.source = source
        self.success = error is None and data is not None


class _Registry:
    def __init__(self):
        self.now = datetime(2026, 8, 31, 9, 30, tzinfo=TZ)
        self.realtime_calls = 0
        self.basic_calls = 0
        self.industry_calls = 0
        self.fail_realtime = False

    def call(self, capability, *args):
        if capability == "get_stock_basic_list":
            self.basic_calls += 1
            return _Result([{"ts_code": code} for code in STOCK_CODES])
        if capability == "get_stock_sw_industry_map":
            self.industry_calls += 1
            return _Result({
                code: {"sw_l2": f"行业{index % 10}"}
                for index, code in enumerate(STOCK_CODES)
            })
        if capability == "get_realtime_quotes":
            self.realtime_calls += 1
            if self.fail_realtime:
                return _Result(error="realtime unavailable")
            codes = args[0]
            minute = self.now.hour * 60 + self.now.minute
            step = max(0, minute - (9 * 60 + 30)) // 30
            rows = []
            for index, code in enumerate(codes):
                direction = 1 if index % 2 == 0 else -1
                if code in INDEX_CODES:
                    price = 1000 + step * direction
                    name = code
                    amount = 0
                else:
                    price = 10 * (1 + step * direction * 0.01)
                    name = f"股票{index}"
                    amount = 1e7 + step * 1e6
                rows.append({
                    "code": code,
                    "name": name,
                    "price": price,
                    "pct_chg": (price / (1000 if code in INDEX_CODES else 10) - 1) * 100,
                    "amount": amount,
                    "quote_date": self.now.date().isoformat(),
                    "quote_time": self.now.strftime("%H:%M:%S"),
                })
            return _Result(rows)
        raise AssertionError(f"unexpected capability: {capability}")


class _Pusher:
    def __init__(self, succeed=True):
        self.succeed = succeed
        self.messages = []

    def initialize(self):
        return True

    def send_markdown(self, title, content):
        self.messages.append((title, content))
        return self.succeed


def _calendar(tmp_path: Path, *, is_open: int = 1) -> Path:
    path = tmp_path / "trade.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE trade_calendar (date TEXT PRIMARY KEY, is_open INTEGER NOT NULL)")
    conn.execute("INSERT INTO trade_calendar VALUES ('2026-08-31', ?)", (is_open,))
    conn.commit()
    conn.close()
    return path


def _run(tmp_path, registry, pusher, now, *, dry_run=False, no_push=False, db_path=None):
    registry.now = now
    return run(
        registry,
        now=now,
        state_path=tmp_path / "state.json",
        report_root=tmp_path / "reports",
        db_path=db_path or _calendar(tmp_path),
        dry_run=dry_run,
        no_push=no_push,
        pusher_factory=lambda: pusher,
    )


def test_baseline_then_half_hour_summary_pushes_once(tmp_path):
    db_path = _calendar(tmp_path)
    registry = _Registry()
    pusher = _Pusher()
    baseline_at = datetime(2026, 8, 31, 9, 30, tzinfo=TZ)

    baseline = _run(tmp_path, registry, pusher, baseline_at, db_path=db_path)
    summary_at = datetime(2026, 8, 31, 10, 0, tzinfo=TZ)
    summary = _run(tmp_path, registry, pusher, summary_at, db_path=db_path)
    calls_before_retry = registry.realtime_calls
    duplicate = _run(tmp_path, registry, pusher, summary_at + timedelta(minutes=1), db_path=db_path)

    assert baseline["status"] == "baseline_saved"
    assert summary["status"] == "complete"
    assert summary["pushed"] is True
    assert duplicate["status"] == "already_sent"
    assert registry.realtime_calls == calls_before_retry
    assert len(pusher.messages) == 1
    assert "09:30 → 10:00" in pusher.messages[0][1]
    assert "上涨 2000 / 下跌 2000" in pusher.messages[0][1]
    report = Path(summary["report_path"])
    assert report.exists()
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["pending_reports"] == []
    assert "2026-08-31T10:00" in state["sent_slot_ids"]


def test_baseline_slot_is_captured_only_once(tmp_path):
    db_path = _calendar(tmp_path)
    registry = _Registry()
    pusher = _Pusher()
    now = datetime(2026, 8, 31, 9, 30, tzinfo=TZ)

    first = _run(tmp_path, registry, pusher, now, db_path=db_path)
    second = _run(tmp_path, registry, pusher, now + timedelta(minutes=1), db_path=db_path)

    assert first["status"] == "baseline_saved"
    assert second["status"] == "already_captured"
    assert registry.realtime_calls == 1


def test_missing_baseline_pushes_partial_current_snapshot(tmp_path):
    db_path = _calendar(tmp_path)
    registry = _Registry()
    pusher = _Pusher()
    now = datetime(2026, 8, 31, 10, 0, tzinfo=TZ)

    result = _run(tmp_path, registry, pusher, now, db_path=db_path)

    assert result["status"] == "partial"
    assert result["pushed"] is True
    assert "缺少 09:30 基线快照" in pusher.messages[0][1]
    assert "最近半小时变化未计算" in pusher.messages[0][1]


def test_failed_push_stays_pending_and_retries_without_refetch(tmp_path):
    db_path = _calendar(tmp_path)
    registry = _Registry()
    pusher = _Pusher(succeed=True)
    baseline_at = datetime(2026, 8, 31, 9, 30, tzinfo=TZ)
    _run(tmp_path, registry, pusher, baseline_at, db_path=db_path)
    pusher.succeed = False
    summary_at = datetime(2026, 8, 31, 10, 0, tzinfo=TZ)
    failed = _run(tmp_path, registry, pusher, summary_at, db_path=db_path)
    calls_before_retry = registry.realtime_calls
    pusher.succeed = True
    retried = _run(tmp_path, registry, pusher, summary_at + timedelta(minutes=1), db_path=db_path)

    assert failed["status"] == "push_failed"
    assert failed["pending_count"] == 1
    assert retried["status"] == "complete"
    assert retried["retry_only"] is True
    assert registry.realtime_calls == calls_before_retry
    assert len(pusher.messages) == 2


def test_failed_push_expires_instead_of_cross_slot_delivery(tmp_path):
    db_path = _calendar(tmp_path)
    registry = _Registry()
    pusher = _Pusher(succeed=True)
    _run(
        tmp_path,
        registry,
        pusher,
        datetime(2026, 8, 31, 9, 30, tzinfo=TZ),
        db_path=db_path,
    )
    pusher.succeed = False
    failed = _run(
        tmp_path,
        registry,
        pusher,
        datetime(2026, 8, 31, 10, 0, tzinfo=TZ),
        db_path=db_path,
    )
    pusher.succeed = True
    next_slot = _run(
        tmp_path,
        registry,
        pusher,
        datetime(2026, 8, 31, 10, 30, tzinfo=TZ),
        db_path=db_path,
    )

    assert failed["status"] == "push_failed"
    assert next_slot["status"] == "complete"
    assert len(pusher.messages) == 2
    assert "10:00" in pusher.messages[0][0]
    assert "10:30" in pusher.messages[1][0]
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert "summary:2026-08-31T10:00" in state["expired_pending_ids"]
    assert "2026-08-31T10:00" not in state["sent_slot_ids"]


def test_partial_report_retry_keeps_partial_status(tmp_path):
    db_path = _calendar(tmp_path)
    registry = _Registry()
    pusher = _Pusher(succeed=False)
    summary_at = datetime(2026, 8, 31, 10, 0, tzinfo=TZ)

    failed = _run(tmp_path, registry, pusher, summary_at, db_path=db_path)
    pusher.succeed = True
    retried = _run(
        tmp_path,
        registry,
        pusher,
        summary_at + timedelta(minutes=1),
        db_path=db_path,
    )

    assert failed["status"] == "push_failed"
    assert failed["data_status"] == "partial"
    assert retried["status"] == "partial"
    assert retried["data_status"] == "partial"
    assert retried["retry_only"] is True


def test_realtime_failure_is_source_failed_not_empty_summary(tmp_path):
    db_path = _calendar(tmp_path)
    registry = _Registry()
    registry.fail_realtime = True
    pusher = _Pusher()

    result = _run(
        tmp_path,
        registry,
        pusher,
        datetime(2026, 8, 31, 10, 0, tzinfo=TZ),
        db_path=db_path,
    )

    assert result["status"] == "source_failed"
    assert registry.realtime_calls == 2
    assert pusher.messages == []
    assert not (tmp_path / "reports").exists()


def test_non_trade_day_skips_before_sources(tmp_path):
    db_path = _calendar(tmp_path, is_open=0)
    registry = _Registry()
    result = _run(
        tmp_path,
        registry,
        _Pusher(),
        datetime(2026, 8, 31, 10, 0, tzinfo=TZ),
        db_path=db_path,
    )
    assert result["status"] == "non_trade_day"
    assert registry.realtime_calls == 0
    assert registry.basic_calls == 0


def test_dry_run_has_no_state_report_or_push(tmp_path):
    db_path = _calendar(tmp_path)
    registry = _Registry()
    pusher = _Pusher()
    result = _run(
        tmp_path,
        registry,
        pusher,
        datetime(2026, 8, 31, 10, 0, tzinfo=TZ),
        db_path=db_path,
        dry_run=True,
    )
    assert result["status"] == "dry_run"
    assert result["data_status"] == "partial"
    assert not (tmp_path / "state.json").exists()
    assert not (tmp_path / "reports").exists()
    assert pusher.messages == []
