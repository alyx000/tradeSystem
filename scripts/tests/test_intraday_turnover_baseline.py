from datetime import datetime, timedelta
from hashlib import sha256
import json

import pytest

from services.intraday_events import turnover_baseline as base


DAYS = ['2026-11-20', '2026-11-23', '2026-11-24', '2026-11-25', '2026-11-26']
TODAY = '2026-11-27'
CODE = '009971.SZ'


def snapshot(day, hhmm, quote_lag=0, amount=0, source='sina', **extra):
    at = datetime.fromisoformat(f'{day}T{hhmm}:00+08:00')
    row = dict(amount=amount, quote_at=(at-timedelta(seconds=quote_lag)).isoformat())
    return dict(trade_date=day, captured_at=at.isoformat(), interval_minutes=10,
                context_sha256='context-'+day, source=source, stocks={CODE:row}, **extra)


def archive(root, batch, snap):
    folder = root/snap['trade_date']/batch
    folder.mkdir(parents=True, exist_ok=True)
    report = dict(trade_date=snap['trade_date'], captured_at=snap['captured_at'], input_by='test', snapshot=snap)
    blob = json.dumps(report).encode()
    (folder/'report.json').write_bytes(blob)
    receipt = dict(trade_date=snap['trade_date'], input_by='test', batch=batch, sha256=sha256(blob).hexdigest())
    (folder/'receipt.json').write_text(json.dumps(receipt))


@pytest.fixture
def crossing(tmp_path):
    for i, day in enumerate(DAYS, 1):
        archive(tmp_path, 'a', snapshot(day,'13:52', amount=3000))
        archive(tmp_path, 'b', snapshot(day,'14:02', amount=3000 + i*1000))
    root = tmp_path/TODAY
    root.mkdir()
    # 两批采样间隔 10 分钟，两张逐股报价间隔 9 分钟。
    first, last = snapshot(TODAY,'13:52', amount=1000), snapshot(TODAY,'14:02',60,10000)
    pairs = {(CODE,last['captured_at']): (first,last)}
    return root, pairs


def evaluate(crossing):
    root, pairs = crossing
    return next(iter(base.crossing_turnover_baselines(root, pairs, history_days=DAYS).values()))


def test_uses_same_slot_rates_and_five_open_days(crossing):
    result = evaluate(crossing)
    assert result['ratio'] == pytest.approx(1000/300)
    assert result['valid_days'] == result['expected_days'] == 5
    assert result['current']['amount_yuan'] == 9000
    assert result['current']['quote_seconds'] == 540
    assert [row['date'] for row in result['samples']] == DAYS
    assert [(s['start_batch'],s['end_batch']) for s in result['samples']] == [('a','b')]*5


@pytest.mark.parametrize('bad', ['different_source','context_change','duplicate','slot_gap','failed_middle',
                                 'roll_back','stale','missing_stock','short_cadence','hash','out_of_window'])
def test_history_gaps_are_not_filled_with_zeros(crossing,bad):
    root, _ = crossing
    day = DAYS[2]
    snap = snapshot(day,'14:02', amount=8000)
    if bad == 'different_source': snap['source'] = 'other'
    elif bad == 'context_change': snap['context_sha256']='new freeze'
    elif bad == 'duplicate': archive(root.parent,'b2',snap)
    elif bad == 'slot_gap': snap = snapshot(day,'14:12',amount=8000)
    elif bad == 'failed_middle':
        snap_mid = snapshot(day,'13:57')
        archive(root.parent,'failed',snap_mid)
        p=root.parent/day/'failed'
        r=json.loads((p/'report.json').read_bytes())
        r['snapshot']=None
        blob=json.dumps(r).encode()
        (p/'report.json').write_bytes(blob)
        rec=json.loads((p/'receipt.json').read_text()); rec['sha256']=sha256(blob).hexdigest()
        (p/'receipt.json').write_text(json.dumps(rec))
    elif bad == 'roll_back': snap['stocks'][CODE]['amount']=1
    elif bad == 'stale': snap = snapshot(day,'14:02',91,8000)
    elif bad == 'missing_stock': snap['stocks'].clear()
    elif bad == 'short_cadence': snap['interval_minutes']=5
    elif bad == 'out_of_window':
        snap=snapshot(day,'14:05', amount=8000)
        # The far edge of the end slot cannot cover an immediately preceding 13:49 quote.
        archive(root.parent,'a',snapshot(day,'13:49'))
    archive(root.parent,'b',snap)
    if bad=='hash':
        path=root.parent/day/'b'/'report.json'
        path.write_bytes(path.read_bytes()+b' ')
    result=evaluate(crossing)
    assert result['ratio'] is None and result['valid_days'] == 4
    assert result['median_yuan_per_minute'] is None
    assert result['samples'][2]['reason'] and 'yuan_per_minute' not in result['samples'][2]


