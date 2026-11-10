"""10点环境证据：合成行情/临时日历/模拟推送，不访问实盘。"""
import json
from copy import deepcopy

import pytest

from services.index_volume_monitor.opening_environment import evaluate_opening
from services.index_volume_monitor.rules import INDEX_VOLUME_GROWTH, INDEX_NAMES
from services.index_volume_monitor.service import run_index_volume
from scripts.tests.test_index_volume_monitor import bars, moment, CODE, calendar, setup  # noqa: F401


def source(code=CODE, *, volume='100', opening='101', close='100.5', amount='100'):
    rows = bars(code, current=amount)
    for row in rows:
        today = row['trade_time'].startswith('2026-11-04')
        row.update(open=opening if today else '100', close=close if today else '100',
                   high='110', low='90', volume=volume if today else '100')
    return rows


def evaluate(rows):
    return evaluate_opening(CODE, moment('10:00'), '2026-11-03', rows, rule=INDEX_VOLUME_GROWTH)


@pytest.mark.parametrize('volume,state,matched', [
    ('94.99999999', 'contracted', True), ('95', 'near_flat', True),
    ('100', 'near_flat', True), ('105', 'near_flat', True),
    ('105.00000001', 'expanded', False), ('0', 'contracted', True)])
def test_volume_boundaries_are_exact_and_amount_is_separate(volume, state, matched):
    item = evaluate(source(volume=volume, amount='200'))
    assert item['volume_state'] == state and item['weak_environment_candidate'] is matched
    assert item['amount_growth_pct'] == '100'
    assert len(item['source_bars']) == 5
    assert item['previous_close'] == '100' and item['price_at_1000'] == '100.5'
    assert item['flat_tolerance_pct'] == 5


@pytest.mark.parametrize('opening,close,high,below', [
    ('100', '99', False, True), ('99', '98', False, True),
    ('101', '101', True, False), ('101', '102', True, False)])
def test_price_equality_not_high_open_or_below(opening, close, high, below):
    item = evaluate(source(opening=opening, close=close))
    assert item['high_open'] is high and item['below_open_at_1000'] is below
    assert item['weak_environment_candidate'] is False
    assert '不据此判定强势' in item['summary']


@pytest.mark.parametrize('stamp', ['2026-11-03 09:45', '2026-11-03 10:00',
                                   '2026-11-03 15:00', '2026-11-04 09:45', '2026-11-04 10:00'])
def test_every_required_bar_is_mandatory(stamp):
    with pytest.raises(ValueError):
        evaluate([r for r in source() if r['trade_time'] != stamp])


@pytest.mark.parametrize('field,value', [('volume', None), ('volume', 'NaN'), ('volume', True),
    ('volume', '-1'), ('close', 'inf'), ('open', '0'), ('high', '95'), ('low', '105'),
    ('ts_code', '399001.SZ'), ('amount_unit', '万元')])
def test_invalid_required_fields_cannot_be_zero_or_unmatched(field, value):
    rows = source()
    rows[0][field] = value
    with pytest.raises((ValueError, TypeError)):
        evaluate(rows)


def test_zero_baseline_duplicate_wrong_date_and_other_slot():
    rows = source()
    for row in rows:
        if row['trade_time'].startswith('2026-11-03'):
            row['volume'] = '0'
    with pytest.raises(ValueError, match='成交量为0'):
        evaluate(rows)
    with pytest.raises(ValueError):
        evaluate(source()+source()[:1])
    with pytest.raises(ValueError):
        evaluate_opening(CODE, moment('10:30'), '2026-11-03', source(), rule=INDEX_VOLUME_GROWTH)
    with pytest.raises(ValueError):
        evaluate_opening(CODE, moment('10:00'), '2026-11-02', source(), rule=INDEX_VOLUME_GROWTH)


@pytest.mark.parametrize('fields', [('amount',), ('volume',), ('open', 'high', 'low', 'close')])
def test_smallest_valid_decimals_remain_replayable(fields):
    rows = source()
    for row in rows:
        for field in fields:
            row[field] = '0.00000001'
    first = evaluate(rows)
    assert evaluate(first['source_bars']) == first
    for row in first['source_bars']:
        for field in fields:
            assert row[field] == '0.00000001'


