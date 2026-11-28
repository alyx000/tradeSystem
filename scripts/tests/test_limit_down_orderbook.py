from datetime import datetime, timedelta
import hashlib
import json

import pytest

from providers.base import DataResult
from providers.registry import ProviderRegistry
from services.intraday_events import limit_down_book as ob, sources
from services.board_break_intraday.data import digest

DAY = '2026-11-27'
PRIOR = '2026-11-26'
AT = datetime.fromisoformat(DAY+'T09:32:07+08:00')
CODE = '600001.SH'
OTHER = '600002.SH'
CTX = dict(trade_date=DAY, previous_date=PRIOR, universe=[CODE, OTHER])
CAL = dict(is_open=True, previous=PRIOR)
REF = dict(trade_date=DAY, previous_date=PRIOR, previous_members={CODE:'甲'}, floors={CODE:9, OTHER:9},
           universe_sha256=digest(sorted(CTX['universe'])), status='complete', selection_basis='live_prior_archive')
BOOK = dict(bid1_price=0.0, bid1_size=0, ask1_price=9.0, ask1_size=317, size_unit='shares', unit_evidence='sina')


def batch(at, price=9, book=None, code=CODE, *, low=9):
    raw = dict(price=price, low=low, name='甲', code=code, quote_date=DAY, quote_time=at.strftime('%H:%M:%S'))
    snapshot = dict(trade_date=DAY, raw_rows=[raw], raw_sha256=digest([raw]), source='sina', context_sha256=digest(CTX),
                    stocks={code:dict(name='甲', price=price, quote_at=at.isoformat())}, interval_minutes=10,
                    captured_at=(at+timedelta(seconds=1)).isoformat())
    report = dict(schema='intraday-events-v1', trade_date=DAY, captured_at=snapshot['captured_at'],
                  input_by='test', context=CTX, snapshot=snapshot)
    receipt = dict(batch=at.strftime('%H%M%S'), trade_date=DAY, input_by='test',
                   sha256=hashlib.sha256(json.dumps(report).encode()).hexdigest())
    return report, receipt, {code:book} if book is not None else {}


def compact(*args, **kwargs):
    report, receipt, books = batch(*args, **kwargs)
    return ob._batch(report, receipt, REF, books)


def summary(rows):
    return ob._summarize(DAY, rows, REF, {}, ['USER'])


@pytest.mark.parametrize('book,state,sell_rows,both', [
    (None, 'missing', 0, 0), (dict(BOOK, bid1_price=None, bid1_size=None), 'queue_unconfirmed', 1, 0),
    (dict(BOOK, ask1_price=None, ask1_size=None), 'missing', 0, 0),
    (dict(BOOK, ask1_price=0., ask1_size=0), 'queue_unconfirmed', 1, 1),
    (BOOK,'sell_queue_at_limit',1,1)])
def test_observed_queue_needs_real_sides_and_units(book,state,sell_rows,both):
    b = compact(AT, book=book)
    assert b['rows'][CODE]['book_state'] == state
    report = summary([b])
    assert report['batches'][0]['rows'][CODE]['sell_queue_amount_yuan'] == (2853 if state == 'sell_queue_at_limit' else None)
    coverage = report['coverage']
    assert coverage['book_rows'] == sell_rows and coverage['both_sides_rows'] == both
    assert coverage['sell_queue_rows'] == (int(state == 'sell_queue_at_limit') if both else None)


def test_price_changes_and_confirmed_queue_changes_are_distinct():
    rows = [compact(AT,9,BOOK), compact(AT+timedelta(minutes=10),9.1,None),
            compact(AT+timedelta(minutes=20),9,BOOK)]
    report = summary(rows)
    assert [e['kind'] for e in report['events']] == ['left_observed_sell_queue','returned_with_observed_sell_queue']
    assert report['events'][0]['start_exclusive'] == AT.isoformat()
    assert report['coverage']['exact_intraday_open_count'] is None
    prices = summary([compact(AT), compact(AT+timedelta(minutes=10),9.1),compact(AT+timedelta(minutes=20))])
    assert [e['kind'] for e in prices['events']] == ['left_limit_price','returned_to_limit_price']


