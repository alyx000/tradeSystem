from copy import deepcopy
from datetime import date, timedelta
import importlib.util
from pathlib import Path

import pytest
from services.emotion_leader import wave_evidence as w
from services.emotion_leader import metrics


def sample():
    start=date(2026,9,1)
    bars=[]
    for i in range(30):
        bars.append(dict(trade_date=(start+timedelta(days=i)).isoformat(),open=10.,close=10.1,high=10.2,low=9.9,vol=100.))
    bars[-4].update(open=12.,close=13.,high=14.,low=11.)
    bars[-3].update(open=13.,close=12.,high=13.1,low=11.8)
    bars[-2].update(open=12.,close=11.5,high=12.1,low=11.3)
    bars[-1].update(open=11.4,close=13.2,high=13.5,low=11.3,vol=200.)
    factors=[dict(trade_date=b['trade_date'],adj_factor=1.) for b in bars]
    return bars,factors,bars[20]['trade_date'],bars[-1]['trade_date']


def test_short_engulf_ma_and_volume_use_distinct_facts():
    b,f,launch,day=sample();v=w.calculate(b,f,launch,day);x=v['facts']
    assert v['status']=='complete' and v['interpretation']=='待人工定性'
    assert x['interval_bars']==2 and x['short_engulf'] is True
    assert x['pullback_pct']==pytest.approx((11.3/14-1)*100)
    assert x['volume_ratios']=={'5':2.,'13':2.}
    assert x['ma20_position']=='上方' and x['held_ma20'] is True
    assert x['touched_prior_peak'] is False
    assert 'wave_label' not in v


def test_qfq_ohlc_is_equivalent_after_split_but_raw_volume_not_comparable():
    b,f,l,d=sample();expected=w.calculate(b,f,l,d)
    for row in b[:-1]:
        for k in ('open','high','low','close'):row[k]*=2
    f[-1]['adj_factor']=2
    v=w.calculate(b,f,l,d)
    for k in ('short_engulf','interval_bars','ma20_position','pullback_pct'):
        assert v['facts'][k]==expected['facts'][k]
    assert v['facts']['volume_ratios']=={'5':None,'13':None} and v['status']=='partial'


@pytest.mark.parametrize('kind',['duplicate_bar','duplicate_factor','future_bar','future_factor','missing_factor','nan','bad_ohlc','bool_price'])
def test_dirty_sources_fail_closed(kind):
    b,f,l,d=sample()
    if kind=='duplicate_bar':b.append(b[-1].copy())
    if kind=='duplicate_factor':f.append(f[-1].copy())
    if kind=='future_bar':b[-1]['trade_date']='2026-10-01'
    if kind=='future_factor':f[-1]['trade_date']='2026-10-01'
    if kind=='missing_factor':f.pop()
    if kind=='nan':b[-1]['high']=float('nan')
    if kind=='bad_ohlc':b[-1]['low']=100
    if kind=='bool_price':b[-1]['open']=True
    assert w.calculate(b,f,l,d)['status']=='source_failed'


@pytest.mark.parametrize('kind',['missing','negative','zero','amount_only'])
def test_volume_gaps_never_replace_with_amount_or_zero(kind):
    b,f,l,d=sample()
    for row in b:
        if kind in ('missing','amount_only'):row.pop('vol')
        if kind=='amount_only':row['amount']=1000
        if kind=='negative':row['vol']=-1
        if kind=='zero':row['vol']=0
    v=w.calculate(b,f,l,d)
    assert v['facts']['volume_ratios']=={'5':None,'13':None}
    assert v['status']=='partial'


def test_long_adjustment_is_not_short_engulf_and_ma_shortage_is_unknown():
    b,f,l,d=sample();b[20].update(high=20.)
    v=w.calculate(b,f,l,d)
    assert v['facts']['interval_bars']==8 and v['facts']['short_engulf'] is False
    v=w.calculate(b[-5:],f[-5:],l,d)
    assert v['facts']['ma20_position'] is None and v['facts']['held_ma20'] is None


def test_peak_today_compares_with_yesterday_anchor_and_ties_use_latest():
    b,f,l,d=sample();b[-5]['high']=14.;b[-1].update(high=15.)
    v=w.calculate(b,f,l,d)['facts']
    assert v['peak_date']==b[-4]['trade_date'] and v['touched_prior_peak'] is True


def context():
    _,_,_,d=sample()
    points=[]
    for i in range(5):
        points.append(dict(date=(date.fromisoformat(d)-timedelta(days=4-i)).isoformat(),status='complete',
                           sectors={'半导体':dict(total=2,valid=2,above_pct=50.)},
                           sector_comparisons={'半导体':dict(status='complete',daily_delta_pp=0.)}))
    return dict(calendar_status='complete',history=points)


