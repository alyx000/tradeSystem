"""新浪分钟原值与消费端完整窗口；禁止真网、填补、日线替代。"""
import json
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from providers.sina_provider import SinaProvider, SINA_MINUTE_URL
from providers.base import DataProvider, DataResult
from services.board_break_intraday import data

DAY='2026-11-12'
CODE='600715.SH'


def bars(day=DAY,cutoff='09:40'):
    return [dict(day=t,open='3.0',high='3.1',low='2.9',close='3.0',volume='100',amount='300')
            for t in data.expected_minutes(day,cutoff)]


class Session:
    def __init__(self,text,status=200):self.text,self.status,self.calls=text,status,[]
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def get(self,url,**kwargs):
        self.calls.append((url,kwargs))
        return SimpleNamespace(text=self.text,status_code=self.status)


def provider(monkeypatch,rows=None,text=None,status=200):
    p=SinaProvider({});p.initialize()
    session=Session(text if text is not None else '/* harmless callback comment */\n=('+json.dumps(rows)+');',status)
    monkeypatch.setattr(p,'_make_session',lambda:session)
    return p,session


def test_minute_request_identity_units_and_date(monkeypatch):
    p,s=provider(monkeypatch,bars('2026-11-11')+bars())
    r=p.get_stock_minutes(CODE,DAY)
    assert r.success and len(r.data)==10
    assert r.data[0]['vol']==100 and r.data[0]['amount']==300
    assert all(row['ts_code']==CODE and row['trade_time'].startswith(DAY) for row in r.data)
    url,kw=s.calls[0]
    assert url==SINA_MINUTE_URL and kw['allow_redirects'] is False and kw['timeout']==10
    assert kw['params']==dict(symbol='sh600715',scale='1',ma='no',datalen='1970')
    assert len(data.normalize_minutes(r.data,CODE,DAY,'09:40')['rows'])==10


@pytest.mark.parametrize('bad',['missing_amount','duplicate','nonfinite','negative','ohlc','boolean','stamp','empty','wrong_day','truncated','bad_identity'])
def test_reject_bad_minute_source(monkeypatch,bad):
    rows=bars()
    if bad=='missing_amount':rows[0].pop('amount')
    if bad=='duplicate':rows.append(rows[0])
    if bad=='nonfinite':rows[0]['volume']='nan'
    if bad=='negative':rows[0]['amount']='-1'
    if bad=='ohlc':rows[0]['high']='1'
    if bad=='boolean':rows[0]['open']=True
    if bad=='stamp':rows[0]['day']=DAY+' 09:31:03'
    if bad=='empty':rows=[]
    if bad=='wrong_day':rows=bars('2026-11-11')
    if bad=='truncated':rows=bars()*198
    p,s=provider(monkeypatch,rows)
    result=p.get_stock_minutes('600715,sz000001.SH' if bad=='bad_identity' else CODE,DAY)
    assert not result.success and result.data is None
    if bad=='bad_identity':assert not s.calls


@pytest.mark.parametrize('text,status', [('=(null);',200),('callback([]);',200),('=([]);',302),('not json',200)])
def test_wrapper_or_redirect_failure(monkeypatch,text,status):
    p,_=provider(monkeypatch,text=text,status=status)
    assert not p.get_stock_minutes(CODE,DAY).success


def fake_source(monkeypatch,rows):
    p,_=provider(monkeypatch,rows)
    source=object.__new__(data.MarketSource);source.sina=p
    source._minute_now=lambda:datetime.fromisoformat(DAY+'T09:40:01').replace(tzinfo=data.TZ)
    calls=[]
    def query(*a,**kw):calls.append(1);raise RuntimeError('您的权限不足')
    source.provider=SimpleNamespace(pro=SimpleNamespace(query=query))
    return source,calls


