from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
import json
import sqlite3
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
import yaml
from analyzers import market_strength as m
from analyzers import market_strength_report as r

DAY='2026-10-30'


def sources(day=DAY,n=100):
    q=[];identities=[];mapping={}
    for i in range(n):
        code=f'{600000+i:06}.SH';pct=[5.,-5.,4.9999,-4.9999,0.][i%5]
        q.append(dict(ts_code=code,trade_date=day,close=100+pct,pre_close=100.,pct_chg=pct))
        identities.append(dict(ts_code=code,name=f'测试{i}',list_date='20200101',delist_date=None))
        mapping[code]={'sw_l2':'半导体' if i%2 else '电力'}
    return q,identities,[dict(ts_code='000099.SZ',trade_date=day)],mapping


def block(day=DAY,n=100):
    return m.calculate(*sources(day,n),day,min_count=1)


def db():
    conn=sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE trade_calendar(exchange TEXT,date TEXT,is_open INTEGER)')
    conn.execute('CREATE TABLE daily_market(date TEXT, raw_data TEXT)')
    days=[]
    for i in range(70):
        d=(date.fromisoformat(DAY)-timedelta(days=69-i));open_=int(d.weekday()<5)
        conn.execute('INSERT INTO trade_calendar VALUES(?,?,?)',('SSE',d.isoformat(),open_))
        if open_:days.append(d.isoformat())
    return conn,days[-21:]


def put(conn,day,value):
    conn.execute('INSERT INTO daily_market VALUES(?,?)',(day,json.dumps({'date':day,'raw_data':{m.KEY:value}})))


def test_inclusive_thresholds_and_denominator():
    v=block(n=5)
    assert v['status']=='complete'
    assert v['market']==dict(sample_count=5,up_count=1,down_count=1,up_pct=20.,down_pct=20.)
    assert sum(s['sample_count'] for s in v['sectors'].values())==5
    assert m.view(v,DAY)==v


def test_excludes_st_b_delisted_but_includes_first_day():
    q,b,st,sw=sources()
    b[0]['name']='*ST测试';st.append(dict(ts_code=b[1]['ts_code'],trade_date=DAY))
    b[2]['name']='退市测试';b[3]['delist_date']=DAY;b[4]['list_date']=DAY
    b[5]['ts_code']=q[5]['ts_code']='200001.SZ'
    v=m.calculate(q,b,st,sw,DAY,min_count=1)
    codes={x['code'] for x in v['rows']}
    assert v['market']['sample_count']==95 and b[4]['ts_code'] in codes
    assert not codes.intersection(x['ts_code'] for x in b[:4]+[b[5]])


@pytest.mark.parametrize('kind',['quote_duplicate','identity_duplicate','date','identity_missing','st_empty','nan_majority','bad_coverage','industry_duplicate'])
def test_bad_sources_fail_without_zero(kind):
    q,b,st,sw=sources()
    if kind=='quote_duplicate':q.append(q[0])
    if kind=='identity_duplicate':b.append(b[0])
    if kind=='date':q[0]['trade_date']='2026-10-29'
    if kind=='identity_missing':b.pop()
    if kind=='st_empty':st=[]
    if kind=='nan_majority':
        for x in q:x['pct_chg']=float('nan')
    if kind=='bad_coverage':q=q[:97]
    if kind=='industry_duplicate':sw['600000']={'sw_l2':'重复'}
    v=m.calculate(q,b,st,sw,DAY,min_count=1)
    assert v['status']=='source_failed' and v['market'] is None and v['rows']==[]


def test_missing_quote_and_mapping_are_partial_not_zero():
    q,b,st,sw=sources();q.pop();sw.pop(q[0]['ts_code'])
    v=m.calculate(q,b,st,sw,DAY,min_count=1)
    assert v['status']=='partial' and v['market']['sample_count']==99
    assert v['coverage']['unclassified_count']==1
    assert v['sectors']['未分类']['sample_count']==1
    assert m.view(v,DAY)['status']=='partial'
    v=m.calculate(q,b,st,None,DAY,min_count=1)
    assert v['market']['up_count']==20 and set(v['sectors'])=={'未分类'}


