"""Explicit date roles for read-only report charts; never infer holidays from weekdays."""
from datetime import date, timedelta
from contextlib import closing
from pathlib import Path
import sqlite3

from services.review_calendar import load_review_context


def visual_context(report_date, fact_date=None, review_context=None):
    date.fromisoformat(report_date)
    fact_date = fact_date or report_date
    start, end = date.fromisoformat(fact_date), date.fromisoformat(report_date)
    if start == end:
        if review_context is not None:
            raise ValueError('同日图形不能带休市窗口')
    else:
        span = (end - start).days
        if not 0 < span <= 31 or not isinstance(review_context, dict):
            raise ValueError('历史图形缺少有效休市日历')
        expected = [[(start + timedelta(days=i)).isoformat(), int(i == 0)] for i in range(span + 1)]
        calendar = review_context.get('calendar')
        if (review_context.get('report_date') != report_date or calendar != expected or
                any(type(row[1]) is not int for row in calendar)):
            raise ValueError('休市窗口缺日、含开放日或日期不符')
    return dict(schema='review-visual-context-v1', report_date=report_date,
                fact_date=fact_date, status='complete', review_context=review_context)


def validate_visual_context(payload, report_date):
    if payload is None:
        return visual_context(report_date)  # Legacy same-day callers never gain historical access.
    if not isinstance(payload, dict) or payload.get('report_date') != report_date:
        raise ValueError('图形复核日不符')
    if payload.get('status') == 'source_failed':
        if (payload.get('schema') != 'review-visual-context-v1' or payload.get('fact_date') is not None or
                not isinstance(payload.get('reason'), str) or not payload['reason']):
            raise ValueError('图形日历失败状态非法')
        return payload
    expected = visual_context(report_date, payload.get('fact_date'), payload.get('review_context'))
    if payload != expected:
        raise ValueError('图形日期上下文不完整或非法')
    return expected


def load_visual_context(db_path, report_date):
    try:
        fact_date, context = load_review_context(db_path, report_date)
        expected = context['calendar'] if context else [[report_date, 1]]
        # The shared helper establishes the window; this chart gate also proves
        # its market source and rejects a changed calendar between the two reads.
        with closing(sqlite3.connect(Path(db_path).resolve().as_uri()+'?mode=ro', uri=True)) as conn:
            rows = conn.execute('SELECT date,is_open,exchange FROM trade_calendar '
                                'WHERE date BETWEEN ? AND ? ORDER BY date',
                                (fact_date, report_date)).fetchall()
        if rows != [(day, is_open, 'SSE') for day, is_open in expected]:
            raise ValueError('图形交易日历窗口须完整来自 SSE，且读取期间保持一致')
        return visual_context(report_date, fact_date, context)
    except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
        return dict(schema='review-visual-context-v1', report_date=report_date,
                    fact_date=None, status='source_failed', reason=str(exc))
