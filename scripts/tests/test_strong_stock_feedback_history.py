from copy import deepcopy
from datetime import date,timedelta
import importlib.util
from pathlib import Path
import sqlite3

import pytest
import yaml

from analyzers import strong_stock_feedback as sf
from analyzers import strong_stock_feedback_history as h

DAY='2026-10-30'


def market(tmp_path):
    end=date.fromisoformat(DAY);dates=[end-timedelta(days=i) for i in reversed(range(65))]
    db=tmp_path/'calendar.sqlite';c=sqlite3.connect(db)
    c.execute('CREATE TABLE trade_calendar(exchange TEXT,date TEXT,is_open INTEGER,PRIMARY KEY(exchange,date))')
    c.executemany('INSERT INTO trade_calendar VALUES(?,?,?)',[('SSE',d.isoformat(),int(d.weekday()<5)) for d in dates]);c.commit();c.close()
    days=[d.isoformat() for d in dates if d.weekday()<5][-21:]
    daily=tmp_path/'daily';daily.mkdir()
    return daily,db,days


def block(day,previous,specs=None):
    if specs is None: specs=[('000001.SZ',1,True,True,0)]
    section=dict(_source='tushare:limit_list_d',count=len(specs),stocks=[])
    data={k:[] for k in ('previous_quotes','current_quotes','previous_factors','current_factors')}
    for code,height,touched,bearish,pct in specs:
        section['stocks'].append(dict(code=code,name='测试'+code,limit_times=height,limit_times_verified=h.limit_times(height) is not None))
        close=10*(1+pct/100);opening=close+0.1 if bearish else close-0.1
        high=11.5 if touched else max(opening,close)+0.01
        assert touched or high<11
        data['previous_quotes'].append(dict(ts_code=code,trade_date=previous,open=10,high=11,low=9.5,close=10,pre_close=9,pct_chg=100/9))
        data['current_quotes'].append(dict(ts_code=code,trade_date=day,open=opening,high=high,low=min(opening,close)-.1,close=close,pre_close=10,pct_chg=pct))
        for prefix,d in [('previous',previous),('current',day)]:
            data[prefix+'_factors'].append(dict(ts_code=code,trade_date=d,adj_factor=1))
    return sf.calculate(day,previous,section,data),section


def save(daily,day,value=None,limit=None):
    raw={}
    if value is not None:raw[sf.KEY]=value
    if limit is not None:raw['limit_up']=limit
    p=daily/day/'post-market.yaml';p.parent.mkdir(exist_ok=True);p.write_text(yaml.safe_dump(dict(date=day,raw_data=raw),allow_unicode=True));return p


def filled(tmp_path):
    daily,db,days=market(tmp_path)
    for i,d in enumerate(days[1:],1):
        b,_=block(d,days[i-1]);save(daily,d,b)
    return daily,db,days


@pytest.mark.parametrize('value,expected',[(1,1),('2',2),(2.0,2),(None,None),(True,None),(0,None),(-1,None),(1.5,None),('3天2板',None),(float('inf'),None)])
def test_height_requires_positive_integer(value,expected):
    assert h.limit_times(value)==expected


def test_current_groups_use_source_height_and_board_independently(tmp_path):
    daily,db,days=market(tmp_path)
    b,_=block(DAY,days[-2],[('000001.SZ',1,True,True,0),('300001.SZ',4,False,False,3),('920001.BJ',None,True,False,2),('688001.SH',1,True,True,1)])
    result=h.build(daily,DAY,current=b,db_path=db);g=result['current']['groups']
    assert [g[k]['sample_count'] for k in ('all','first','consecutive','height_unknown','10cm','20cm','30cm')]==[4,2,1,1,1,2,1]
    assert g['consecutive']['touched_rate_pct']==0
    assert not result['current']['classification_complete']
    assert result['windows'][0]['valid_days']==1