def test_fallback_complete_path_keeps_primary_failure_audit(monkeypatch):
    source,calls=fake_source(monkeypatch,bars(cutoff='14:50'))
    for cutoff in ('09:40','14:50'):
        r=source.minutes(CODE,DAY,cutoff,'rehearsal')
        assert r['source'].startswith('sina:') and r['attempts'][0]['status']=='source_failed'
        assert len(r['rows'])==(10 if cutoff=='09:40' else 230)
        assert r['rows'][-1]['time']==DAY+' '+cutoff+':00'
        assert r['adjust']=='none' and r['raw_sha256']
    assert len(calls)==1  # 镜像权限失败本次只探一次；新浪每个目标单独核验。


def test_fallback_never_fills_missing_minutes(monkeypatch):
    source,_=fake_source(monkeypatch,bars()[1:])
    with pytest.raises(data.EvidenceError,match='分钟覆盖9/10'):
        source.minutes(CODE,DAY,'09:40','rehearsal')


def test_tail_cutoff_exclusion_uses_sina_bar_without_requesting_more_sources(monkeypatch):
    source, _ = fake_source(monkeypatch, bars(cutoff='14:50')[1:])
    source._minute_now = lambda: datetime.fromisoformat(DAY+'T14:50:01').replace(tzinfo=data.TZ)
    source.tdx = SimpleNamespace(get_stock_minutes=lambda *a, **k: pytest.fail('已按截止价排除'))
    result = source.minutes(CODE, DAY, '14:50', 'live', cutoff_exclusion=lambda price: '涨停')
    assert result['status'] == 'cutoff_exclusion'
    assert result['verified_minutes'] == 229 and result['expected'] == 230
    assert result['cutoff_bar']['time'] == DAY + ' 14:50:00'
    assert result['source'].startswith('sina:') and result['raw_sha256']
    assert 'rows' not in result


def test_primary_valid_does_not_fetch_fallback(monkeypatch):
    source,_=fake_source(monkeypatch,bars())
    expected=dict(rows=[],status='complete')
    monkeypatch.setattr(source,'_tushare_minutes',lambda *a:expected)
    monkeypatch.setattr(source.sina,'get_stock_minutes',lambda *a:pytest.fail('主源通过时不额外请求'))
    assert source.minutes(CODE,DAY,'09:40','live') is expected


def test_stale_realtime_fallback_rejected(monkeypatch):
    source,_=fake_source(monkeypatch,bars('2026-11-11'))
    with pytest.raises(data.EvidenceError):source.minutes(CODE,DAY,'09:40','live')


def test_fallback_failure_does_not_claim_empty(monkeypatch):
    source,_=fake_source(monkeypatch,[])
    with pytest.raises(data.EvidenceError,match='权限不足.*新浪分钟'):
        source.minutes(CODE,DAY,'09:40','rehearsal')


def tdx_bars(day=DAY):
    return [dict(datetime=r['day'][:-3],open=3,high=3.1,low=2.9,close=3,vol=100,amount=300) for r in bars(day)]


def test_tdx_single_node_source_units_and_symbol(monkeypatch):
    import pytdx.hq
    from providers.tdx_provider import TdxProvider
    calls=[]
    class API:
        def connect(self,host,port,**kw):calls.append(('connect',host));return host=='good'
        def get_security_bars(self,*a):calls.append(a);return tdx_bars()
        def disconnect(self):pass
    monkeypatch.setattr(pytdx.hq,'TdxHq_API',API)
    p=TdxProvider(dict(servers=[('bad',7709),('good',7709)]))
    r=p.get_stock_minutes(CODE,DAY)
    assert r.success and len(r.data)==10 and r.data[0]['vol']==100
    assert (8,1,'600715',0,800) in calls
    assert len(data.normalize_minutes(r.data,CODE,DAY,'09:40')['rows'])==10
    calls.clear();assert p.get_stock_minutes(CODE,DAY).success
    assert calls[0]==('connect','good')


