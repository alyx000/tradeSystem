from datetime import date, timedelta
import sqlite3

import pytest
import yaml

from services.review_consecutive_pool import load, render
from tests.test_consecutive_pool import evidence
from tests.test_daily_review_html_report import DATE, _load_assembler, _write_chunks


def history(tmp_path, report_date='2026-11-10'):
    db = tmp_path/'calendar.db'
    end = date.fromisoformat(report_date)
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE trade_calendar (date TEXT, exchange TEXT, is_open INTEGER)')
        for i in range(70):
            d = end-timedelta(days=i)
            conn.execute('INSERT INTO trade_calendar VALUES (?, ?, ?)', (d.isoformat(), 'SSE', int(d.weekday() < 5)))
    return db


def archive(root, day, *, bad_amount=False):
    e = evidence(day)
    if bad_amount: e['quotes'][0]['amount'] = None
    p = root/day/'post-market.yaml'
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(dict(date=day, raw_data=dict(limit_up=e['limit_up'],
        zhong_prefix_effect=dict(trade_date=day, evidence=e))), allow_unicode=True))


def test_twenty_open_days_gaps_weekend_and_recompute(tmp_path):
    db = history(tmp_path, '2026-11-15')
    for day in ['2026-11-11', '2026-11-12', '2026-11-13']:
        archive(tmp_path, day, bad_amount=day == '2026-11-12')
    payload = load(tmp_path, db, '2026-11-15')
    assert payload['fact_date'] == '2026-11-13' and len(payload['points']) == 20
    assert not any(p['trade_date'] == '2026-11-14' for p in payload['points'])
    payload['points'][-1]['amount_yi'] = 99999
    html, gaps = render(payload, '2026-11-15')
    assert '99999' not in html and '6.23亿元' in html and gaps
    assert 'data-reviewed-through="2026-11-15"' in html
    assert html.count('data-pool-line="amount_yi"') == 0  # 真缺口两侧不可相连
    assert html.count('data-pool-line="count"') == 2  # 数量仍完整
    assert '甲 000001.SZ' in html and '旧归档只读重算' in html
    with sqlite3.connect(db) as conn: conn.execute("DELETE FROM trade_calendar WHERE date='2026-11-14'")
    assert load(tmp_path, db, '2026-11-15')['points'] == []


def test_formal_assembler_injects_into_emotion_and_rejects_duplicate(tmp_path):
    db = history(tmp_path, DATE)
    archive(tmp_path, DATE)
    payload = load(tmp_path, db, DATE)
    assembler = _load_assembler()
    _write_chunks(tmp_path)
    html = assembler.render_report(tmp_path, DATE, consecutive_pool_trend=payload)
    assert '连板股票池（2只）（6.23亿元）' in html
    assert html.count('data-consecutive-pool-trend="v1"') == 1
    assembler.validate_report(html)
    chunk = tmp_path/f's456_{DATE}.html'
    if not chunk.exists():
        chunk = next(tmp_path.glob('*s456*'))
    chunk.write_text(chunk.read_text().replace('</section>', '<p data-consecutive-pool-trend="v1">重复</p></section>', 1))
    with pytest.raises(assembler.ReportValidationError, match='统一生成'):
        assembler.render_report(tmp_path, DATE, consecutive_pool_trend=payload)


def test_small_count_ticks_are_distinct_integers():
    from services.review_consecutive_pool import _chart
    import re
    html = _chart([{'trade_date': DATE, 'count': 1}], 'count', '连板股数量', '只', '#c04b3c')
    labels = re.findall(r'text-anchor="end">([^<]+)</text>', html)
    assert labels == ['0', '1', '2']
