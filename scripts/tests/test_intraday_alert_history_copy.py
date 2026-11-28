from datetime import datetime, timedelta
from hashlib import sha256
import json

import pytest

from services.intraday_events.service import load_anchor_history


CODE = '689971.SH'
UP = f'anchor:{CODE}:up'
DOWN = f'anchor:{CODE}:down'
OPEN = datetime.fromisoformat('2025-08-15T09:32:00+08:00')


def archive_batch(root, minutes, price, *, direction='up', status='accepted', change_report=None,
                  change_receipt=None, change_delivery=None):
    at = OPEN + timedelta(minutes=minutes)
    day = at.date().isoformat()
    folder = root / day / f'batch{minutes}'
    folder.mkdir(parents=True)
    row = dict(code=CODE, name='样本丁股', key=f'anchor:{CODE}:{direction}', price=price, direction=direction,
               interval_pct=3.0 if direction == 'up' else -3.0, pct_chg=minutes / 10)
    stock = dict(price=price, quote_at=at.isoformat())
    report = dict(trade_date=day, input_by='codex_automation', captured_at=at.isoformat(),
                  push_requested=True, context={'anchors': {'stocks': [{'code': CODE}]}},
                  snapshot=dict(trade_date=day, captured_at=at.isoformat(), interval_minutes=10,
                                stocks={CODE: stock}), analysis={'alert': {'anchors': [row]}})
    if change_report:
        change_report(report)
    raw = json.dumps(report).encode()
    (folder / 'report.json').write_bytes(raw)
    receipt = dict(trade_date=day, batch=folder.name, sha256=sha256(raw).hexdigest(),
                   input_by='codex_automation')
    if change_receipt:
        receipt.update(change_receipt)
    (folder / 'receipt.json').write_text(json.dumps(receipt))
    # 旧格式只含名称、代码、区间与当日涨跌，提醒价由同批原始行情提供。
    delivery = dict(trade_date=day, batch=folder.name, report_sha256=receipt['sha256'],
                    status=status, recorded_at=(at+timedelta(seconds=1)).isoformat(),
                    input_by='codex_automation', content=f'**样本丁股（{CODE}）**　区间 {row["interval_pct"]:+.2f}%')
    if change_delivery:
        delivery.update(change_delivery)
    (folder / 'delivery.json').write_text(json.dumps(delivery))


def history(root, minutes):
    return load_anchor_history(root / OPEN.date().isoformat(), OPEN + timedelta(minutes=minutes), {CODE})


def test_old_confirmed_alert_and_newer_possible_deliveries_are_kept_separate(tmp_path):
    archive_batch(tmp_path, 30, 106)
    archive_batch(tmp_path, 10, 101)
    archive_batch(tmp_path, 20, 98, direction='down')
    archive_batch(tmp_path, 40, 111, status='sending')
    archive_batch(tmp_path, 50, 115, status='unconfirmed')
    archive_batch(tmp_path, 60, 119)
    state = history(tmp_path, 60)
    assert state['last_accepted'] == {
        UP: {'captured_at': (OPEN+timedelta(minutes=30)).isoformat(), 'price': 106},
        DOWN: {'captured_at': (OPEN+timedelta(minutes=20)).isoformat(), 'price': 98},
    }
    assert state['recent_codes'] == {CODE} and state['last_pct'][UP] == 5
    assert state['ranges'][CODE] == (98, 115)


def test_historical_replay_uses_only_receipts_already_returned(tmp_path):
    archive_batch(tmp_path, 10, 101)
    archive_batch(tmp_path, 20, 105, change_delivery={'recorded_at': (OPEN+timedelta(minutes=34)).isoformat()})
    assert history(tmp_path, 30)['last_accepted'][UP]['price'] == 101
    assert history(tmp_path, 40)['last_accepted'][UP]['price'] == 105


@pytest.mark.parametrize('change', [
    {'change_delivery': {'content': f'另一只（{CODE}）'}},
    {'change_delivery': {'content': f'**另一家公司样本丁股（{CODE}）**'}},
    {'change_delivery': {'input_by': 'other'}},
    {'change_delivery': {'recorded_at': 'not-a-time'}},
    {'change_delivery': {'recorded_at': OPEN.isoformat()}},
    {'change_receipt': {'input_by': 'other'}},
    {'change_receipt': {'scheduled_at': OPEN.isoformat()}},
    {'change_report': lambda r: r.update(push_requested=False)},
    {'change_report': lambda r: r['analysis']['alert']['anchors'][0].update(price=799)},
    {'change_report': lambda r: r['snapshot']['stocks'][CODE].update(quote_at=OPEN.isoformat())},
    {'change_report': lambda r: r['analysis']['alert']['anchors'][0].update(direction='down')},
])
def test_bad_latest_accepted_evidence_disables_price_copy_only(tmp_path, change):
    archive_batch(tmp_path, 10, 101)
    archive_batch(tmp_path, 20, 106, **change)
    state = history(tmp_path, 30)
    assert state['last_accepted'] == {}
    assert state['last_pct'] == {UP: 2.0} and state['recent_codes'] == {CODE}