@pytest.mark.parametrize('kind',['empty','duplicate','stale','missing_amount','bad_price'])
def test_tdx_bad_response_rejected(monkeypatch,kind):
    import pytdx.hq
    from providers.tdx_provider import TdxProvider
    rows=tdx_bars()
    if kind=='empty':rows=[]
    if kind=='duplicate':rows.append(rows[0])
    if kind=='stale':rows=tdx_bars('2026-11-11')
    if kind=='missing_amount':rows[0].pop('amount')
    if kind=='bad_price':rows[0]['open']=float('nan')
    class API:
        def connect(self,*a,**kw):return True
        def get_security_bars(self,*a):return rows
        def disconnect(self):pass
    monkeypatch.setattr(pytdx.hq,'TdxHq_API',API)
    p=TdxProvider(dict(servers=[('one',7709)]))
    assert not p.get_stock_minutes(CODE,DAY).success


def test_final_fallback_uses_whole_tdx_window_not_patch(monkeypatch):
    source,_=fake_source(monkeypatch,bars()[1:])
    complete=[dict(ts_code=CODE,trade_time=b['day'],open=4,high=4,low=4,close=4,vol=200,amount=800) for b in bars()]
    source.tdx=SimpleNamespace(get_stock_minutes=lambda *a,**kw:DataResult(complete,'tdx:get_security_bars:1min'))
    r=source.minutes(CODE,DAY,'09:40','rehearsal')
    assert r['source'].startswith('tdx:') and len(r['attempts'])==2
    assert all(b['close']==4 and b['vol']==200 for b in r['rows'])


def test_all_sources_failed_stay_failed(monkeypatch):
    source,_=fake_source(monkeypatch,bars()[1:])
    source.tdx=SimpleNamespace(get_stock_minutes=lambda *a,**kw:DataResult(None,'tdx:minutes',error='missing'))
    with pytest.raises(data.EvidenceError,match='tdx:minutes'):
        source.minutes(CODE,DAY,'09:40','rehearsal')


@pytest.mark.parametrize('second_complete', [False, True])
def test_tdx_falls_back_from_incomplete_preferred_node(monkeypatch, second_complete):
    import pytdx.hq
    from providers.tdx_provider import TdxProvider

    calls = []

    class API:
        def connect(self, host, port, **kwargs):
            self.host = host
            calls.append(host)
            return True

        def get_security_bars(self, *args):
            rows = tdx_bars()
            if self.host == 'stale':
                return rows[:-1]
            return rows if second_complete else rows[1:]

        def disconnect(self):
            pass

    monkeypatch.setattr(pytdx.hq, 'TdxHq_API', API)
    source, _ = fake_source(monkeypatch, [])
    source.tdx = TdxProvider(dict(servers=[('fresh', 7709), ('stale', 7709)]))
    source.tdx._minute_server = ('stale', 7709)
    if second_complete:
        result = source.minutes(CODE, DAY, '09:40', 'rehearsal')
        assert result['expected'] == 10 and '节点fresh:7709' in result['source_note']
        assert 'stale:7709 分钟覆盖9/10' in result['source_note']
        assert source.tdx._minute_server == ('fresh', 7709)
    else:
        with pytest.raises(data.EvidenceError, match='stale:7709.*fresh:7709'):
            source.minutes(CODE, DAY, '09:40', 'rehearsal')
    assert calls == ['stale', 'fresh']