@pytest.mark.parametrize('field,value',[('market',{}),('rows',[]),('coverage',{}),('gaps','bad'),('status','ok')])
def test_archive_tamper_is_rejected(field,value):
    v=block();v[field]=value
    assert m.view(v,DAY)['status']=='source_failed'


def test_strict_history_weighting_and_missing_day_breaks_delta():
    conn,days=db()
    for i,d in enumerate(days):
        if i==19:continue
        v=block(d,100 if i%2 else 50)
        if i==18:
            v['rows'][0]['industry']='未分类';v['market'],v['sectors']=m._aggregate(v['rows'])
            v['coverage']['unclassified_count']=1;v['status']='partial';v['gaps']=['未分类']
        put(conn,d,v)
    h=m.history(conn,DAY)
    assert len(h['points'])==20 and h['status']=='partial'
    assert h['points'][-2]['status']=='missing-data'
    assert h['points'][-1]['delta'] is None
    assert h['windows']['5']['valid_days']==4 and h['windows']['5']['complete_days']==3
    assert h['windows']['5']['market']['stock_days']==250
    assert h['windows']['5']['market']['up_pct']==20.
    c=m.card_context(h,'半导体');assert m.valid_context(c,DAY)
    assert c['windows']['5']['valid_days']==4
    bad=deepcopy(c);bad['windows']['5']['complete_days']=True
    assert not m.valid_context(bad,DAY)
    assert m.card_context(h,'不存在')['stats'] is None
    conn.close()


def test_empty_history_is_unknown_and_current_batch_overrides_archive(tmp_path):
    conn,days=db();h=m.history(conn,DAY)
    assert h['windows']['5']['market']['up_count'] is None
    put(conn,DAY,block());h=m.history(conn,DAY,current=m.failed(DAY,'本批失败'))
    assert h['current']['status']=='source_failed' and h['points'][-1]['market'] is None
    folder=tmp_path/DAY;folder.mkdir();(folder/'post-market.yaml').write_text(yaml.safe_dump(dict(date=DAY,raw_data={m.KEY:block()})))
    h=m.history(conn,DAY,daily_dir=tmp_path)
    assert h['current']['status']=='complete' and h['windows']['5']['valid_days']==1
    conn.execute('DELETE FROM trade_calendar')
    h=m.history(conn,DAY,daily_dir=tmp_path)
    assert h['status']=='source_failed' and h['current']['market']['up_count']==20
    conn.close()


def test_calendar_gap_never_fills_with_older_rows():
    conn,days=db();conn.execute('DELETE FROM trade_calendar WHERE date=?',(days[-2],))
    h=m.history(conn,DAY,current=block())
    assert h['points']==[] and h['windows']=={} and h['current']['status']=='complete'
    conn.close()


@pytest.mark.parametrize('time,day,expected',[('15:59:00',DAY,'skipped'),('16:00:00','2026-10-31','source_failed')])
def test_collect_time_guard_avoids_identity_fetch(monkeypatch,time,day,expected):
    from db import connection as connection_module
    conn,_=db();monkeypatch.setattr(connection_module,'get_readonly_connection',lambda path:conn)
    registry=SimpleNamespace(call=lambda *a:pytest.fail('guard must precede provider'))
    now=datetime.fromisoformat(DAY+'T'+time).replace(tzinfo=ZoneInfo('Asia/Shanghai'))
    v=m.collect(registry,day,quote_result=None,stock_st_result=None,now=now)
    assert v['status']==expected


def test_collect_reuses_same_batch_quotes_and_real_gate(monkeypatch):
    from db import connection as connection_module
    conn,_=db();monkeypatch.setattr(connection_module,'get_readonly_connection',lambda path:conn)
    q,b,st,sw=sources(n=4000);calls=[]
    def result(x):return SimpleNamespace(success=True,data=x,source='test',fetched_at='2026-10-30T20:00:00')
    def call(name,*a):
        calls.append(name);return result(b if name=='get_stock_universe_as_of' else sw)
    v=m.collect(SimpleNamespace(call=call),DAY,quote_result=result(q),stock_st_result=result(st),now=datetime(2026,10,30,20,tzinfo=ZoneInfo('Asia/Shanghai')))
    assert v['status']=='complete' and v['market']['sample_count']==4000
    assert calls==['get_stock_universe_as_of','get_stock_sw_industry_map']


