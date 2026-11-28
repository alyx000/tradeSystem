from datetime import datetime
from io import BytesIO
import json
import sqlite3
from zipfile import ZipFile

import pytest
import requests

from services import domestic_index_futures as s
from services import domestic_index_futures_sources as src

DAY='2026-11-27'
D='20261127'
NOW=datetime(2026,11,28,10,tzinfo=s.TZ)
CONTRACTS=tuple(p+m+'.CFX' for m in ('2612','2701','2703','2706') for p in ('IF','IM'))


def fake_transport(api, params):
    if api=='fut_basic':
        return [dict(ts_code=k,symbol=k[:-4],exchange='CFFEX',fut_code=k[:2],list_date='20261015',delist_date='20'+k[2:6]+'18') for k in CONTRACTS],{}
    if api=='fut_daily':
        return [dict(ts_code=k,trade_date=D,open=101,high=107,low=100,close=105,settle=102,pre_settle=100,oi=300,oi_chg=-7,vol=1000) for k in CONTRACTS],{}
    if api=='fut_holding':
        return [dict(symbol=params['symbol'],trade_date=D,exchange='CFFEX',broker='会员'+str(i),
                     long_hld=60+i,long_chg=i-4,short_hld=100-i,short_chg=19-i) for i in range(20)],{}
    raise AssertionError(api)


@pytest.fixture
def run(tmp_path,monkeypatch):
    monkeypatch.setattr(s,'calendar',lambda *a:{'is_open':True})
    def spot(_conn,day):
        return dict(indices={key:dict(_source='tushare:index_daily',code=code,trade_date=day,close=103) for key,code in s.PRODUCTS.values()}),dict(date=day)
    monkeypatch.setattr(s,'archive',spot)
    return lambda **kw:s.collect(DAY,'USER',root=tmp_path,connect=lambda _:sqlite3.connect(':memory:'),
                               clock=kw.pop('clock',lambda:NOW),**kw)


def test_pipeline_day_and_idempotence(run,tmp_path):
    calls=[]
    def loader(api,params):
        calls.append((api,params))
        return fake_transport(api,params)
    out=run(transport=loader)
    assert out['status']=='complete'
    assert len(calls)==10
    for code in CONTRACTS:
        p=out['products'][code[:2]]
        assert p['basis_points'][code]==2 and p['spot']
        assert p['bars'][code]['data']['settle']==102
        assert p['members'][code]['long']['会员0']['data']['change']==-4
        assert p['coverage']['disclosed_rows'][code]==dict(long=20,short=20)
    before=(tmp_path/(DAY+'.json')).read_bytes()
    again=run(transport=lambda *a:pytest.fail('complete 日禁止重复抓取'))
    assert again['sha256']==out['sha256'] and (tmp_path/(DAY+'.json')).read_bytes()==before


def test_partial_no_gap_filling_and_later_recovery(run):
    def limited(api,params):
        if api=='fut_holding':
            return fake_transport(api,params)[0][:2], {}
        if api=='cffex_members':
            raise requests.ConnectionError()
        return fake_transport(api,params)
    partial=run(transport=limited)
    assert partial['status']=='partial' and partial['products']['IM']['coverage']['contract_member_books']==0
    def failed(api,params):
        raise requests.exceptions.ProxyError()
    retry=run(transport=failed)
    assert retry['products']['IM']['members']==partial['products']['IM']['members']
    assert retry['products']['IF']['basis_points']==partial['products']['IF']['basis_points']
    assert retry['attempts'][-1]['receipts']
    assert any(r.get('error')=='代理连接失败' for r in retry['receipts'].values())
    assert run(transport=fake_transport)['status']=='complete'


def test_holidays_before_close_no_write(run,tmp_path,monkeypatch):
    def bad(*a): pytest.fail('不可访问来源')
    assert run(transport=bad,clock=lambda:datetime(2026,11,27,15,59,tzinfo=s.TZ))['status']=='skipped'
    monkeypatch.setattr(s,'calendar',lambda *a:{'is_open':False})
    assert run(transport=bad)['status']=='skipped'
    assert not (tmp_path/(DAY+'.json')).exists()


@pytest.mark.parametrize('field,value',[('trade_date','20261128'),('symbol','IF'),('exchange','DCE'),
                                         ('broker','合计'),('long_hld',-1)])
def test_reject_product_totals_dates_exchange_and_bad_quantities(field,value):
    row=fake_transport('fut_holding',dict(symbol='IF2612'))[0][0]
    row[field]=value
    with pytest.raises(ValueError):
        s.member_book([row],D,CONTRACTS[0])


