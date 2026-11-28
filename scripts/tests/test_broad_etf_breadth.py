from datetime import datetime
from hashlib import sha256
import json

import pytest

from services import broad_etf_breadth as breadth
from services.board_break_intraday.data import TZ, digest


DAY = '2026-11-27'
CODES = ['600000.SH', '000001.SZ', '600111.SH']


def at(t):
    return datetime.fromisoformat(f'{DAY}T{t}+08:00')


def batch(root, name, captured='09:32:04', codes=CODES, *, prices=(10, 11, 12), quotes=None):
    moment = at(captured).isoformat()
    ctx = dict(trade_date=DAY, universe=codes, frozen_at=at('09:32:01').isoformat())
    rows, stocks = [], {}
    for i, (code, price) in enumerate(zip(codes, prices)):
        time = quotes[i] if quotes else captured
        rows.append(dict(code=code, price=price, pre_close=11, quote_date=DAY, quote_time=time, amount=100))
        stocks[code] = dict(price=price, quote_at=at(time).isoformat(), amount=100)
    snap = dict(trade_date=DAY, captured_at=moment, source='sina', expected=len(codes), interval_minutes=10,
                raw_rows=rows, raw_sha256=digest(rows), stocks=stocks, context_sha256=digest(ctx))
    report = dict(input_by='USER', trade_date=DAY, captured_at=moment, snapshot=snap, context=ctx)
    p = root / DAY / name
    p.mkdir(parents=True)
    body = json.dumps(report).encode()
    (p/'report.json').write_bytes(body)
    (p/'receipt.json').write_text(json.dumps(dict(batch=name, input_by='USER', trade_date=DAY,
                                                  sha256=sha256(body).hexdigest())))
    return p


@pytest.fixture(autouse=True)
def small_sample(monkeypatch):
    monkeypatch.setattr(breadth, 'MIN_UNIVERSE', 3)


def test_breadth_uses_only_already_received_quotes_and_actual_times(tmp_path):
    batch(tmp_path, 'a')
    batch(tmp_path, 'future', '09:42:03')
    result = breadth.breadth_reference(tmp_path, DAY, at('09:42:02'))
    assert result['expected_slots'] == ['09:32', '09:42']
    assert result['missing_slots'] == ['09:42']
    point = result['timeseries']['09:32']
    assert (point['up'], point['flat'], point['down']) == (1, 1, 1)
    assert (point['valid'], point['expected'], point['status']) == (3, 3, 'complete')
    assert point['captured_at'] == point['quote_at_max'] == at('09:32:04').isoformat()
    assert point['source_sha256'] and result['status'] == 'partial'


def test_stale_or_missing_members_are_excluded_with_coverage(tmp_path):
    batch(tmp_path, 'a', quotes=['09:28:00', '09:32:02', '09:32:04'])
    point = breadth.breadth_reference(tmp_path, DAY, at('09:33:00'))['timeseries']['09:32']
    assert (point['valid'], point['expected'], point['up'], point['flat'], point['down']) == (2, 3, 1, 1, 0)
    assert point['status'] == 'partial'


def test_duplicates_and_different_universe_are_not_ranked(tmp_path):
    batch(tmp_path, 'a')
    batch(tmp_path, 'b', '09:42:02')
    batch(tmp_path, 'c', '09:42:40')
    batch(tmp_path, 'd', '09:52:03', ['600000.SH', '600111.SH', '600002.SH'])
    result = breadth.breadth_reference(tmp_path, DAY, at('09:53:00'))
    assert set(result['timeseries']) == {'09:32'}
    assert result['conflicts'] == ['09:42', '09:52']


def test_bad_receipt_and_unknown_day_do_not_report_zero_advances(tmp_path):
    f = batch(tmp_path, 'bad') / 'report.json'
    f.write_bytes(f.read_bytes() + b' ')
    result = breadth.breadth_reference(tmp_path, DAY, at('09:33:00'))
    assert result['status'] == 'source_failed' and not result['timeseries'] and len(result['gaps']) == 1
    with pytest.raises(ValueError):
        breadth.breadth_reference(tmp_path, DAY, datetime(2026, 11, 26, 15, tzinfo=TZ))


def test_cached_points_still_check_file_and_duplicate_slots(tmp_path, monkeypatch):
    old = batch(tmp_path, 'a')
    through = at('09:45:00')
    previous = breadth.breadth_reference(tmp_path, DAY, through)
    def unexpected(*args):
        pytest.fail('已核对两份哈希的旧批次无需重复解码')
    with monkeypatch.context() as m:
        m.setattr(breadth, '_breadth_point', unexpected)
        assert breadth.breadth_reference(tmp_path, DAY, through, previous=previous) == previous
    batch(tmp_path, 'b', '09:32:21')
    result = breadth.breadth_reference(tmp_path, DAY, through, previous=previous)
    assert result['conflicts'] == ['09:32'] and not result['timeseries']
    (old/'report.json').write_text('{}')
    result = breadth.breadth_reference(tmp_path, DAY, through, previous=previous)
    assert any(g['batch'] == 'a' and '哈希' in g['reason'] for g in result['gaps'])