def test_sector_background_missing_is_not_zero_and_fallback_not_usable():
    h=context();c=w.sector_context(h,'半导体')
    assert c['status']=='complete' and c['delta_pp']==0
    assert w.sector_context(h,'人工概念')['points'][-1]['above_pct'] is None
    h['history'][-1]['status']='fallback_preserved';h['history'][-1]['sector_comparisons']={}
    c=w.sector_context(h,'半导体')
    assert c['status']=='partial' and c['points'][-1]['valid'] is None and c['delta_pp'] is None
    h['calendar_status']='source_failed'
    assert not w.sector_context(h,'半导体')['points']


def payload():
    b,f,l,d=sample();card=w.attach_sector(w.calculate(b,f,l,d),context(),'半导体')
    card['breadth']=dict(date=d,industry='半导体',status='complete',stats=dict(sample_count=2,up_count=1,down_count=0,up_pct=50.,down_pct=0.),delta=None,windows={})
    return dict(date=d,status='ok',active=[dict(code='000001.SZ',name='测试股票',metric_status='ok',metric_as_of=d,
                wave_evidence=card,new_peak_today=True)],archived=[],tracking_calendar=[dict(date=d,is_open=1)])


@pytest.mark.parametrize('kind',['old','stale','suspended','failed','wrong_day','cached'])
def test_old_or_unfresh_cards_not_used(kind):
    p=payload();r=p['active'][0]
    if kind=='old':r.pop(w.KEY)
    if kind=='stale':r['metric_as_of']='2026-09-29'
    if kind=='suspended':r['metric_status']='suspended'
    if kind=='failed':r['metric_status']='source_failed'
    if kind=='wrong_day':r[w.KEY]['date']='2026-09-29'
    if kind=='cached':r['metric_status']='cached_archived'
    rows=w.cards(p,p['date'])
    assert not rows or not rows[0]['card']['facts']


def test_markdown_html_escape_and_no_automatic_classification():
    p=payload();p['active'][0]['name']='<script>alert(1)</script>'
    html,gap=w.render_html(p,p['date'])
    assert '&lt;script&gt;' in html and '<script>alert(1)' not in html
    assert '待人工定性' in html and not gap
    assert '短分歧实体反包是' in w.render_markdown(p,p['date'])


def test_metrics_attach_card_without_overwriting_old_wave_label():
    b,f,l,d=sample();v=metrics.calculate_metrics(b,f,l,d)
    assert v['wave_label']==metrics.infer_wave_evidence([r for r in b if r['trade_date']>=l])['wave_label']
    assert v[w.KEY]['facts']['short_engulf'] is True


def fixture(name):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(name+'.py'))
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def test_formal_source_guard_and_injection(tmp_path,monkeypatch):
    from services.volume_concentration import capacity_trend as ct
    monkeypatch.setattr(ct,'read_snapshot',lambda day,*args:ct.from_record(None,day))
    f=fixture('test_daily_review_html_report');a=f._load_assembler();p=f._emotion_leader_report(status='ok');d=f.DATE
    for r in p['active']:
        r.update(metric_status='ok',metric_as_of=d,wave_evidence=w.missing(d,'测试缺口','partial'))
    f._write_chunks(tmp_path,d)
    html=a.render_report(tmp_path,d,emotion_leader_report=p)
    a.validate_report(html,wave_evidence_report=p)
    fragment,_=w.render_html(p,d)
    for changed in (html.replace('测试缺口','伪造缺口'),html.replace('</article>',fragment+'</article>'),html.replace(fragment,'<!--'+fragment+'-->')):
        with pytest.raises(a.ReportValidationError):a.validate_report(changed,wave_evidence_report=p)
    path=tmp_path/f'b{d}_s456.html';path.write_text(path.read_text()+fragment)
    with pytest.raises(a.ReportValidationError):a.render_report(tmp_path,d,emotion_leader_report=p)


def test_service_attaches_sector_by_sw_not_manual_and_does_not_change_ranking(monkeypatch):
    from services.emotion_leader import service,renderer
    from services.volume_concentration import capacity_history
    b,f,l,d=sample()
    row=dict(code='000001.SZ',name='测试股票',launch_date=l,max_height=3,manual_sector='人工题材',manual_phase='多波',last_limit_up_date=d)
    monkeypatch.setattr(service,'load_history',lambda *a,**kw:dict(coverage={},missing_dates=[],errors=[],target_ok=True))
    monkeypatch.setattr(service,'discover_lifecycles',lambda *a:dict(promoted=[row],candidates=[],current_limit_up_codes=set(),current_down_codes=set(),height_breakthrough={'status':'none'}))
    monkeypatch.setattr(service,'load_tracking_calendar',lambda *a:[dict(date=d,is_open=1)])
    monkeypatch.setattr(service,'load_suspensions',lambda *a:{})
    monkeypatch.setattr(service,'_industry_map',lambda *a:({'000001':{'sw_l2':'半导体'}},None,'test-sw'))
    monkeypatch.setattr(service,'fetch_metrics',lambda *a:metrics.calculate_metrics(b,f,l,d))
    monkeypatch.setattr(capacity_history,'build',lambda *a,**kw:context())
    import sqlite3
    conn=sqlite3.connect(':memory:')
    try:
        result=service.run_daily(conn,None,d)
    finally:
        conn.close()
    actual=result['active'][0]
    assert actual['wave_label']=='多波' and actual['industry']=='人工题材'
    assert actual[w.KEY]['sector']['industry']=='半导体' and actual[w.KEY]['status']=='complete'
    assert '一波延续／二波重启证据卡' in renderer.render_daily(result)