@pytest.mark.parametrize('mixed',[False,True])
def test_tdx_protocol_zero_decoded_and_audited(monkeypatch,mixed):
    import pytdx.hq
    from pytdx.helper import get_volume
    from providers.tdx_provider import TdxProvider
    rows=tdx_bars()
    for i,row in enumerate(rows):
        if not mixed or i>0:row.update(vol=get_volume(0),amount=get_volume(0))
    class API:
        def connect(self,*a,**kw):return True
        def get_security_bars(self,*a):return rows
        def disconnect(self):pass
    monkeypatch.setattr(pytdx.hq,'TdxHq_API',API)
    source,_=fake_source(monkeypatch,[])
    source.tdx=TdxProvider(dict(servers=[('one',7709)]))
    r=source.minutes(CODE,DAY,'09:40','rehearsal')
    assert sum(b['vol'] for b in r['rows'])==(100 if mixed else 0)
    assert sum(b['amount'] for b in r['rows'])==(300 if mixed else 0)
    corrections=r['source_corrections']
    assert len(corrections)==(18 if mixed else 20)
    assert all(c['raw_value']==get_volume(0) and c['normalized_value']==0 for c in corrections)


def test_tdx_other_sub_share_volume_is_invalid(monkeypatch):
    import pytdx.hq
    from providers.tdx_provider import TdxProvider
    rows=tdx_bars();rows[0]['vol']=0.5
    class API:
        def connect(self,*a,**kw):return True
        def get_security_bars(self,*a):return rows
        def disconnect(self):pass
    monkeypatch.setattr(pytdx.hq,'TdxHq_API',API)
    assert not TdxProvider(dict(servers=[('one',7709)])).get_stock_minutes(CODE,DAY).success


def retry_source(monkeypatch, responses, offset=1):
    source,primary_calls=fake_source(monkeypatch,[])
    clock=[datetime.fromisoformat(DAY+'T09:40:00').replace(tzinfo=data.TZ)+timedelta(seconds=offset)]
    monkeypatch.setattr(source,'_minute_now',lambda:clock[0])
    monkeypatch.setattr(data.time,'sleep',lambda seconds:clock.__setitem__(0,clock[0]+timedelta(seconds=seconds)))
    calls=[]
    def fetch(*a):
        calls.append(clock[0]);return responses[min(len(calls)-1,len(responses)-1)]
    source.sina=SimpleNamespace(get_stock_minutes=fetch)
    return source,calls,primary_calls,clock


def response(rows):
    return DataResult([dict(ts_code=CODE,trade_time=r['day'],open=float(r['open']),high=float(r['high']),
                           low=float(r['low']),close=float(r['close']),vol=float(r['volume']),amount=float(r['amount']))
                       for r in rows],'sina:minutes')


def test_live_retry_uses_one_complete_response_with_audit(monkeypatch):
    full=bars()
    for b in full:b.update(open='4',high='4',low='4',close='4',volume='200',amount='800')
    source,calls,primary,_=retry_source(monkeypatch,[response(bars()[1:]),response(full)])
    r=source.minutes(CODE,DAY,'09:40','live')
    assert len(calls)==2 and len(primary)==1 and r['attempt']==2
    assert all(b['close']==4 and b['vol']==200 for b in r['rows'])
    assert r['attempts'][-1]['attempt']==1 and r['attempts'][-1]['raw_sha256']


@pytest.mark.parametrize('expire_on_call', [1, 3])
def test_live_retry_stops_before_next_tdx_node_outside_window(monkeypatch, expire_on_call):
    import pytdx.hq
    from providers.tdx_provider import TdxProvider

    source, _, _, clock = retry_source(monkeypatch, [response(bars()[1:])])
    calls = []

    class API:
        def connect(self, host, port, **kwargs):
            calls.append(host)
            return True

        def get_security_bars(self, *args):
            if len(calls) == expire_on_call:
                clock[0] += timedelta(seconds=data.LIVE_COLLECTION_GRACE_SECONDS)
            return tdx_bars()[:-1]

        def disconnect(self):
            pass

    monkeypatch.setattr(pytdx.hq, 'TdxHq_API', API)
    source.tdx = TdxProvider(dict(servers=[('first', 7709), ('second', 7709)]))
    with pytest.raises(data.EvidenceError, match='实时采集窗口已结束'):
        source.minutes(CODE, DAY, '09:40', 'live')
    assert calls == ['first', 'second', 'first'][:expire_on_call]