def test_fixed_window_pools_stock_days_not_daily_percentages(tmp_path):
    daily,db,days=filled(tmp_path)
    for d in days[-5:]:
        i=days.index(d);b,_=block(d,days[i-1],[]);save(daily,d,b)
    b,_=block(days[-2],days[-3],[('000001.SZ',1,True,True,5)]);save(daily,days[-2],b)
    b,_=block(DAY,days[-2],[(f'{i:06d}.SZ',1,False,False,0) for i in range(1,10)]);save(daily,DAY,b)
    r=h.build(daily,DAY,db_path=db);w=r['windows'][0];g=w['groups']['all']
    assert w['status']=='complete' and w['valid_days']==5
    assert g['sample_count']==10 and g['touched_rate_pct']==10 and g['close_mean_pct']==.5 and g['close_median_pct']==0
    assert r['current']['delta']['touched_rate_pct']==-100
    assert r['current']['delta']['close_mean_pct']==-5


def test_window_does_not_expand_across_missing_or_failed_day(tmp_path):
    daily,db,days=filled(tmp_path)
    (daily/days[-3]/'post-market.yaml').unlink()
    save(daily,days[-2])
    b,_=block(DAY,days[-2]);b['details'][0]['touched_previous_high']=None;b['status']='partial';b['summary']=sf._summary(b['details']);save(daily,DAY,b)
    r=h.build(daily,DAY,db_path=db)
    assert [p['status'] for p in r['history'][-3:]]==['missing-data','not_computed','partial']
    assert [w['valid_days'] for w in r['windows']]==[2,7,17]
    assert r['windows'][0]['start']==days[-5]
    assert r['current']['delta'] is None
    assert r['current']['groups']['all']['quote_count']==1 and r['current']['groups']['all']['touch_count']==0


@pytest.mark.parametrize('damage',['missing_calendar','closed_target','wrong_source','wrong_envelope','bad_summary','duplicate'])
def test_gates_reject_unverifiable_history(tmp_path,damage):
    daily,db,days=filled(tmp_path)
    if damage in ('missing_calendar','closed_target'):
        c=sqlite3.connect(db)
        if damage=='missing_calendar':c.execute('DELETE FROM trade_calendar WHERE date=?',(days[-4],))
        else:c.execute('UPDATE trade_calendar SET is_open=0 WHERE date=?',(DAY,))
        c.commit();c.close()
    else:
        b,_=block(DAY,days[-2])
        if damage=='wrong_source':b['source_date']=days[-3]
        if damage=='bad_summary':b['summary']['quote_count']=9
        if damage=='duplicate':b['details']*=2
        p=save(daily,DAY,b)
        if damage=='wrong_envelope':p.write_text(yaml.safe_dump(dict(date=days[-2],raw_data={sf.KEY:b})))
    r=h.build(daily,DAY,db_path=db)
    if damage in ('missing_calendar','closed_target'):
        assert r['calendar_status']=='source_failed' and not r['history']
        assert all(w['groups'] is None for w in r['windows'])
    else:
        assert r['current']['status']=='source_failed' and r['windows'][0]['valid_days']==4


def test_legacy_height_only_uses_exact_matching_prior_archive(tmp_path):
    daily,db,days=market(tmp_path);b,section=block(DAY,days[-2],[('300001.SZ',3,True,False,0)])
    b['details'][0].pop('limit_times');b['details'][0].pop('limit_times_verified');save(daily,DAY,b);save(daily,days[-2],limit=section)
    assert h.build(daily,DAY,db_path=db)['current']['groups']['consecutive']['sample_count']==1
    section['stocks'].append(dict(code='000001.SZ',name='其他',limit_times=1));section['count']=2;save(daily,days[-2],limit=section)
    result=h.build(daily,DAY,db_path=db)
    assert result['current']['groups']['height_unknown']['sample_count']==1
    assert result['current']['groups']['20cm']['sample_count']==1
    b['details'][0]['limit_times']=False
    save(daily,days[-2],limit={**section,'count':1,'stocks':section['stocks'][:1]})
    assert h.build(daily,DAY,current=b,db_path=db)['current']['groups']['height_unknown']['sample_count']==1


