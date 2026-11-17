from copy import deepcopy
from datetime import date, timedelta
import json
import sqlite3

import pytest

from services.market_timing import index_repair as r
from services.review_visual_context import visual_context

DAY = '2026-11-17'
CODE = '000001.SH'


def fixture(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS trade_calendar(date TEXT PRIMARY KEY, exchange TEXT, is_open INTEGER)')
    end = date.fromisoformat(DAY)
    calendar = [(end-timedelta(days=i)).isoformat() for i in range(150)]
    conn.executemany('INSERT OR REPLACE INTO trade_calendar(date,exchange,is_open) VALUES (?,"SSE",?)',
                     [(d, int(date.fromisoformat(d).weekday()<5)) for d in calendar])
    dates = sorted(d for d in calendar if date.fromisoformat(d).weekday()<5)[-61:]
    bars = [dict(trade_date=d, open=101., high=102., low=100., close=101.) for d in dates]
    # 缺口100—98：随后部分回补、触及100但收盘仍低于100。
    bars[-3].update(open=97., high=98., low=96., close=97.)
    bars[-2].update(open=98., high=99., low=97., close=98.)
    bars[-1].update(open=98., high=100., low=97., close=99.)
    return dates, bars


def captured():
    with sqlite3.connect(':memory:') as conn:
        dates, bars = fixture(conn)
        return r.capture(conn, bars, DAY, CODE, 'tushare:index_daily')


def report(block=None):
    block = captured() if block is None else block
    rows = []
    for item in r.INDEX_LIST:
        value = deepcopy(block)
        value['index_code'] = item['code']
        if 'evidence' in value:
            value['evidence']['index_code'] = item['code']
        rows.append(dict(name=item['name'], block=value))
    return dict(report_date=DAY, context=visual_context(DAY), rows=rows)


def test_gap_touch_fill_is_distinct_from_close_fill_and_counts_open_days():
    block = captured()
    g = block['gaps'][-1]
    assert block['status']=='complete' and block['unfilled_count']==0
    assert g['state']=='filled' and g['fill_date']==DAY and g['days_to_fill']==2
    assert g['close_fill_date'] is None and not g['current_close_above']
    assert g['elapsed_open_days']==2 and (g['lower'],g['upper'])==(98,100)
    assert block['ma20']==pytest.approx((17*101+97+98+99)/20)
    html, gap = r.render_html(report(block), DAY)
    assert not gap and '首次盘中完全回补2026-11-17' in html and '首次收盘回补尚未' in html


def test_partial_remaining_and_later_close_recross_do_not_reopen_gap():
    b = captured()['evidence']
    b['bars'][-1]['high']=99.5
    g = r.calculate(b,DAY,CODE)['gaps'][-1]
    assert g['state']=='partial' and g['remaining_lower']==99.5
    b['bars'][-2].update(high=101,close=100)
    result = r.calculate(b,DAY,CODE)
    g = result['gaps'][-1]
    assert g['state']=='filled' and g['close_fill_date']==b['open_dates'][-2]
    assert not g['current_close_above'] and result['unfilled_count']==0


def test_touching_formation_boundary_and_opening_gap_are_not_daily_gap():
    b = captured()['evidence']
    b['bars'][-3]['high']=100
    assert r.calculate(b,DAY,CODE)['gaps']==[]


@pytest.mark.parametrize('mode',['missing','duplicate','weekend','wrong_identity','bad_price','nan','bool','bad_ohlc','calendar_gap','closed_target','no_source'])
def test_bad_evidence_is_unknown_not_zero(mode):
    with sqlite3.connect(':memory:') as conn:
        dates,bars=fixture(conn)
        source='tushare:index_daily'
        if mode=='missing': bars.pop(-7)
        if mode=='duplicate': bars.insert(-7,deepcopy(bars[-7]))
        if mode=='weekend': bars[-2]['trade_date']='2026-11-15'
        if mode=='wrong_identity': bars[-1]['ts_code']='399001.SZ'
        if mode=='bad_price': bars[-1]['close']=0
        if mode=='nan': bars[-1]['high']=float('nan')
        if mode=='bool': bars[-1]['close']=True
        if mode=='bad_ohlc': bars[-1]['low']=110
        if mode=='calendar_gap': conn.execute('DELETE FROM trade_calendar WHERE date="2026-11-15"')
        if mode=='closed_target': conn.execute('UPDATE trade_calendar SET is_open=0 WHERE date=?',(DAY,))
        if mode=='no_source': source=''
        block=r.capture(conn,bars,DAY,CODE,source)
        assert block['status']=='source_failed' and 'unfilled_count' not in block


def test_future_and_outside_window_bars_do_not_change_result():
    with sqlite3.connect(':memory:') as conn:
        dates,bars=fixture(conn)
        before=r.capture(conn,bars,DAY,CODE,'tushare:index_daily')
        bars.append(dict(trade_date='2026-11-18',open=1,high=1,low=1,close=1))
        assert before==r.capture(conn,bars,DAY,CODE,'tushare:index_daily')


def test_tampered_derived_fields_and_failure_smuggling_rejected():
    b=captured(); b['unfilled_count']=9
    assert r.validate(b,DAY,CODE)['status']=='source_failed'
    b=r.failed(DAY,CODE,'来源失败'); b['gaps']=[dict(state='filled')]
    assert 'gaps' not in r.validate(b,DAY,CODE)
    assert r.validate(None,DAY,CODE)['status']=='not_computed'


def test_readonly_loader_old_corrupt_calendar_and_closed_day(tmp_path):
    db=tmp_path/'trade.db'
    with sqlite3.connect(db) as c:
        dates,bars=fixture(c)
        c.execute('CREATE TABLE market_timing_signal(trade_date TEXT,index_code TEXT,fractal_json TEXT)')
        b=r.capture(c,bars,DAY,CODE,'tushare:index_daily')
        c.execute('INSERT INTO market_timing_signal VALUES (?,?,?)',(DAY,CODE,json.dumps({r.KEY:b})))
        c.execute('INSERT INTO market_timing_signal VALUES (?,?,?)',(DAY,'399001.SZ','{broken'))
        c.execute('INSERT INTO trade_calendar VALUES ("2026-11-18","SSE",0)')
    before=db.read_bytes()
    p=r.load_for_review(db,'2026-11-18')
    assert p['rows'][0]['block']['status']=='complete'
    assert p['rows'][1]['block']['status']=='source_failed'
    assert p['rows'][2]['block']['status']=='not_computed'
    html,gap=r.render_html(p,'2026-11-18')
    assert '休市展示' in html and f'事实日{DAY}' in html and gap
    assert db.read_bytes()==before
    with sqlite3.connect(db) as c:
        c.execute('DELETE FROM trade_calendar WHERE date="2026-11-15"')
    assert all(x['block']['status']=='source_failed' for x in r.load_for_review(db,DAY)['rows'])


def test_scanner_reuses_requests_persists_evidence_and_dry_run_is_readonly():
    from db.schema import init_schema
    from services.market_timing import scanner, repo, web_payload
    from scripts.tests.test_market_timing_service import FakeRegistry
    with sqlite3.connect(':memory:') as conn:
        init_schema(conn)
        dates,bars=fixture(conn)
        calls=[]
        class Registry(FakeRegistry):
            def _range(self,code,start,end):
                calls.append((code,start,end)); return super()._range(code,start,end)
        reg=Registry({CODE:bars})
        before=conn.total_changes
        result=scanner.run_daily(conn,reg,DAY,indices=[dict(code=CODE,name='上证综指')],dry_run=True)
        assert conn.total_changes==before
        assert isinstance(result['signals'][0]['fractal_json'], str)
        from services.market_timing.detectors import evaluate_fractal_status
        original=evaluate_fractal_status(bars)
        raw=json.loads(result['signals'][0]['fractal_json'])
        expected={k:original[k] for k in ('low_date','low_price','left_high','right_high','right_date')} if original['status']!='none' else {}
        assert {k:v for k,v in raw.items() if k!=r.KEY}==expected
        assert result['signals'][0]['fractal_status']==original['status']
        assert json.loads(result['signals'][0]['fractal_json'])[r.KEY]['status']=='complete'
        assert sum(code==CODE for code,_,_ in calls)==1
        scanner.run_daily(conn,reg,DAY,indices=[dict(code=CODE,name='上证综指')])
        saved=json.loads(repo.list_signals(conn,date=DAY)[0]['fractal_json'])[r.KEY]
        assert r.validate(saved,DAY,CODE)==saved
        assert r.KEY not in json.dumps(web_payload.build_daily_payload(conn,DAY))


@pytest.mark.parametrize('mode',['value','hide','move','delete','different_source'])
def test_official_report_recomputes_and_guards_source(tmp_path,mode):
    from scripts.tests import test_daily_review_html_report as h
    a=h._load_assembler(); h._write_chunks(tmp_path,DAY)
    p=report()
    html=a.render_report(tmp_path,DAY,capacity_trend_db_path=tmp_path/"missing.db",index_repair=p)
    a.validate_report(html,index_repair=p)
    fragment=r.render_html(p,DAY)[0]
    if mode=='value': html=html.replace(fragment,fragment.replace('未完全回补0个','未完全回补9个'))
    if mode=='hide': html=html.replace(fragment,'<div hidden>'+fragment+'</div>')
    if mode=='move': html=html.replace(fragment,'').replace('<section id="s2"',fragment+'<section id="s2"')
    if mode=='delete': html=html.replace(fragment,'')
    if mode=='different_source': p['rows'][0]['block']['unfilled_count']=9
    with pytest.raises(a.ReportValidationError): a.validate_report(html,index_repair=p)


def test_chunk_cannot_inject_gap_block(tmp_path):
    from scripts.tests import test_daily_review_html_report as h
    a=h._load_assembler(); h._write_chunks(tmp_path,DAY)
    path=next(tmp_path.glob('*s1.html'))
    path.write_text(path.read_text()+r.render_html(report(),DAY)[0])
    with pytest.raises(a.ReportValidationError): a.render_report(tmp_path,DAY,capacity_trend_db_path=tmp_path/"missing.db",index_repair=report())
