import json
from datetime import datetime, timedelta

import pytest

from services.intraday_events.alert_followup import collect_followup_batch, render_alert_followup

DAY = '2026-11-27'
START = datetime.fromisoformat(DAY + 'T10:02:05+08:00')
CODE = '009971.SZ'


def make_batch(root, when, price, *, previous=None, first_price=None, delivery_status='accepted',
               source='sina', quote_at=None, cadence=10, missing_stock=False, alter=None,
               prepend_alert=None):
    folder = root / DAY / when.strftime('%H%M%S')
    folder.mkdir(parents=True, exist_ok=True)
    receipt = dict(trade_date=DAY, batch=folder.name, input_by='launchd', sha256='bound')
    direction = 'up' if first_price is None or price > first_price else 'down'
    item = dict(name='样本甲股', code=CODE, direction=direction, price=price,
                interval_pct=(price / first_price - 1) * 100 if first_price else None)
    alert = dict(anchors=[item] if first_price is not None else [], window_start_at=previous)
    if prepend_alert is not None:
        alert['anchors'].insert(0, dict(item, **prepend_alert))
    stocks = {} if missing_stock else {CODE: dict(price=price, quote_at=(quote_at or when).isoformat())}
    report = dict(trade_date=DAY, captured_at=when.isoformat(), push_requested=True, input_by='launchd',
                  context={'anchors': {'stocks': [{'code': CODE}]}}, analysis={'alert': alert},
                  snapshot=dict(trade_date=DAY, captured_at=when.isoformat(), source=source,
                                context_sha256='ctx', interval_minutes=cadence, stocks=stocks))
    delivery = dict(trade_date=DAY, batch=folder.name, input_by='launchd', report_sha256='bound',
                    status=delivery_status, content='**样本甲股（009971.SZ）**')
    if alter:
        delivery.update(alter)
    (folder / 'delivery.json').write_text(json.dumps(delivery))
    return collect_followup_batch(folder, report, receipt)


def run_three(root, first_price, trigger_price, next_price, **next_kwargs):
    prior = make_batch(root, START, first_price, delivery_status='quiet')
    trigger = make_batch(root, START + timedelta(minutes=10), trigger_price,
                         first_price=first_price, previous=prior['captured_at'])
    after = make_batch(root, START + timedelta(minutes=20), next_price,
                       previous=trigger['captured_at'], delivery_status='quiet', **next_kwargs)
    return '\n'.join(render_alert_followup([after, prior, trigger], now=START + timedelta(minutes=20)))


@pytest.mark.parametrize('trigger,following,expected', [
    (105, 106, '延续'), (105, 103, '部分回吐'), (105, 100, '完全回吐'), (105, 99, '完全回吐'),
    (105, 105, '持平'), (95, 94, '延续'), (95, 98, '部分回吐'), (95, 101, '完全回吐'),
])
def test_outcomes_use_original_interval_in_both_directions(tmp_path, trigger, following, expected):
    text = run_three(tmp_path, 100, trigger, following)
    assert '共核实 1 条' in text and f'{expected} 1。' in text
    assert '[10:12](101205/delivery.json)' in text and '[10:22](102205/report.md)' in text
    assert f'{(following / trigger - 1) * 100:+.2f}%' in text


@pytest.mark.parametrize('changes', [dict(source='other'), dict(cadence=5), dict(missing_stock=True),
                                     dict(quote_at=START), dict(quote_at=START+timedelta(minutes=21))])
def test_changed_source_cadence_and_bad_quotes_are_incomparable(tmp_path, changes):
    assert '不可比 1。' in run_three(tmp_path, 100, 105, 106, **changes)


@pytest.mark.parametrize('delivery_change', [
    {'batch': 'foreign'}, {'input_by': 'other'}, {'trade_date': '2026-11-26'},
    {'report_sha256': 'wrong'}, {'content': '其他个股'}, {'status': 'unconfirmed'}, {'status': 'sending'},
])
def test_invalid_or_unconfirmed_delivery_never_counts(tmp_path, delivery_change):
    prior = make_batch(tmp_path, START, 100, delivery_status='quiet')
    trigger = make_batch(tmp_path, START + timedelta(minutes=10), 105, first_price=100,
                         previous=prior['captured_at'], alter=delivery_change)
    assert not trigger['alerts'] and trigger['delivery_gap']
    assert '覆盖不完整' in '\n'.join(render_alert_followup([prior, trigger], now=START))