def test_zero_cohort_and_unknown_sources_stay_distinct(tmp_path):
    daily,db,days=market(tmp_path);b,_=block(DAY,days[-2],[])
    r=h.build(daily,DAY,current=b,db_path=db)
    assert r['current']['status']=='empty' and r['windows'][0]['valid_days']==1
    assert r['windows'][0]['groups']['all']['sample_count']==0
    assert r['windows'][0]['groups']['all']['touched_rate_pct'] is None
    unknown=h.build(daily,DAY,db_path=db)
    assert unknown['current']['groups'] is None and unknown['windows'][0]['groups'] is None


def test_read_only_and_current_override_ignores_stale_same_day_file(tmp_path):
    daily,db,days=filled(tmp_path);b,_=block(DAY,days[-2],[('000001.SZ',1,False,False,0)])
    before={p:p.read_bytes() for p in daily.rglob('*') if p.is_file()};db_before=db.read_bytes()
    r=h.build(daily,DAY,current=b,db_path=db)
    assert r['current']['groups']['all']['touched_count']==0
    assert before=={p:p.read_bytes() for p in daily.rglob('*') if p.is_file()} and db_before==db.read_bytes()
    save(daily,'2026-11-02',b)
    assert h.build(daily,DAY,current=b,db_path=db)==r


def test_chart_breaks_at_missing_dates_and_markdown_is_bounded(tmp_path):
    daily,db,days=filled(tmp_path);(daily/days[-2]/'post-market.yaml').unlink()
    r=h.build(daily,DAY,db_path=db);html,gap=h.render_html(r)
    assert gap and '19/20' in gap and 'missing-data' in html
    assert html.count('<circle')==19*3
    # 每条曲线：前18点连17段，末日隔缺口不得跨日连接；另有4条轴线。
    assert html.count('<line')==17*3+4
    assert len(h.render_markdown(r).encode())<6000


def test_formal_report_matches_history_and_rejects_mutation(tmp_path):
    daily,db,days=filled(tmp_path);b,_=block(DAY,days[-2]);result=h.build(daily,DAY,current=b,db_path=db)
    spec=importlib.util.spec_from_file_location('history_html_fixture',Path(__file__).with_name('test_daily_review_html_report.py'))
    f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f);a=f._load_assembler()
    chunks=tmp_path/'chunks';f._write_chunks(chunks,DAY)
    html=a.render_report(chunks,DAY,strong_stock_feedback=b,strong_stock_history=result,capacity_trend_db_path=tmp_path/'missing.sqlite')
    a.validate_report(html,strong_stock_feedback=b,strong_stock_history=result)
    fragment=sf.render_html(b,DAY,history=result)[0]
    assert fragment in html
    for changed in [fragment.replace('20/20','19/20'),fragment.replace('近5日','近6日'),fragment.replace('<circle','<circle hidden'),fragment.replace('10cm','错误')+'<!--'+fragment+'-->']:
        with pytest.raises(a.ReportValidationError):a.validate_report(html.replace(fragment,changed),strong_stock_feedback=b,strong_stock_history=result)


def test_missing_calendar_rendering_and_daily_generator_use_fresh_current(tmp_path,monkeypatch):
    daily,db,days=market(tmp_path);b,_=block(DAY,days[-2]);captured=[]
    from generators.report import ReportGenerator
    real=h.build
    def build(directory,day,*,current,db_path=None):
        captured.append(current)
        return real(directory,day,current=current,db_path=db)
    monkeypatch.setattr(h,'build',build)
    g=ReportGenerator();g.daily_dir=daily
    text,path=g.generate_post_market(date=DAY,raw_data={sf.KEY:b})
    assert captured==[b] and '近20日' in text and '当日分组' in text
    saved=yaml.safe_load(Path(path).read_text())
    assert saved['raw_data'][sf.KEY]==b and 'history' not in saved['raw_data'][sf.KEY]


def test_unverified_older_board_is_unknown_and_cross_date_history_rejected(tmp_path):
    b,_=block('2020-01-03','2020-01-02',[('300001.SZ',1,True,False,0)])
    p=h._point(b,'2020-01-03','2020-01-02',tmp_path,{})
    assert p['groups']['board_unknown']['sample_count']==1
    daily,db,days=filled(tmp_path);b,_=block(DAY,days[-2]);history=h.build(daily,DAY,current=b,db_path=db)
    history['date']='2026-10-29'
    for renderer in (sf.render_html,sf.render_markdown):
        with pytest.raises(ValueError):renderer(b,DAY,history=history)