def prepare(registry, **kw):
    registry.dirty = {code: source(code, **kw) for code in INDEX_NAMES}


def test_receipt_contains_replayable_evidence_without_new_push_or_state_fields(setup):
    registry, pusher, kw = setup
    prepare(registry)
    before = deepcopy(registry.dirty)
    result = run_index_volume(registry, **kw)
    card = result['opening_environment']
    assert card['status'] == 'complete' and card['complete_count'] == 4
    assert all(x['weak_environment_candidate'] for x in card['items'])
    assert not result['pushed'] and not pusher.messages and not result['events']
    assert len(registry.calls) == 4 and registry.dirty == before
    receipt = json.loads(open(result['report_path']).read())
    assert receipt['opening_environment'] == card
    assert card['input_by'] == 'pytest' and card['teacher_note_id'] == 1178
    for item in card['items']:
        replay = evaluate_opening(item['code'], moment('10:00'), '2026-11-03',
                                  item['source_bars'], rule=INDEX_VOLUME_GROWTH)
        assert replay == item
    state = json.loads(kw['state_path'].read_text())
    assert 'opening_environment' not in state and not state['pending_events']
    again = run_index_volume(registry, **kw)
    assert again['opening_environment']['status'] == 'not_recomputed'
    assert len(registry.calls) == 4 and len(list(kw['report_root'].rglob('*.json'))) == 1


def test_environment_gap_does_not_block_original_amount_alert_or_retry_it(setup):
    registry, pusher, kw = setup
    prepare(registry, amount='150')
    registry.dirty[CODE] = [r for r in registry.dirty[CODE] if r['trade_time'] != '2026-11-03 15:00']
    result = run_index_volume(registry, **kw)
    assert result['status'] == 'complete' and result['pushed'] and len(result['events']) == 8
    card = result['opening_environment']
    assert card['status'] == 'partial' and card['complete_count'] == 3
    missing = card['items'][0]
    assert missing['status'] == 'source_failed' and 'weak_environment_candidate' not in missing
    assert '10点' not in pusher.messages[0]['content']
    run_index_volume(registry, **kw)
    assert len(registry.calls) == 4 and len(pusher.messages) == 1


def test_failed_source_retry_is_attempt_scoped_and_does_not_erase_prior_receipt(setup):
    registry, pusher, kw = setup
    prepare(registry)
    registry.failures.add(CODE)
    first = run_index_volume(registry, **kw)
    first_bytes = open(first['report_path'], 'rb').read()
    assert first['opening_environment']['status'] == 'partial'
    registry.failures.clear()
    second = run_index_volume(registry, **kw)
    assert len(registry.calls) == 5
    assert second['opening_environment']['complete_count'] == 1
    assert [x['status'] for x in second['opening_environment']['items']].count('not_recomputed') == 3
    assert open(first['report_path'], 'rb').read() == first_bytes
    assert not pusher.messages


def test_dry_run_no_write_and_all_failure_not_empty(setup):
    registry, pusher, kw = setup
    registry.failures = set(INDEX_NAMES)
    result = run_index_volume(registry, dry_run=True, **kw)
    assert result['opening_environment']['status'] == 'source_failed'
    assert result['opening_environment']['complete_count'] == 0
    assert not kw['state_path'].exists() and not kw['report_root'].exists() and not pusher.messages


@pytest.mark.parametrize('hm', ['09:48', '10:18', '10:33', '11:33', '15:03', '21:00'])
def test_other_slots_do_not_produce_ten_oclock_card(setup, hm):
    registry, pusher, kw = setup
    prepare(registry)
    kw['clock'] = lambda: moment(hm)
    result = run_index_volume(registry, dry_run=True, **kw)
    assert 'opening_environment' not in result


def test_expired_fetch_does_not_publish_opening_card(setup):
    registry, pusher, kw = setup
    prepare(registry)
    times = iter([moment('10:03'), moment('10:06')])
    kw['clock'] = lambda: next(times)
    result = run_index_volume(registry, **kw)
    assert result['status'] == 'expired_window' and 'opening_environment' not in result
    assert not kw['report_root'].exists() and not pusher.messages