def test_complementary_partial_responses_never_join(monkeypatch):
    source,calls,_,_=retry_source(monkeypatch,[response(bars()[1:]),response(bars()[:-1])])
    with pytest.raises(data.EvidenceError) as error:source.minutes(CODE,DAY,'09:40','live')
    assert len(calls)==2 and len(error.value.attempts)==3
    assert all(a['status']=='source_failed' for a in error.value.attempts)


@pytest.mark.parametrize('mode,offset',[('rehearsal',1),('live',-1),('live',179),('live',181),('live',86400)])
def test_retry_only_inside_original_live_window(monkeypatch,mode,offset):
    source,calls,_,_=retry_source(monkeypatch,[response(bars()[1:])],offset)
    with pytest.raises(data.EvidenceError):source.minutes(CODE,DAY,'09:40',mode)
    assert len(calls)==(1 if mode=='rehearsal' or 0<=offset<=180 else 0)


def test_retry_rechecks_clock_after_sleep(monkeypatch):
    source,calls,_,clock=retry_source(monkeypatch,[response(bars()[1:])])
    monkeypatch.setattr(data.time,'sleep',lambda s:clock.__setitem__(0,clock[0]+timedelta(seconds=181)))
    with pytest.raises(data.EvidenceError):source.minutes(CODE,DAY,'09:40','live')
    assert len(calls)==1


def test_illegal_midday_stamp_cannot_replace_missing_bar():
    rows=response(bars(cutoff='14:50')).data
    next(r for r in rows if r['trade_time'].endswith('11:30:00'))['trade_time']=DAY+' 13:00:00'
    with pytest.raises(data.EvidenceError,match=r'非交易时段分钟.*13:00:00.*229/230.*11:30:00'):
        data.normalize_minutes(rows,CODE,DAY,'14:50')


@pytest.mark.parametrize('prior_nan', [False, True])
def test_tdx_rejected_node_keeps_the_time_and_window_gap(monkeypatch, prior_nan):
    from pytdx.hq import TdxHq_API
    from providers.tdx_provider import TdxProvider
    raw = tdx_bars()
    raw[0]['datetime'] = DAY+' 09:29'
    if prior_nan:
        raw.insert(0, dict(raw[0], datetime='2026-11-11 09:31', vol=float('nan')))
    monkeypatch.setattr(TdxHq_API, 'connect', lambda *a, **kw: True)
    monkeypatch.setattr(TdxHq_API, 'disconnect', lambda *a: None)
    monkeypatch.setattr(TdxHq_API, 'get_security_bars', lambda *a: raw)
    provider = TdxProvider(dict(servers=[('node',7709)]))
    result = provider.get_stock_minutes(CODE, DAY, validate=lambda rows: data.normalize_minutes(rows,CODE,DAY,'09:40'))
    assert not result.success and '09:29:00' in result.error and '9/10' in result.error and '09:31:00' in result.error
    assert ('SHA256=不可用' if prior_nan else 'SHA256='+data.digest(raw)) in result.error


def test_tdx_connection_cannot_start_a_query_after_cutoff_grace(monkeypatch):
    from pytdx.hq import TdxHq_API
    from providers.tdx_provider import TdxProvider
    monkeypatch.setattr(TdxHq_API, 'connect', lambda *a, **kw: True)
    monkeypatch.setattr(TdxHq_API, 'disconnect', lambda *a: None)
    monkeypatch.setattr(TdxHq_API, 'get_security_bars', lambda *a: pytest.fail('窗口外发起查询'))
    checks = iter([True, False])
    result = TdxProvider(dict(servers=[('node',7709)])).get_stock_minutes(CODE, DAY, can_request=lambda: next(checks))
    assert not result.success and '实时采集窗口已结束' in result.error