def test_card_and_render_keep_missing_unknown_escape_and_inclusive_text():
    conn,_=db();h=m.history(conn,DAY,current=block());md=r.render_markdown(h);html,gap=r.render_html(h,DAY)
    assert '≥5%' in md and '≤-5%' in md and '20只' in md and '4.9999' not in md
    assert '近20开放日' in html and '未计算' in html and gap
    c=m.card_context(h,'不存在');assert c['status']=='not_computed' and m.valid_context(c,DAY)
    c['stats']={'sample_count':2,'up_count':2,'down_count':2,'up_pct':100.,'down_pct':100.}
    assert not m.valid_context(c,DAY)
    conn.close()


def test_formal_source_proof_and_standard_write(tmp_path,monkeypatch):
    import importlib.util
    def fixture(name):
        spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(name+".py"))
        mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod
    from services.volume_concentration import capacity_trend as ct
    monkeypatch.setattr(ct,'read_snapshot',lambda day,*args:ct.from_record(None,day))
    f=fixture('test_daily_review_html_report');a=f._load_assembler();d=f.DATE
    conn,_=db();h=m.history(conn,d,current=block(d));conn.close();f._write_chunks(tmp_path,d)
    html=a.render_report(tmp_path,d,market_strength_report=h)
    kwargs=dict(market_strength_report=h)
    a.validate_report(html,**kwargs)
    for changed in (html.replace('每日广度','错误广度'),html.replace('</article>',r.render_html(h,d)[0]+'</article>')):
        with pytest.raises(a.ReportValidationError):a.validate_report(changed,**kwargs)
    with pytest.raises(a.ReportValidationError):a.validate_report(html)
    # Existing runtime variants require their additional official sidecar; isolate only matching arguments.
    import inspect
    write_kwargs=dict(kwargs,capacity_manifest=f._capacity_manifest(),new_high_manifest=f._new_high_manifest())
    if 'index_intraday_manifest' in inspect.signature(a.write_report).parameters:
        write_kwargs['index_intraday_manifest']=f._index_intraday_manifest()
    # Rendering without sidecars is valid only in generic validation; formal write needs a render with them.
    render_kwargs={k:v for k,v in write_kwargs.items() if k in inspect.signature(a.render_report).parameters}
    html=a.render_report(tmp_path,d,**render_kwargs)
    a.write_report(html,tmp_path/'final.html',**write_kwargs)


def test_collector_shares_full_quote_and_st_results_without_extra_quote_request(monkeypatch):
    from unittest.mock import MagicMock
    from providers.base import DataResult
    from collectors.market import MarketCollector
    quotes=DataResult(data=sources()[0],source='test:quotes')
    st=DataResult(data=[{'ts_code':f'{i:06}.SZ'} for i in range(150)],source='test:st')
    registry=MagicMock()
    registry.call.side_effect=lambda name,*a,**kw:quotes if name=='get_market_daily_quotes' else st if name=='get_stock_st' else DataResult(data=None,source='test',error='skip')
    low=MagicMock(return_value={'status':'complete','low_price':{}})
    strength=MagicMock(return_value=block())
    monkeypatch.setattr('analyzers.low_price_effect.collect_low_price_effect',low)
    monkeypatch.setattr(m,'collect',strength)
    monkeypatch.setattr('analyzers.ipo_effect.collect_ipo_effect',lambda *a,**kw:{'status':'source_failed'})
    monkeypatch.setattr('analyzers.StyleAnalyzer.analyze',lambda *a,**kw:{})
    monkeypatch.setattr('analyzers.NodeSignalAnalyzer.analyze',lambda *a,**kw:[])
    monkeypatch.setattr('analyzers.board_break_feedback.collect_board_break_feedback',lambda *a,**kw:{'status':'source_failed'})
    collector=MarketCollector(registry)
    collector._compute_index_ma=MagicMock();collector._collect_research_coverage=MagicMock(return_value=[])
    collector._rhythm_analyzer=MagicMock();collector._rhythm_analyzer.load_main_theme_names.return_value=[];collector._rhythm_analyzer.analyze.return_value=[]
    result=collector.collect_post_market(DAY)
    assert result[m.KEY]['market']['up_count']==20
    assert low.call_args.kwargs['quote_result'] is strength.call_args.kwargs['quote_result'] is quotes
    assert strength.call_args.kwargs['stock_st_result'] is st and len(st.data)==150
    assert sum(call.args[0]=='get_market_daily_quotes' for call in registry.call.call_args_list)==1