@pytest.mark.parametrize('provider',['tushare','akshare'])
@pytest.mark.parametrize('raw',['missing',1,2,True,1.5,0,-1])
def test_provider_field_presence_controls_group_identity(provider,raw):
    import pandas as pd
    from types import SimpleNamespace
    if provider=='tushare':
        from providers.tushare_provider import TushareProvider
        p=TushareProvider({});record={'ts_code':'000001.SZ','name':'测试'}
        if raw!='missing':record['limit_times']=raw
        p.pro=SimpleNamespace(limit_list_d=lambda **_:pd.DataFrame([record]))
    else:
        from providers.akshare_provider import AkshareProvider
        p=AkshareProvider({});record={'代码':'000001','名称':'测试'}
        if raw!='missing':record['连板数']=raw
        p.ak=SimpleNamespace(stock_zt_pool_em=lambda **_:pd.DataFrame([record]))
    response=p.get_limit_up_list(DAY)
    assert response.success
    expected=not isinstance(raw,bool) and raw in (1,2)
    assert response.data['stocks'][0]['limit_times_verified'] is expected
    # 旧梯队仍可读原先兼容字段，但新分组不得消费默认值/截断值。
    if raw=='missing':assert response.data['stocks'][0]['limit_times']==1
    section={**response.data,'_source':response.source}
    cohort=sf._cohort(section)
    assert cohort[0]['limit_times']==(raw if expected else None)


@pytest.mark.parametrize('height,stat,verified,expected',[(1,'',None,None),(1,'1/1',None,1),(2,'',None,2),(2,'1/1',None,None),(1,'1/1',False,None),(1,'',True,1)])
def test_legacy_defaults_need_independent_evidence(height,stat,verified,expected):
    row=dict(code='000001',name='测试',limit_times=height,limit_stat=stat)
    if verified is not None:row['limit_times_verified']=verified
    cohort=sf._cohort(dict(_source='tushare:limit_list_d',count=1,stocks=[row]))
    assert cohort[0]['limit_times']==expected


def test_calendar_failure_cannot_publish_wrong_source_groups(tmp_path):
    daily,db,days=market(tmp_path);b,_=block(DAY,days[-3])
    c=sqlite3.connect(db);c.execute('DELETE FROM trade_calendar WHERE date=?',(days[-4],));c.commit();c.close()
    r=h.build(daily,DAY,current=b,db_path=db)
    assert r['calendar_status']=='source_failed' and r['current']['groups'] is None
    html,gap=h.render_html(r)
    assert '未核验' in html and '| 当日分组 |' not in h.render_markdown(r)
    assert all(w['groups'] is None for w in r['windows'])


def test_formal_history_marker_must_be_unique_and_nested(tmp_path):
    daily,db,days=filled(tmp_path);b,_=block(DAY,days[-2]);result=h.build(daily,DAY,current=b,db_path=db)
    spec=importlib.util.spec_from_file_location('history_duplicate_fixture',Path(__file__).with_name('test_daily_review_html_report.py'))
    f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f);a=f._load_assembler();chunks=tmp_path/'chunks';f._write_chunks(chunks,DAY)
    kwargs=dict(strong_stock_feedback=b,strong_stock_history=result)
    html=a.render_report(chunks,DAY,**kwargs)
    rogue=f'<div data-strong-feedback-history="{DAY}">伪造100%</div>'
    with pytest.raises(a.ReportValidationError):a.validate_report(html.replace('</article>',rogue+'</article>'),**kwargs)
    expected=h.render_html(result)[0]
    with pytest.raises(a.ReportValidationError):a.validate_report(html.replace(expected,'').replace('</article>',expected+'</article>'),**kwargs)
    chunk=chunks/f'b{DAY}_s456.html';chunk.write_text(chunk.read_text()+rogue)
    with pytest.raises(a.ReportValidationError):a.render_report(chunks,DAY,**kwargs)
