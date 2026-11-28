"""指数成交额覆盖率只反映当时的盘中回执。"""
import json
from datetime import date, datetime

import pytest

from services.index_volume_monitor import coverage


DAY = date(2026, 11, 27)
PREVIOUS = "2026-11-26"


def moment(hm):
    return datetime.fromisoformat(f"{DAY}T{hm}+08:00")


def receipt(root, slot, hhmm, status, number, **extra):
    folder = root / str(DAY)
    folder.mkdir(exist_ok=True)
    body = {"captured_at": moment(hhmm).isoformat(), "status": status,
            "input_by": "launchd", "previous_trade_date": PREVIOUS,
            "evaluations": [], "errors": [], **extra}
    path = folder / f"{slot}-{number:032x}.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


@pytest.fixture
def read_day(monkeypatch, tmp_path):
    monkeypatch.setattr(coverage, "calendar_basis", lambda *_: ("complete", PREVIOUS))

    def read(hm="15:06"):
        return coverage.receipt_coverage(tmp_path, DAY, DAY, now=moment(hm))["days"][0]

    return read


def test_final_attempt_counts_once_and_splits_sessions(tmp_path, read_day):
    # 故意让文件名排序与执行时间排序相反。
    receipt(tmp_path, "0935", "09:36", "source_failed", 99)
    receipt(tmp_path, "0935", "09:39", "complete", 1)
    receipt(tmp_path, "1305", "13:07", "partial", 4)
    receipt(tmp_path, "1500", "15:03", "source_failed", 5)
    result = read_day()
    assert result["counts"] == {"complete": 1, "partial": 1, "source_failed": 1,
                                "no_receipt": 45, "invalid_receipt": 0, "in_progress": 0}
    assert result["morning"]["no_receipt"] == 23
    assert result["afternoon"]["no_receipt"] == 22
    assert result["slots"]["0935"] == "complete" and result["slots"]["1500"] == "source_failed"


def test_latest_timestamp_conflict_and_agreement(tmp_path, read_day):
    receipt(tmp_path, "0935", "09:37", "complete", 1)
    receipt(tmp_path, "0935", "09:37", "partial", 2)
    receipt(tmp_path, "0940", "09:43", "partial", 3)
    receipt(tmp_path, "0940", "09:43", "partial", 4)
    result = read_day()
    assert result["slots"]["0935"] == "invalid_receipt"
    assert result["slots"]["0940"] == "partial"


@pytest.mark.parametrize("stamp", ["2026-11-27T09:37", "2026-11-26T09:37+08:00",
                                   "2026-11-27T09:41+08:00", "garbage"])
def test_unorderable_timestamps_mark_slot_invalid(tmp_path, read_day, stamp):
    receipt(tmp_path, "0935", "09:37", "complete", 1, captured_at=stamp)
    assert read_day()["slots"]["0935"] == "invalid_receipt"


def test_prior_invalid_payload_does_not_hide_later_valid_receipt(tmp_path, read_day):
    receipt(tmp_path, "0935", "09:36", "complete", 1, previous_trade_date="2026-11-25")
    receipt(tmp_path, "0935", "09:39", "partial", 2)
    assert read_day()["slots"]["0935"] == "partial"


def test_as_of_uses_only_received_attempts_and_due_slots(tmp_path, read_day):
    receipt(tmp_path, "0935", "09:37", "complete", 1)
    receipt(tmp_path, "0935", "09:39", "unknown_status", 2)
    assert read_day("09:35")["slots"] == {}
    assert read_day("09:36")["slots"] == {"0935": "in_progress"}
    assert read_day("09:38")["slots"] == {"0935": "complete"}
    assert read_day("09:42")["slots"] == {"0935": "invalid_receipt", "0940": "in_progress"}


def test_post_close_ohlcv_cannot_backfill_intraday_receipts(tmp_path, read_day):
    archive = tmp_path / "ohlcv-5m"
    archive.mkdir()
    path = archive / f"{DAY}.json"
    content = json.dumps({"schema": "index-ohlcv-5m-v1", "trade_date": str(DAY),
                          "status": "complete", "valid_count": 192, "expected_full_day": 192})
    path.write_text(content, encoding="utf-8")
    receipt(tmp_path, "1500", "15:03", "source_failed", 1)
    result = read_day()
    assert result["counts"]["no_receipt"] == 47
    assert result["slots"]["1500"] == "source_failed"
    assert result["counts"]["complete"] == 0 and path.read_text(encoding="utf-8") == content


def test_legacy_frequency_rejected_and_closed_day_excluded(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="2026-11-18"):
        coverage.receipt_coverage(tmp_path, date(2026, 11, 17), DAY, now=moment("15:06"))
    monkeypatch.setattr(coverage, "calendar_basis", lambda *_: ("non_trade_day", None))
    saturday = date(2026, 11, 28)
    result = coverage.receipt_coverage(tmp_path, saturday, saturday,
                                       now=datetime.fromisoformat("2026-11-28T12:00+08:00"))
    assert result["days"] == [{"date": str(saturday), "status": "non_trade_day"}]
    assert sum(result["counts"].values()) == 0
