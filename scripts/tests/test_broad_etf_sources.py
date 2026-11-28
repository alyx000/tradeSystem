import hashlib
import json

import pandas as pd
import pytest
import requests

from services import broad_etf_sources as s

DAY = '2026-11-25'
CODE = '512100.SH'


def tiny(clock='09:35', **overrides):
    return dict(dict(ts_code=CODE, trade_time=DAY+' '+clock+':00', open=2., close=2.01,
                     high=2.02, low=1.99, vol=12300., amount=24601.), **overrides)


def em(clock='09:35', volume='123', amount='24601'):
    return ','.join((DAY+' '+clock,'2','2.01','2.02','1.99',volume,amount))


def fake_read(monkeypatch, rows):
    monkeypatch.setattr(s, '_read_tushare', lambda code, day: (rows, {'raw_sha256':'test'}))


def test_common_share_units_keep_the_original_unadjusted_values():
    ts, errs = s.decode('tushare', CODE, DAY, [tiny()])
    east, errs2 = s.decode('eastmoney', CODE, DAY, [em()])
    assert not errs and not errs2
    for source, label in ((ts,'份'), (east,'手')):
        row = source['09:35']
        assert (row['volume'],row['volume_unit'],row['amount_yuan']) == (12300,'份',24601.)
        assert row['source_time'] == DAY+'T09:35:00+08:00'
        assert row['raw_volume_unit'] == label
    assert ts['09:35']['raw'] == tiny()
    assert east['09:35']['raw'] == em()


@pytest.mark.parametrize('change',[
    {'vol':True},{'close':float('nan')},{'high':float('inf')},{'low':0},{'open':3.},
    {'amount':-1},{'vol':123.2},{'vol':0},{'amount':0},{'amount':2460100.},
])
def test_bad_numeric_row_does_not_create_an_observation(change):
    assert not s.decode('tushare', CODE, DAY, [tiny(**change)])[0]


def test_bad_row_or_conflicting_duplicate_blocks_its_own_time():
    bars, rejected = s.decode('tushare',CODE,DAY,[tiny(),tiny(),tiny(close=1.99),
        tiny(),tiny('09:40'),tiny('09:45'),tiny('09:45', amount='NaN'),tiny('09:45')])
    assert list(bars) == ['09:40']
    assert len(rejected) == 2


def test_verified_zero_pair_and_daily_session_edges():
    rows = [tiny(t,vol=0,amount=0) for t in ('09:30','11:30','11:35','12:00','13:00','13:05','15:00','15:05')]
    bars, rejected = s.decode('tushare', CODE, DAY, rows)
    assert set(bars) == {'11:30','13:05','15:00'}
    assert bars['15:00']['volume'] == bars['15:00']['amount_yuan'] == 0
    assert len(rejected) == 5


@pytest.mark.parametrize('row', [tiny(ts_code='510300.SH'),tiny(trade_time='2026-11-26 09:35:00'),
                                  tiny(trade_time='2026-11-26 00:00:00+00:00'),tiny(trade_time='-')])
def test_wrong_fund_or_mixed_date_refuses_response(row):
    with pytest.raises(ValueError):
        s.decode('tushare', CODE, DAY, [tiny(),row])


def test_48_distinct_bar_endings_and_empty_or_partial_receipts(monkeypatch):
    times = sorted(s.ENDS)
    assert len(times) == 48 and (times[23],times[24]) == (690,785)
    rows = [tiny(f'{m//60:02d}:{m%60:02d}') for m in times]
    for raw, state in ((rows,'complete'),(rows[1:],'partial'),([],'source_failed')):
        fake_read(monkeypatch, raw)
        receipt = s.fetch_source('tushare',CODE,DAY)
        assert receipt['status'] == state
        assert receipt['received_at'] >= receipt['requested_at']
        assert receipt['raw_sha256'] == 'test' and len(receipt['bars']) == len(raw)


