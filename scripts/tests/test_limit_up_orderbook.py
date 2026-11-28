import hashlib
import json
from datetime import datetime, timedelta

import pytest

from services.board_break_intraday import data
from services.intraday_events import limit_down_book as down, limit_up_book as up


DAY = '2026-11-27'
PREV = '2026-11-26'
AT = datetime.fromisoformat(DAY + 'T09:32:06+08:00')
CODE = '000034.SZ'
OTHER = '600001.SH'
CAL = dict(is_open=True, previous=PREV)
BOOK = dict(bid1_price=21.35, bid1_size=317, ask1_price=0., ask1_size=0,
            size_unit='shares', unit_evidence='新浪实时盘口委托股数')
REF = dict(trade_date=DAY, previous_date=PREV, status='complete', ceilings={CODE:21.35,OTHER:21.35},
           no_daily_limit=[], source={'source_sha256':'a'*64, 'requested_at':AT.isoformat(), 'source':down.SOURCE})
CTX = dict(universe=[CODE, OTHER], trade_date=DAY, previous_date=PREV, frozen_at=AT.isoformat(),
           cores=[dict(code=OTHER, source_date=PREV)], anchors={'stocks':[]})


def origin(folder, at=AT, *, price=21.35, high=21.35, code=CODE, ctx=CTX):
    raw = dict(name='测试', code=code, price=price, high=high, quote_date=DAY, quote_time=at.strftime('%H:%M:%S'))
    snap = dict(trade_date=DAY, captured_at=at.isoformat(), source='sina', interval_minutes=10,
                raw_rows=[raw], raw_sha256=data.digest([raw]), context_sha256=data.digest(ctx),
                stocks={code:dict(name='测试', price=price, quote_at=at.isoformat())})
    report = dict(schema='intraday-events-v1',trade_date=DAY, captured_at=snap['captured_at'],
                  input_by='USER', context=ctx, snapshot=snap)
    path = folder / DAY / at.strftime('%H%M%S')
    path.mkdir(parents=True,exist_ok=True)
    encoded=json.dumps(report).encode()
    rec=dict(batch=path.name,trade_date=DAY,input_by='USER',sha256=hashlib.sha256(encoded).hexdigest())
    (path/'report.json').write_bytes(encoded)
    (path/'receipt.json').write_text(json.dumps(rec))
    return path,report,rec


@pytest.mark.parametrize('book, expected', [
    (BOOK, 6767.95), (None,None), (dict(BOOK,ask1_size=None),None),
    (dict(BOOK,bid1_size=0),None), (dict(BOOK,ask1_price=21.35,ask1_size=10),None),
    (dict(BOOK,size_unit='lots'),None), (dict(BOOK,bid1_size=True),None),
    (dict(BOOK,bid1_price=float('inf')),None), (dict(BOOK,unit_evidence=None),None)])
def test_amount_requires_verified_buy_queue(tmp_path,book,expected):
    _, report, rec = origin(tmp_path)
    batch = up._batch(report,rec,REF,{CODE:book})
    result=up._summarize(DAY,[batch],REF,{},['USER'])
    assert result['batches'][0]['rows'][CODE]['buy_queue_amount_yuan'] == expected


def test_frozen_cores_and_observed_high_select_separately(tmp_path):
    _,report,rec = origin(tmp_path,price=20,high=21)
    assert not up._batch(report,rec,REF,{})['rows']
    _,report,rec = origin(tmp_path,price=20,high=21.35)
    r=up._batch(report,rec,REF,{})['rows'][CODE]
    assert r['high_touched'] and not r['at_limit'] and not r['fixed_reasons']
    _,report,rec = origin(tmp_path,price=20,high=21,code=OTHER)
    assert up._batch(report,rec,REF,{})['rows'][OTHER]['fixed_reasons']==['previous_core']