@pytest.mark.parametrize('gap', ['lunch','two_slots','too_late','duplicate','no_stock','src','conflict','failed'])
def test_breaks_block_interval_and_reseal(gap):
    before = compact(AT,9,BOOK)
    end = compact(AT+timedelta(minutes=10),9.1)
    rows = [before, end]
    if gap == 'lunch':
        rows = [compact(AT.replace(hour=11,minute=22),9,BOOK), compact(AT.replace(hour=13,minute=2),9.1)]
    elif gap == 'two_slots': rows[1] = compact(AT+timedelta(minutes=20),9.1)
    elif gap == 'too_late': rows[1] = compact(AT+timedelta(minutes=14),9.1)
    elif gap == 'duplicate': rows.append(dict(end, batch='another'))
    elif gap == 'no_stock': end['rows'] = {}
    elif gap == 'src': end['source'] = 'other'
    elif gap == 'conflict': end['context_sha256'] = 'changed'
    else: end['status'] = 'source_failed'
    assert summary(rows)['events'] == []


def test_first_upper_bound_uses_day_low_actual_floor_and_valid_quote():
    assert not compact(AT,9.3,code=OTHER,low=9.1)['rows']
    touched = compact(AT,9.3,code=OTHER)
    assert summary([touched])['first_observed_low_at_limit'][OTHER] == AT.isoformat()
    assert touched['rows'][OTHER]['at_limit'] is False
    report, receipt, books = batch(AT)
    report['snapshot']['stocks'][CODE]['quote_at'] = (AT-timedelta(minutes=10)).isoformat()
    assert CODE in ob._batch(report,receipt,REF,books)['errors']
    report['snapshot']['raw_sha256'] = 'tampered'
    assert ob._batch(report,receipt,REF,books)['status'] == 'source_failed'


def write_origin(root, report, receipt):
    folder = root / DAY / receipt['batch']
    folder.mkdir(parents=True)
    (folder/'report.json').write_text(json.dumps(report))
    (folder/'receipt.json').write_text(json.dumps(receipt))
    return folder


def test_replay_is_idempotent_preserves_live_book_and_refuses_conflict(tmp_path, monkeypatch):
    root = tmp_path/'out'
    origin = tmp_path/'in'
    report, receipt, books = batch(AT,book=BOOK)
    folder = write_origin(origin, report, receipt)
    monkeypatch.setattr(ob,'_references',lambda *args: REF)
    before = ob.collect(root,None,DAY,CAL,[folder],'USER',books=books,now=AT)
    path = root/(DAY+'.json')
    raw = path.read_bytes()
    ob.collect(root,None,DAY,CAL,[folder],'USER',now=AT,replay=True)
    assert raw == path.read_bytes()
    assert before['coverage']['sell_queue_rows'] == 1
    (folder/'report.json').write_text('{}')
    failed = ob.collect(root,None,DAY,CAL,[folder],'USER',now=AT,replay=True)
    assert failed['source_errors'] and failed['coverage']['sell_queue_rows'] == 1


def test_existing_capture_removes_level_one_from_whole_market_and_preserves_plain_fallback():
    raw = dict(code=CODE,name='甲',price=9,low=9,amount=100,pct_chg=-10,quote_date=DAY,quote_time='09:32:07')
    reg = ProviderRegistry()
    attempts = []
    class Fake:
        priority = 1
        name = 'sina'
        available = True
        def supports(self, capability): return capability == 'get_realtime_quotes'
        def initialize(self): return True
        def get_realtime_quotes(self, codes, include_book=False):
            attempts.append(('sina',include_book))
            return DataResult([dict(raw,book=BOOK)], 'sina') if self.available else DataResult(None,'sina',error='failure')
    class Fallback(Fake):
        name = 'back'
        priority = 2
        def get_realtime_quotes(self, codes):
            attempts.append(('back',False))
            return DataResult([dict(raw)],'back')
    first = Fake()
    reg.register(first)
    reg.register(Fallback())
    buf = {}
    ctx = dict(universe=[CODE])
    res = sources.capture(reg,ctx,AT,books=buf)
    assert attempts == [('sina',True)] and buf == {CODE: BOOK} and res['raw_rows'] == [raw]
    first.available=False
    buf.clear()
    res = sources.capture(reg,ctx,AT,books=buf)
    assert attempts[-2:] == [('sina',True),('back',False)] and buf == {} and res['source'] == 'back'