@pytest.mark.parametrize('bad', [
    {'interval_pct': None}, {'interval_pct': '2.50'}, {'interval_pct': float('nan')},
    {'interval_pct': 10**500}, {'price': None}, {'price': '105'}, {'price': True},
    {'code': [CODE]}, {'name': {'name': '样本甲股'}}, {'direction': ['up']},
    {'direction': 'down'}, {'name': ' '},
])
def test_bad_accepted_alert_item_does_not_lose_valid_neighbor_or_break_formatting(tmp_path, bad):
    prior = make_batch(tmp_path, START, 100, delivery_status='quiet')
    when = START + timedelta(minutes=10)
    trigger = make_batch(tmp_path, when, 105, first_price=100,
                         previous=prior['captured_at'], prepend_alert=bad)
    assert trigger['delivery_gap'] and len(trigger['alerts']) == 1
    text = '\n'.join(render_alert_followup([prior, trigger], now=when))
    assert '共核实 1 条。待下一批 1' in text and '个股提醒字段不完整' in text


def test_pending_expires_at_actual_limit_and_preserves_counts(tmp_path):
    prior = make_batch(tmp_path, START, 100, delivery_status='quiet')
    at = START + timedelta(minutes=10)
    trigger = make_batch(tmp_path, at, 105, first_price=100, previous=prior['captured_at'])
    assert '待下一批 1' in '\n'.join(render_alert_followup([prior, trigger], now=at+timedelta(minutes=13)))
    assert '下一批归档缺失' in '\n'.join(render_alert_followup(
        [prior, trigger], now=at+timedelta(minutes=13, seconds=1)))


@pytest.mark.parametrize('minutes', [70, 280])  # 11:22 / 14:52 的上一批
def test_session_final_slot_never_waits_for_lunch_or_tomorrow(tmp_path, minutes):
    base = START + timedelta(minutes=minutes)
    prior = make_batch(tmp_path, base, 100, delivery_status='quiet')
    at = base + timedelta(minutes=10)
    trigger = make_batch(tmp_path, at, 105, first_price=100, previous=prior['captured_at'])
    assert '当半场末批' in '\n'.join(render_alert_followup([prior, trigger], now=at))


def test_never_skips_next_attempt_or_crosses_missing_archive(tmp_path):
    prior = make_batch(tmp_path, START, 100, delivery_status='quiet')
    trigger = make_batch(tmp_path, START + timedelta(minutes=10), 105,
                         previous=prior['captured_at'], first_price=100)
    invalid = dict(trigger, batch='failed', captured_at=(START+timedelta(minutes=16)).isoformat(),
                   previous_at=None, interval_minutes=None, quotes={}, source=None, alerts=[])
    after = make_batch(tmp_path, START + timedelta(minutes=20), 110,
                       previous=invalid['captured_at'], delivery_status='quiet')
    for rows in [[prior, trigger, invalid, after], [prior, trigger, after]]:
        assert '不可比 1' in '\n'.join(render_alert_followup(rows, now=START+timedelta(minutes=24)))


def test_actual_interval_must_also_link_consecutive_scheduled_slots(tmp_path):
    # 09:56、10:06、10:17分别映射到09:52、10:02、10:22。后一段仅过11分钟，却漏了10:12槽。
    start = START.replace(hour=9, minute=56)
    before = make_batch(tmp_path, start, 100, delivery_status='quiet')
    fired = make_batch(tmp_path, start + timedelta(minutes=10), 105, first_price=100,
                       previous=before['captured_at'])
    end = start + timedelta(minutes=21)
    after = make_batch(tmp_path, end, 110, previous=fired['captured_at'], delivery_status='quiet')
    text = '\n'.join(render_alert_followup([before, fired, after], now=end))
    assert '不可比 1' in text and '下一批断档' in text