def test_tushare_actual_method_parameters_and_hash(monkeypatch):
    params = {}
    class Client:
        def __init__(self, token, timeout):
            assert token == 'local-test' and 0 < timeout < 15
        def etf_mins(self, **query):
            params.update(query)
            return pd.DataFrame([tiny()])
    monkeypatch.setenv('TUSHARE_TOKEN','local-test')
    monkeypatch.setattr(s,'TushareMirrorClient',Client)
    v = s.fetch_source('tushare',CODE,DAY)
    assert params['freq'] == '5min' and params['ts_code'] == CODE
    assert params['start_date'] == DAY+' 09:30:00' and params['end_date'] == DAY+' 15:00:00'
    assert len(v['raw_sha256'])==64 and 'local-test' not in json.dumps(v)


def test_eastmoney_actual_fields_and_instrument(monkeypatch):
    packet = dict(rc=0,data=dict(code='512100',market=1,name='中证1000ETF',klines=[em()]))
    class Client(requests.Session):
        def get(self,url,**kwargs):
            assert kwargs['params']['secid']=='1.512100'
            assert kwargs['params']['klt']=='5' and kwargs['params']['fqt']=='0'
            assert kwargs['params']['beg'] == kwargs['params']['end'] == '20261125'
            ret=requests.Response()
            ret.status_code=200
            ret._content=json.dumps(packet).encode()
            return ret
    monkeypatch.setattr(requests,'Session',Client)
    v=s.fetch_source('eastmoney',CODE,DAY)
    assert v['raw_sha256']==hashlib.sha256(json.dumps(packet).encode()).hexdigest()
    assert v['bars']['09:35']['close']==2.01
    for invalid in ({'market':0},{'market':1,'code':'510300'}):
        packet['data'].update(invalid)
        assert s.fetch_source('eastmoney',CODE,DAY)['status']=='source_failed'


def test_network_and_permission_are_distinct_and_redacted(monkeypatch):
    def load(*args):
        raise requests.exceptions.ProxyError('http://test-user:test-pass@broken/')
    monkeypatch.setattr(s,'_read_eastmoney',load)
    v=s.fetch_source('eastmoney',CODE,DAY)
    assert '代理' in v['gap'] and 'test-pass' not in str(v)
    class Client:
        def __init__(self,*args,**kwargs):
            pass
        def etf_mins(self,**kwargs):
            raise RuntimeError('本账户无此权限:test-secret')
    monkeypatch.setattr(s,'TushareMirrorClient',Client)
    monkeypatch.setenv('TUSHARE_TOKEN','test-secret')
    v=s.fetch_source('tushare',CODE,DAY)
    assert '权限不足' in v['gap'] and 'test-secret' not in str(v)


def test_cash_benchmark_uses_frozen_code_and_name_without_etf_volume(monkeypatch):
    packet = dict(rc=0, data=dict(code='000852', market=1, name='中证1000',
                                klines=[DAY+' 09:35,6200,6201.2,6203,6198,-,-']))
    class Client(requests.Session):
        def get(self,url,**kwargs):
            p=kwargs['params']
            assert p['secid']=='1.000852' and p['klt']=='5' and p['fqt']=='0'
            response=requests.Response()
            response.status_code=200
            response._content=json.dumps(packet).encode()
            return response
    monkeypatch.setattr(requests,'Session',Client)
    receipt=s.fetch_benchmark('000852.SH', DAY)
    assert receipt['provider']=='eastmoney_index' and receipt['status']=='partial'
    bar=receipt['bars']['09:35']
    assert bar['close']==6201.2 and 'amount_yuan' not in bar and 'volume' not in bar
    assert len(receipt['raw_sha256'])==64 and bar['raw']==packet['data']['klines'][0]
    packet['data']['name']='中证1000ETF南方'
    assert '名称' in s.fetch_benchmark('000852.SH', DAY)['gap']
    packet['data'].update(code='512100',name='中证1000')
    assert '代码' in s.fetch_benchmark('000852.SH', DAY)['gap']
    with pytest.raises(ValueError):
        s.fetch_benchmark('512100.SH', DAY)


def test_index_conflicting_prices_are_quarantined_and_duplicate_prices_accepted():
    rows=[DAY+' 09:35,2000,2001,2003,1998,9,3',DAY+' 09:35,2000,2001,2003,1998,9,3',
          DAY+' 09:40,2000,2001,2003,1998,9,3',DAY+' 09:40,2000,2002,2003,1998,9,3']
    bars,bad=s.decode('eastmoney_index','000300.SH',DAY,rows)
    assert list(bars)==['09:35'] and len(bad)==1