def test_real_scheduler_hook_observes_persisted_report_after_delivery(tmp_path, monkeypatch):
    from services.intraday_events import service
    from types import SimpleNamespace

    dataset, _, buf = batch(AT,book=BOOK)
    raw_snapshot = dataset['snapshot']
    raw_snapshot['raw_rows'][0]['high'] = 11.
    raw_snapshot['raw_sha256'] = digest(raw_snapshot['raw_rows'])
    ctx = dict(CTX, gaps=[])
    raw_snapshot['context_sha256'] = digest(ctx)
    def capture(registry, context, now, clock, books):
        books.update(buf)
        return raw_snapshot
    order = []
    monkeypatch.setattr(service, 'get_readonly_connection', lambda path: SimpleNamespace(close=lambda:None))
    monkeypatch.setattr(service, 'calendar',lambda conn,day:CAL)
    monkeypatch.setattr(service, 'load_context',lambda *args:ctx)
    monkeypatch.setattr(service, 'capture',capture)
    raw_snapshot['rejected'] = []
    monkeypatch.setattr(service,'analyze',lambda *a,**k:dict(baseline_only=True, sectors=[],feedback=[],
                   following=[],pending_following=[],anchor_watch=[],events=[],rules={'interval_seconds':[420,780]}))
    monkeypatch.setattr(service,'_deliver',lambda folder,payload,clock: order.append('original_delivery'))
    monkeypatch.setattr(service,'_write_level_watch',lambda *a:None)
    monkeypatch.setattr(service,'write_day_summary',lambda *a,**k: tmp_path/'summary')
    monkeypatch.setattr(ob,'_references',lambda *args:REF)
    def observe(*args,**kwargs):
        assert (args[4][0]/'receipt.json').exists()
        order.append('local_book')
        return original(*args,**kwargs)
    original=ob.collect
    monkeypatch.setattr(ob,'collect',observe)
    from services.intraday_events import limit_up_book
    upper = dict(REF, ceilings={CODE:11., OTHER:11.}, source={}, no_daily_limit=[])
    monkeypatch.setattr(limit_up_book, '_references', lambda *args: upper)
    res=service.run(None,input_by='launchd',root=tmp_path/'intraday-events',push=True,interval_minutes=10,
                    scheduled_at=AT.replace(second=0),clock=lambda:AT)
    assert order==['original_delivery','local_book'], res
    assert res['limit_down_orderbook']['coverage']['sell_queue_rows']==1
    assert res['limit_up_orderbook']['coverage']['verified_book_rows']==1
    assert res['limit_up_orderbook']['coverage']['buy_queue_rows']==0
    assert res['status']=='partial'


def test_unlimited_new_listing_is_verified_and_reference_retry_keeps_original(tmp_path, monkeypatch):
    from services.course_core_minutes import _daily_rows
    supplied = DataResult([dict(ts_code=CODE,trade_date=DAY,down_limit=9.,up_limit=11.),
                           dict(ts_code=OTHER,trade_date=DAY,down_limit=0.,up_limit=99999.99)],ob.SOURCE)
    fields = ('up_limit','down_limit')
    assert OTHER not in _daily_rows(supplied,ob.SOURCE,DAY,set(CTX['universe']),fields)
    assert OTHER in _daily_rows(supplied,ob.SOURCE,DAY,set(CTX['universe']),fields,include_unlimited=True)
    monkeypatch.setattr(ob,'_local_limits',lambda *a,**k:{})
    monkeypatch.setattr(ob,'call',lambda *a:(supplied.data,supplied.source))
    monkeypatch.setattr(ob.data,'archive',lambda *a:({'limit_down':{'_source':'tushare:limit_list_d'}},{}))
    monkeypatch.setattr(ob.data,'limit_rows',lambda *a:[dict(code=CODE,name='甲')])
    refs=ob._references(tmp_path,None,DAY,PRIOR,set(CTX['universe']),object(),AT,'USER')
    assert refs['status']=='complete' and refs['no_daily_limit']==[OTHER] and refs['missing_floor_codes']==[]