@pytest.mark.parametrize('case', ['ok','missing_book','lunch','gap','duplicate','source','old_quote','bad_receipt'])
def test_only_comparable_real_endpoint_amounts_get_a_delta(tmp_path,case):
    first = AT.replace(hour=11,minute=22) if case=='lunch' else AT
    second = AT.replace(hour=13, minute=2) if case=='lunch' else first+timedelta(minutes=20 if case=='gap' else 10)
    def sample(at,book):
        _,report,rec=origin(tmp_path,at)
        return up._batch(report,rec,REF,{CODE:book})
    before,after = sample(first,None if case=='missing_book' else BOOK),sample(second,dict(BOOK,bid1_size=417))
    if case=='source': after['source']='unknown'
    if case=='old_quote': after['rows'][CODE]['quote_at']=first.isoformat()
    rows=[before,after,dict(after,batch='dup')] if case=='duplicate' else [before,after]
    errors={'unlocated':'bad'} if case=='bad_receipt' else {}
    changes=up._summarize(DAY,rows,REF,errors,['USER'])['queue_changes']
    assert [e['delta_yuan'] for e in changes] == ([2135.] if case=='ok' else [])


def test_preserves_verified_source_and_rejects_conflicting_replay(tmp_path):
    path,report,rec=origin(tmp_path/'origin')
    root=tmp_path/'limit-up-orderbook'
    limits=dict(REF,universe_sha256=data.digest(sorted(CTX['universe'])),floor_source=REF['source'],unknown=None)
    down._write(up.shared_reference(root,DAY),limits)
    up.stage_books(root,None,path,report,CAL,{CODE:BOOK})
    assert down._book_evidence(path,rec,up.BOOK_FILE)[CODE]['book']==BOOK
    result = up.collect(root,None,DAY,CAL,[path],'USER',now=AT,replay=True)
    daily=root/(DAY+'.json')
    signed=daily.read_bytes()
    assert result['coverage']['buy_queue_rows']==1
    up.collect(root,None,DAY,CAL,[path],'USER',now=AT,replay=True)
    assert signed==daily.read_bytes()
    (path/'report.json').write_text('{}')
    result=up.collect(root,None,DAY,CAL,[path],'USER',now=AT,replay=True)
    assert result['source_errors'] and result['coverage']['buy_queue_rows']==1


def test_only_frozen_symbols_stage_with_missing_limits(tmp_path):
    root=tmp_path/'limit-up-orderbook'
    path,report,rec=origin(tmp_path/'origin', code=OTHER,high=21,price=20)
    up.stage_books(root,None,path,report,CAL,{OTHER:BOOK})
    assert set(down._book_evidence(path,rec,up.BOOK_FILE))=={OTHER}
    result=up.collect(root,None,DAY,CAL,[path],'USER',now=AT,replay=True)
    assert result['status']=='partial'
    row=down._read(root/(DAY+'.json'),DAY)['batches'][0]['without_limit'][OTHER]
    assert row['reason']=='missing_limit' and row['book']==BOOK


def test_shared_limit_response_supplies_both_sides_once(tmp_path, monkeypatch):
    requests = []
    def get_limits(registry, cap, day):
        requests.append((cap, day))
        rows = [dict(ts_code=c, trade_date=DAY, up_limit=21.35, down_limit=17.47) for c in CTX['universe']]
        return rows, down.SOURCE
    monkeypatch.setattr(down, 'call', get_limits)
    monkeypatch.setattr(down.data, 'archive', lambda *a: ({'limit_down':{'_source':'tushare:limit_list_d'}}, {}))
    monkeypatch.setattr(down.data, 'limit_rows', lambda *a: [])
    lower = down._references(tmp_path/'limit-down-orderbook', None, DAY, PREV, set(CTX['universe']), object(), AT,'USER')
    upper = up._references(tmp_path/'limit-up-orderbook', None, DAY, PREV, set(CTX['universe']), None, AT,'USER')
    assert requests == [('get_stock_limit_prices', DAY)]
    assert lower['ceilings'] == upper['ceilings'] == REF['ceilings']
    assert lower['floor_source'] == upper['source'] and lower['floors'][CODE] == 17.47
