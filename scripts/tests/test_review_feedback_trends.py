"""反馈图的日期、样本与断线语义；与正式复盘入口联动。"""
import copy
import sqlite3
from datetime import date, timedelta

import pytest

from services.review_feedback_trends import (
    _board_point, _calendar, _core_point, _panel, render_feedback_trends,
)
from tests.test_daily_review_html_report import DATE, _load_assembler, _write_chunks, _appendix, _table


DAYS = ["2026-09-04", "2026-09-07", "2026-09-08"]


def board():
    return {"date": DAYS[2], "raw_data": {"board_break_feedback": {
        "status": "ok", "source_connected_date": DAYS[0], "break_date": DAYS[1], "outcome_date": DAYS[2],
        "sample_count": 2, "break_count": 2, "break_candidate_count": 2, "connected_count": 3,
        "details": [{"code": "000001", "previous_height": 2, "feedback_open_pct": -2, "feedback_close_pct": -4},
                    {"code": "000002", "previous_height": 3, "feedback_open_pct": 4, "feedback_close_pct": 8}],
        "close_mean_pct": 999, "close_up_rate": 99,
    }}}


def core():
    row = {"code": "000001.SZ", "metric_status": "ok", "current_state": "涨停", "new_peak_today": True, "promoted_date": DAYS[2]}
    return {"date": DAYS[2], "status": "ok", "active": [row], "promoted_today": [row],
            "summary": {"active_count": 1, "today_limit_up_count": 1, "today_limit_down_count": 0, "new_peak_count": 1},
            "coverage": {"expected_open_days": 64, "loaded_limit_days": 64}}


def test_board_recomputes_percentage_and_mean_from_rows():
    point = _board_point(board(), DAYS)
    assert point["status"] == "ok"
    assert point["values"] == {"open": 1, "close": 2, "median": 2, "up_rate": 50}


@pytest.mark.parametrize("field,value", [("break_date", "2026-09-06"), ("sample_count", 3), ("connected_count", 1), ("sample_count", True)])
def test_board_rejects_false_timeline_or_counts(field, value):
    payload = board()
    payload["raw_data"]["board_break_feedback"][field] = value
    point = _board_point(payload, DAYS)
    assert point["status"] == "source_failed" and not point["values"]


@pytest.mark.parametrize("field,value", [("feedback_open_pct", float("nan")), ("previous_height", 1), ("code", "000001")])
def test_board_rejects_invalid_rows(field, value):
    payload = board()
    payload["raw_data"]["board_break_feedback"]["details"][1][field] = value
    assert not _board_point(payload, DAYS)["values"]


def test_empty_is_not_zero_return_and_partial_is_not_complete():
    payload = board()
    block = payload["raw_data"]["board_break_feedback"]
    block.update(sample_count=0, break_count=0, details=[], empty_reason="no_board_breaks")
    assert _board_point(payload, DAYS)["status"] == "empty"
    assert not _board_point(payload, DAYS)["values"]
    block.update(status="source_failed")
    assert _board_point(payload, DAYS)["status"] == "source_failed"
    payload = board()
    payload["raw_data"]["board_break_feedback"].update(break_count=3, break_candidate_count=3)
    assert _board_point(payload, DAYS)["status"] == "partial"


def test_core_uses_whole_pool_and_preserves_partial():
    payload = core()
    assert _core_point(payload, DAYS[2])["values"]["promoted"] == 1
    payload["coverage"]["loaded_limit_days"] = 63
    assert _core_point(payload, DAYS[2])["status"] == "partial"
    payload["status"] = "source_failed"
    assert not _core_point(payload, DAYS[2])["values"]


def test_all_metric_failures_do_not_become_zero_peaks():
    payload = core()
    payload["active"][0].update(metric_status="source_failed", new_peak_today=False)
    payload["summary"]["new_peak_count"] = 0
    point = _core_point(payload, DAYS[2])
    assert "peak" not in point["values"]
    assert point["values"]["limit_up"] == 1
    assert point["sample"] == 0 and point["status"] == "partial"


def test_all_empty_history_preserves_verified_no_samples():
    points = [{"date": DAYS[2], "status": "empty", "sample": 0, "total": 0, "values": {}}]
    html, gaps = render_feedback_trends({"board": points, "core": points}, DAYS[2])
    assert 'data-board-feedback-trend="v1"' in html
    assert 'data-source-status="complete"' in html
    assert "无样本" in html and not gaps