def test_frozen_previous_member_with_no_floor_keeps_source_and_replays_after_recovery(tmp_path,monkeypatch):
    report, receipt, books = batch(AT,book=BOOK)
    folder = write_origin(tmp_path/'origin',report,receipt)
    root = tmp_path/'out'
    refs = dict(REF, floors={}, status='partial')
    ob._write(root/'references'/(DAY+'.json'),refs)
    ob.stage_books(root,None,folder,report,CAL,books)
    monkeypatch.setattr(ob,'_references',lambda *a:refs)
    ob.collect(root,None,DAY,CAL,[folder],'USER',now=AT,replay=True)
    row=ob._read(root/(DAY+'.json'),DAY)['batches'][0]['without_floor'][CODE]
    assert row['book']['ask1_size']==317 and row['down_limit'] is None and row['sell_queue_amount_yuan'] is None
    refs['floors'][CODE]=9
    assert ob.collect(root,None,DAY,CAL,[folder],'USER',now=AT,replay=True)['coverage']['sell_queue_rows']==1


def test_one_source_failure_does_not_block_other_windows():
    rows=[compact(AT+timedelta(minutes=i*10),9 if i%2==0 else 9.1) for i in range(5)]
    rows[2]['status']='source_failed'
    result=ob._summarize(DAY,rows,REF,{},['USER'])
    assert [(e['start_batch'],e['end_batch']) for e in result['events']]==[
        (rows[0]['batch'],rows[1]['batch']),(rows[3]['batch'],rows[4]['batch'])]


def test_missing_previous_list_still_stages_current_floor_touch_and_replays_book(tmp_path, monkeypatch):
    root = tmp_path/'out'
    report, receipt, books = batch(AT, price=9, low=9, code=OTHER, book=BOOK)
    folder = write_origin(tmp_path/'origin', report, receipt)
    def missing_previous(*args):
        raise ob.data.EvidenceError('前一开放日归档不可读')
    monkeypatch.setattr(ob.data, 'archive', missing_previous)
    monkeypatch.setattr(ob, '_local_limits', lambda *a, **k: {
        code: dict(down_limit=9., up_limit=11., archive='limits.pkl', source_sha256='a'*64,
                   requested_at=AT.isoformat(), source=ob.SOURCE) for code in CTX['universe']})
    refs = ob._references(root, None, DAY, PRIOR, set(CTX['universe']), None, AT, 'USER')
    assert refs['previous_members'] is None and refs['floors'][OTHER] == 9 and refs['status'] == 'partial'
    ob.stage_books(root, None, folder, report, CAL, books)
    early = ob._book_evidence(folder, receipt)
    assert set(early) == {OTHER} and early[OTHER] == dict(book=BOOK, price=9, quote_at=AT.isoformat())
    ob.collect(root, None, DAY, CAL, [folder], 'USER', now=AT, replay=True)
    archived = ob._read(root/(DAY+'.json'), DAY)
    row = archived['batches'][0]['rows'][OTHER]
    assert row['previous_limit_down'] is None and row['low_touched'] and row['book']['ask1_size'] == 317
    assert archived['coverage']['sell_queue_rows'] == 1 and archived['reference_status'] == 'partial'


def test_sell_queue_amount_uses_cents_and_verified_shares():
    report, receipt, books = batch(AT, price=9.37, low=9.37, book=dict(BOOK,ask1_price=9.37))
    refs = dict(REF, floors={CODE:9.37})
    def amount():
        b = ob._batch(report, receipt, refs, books)
        return summary([b])['batches'][0]['rows'][CODE]['sell_queue_amount_yuan']
    assert amount() == 2970.29
    books[CODE]['size_unit'] = 'unknown'
    assert amount() is None
    books[CODE]['size_unit'] = 'shares'
    books[CODE]['bid1_price'] = 9.37
    books[CODE]['bid1_size'] = 100
    assert amount() is None