def test_missing_member_side_and_zero_distinguished():
    row=dict(symbol='IF2612',trade_date=D,exchange='CFFEX',broker='甲',short_hld=0,short_chg=0,long_hld=None,long_chg=None)
    book=s.member_book([row],D,CONTRACTS[0])
    assert book=={'long':{},'short':{'甲':{'position':0,'change':0}}}


def test_no_synthetic_complete_from_multiple_partial_responses():
    records={}
    for i in (0,10):
        rows=fake_transport('fut_holding',dict(symbol='IF2612'))[0][i:i+10]
        s._merge_members(records,CONTRACTS[0],s.member_book(rows,D,CONTRACTS[0]),str(i),[])
    assert len(records[CONTRACTS[0]]['long'])==20
    assert not s._member_complete(records[CONTRACTS[0]])


def test_fut_basic_date_and_actual_contract():
    rows=fake_transport('fut_basic',{})[0]
    rows.append(dict(rows[0],ts_code='IF2609.CFX',symbol='IF2609',list_date='20260410',delist_date='20260918'))
    rows.append(dict(rows[0],ts_code='IF.CFX',symbol='IF'))
    assert set(s.listings(rows,D,'IF'))=={code for code in CONTRACTS if code.startswith('IF')}
    with pytest.raises(ValueError):
        s.listings([rows[0],rows[0]],D,'IF')


def test_empty_or_halted_bars_are_not_baselines(run):
    def source(api,params):
        rows,meta=fake_transport(api,params)
        if api=='fut_daily':
            rows[0]['vol']=0
        return rows,meta
    result=run(transport=source)
    assert result['status']=='partial'
    assert result['products']['IF']['basis_points'][CONTRACTS[0]] is None


def test_one_listed_contract_does_not_prove_whole_product(run):
    def limited(api, params):
        rows,meta=fake_transport(api,params)
        return (rows[:2] if api in ('fut_daily','fut_basic') else rows),meta
    out = run(transport=limited)
    assert out['status']=='partial'
    assert out['products']['IF']['coverage']['listed_contracts']==1
    assert not out['products']['IF']['coverage']['usual_four_months_verified']


def test_month_zip_date_member_and_checksums():
    text='合约代码,今开盘,最高价,最低价,今收盘,今结算,前结算,成交量,持仓量,持仓变化\nIF2612,31,35,30,32,33,31,80,45,2\n'
    output=BytesIO()
    with ZipFile(output,'w') as z:
        z.writestr('20261127_1.csv',text.encode('gb18030'))
    rows=src.cffex_month(output.getvalue(),D)
    assert s.daily_bar(rows[0],D,CONTRACTS[0])['oi_chg']==2
    with pytest.raises(ValueError):src.cffex_month(output.getvalue(),'20261125')
    corrupt=bytearray(output.getvalue())
    i=corrupt.find(b'IF2612')
    corrupt[i]^=1
    receipt,_=s._attempt('cffex_month',{'trade_date':D},lambda *_:(src.cffex_month(bytes(corrupt), D),{}),lambda:NOW)
    assert receipt['status']=='source_failed'
    xml=f'<data><dailydata><tradingday>{D}</tradingday><instrumentid>IF2612</instrumentid><volume>80</volume><openinterest>45</openinterest><closeprice>32</closeprice></dailydata></data>'.encode()
    assert src.checked_daily(text.encode('gb18030'),xml,D)==rows
    with pytest.raises(ValueError):src.checked_daily(text.encode('gb18030'),xml.replace(b'45',b'47'),D)


def test_official_double_header_independent_members():
    body=('交易日,合约,排名,成交量排名,,,持买单量排名,,,持卖单量排名,,\n'
          ',,,会员简称,成交量,比上一交易日增减,会员简称,持买单量,比上一交易日增减,会员简称,持卖单量,比上一交易日增减\n'
          '20261127,IF2612,1,丙,9,-1,甲,37,2,乙,48,-5\n').encode('gb18030')
    rows=src.cffex_members(body,D,'IF')
    book=s.member_book(rows,D,CONTRACTS[0])
    assert set(book['long'])=={'甲'} and set(book['short'])=={'乙'}
    assert book['short']['乙']['change']==-5
    with pytest.raises(ValueError):src.cffex_members(body,'20261125','IF')


def test_conflicting_or_corrupt_saved_archive_is_preserved(run,tmp_path):
    run(transport=fake_transport)
    f=tmp_path/(DAY+'.json')
    obj=json.loads(f.read_text()); obj['date']='2026-11-16'
    text=json.dumps(obj)
    f.write_text(text)
    with pytest.raises(ValueError):run(transport=fake_transport)
    assert f.read_text()==text