def test_fetch_preserves_two_source_calls_and_original_history(monkeypatch):
    from types import SimpleNamespace
    b,f,l,d=sample();calls=[]
    class Registry:
        def call(self,method,*args):
            calls.append((method,args));return SimpleNamespace(success=True,data=b if method=='get_stock_daily_range' else f,source='fixture')
    v=metrics.fetch_metrics(Registry(),{'code':'000001.SZ','launch_date':l},d)
    assert len(calls)==2 and calls[0][1][1]==(date.fromisoformat(l)-timedelta(days=20)).isoformat()
    assert v[w.KEY]['facts']['short_engulf'] is True


@pytest.mark.parametrize('field,value',[('volume_ratios',{'5':'2','13':None}),('interval_bars',True),('ma20_position','胡乱标签'),('pullback_pct',float('inf'))])
def test_corrupt_card_fields_do_not_crash_report(field,value):
    p=payload();p['active'][0][w.KEY]['facts'][field]=value
    html,gap=w.render_html(p,p['date'])
    assert '卡片结构损坏' in html and gap


def test_markdown_addition_has_byte_budget_and_preserves_complete_cards():
    p=payload();p['active']=[dict(p['active'][0],code=f'{i:06d}.SZ',name='长名称'*50) for i in range(36)]
    md=w.render_markdown(p,p['date'],max_rows=36)
    assert len(md.encode('utf-8'))<6000
    assert '完整已刷新核心卡片保留在同日JSON' in md
    assert md.count('### ')<=3


def test_source_failure_status_remains_visible():
    p=payload();p['active'][0].update(metric_status='source_failed',metric_error='行情源失败')
    html,gap=w.render_html(p,p['date'])
    assert 'source_failed' in html and '行情源失败' in html and gap


def test_old_metrics_survive_unrequested_older_factor_gap():
    from types import SimpleNamespace
    start=date(2026,6,1)
    b=[dict(trade_date=(start+timedelta(days=i)).isoformat(),open=10.,close=10.1,high=10.2,low=9.9,vol=100.) for i in range(80)]
    f=[dict(trade_date=r['trade_date'],adj_factor=1.) for r in b[1:]]
    launch,day=b[60]['trade_date'],b[-1]['trade_date']
    class Registry:
        def call(self,name,code,left,right):
            data=b if name=='get_stock_daily_range' else f
            return SimpleNamespace(success=True,data=[r for r in data if left<=r['trade_date']<=right],source='fixture')
    v=metrics.fetch_metrics(Registry(),{'code':'000001.SZ','launch_date':launch},day)
    assert v['metric_status']=='ok' and v[w.KEY]['status']=='complete'
    assert v['wave_label']==metrics.infer_wave_evidence(b[60:])['wave_label']


def test_standard_write_requires_matching_wave_source(tmp_path,monkeypatch):
    from services.volume_concentration import capacity_trend as ct
    monkeypatch.setattr(ct,'read_snapshot',lambda day,*args:ct.from_record(None,day))
    f=fixture('test_daily_review_html_report');a=f._load_assembler();p=f._emotion_leader_report(status='ok');d=f.DATE
    for r in p['active']:
        r.update(metric_status='ok',metric_as_of=d,wave_evidence=w.missing(d,'仅卡片缺数据','partial'))
    f._write_chunks(tmp_path,d)
    html=a.render_report(tmp_path,d,emotion_leader_report=p)
    kwargs=dict(capacity_manifest=f._capacity_manifest(),new_high_manifest=f._new_high_manifest())
    # 运行目录可能已有额外标准sidecar契约，保留其校验，不绕过。
    if hasattr(f, '_index_intraday_manifest'):
        kwargs['index_intraday_manifest']=f._index_intraday_manifest()
    import inspect
    render_kwargs={k:v for k,v in kwargs.items() if k in inspect.signature(a.render_report).parameters}
    html=a.render_report(tmp_path,d,emotion_leader_report=p,**render_kwargs)
    out=tmp_path/'valid.html'
    assert a.write_report(html,out,wave_evidence_report=p,**kwargs)==out and out.read_text()==html
    out.write_text('保留原文件')
    for source in (None,dict(p,date='2026-07-15')):
        with pytest.raises(a.ReportValidationError) as e:
            a.write_report(html,out,wave_evidence_report=source,**kwargs)
        assert e.value.code=='wave_evidence_source_mismatch' and out.read_text()=='保留原文件'