def test_card_marks_sparse_history_and_partial_previous_comparison():
    conn,days=db()
    for d in days:
        v=block(d)
        if d==days[-2]:
            v['status']='partial';v['gaps']=['行业覆盖不完整']
        put(conn,d,v)
    h=m.history(conn,DAY);c=m.card_context(h,'半导体')
    assert c['status']=='partial' and c['comparison_status']=='partial' and c['delta'] is not None
    assert c['windows']['5']['complete_days']==4
    conn.execute('DELETE FROM daily_market WHERE date != ?',(DAY,))
    c=m.card_context(m.history(conn,DAY),'半导体')
    assert c['status']=='partial' and c['windows']['20']['valid_days']==1
    conn.execute('DELETE FROM trade_calendar')
    c=m.card_context(m.history(conn,DAY),'半导体')
    assert c['status']=='partial' and c['stats'] and not c['windows']
    c=m.card_context(m.history(conn,DAY,current=m.failed(DAY,'失败')),'半导体')
    assert c['status']=='source_failed' and c['stats'] is None
    conn.close()


@pytest.mark.parametrize('source',['identity','industry','quotes'])
def test_failure_metadata_survives_collection_and_readback(monkeypatch,source):
    from db import connection as connection_module
    conn,_=db();monkeypatch.setattr(connection_module,'get_readonly_connection',lambda path:conn)
    q,b,st,sw=sources(n=4000)
    def good(data):return SimpleNamespace(success=True,data=data,source='test',fetched_at='captured',error=None)
    bad=SimpleNamespace(success=False,data=None,source='tushare:stock_basic',fetched_at='captured',error='quota_exhausted')
    def call(name,*a):
        if name=='get_stock_universe_as_of':return bad if source=='identity' else good(b)
        return bad if source=='industry' else good(sw)
    v=m.collect(SimpleNamespace(call=call),DAY,quote_result=bad if source=='quotes' else good(q),stock_st_result=good(st),now=datetime(2026,10,30,20,tzinfo=ZoneInfo('Asia/Shanghai')))
    assert v['status']==('partial' if source=='industry' else 'source_failed')
    saved=m.view(v,DAY)
    assert saved['sources'][source]['error']=='quota_exhausted'
    assert saved['sources'][source]['fetched_at']=='captured'
    assert saved['sources'][source]['status']=='source_failed'
    if source!='industry':assert saved['market'] is None


def test_existing_report_and_dual_write_preserve_breadth(tmp_path,monkeypatch):
    from generators.report import ReportGenerator
    from db.connection import get_db
    from db.migrate import migrate
    from db.dual_write import sync_daily_market_to_db
    path=tmp_path/'facts.db'
    with get_db(path) as conn:migrate(conn)
    # Use the existing entrypoints against a temporary database, never a business database.
    monkeypatch.setattr('db.connection._DEFAULT_DB_PATH',path)
    gen=ReportGenerator();gen.daily_dir=tmp_path/'daily'
    value=block();raw={m.KEY:value,'date':DAY}
    md,output=gen.generate_post_market(DAY,raw)
    envelope=yaml.safe_load(Path(output).read_text())
    assert envelope['raw_data'][m.KEY]==value and '≥5% 20只' in md
    assert sync_daily_market_to_db(DAY,envelope,db_path=path)
    with get_db(path) as conn:
        saved=m._db_block(conn,DAY)
    assert saved==value and saved['rows']==value['rows']