@pytest.mark.parametrize("mutation", ["counts", "duplicate", "promotion", "nan"])
def test_core_rejects_inconsistent_snapshots(mutation):
    payload = core()
    if mutation == "counts":
        payload["summary"]["today_limit_up_count"] = 0
    elif mutation == "duplicate":
        payload["active"] *= 2
    elif mutation == "promotion":
        payload["promoted_today"] = [{"code": "000002.SZ", "promoted_date": DAYS[2]}]
    else:
        payload["summary"]["active_count"] = float("nan")
    assert not _core_point(payload, DAYS[2])["values"]


def test_partial_is_dashed_and_missing_still_breaks_lines():
    point = _board_point(board(), DAYS)
    points = [copy.deepcopy(point) for _ in range(5)]
    points[1]["status"] = "partial"
    points[3].update(status="source_failed", values={})
    html = _panel(points, [("close", "收盘")], "%", "反馈")
    assert html.count('data-trend-line=') == 2
    assert html.count('data-line-status="partial"') == 2
    assert html.count('stroke-dasharray="5 4"') == 2
    assert html.count('data-trend-point=') == 4
    points[1]["status"] = "ok"
    complete = _panel(points, [("close", "收盘")], "%", "反馈")
    assert complete.count('data-trend-line=') == 2
    assert 'stroke-dasharray=' not in complete


def test_core_uses_four_separate_charts_on_the_same_scale():
    point = _core_point(core(), DAYS[2])
    html, _ = render_feedback_trends({"core": [point]}, DAYS[2])
    assert html.count('viewBox="0 0 470 208"') == 4
    assert 'feedback-small-multiples' in html
    assert html.count('>4只</text>') == 4
    assert "虚线＋空心点" in html


def test_missing_single_metric_breaks_its_own_line():
    points = [_core_point(core(), DAYS[2]) for _ in range(3)]
    points[1]["values"].pop("peak")
    html = _panel(points, [("peak", "区间创新高")], "只", "创新高", compact=True, axis_max=4)
    assert 'data-trend-line=' not in html
    assert '>缺</text>' in html


def test_calendar_needs_complete_natural_days_and_sse(tmp_path):
    db = tmp_path / "calendar.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE trade_calendar(exchange TEXT,date TEXT,is_open INTEGER)")
        for i in range(60):
            day = date(2026, 9, 13) - timedelta(days=i)
            for exchange in ("SSE", "SZSE"):
                conn.execute("INSERT INTO trade_calendar VALUES(?,?,?)", (exchange, str(day), int(day.weekday() < 5)))
    days = _calendar(db, "2026-09-13")
    assert len(days) == 22 and days[-1] == "2026-09-11"
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM trade_calendar WHERE date='2026-09-06' AND exchange='SSE'")
    assert _calendar(db, "2026-09-13") == []


def test_formal_assembler_injects_both_and_rejects_chunk_override(tmp_path):
    assembler = _load_assembler()
    _write_chunks(tmp_path)
    payload = {"board": [_board_point(board(), DAYS)], "core": [_core_point(core(), DAYS[2])]}
    for points in payload.values():
        points[0]["date"] = DATE
    html = assembler.render_report(tmp_path, DATE, feedback_trends=payload)
    assert html.count('data-board-feedback-trend="v1"') == 1
    assert html.count('data-core-feedback-trend="v1"') == 1
    assembler.validate_report(html)
    chunk = tmp_path / f"b{DATE}_s456.html"
    chunk.write_text(chunk.read_text() + '<p data-core-feedback-trend="v1">伪造</p>')
    with pytest.raises(assembler.ReportValidationError, match="duplicate_feedback_trend"):
        assembler.render_report(tmp_path, DATE)


def test_no_history_shows_two_gaps():
    html, gaps = render_feedback_trends(None, DAYS[2])
    assert html.count('="missing-data"') >= 2 and len(gaps) == 2
    assert '<svg' not in html


def test_automatic_tables_preserve_full_original_400_row_budget(tmp_path):
    assembler = _load_assembler()
    _write_chunks(tmp_path)
    base = assembler.render_report(tmp_path, DATE)
    original_rows = assembler.collect_metrics(base).evidence_rows
    point = _board_point(board(), DAYS)
    point["date"] = DATE
    rendered = assembler.render_report(tmp_path, DATE, feedback_trends={"board": [point], "core": [point]})
    at_limit = _appendix(rendered, _table(400-original_rows))
    assert assembler.validate_report(at_limit).evidence_rows == 404
    with pytest.raises(assembler.ReportValidationError, match="evidence_rows_exceeded"):
        assembler.validate_report(_appendix(at_limit, _table(1)))
