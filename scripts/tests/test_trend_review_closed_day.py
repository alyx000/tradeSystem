"""休市复盘只使用日历证明的最近事实日，不丢有效partial或绕过摘要绑定。"""
from copy import deepcopy
import sqlite3

import pytest

from services.trend_leader import review_evidence as R
from tests.test_trend_review_evidence import DAY, summary

REPORT = "2026-08-01"


def calendar(tmp_path, rows=None):
    db = tmp_path / "calendar.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE trade_calendar(date TEXT PRIMARY KEY, is_open INTEGER)")
        conn.executemany("INSERT INTO trade_calendar VALUES(?,?)", rows if rows is not None else [
            (DAY, 1), ("2026-07-31", 0), (REPORT, 0),
        ])
    return db


def archive(tmp_path, partial=False):
    s = summary()
    if partial:
        s["research_cards"] = []
        s["research_coverage"].update(collected=0, not_collected=["600000"])
    p = R.build(s, "original")
    (tmp_path / f"{DAY}.md").write_text("original")
    R.write(tmp_path, DAY, p)
    return p


@pytest.mark.parametrize("partial", [False, True])
def test_closed_day_keeps_fact_date_rows_and_source_status(tmp_path, partial):
    original = archive(tmp_path, partial)
    result = R.load_for_review(tmp_path, REPORT, calendar(tmp_path))
    assert result["trade_date"] == DAY
    assert result["status"] == original["status"]
    assert result["observations"] == original["observations"]
    assert result["cards"] == original["cards"]
    html, gaps = R.render(result, REPORT)
    assert f'data-as-of="{DAY}"' in html
    assert f'data-reviewed-through="{REPORT}"' in html
    assert 'data-review-calendar="closed"' in html and "当日不扫描" in html
    assert "600000" in html
    if partial:
        assert "证据卡0/1" in html and all(g.startswith(f"截至{DAY}：") for g in gaps)
    else:
        assert "证据卡1/1" in html
        assert gaps == [f"截至{DAY}：低位连续堆量（{DAY}）：旧快照未计算低位连续堆量"]


@pytest.mark.parametrize("rows", [
    [(DAY, 1), (REPORT, 0)],  # 缺连续日历中间日
    [(DAY, 1), ("2026-07-31", 0)],  # 缺报告日
    [(DAY, 1), ("2026-07-31", 2), (REPORT, 0)],  # 非法状态
])
def test_incomplete_calendar_never_exposes_historical_rows(tmp_path, rows):
    archive(tmp_path)
    result = R.load_for_review(tmp_path, REPORT, calendar(tmp_path, rows))
    assert result["status"] == "source_failed"
    html, _ = R.render(result, REPORT)
    assert "600000" not in html and "日历未核验" in html


def test_open_day_missing_report_does_not_fall_back(tmp_path):
    archive(tmp_path)
    result = R.load_for_review(tmp_path, REPORT, calendar(tmp_path, [(DAY, 1), (REPORT, 1)]))
    assert result["trade_date"] == REPORT and result["status"] == "missing-data"
    assert result["observations"] == []
    assert "review_context" not in result


def test_closed_day_uses_latest_open_even_if_that_archive_is_missing(tmp_path):
    archive(tmp_path)
    result = R.load_for_review(tmp_path, REPORT, calendar(tmp_path, [(DAY, 1), ("2026-07-31", 1), (REPORT, 0)]))
    assert result["trade_date"] == "2026-07-31"
    assert result["status"] == "missing-data" and result["observations"] == []


def test_closed_day_does_not_bypass_markdown_sha(tmp_path):
    archive(tmp_path)
    (tmp_path / f"{DAY}.md").write_text("changed")
    result = R.load_for_review(tmp_path, REPORT, calendar(tmp_path))
    assert result["status"] == "source_failed"
    html, gaps = R.render(result, REPORT)
    assert "不匹配" in html and "600000" not in html
    assert all(g.startswith(f"截至{DAY}：") for g in gaps)


@pytest.mark.parametrize("mutation", [
    lambda p: p.pop("review_context"),
    lambda p: p["review_context"].update(report_date="2026-08-02"),
    lambda p: p["review_context"]["calendar"][1].__setitem__(1, 1),
    lambda p: p["review_context"]["calendar"].pop(1),
])
def test_renderer_rejects_unproven_or_forged_historical_context(tmp_path, mutation):
    archive(tmp_path)
    p = deepcopy(R.load_for_review(tmp_path, REPORT, calendar(tmp_path)))
    mutation(p)
    html, gaps = R.render(p, REPORT)
    assert "source_failed" in html and "600000" not in html and gaps


def test_historical_render_does_not_accept_future_announcement(tmp_path):
    archive(tmp_path)
    p = R.load_for_review(tmp_path, REPORT, calendar(tmp_path))
    p["cards"][0]["holders"]["latest"]["ann_date"] = "2026-07-31"
    html, _ = R.render(p, REPORT)
    assert "source_failed" in html and "600000" not in html


def test_assembler_keeps_closed_day_fact_date_and_rejects_relabel(tmp_path):
    from tests.test_daily_review_html_report import _load_assembler, _write_chunks

    archive(tmp_path)
    p = R.load_for_review(tmp_path, REPORT, calendar(tmp_path))
    module = _load_assembler()
    _write_chunks(tmp_path, REPORT)
    html = module.render_report(tmp_path, REPORT, trend_review_evidence=p)
    module.validate_report(html, trend_review_evidence=p)
    fragment = R.render(p, REPORT)[0]
    changed = fragment.replace(f'data-as-of="{DAY}"', f'data-as-of="{REPORT}"')
    assert fragment in html and changed != fragment
    with pytest.raises(module.ReportValidationError):
        module.validate_report(html.replace(fragment, changed), trend_review_evidence=p)