def test_zero_is_a_real_amount_but_zero_median_has_no_multiple(crossing):
    root,_=crossing
    for day in DAYS[:3]:
        archive(root.parent,'b',snapshot(day,'14:02',amount=3000))
    result=evaluate(crossing)
    assert result['valid_days']==5 and result['median_yuan_per_minute']==0
    assert result['ratio'] is None
    assert result['samples'][0]['amount_yuan']==0


@pytest.mark.parametrize('bad_quote', [
    None, ['invalid row'], {'amount': 6000}, {'quote_at': f'{DAYS[1]}T14:02:00+08:00'},
    {'quote_at': f'{DAYS[1]}T14:02:00+08:00', 'amount': 'bad'},
], ids=['missing-row', 'non-mapping', 'missing-time', 'missing-amount', 'bad-amount'])
def test_malformed_stock_does_not_discard_other_stock_in_same_window(crossing, bad_quote):
    root, pairs = crossing
    other_code = '009972.SZ'
    for day in DAYS:
        for batch in ('a', 'b'):
            path = root.parent / day / batch / 'report.json'
            snap = json.loads(path.read_bytes())['snapshot']
            good = snap['stocks'][CODE]
            snap['stocks'][other_code] = dict(good)
            if day == DAYS[1] and batch == 'b':
                snap['stocks'][CODE] = bad_quote
            archive(root.parent, batch, snap)

    first, last = next(iter(pairs.values()))
    for snap in (first, last):
        snap['stocks'][other_code] = dict(snap['stocks'][CODE])
    at = last['captured_at']
    pairs[(other_code, at)] = (first, last)
    results = base.crossing_turnover_baselines(root, pairs, history_days=DAYS)
    assert base.crossing_turnover_baselines(root, pairs, history_days=DAYS) == results
    bad = results[(CODE, at)]
    assert bad['ratio'] is None and bad['valid_days'] == 4
    assert bad['samples'][1]['reason'] == '个股缺一端行情'
    healthy = results[(other_code, at)]
    assert healthy['valid_days'] == 5 and healthy['ratio'] == pytest.approx(1000 / 300)


def test_cache_only_reopens_changed_reports(crossing, monkeypatch):
    root,_=crossing
    old=evaluate(crossing)
    reads=[]
    real_read=type(root).read_bytes
    def counted(p):
        if p.name=='report.json':
            reads.append(p)
        return real_read(p)
    monkeypatch.setattr(type(root),'read_bytes',counted)
    assert evaluate(crossing)==old and not reads
    archive(root.parent,'b',snapshot(DAYS[-1],'14:02',amount=2000))
    changed=evaluate(crossing)
    assert changed['ratio'] is None and changed['valid_days']==4
    assert reads==[root.parent/DAYS[-1]/'b'/'report.json']


def test_calendar_unverified_or_crossing_missing(crossing):
    root, pairs=crossing
    x=next(iter(base.crossing_turnover_baselines(root,pairs,db_path=root/'missing.db').values()))
    assert x['ratio'] is None and x['valid_days'] is None and x['samples']==[]
    first,_=next(iter(pairs.values()))
    first['stocks'][CODE]['amount']=999999
    x=evaluate(crossing)
    assert x['ratio'] is None and x['current'] is None


def test_warm_and_cold_caches_reject_changed_receipt(crossing):
    assert evaluate(crossing)['valid_days'] == 5
    root, _ = crossing
    path = root.parent/DAYS[-1]/'b'/'receipt.json'
    receipt = json.loads(path.read_bytes())
    receipt['input_by'] = 'edited'
    path.write_text(json.dumps(receipt))
    warm = evaluate(crossing)
    (root/base._CACHE_NAME).unlink()
    assert evaluate(crossing) == warm
    assert warm['valid_days'] == 4 and warm['ratio'] is None