def test_only_explicit_realtime_diagnostic_can_start_outside_window(monkeypatch):
    source, calls = fake_source(monkeypatch, bars())
    source._minute_now = lambda: datetime.fromisoformat(DAY+'T15:30:00').replace(tzinfo=data.TZ)
    with pytest.raises(data.EvidenceError, match='不在正式采集窗口'):
        source.minutes(CODE, DAY, '09:40', 'live')
    assert not calls
    assert source.minutes(CODE, DAY, '09:40', 'live', realtime_diagnostic=True)['expected']==10


def test_unhashable_raw_response_cannot_become_success(monkeypatch):
    raw=response(bars())
    # 截止后的行虽不参加计算，仍必须能核验整个原响应，不能成功却丢失原始哈希。
    raw.data.append(dict(raw.data[-1],trade_time=DAY+' 09:41:00',close=float('nan')))
    source,_,_,_=retry_source(monkeypatch,[raw])
    with pytest.raises(data.EvidenceError,match='原始响应哈希校验失败'):
        source.minutes(CODE,DAY,'09:40','rehearsal')


@pytest.mark.parametrize('fault', [None, 'identity', 'market', 'date', 'duplicate', 'missing',
                                   'invalid_price', 'invalid_amount', 'window_expired'])
def test_eastmoney_whole_window_after_rejected_tdx(monkeypatch, fault):
    from services.board_break_intraday import eastmoney

    # 新浪与通达信均缺行，东方财富独立提供整窗，包括源明确给出的零成交分钟。
    source, _ = fake_source(monkeypatch, bars()[1:])
    clock = datetime.fromisoformat(DAY+'T09:40:01').replace(tzinfo=data.TZ)
    source._minute_now = lambda: clock
    def rejected(*args, **kwargs):
        nonlocal clock
        if fault == 'window_expired':
            clock += timedelta(seconds=181)
        return DataResult(None, 'tdx:minutes', error='非交易时段分钟')
    source.tdx = SimpleNamespace(get_stock_minutes=rejected)
    source.eastmoney = eastmoney
    stamps = data.expected_minutes(DAY, '09:40')
    values = [t[:16]+',4,4,4,4,2,800,0' for t in stamps]
    values[0] = stamps[0][:16]+',4,4,4,4,0,0,0'
    if fault == 'date':
        values[0] = values[0].replace(DAY, '2026-11-11')
    elif fault == 'duplicate':
        values.append(values[0])
    elif fault == 'missing':
        values.pop()
    elif fault == 'invalid_price':
        values[0] = values[0].replace(',4,4,4,4,', ',4,4,1,4,')
    elif fault == 'invalid_amount':
        values[0] = values[0].replace(',0,0,0', ',0,-1,0')
    payload = dict(rc=0, data=dict(code='000711' if fault=='identity' else CODE[:6],
                                   market=0 if fault=='market' else 1, klines=values))
    def get(url, *, params, timeout, allow_redirects):
        assert fault != 'window_expired'
        assert params['klt']==1 and params['fqt']==0 and params['secid']=='1.'+CODE[:6]
        assert params['beg']==params['end']==DAY.replace('-', '') and not allow_redirects
        return SimpleNamespace(status_code=200, json=lambda:payload, content=json.dumps(payload).encode())
    monkeypatch.setattr(eastmoney.requests, 'get', get)
    if fault:
        with pytest.raises(data.EvidenceError):
            source.minutes(CODE, DAY, '09:40', 'live' if fault=='window_expired' else 'rehearsal')
    else:
        result = source.minutes(CODE, DAY, '09:40', 'rehearsal')
        assert result['source']==eastmoney.SOURCE and len(result['rows'])==10
        assert result['rows'][0]['vol']==result['rows'][0]['amount']==0
        assert result['rows'][1]['vol']==200 and all(row['close']==4 for row in result['rows'])
        assert result['attempts'][-1]['raw_sha256'] is None
