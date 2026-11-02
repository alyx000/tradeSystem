from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest
from analyzers import sector_core_linkage as link
from analyzers import strong_stock_feedback as sf
from services.volume_concentration import capacity_trend as ct


def fixture(name):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(name+'.py'))
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

h=fixture('test_strong_stock_feedback_history')
c=fixture('test_capacity_history')
DAY,PREV='2026-10-30','2026-10-29'


def inputs():
    specs=[('000002.SZ',2,False,True,-3),('000003.SZ',1,True,False,2),('300001.SZ',1,True,False,1)]
    b,old=h.block(DAY,PREV,specs)
    current=dict(_source='tushare:limit_list_d',count=1,stocks=[dict(code='000005.SZ',name='新涨停',industry='行业甲',limit_times=1,limit_times_verified=True)])
    roster=[dict(code=code,name='股票'+code,sector='行业甲',role=role) for code,role in zip(['000001.SZ','000002.SZ','000003.SZ','300001.SZ'],link.ROLES)]
    cap=c.record(DAY,(('000001.SZ','行业甲','下方'),))['source'][ct.KEY]
    prior=c.record(PREV,(('000001.SZ','行业甲','上方'),))['source'][ct.KEY]
    return dict(day=DAY,previous=PREV,identity_date=PREV,identity_age=1,roster=roster,raw={sf.KEY:b,'limit_up':current},prior_raw={'limit_up':old},capacity=cap,prior_capacity=prior)


def test_two_roles_are_weak_and_new_limit_is_not_new_core():
    v=link.calculate(**inputs());s=v['sectors'][0]
    assert v['status']=='complete' and s['covered']==4
    assert set(s['weak_roles'])=={'趋势中军','连板核心'}
    assert s['assessment']=='多类核心同日弱项'
    assert v['members'][0]['change']=='新增弱项'
    assert s['new_limit_up'][0]['code']=='000005.SZ'
    assert len(v['members'])==4


def _html_default_text(fragment):
    import re
    from html import unescape
    return unescape(re.sub('<[^>]+>', '', re.sub(r'<details\b.*?</details>', '', fragment, flags=re.S)))


def test_html_puts_verified_weaknesses_before_folded_rules_without_mutating_view():
    view=link.calculate(**inputs());before=deepcopy(view);markdown=link.render_markdown(view)
    fragment,gap=link.render_html(view,DAY);visible=_html_default_text(fragment)
    assert '已核验4/4只归档核心' in visible and '已核验走弱 2只' in visible
    assert '000001.SZ' in visible and 'MA20下方' in visible and '000002.SZ' in visible
    assert '核心名单归档10-29' in visible
    assert link.NOTE not in visible and link.NOTE in fragment
    assert 'complete' not in visible and '状态：complete' in fragment
    assert '<summary>来源、缺口与判断口径（2项）</summary>' in fragment
    assert f'data-as-of="{DAY}" data-items="2"' in fragment
    assert view==before and link.render_markdown(view)==markdown and not gap


def test_html_partial_preserves_verified_weakness_and_unknown_coverage():
    x=inputs();x['raw'].pop(sf.KEY);view=link.calculate(**x)
    fragment,gap=link.render_html(view,DAY);visible=_html_default_text(fragment)
    assert view['status']=='partial' and '已核验1/4只归档核心' in visible
    assert '已核验走弱 1只' in visible and 'MA20下方' in visible
    assert '待核验 3只' in visible and 'partial' not in visible
    assert all(g in fragment for g in view['gaps']) and gap


def test_html_unknown_weakness_is_not_rendered_as_no_weakness():
    x=inputs();x['raw'].pop(sf.KEY);x['capacity']={'status':'not_computed'}
    view=link.calculate(**x);fragment,_=link.render_html(view,DAY);visible=_html_default_text(fragment)
    assert '已核验0/4只归档核心' in visible and '待核验 4只' in visible
    assert '未触发本模块弱项' not in visible and '0只出现走弱信号' not in visible
    assert link.NOTE not in visible and link.NOTE in fragment


@pytest.mark.parametrize('status,expected',[
    ('empty','最票名单为空'),('missing-data','核心名单或行情尚未核验'),
    ('source_failed','核心名单或行情尚未核验'),
])
def test_html_empty_and_failed_identity_remain_distinct(status,expected):
    view={**link._base(DAY),'status':status,'gaps':['完整来源缺口']}
    fragment,_=link.render_html(view,DAY);visible=_html_default_text(fragment)
    assert expected in visible and status not in visible
    assert '完整来源缺口' not in visible and '完整来源缺口' in fragment
    assert link.NOTE not in visible and link.NOTE in fragment


@pytest.mark.parametrize('mode',['missing_current','missing_prior','unknown_height','missing_quote','wrong_feedback_date'])
def test_break_requires_valid_prior_height_full_list_and_trading(mode):
    x=inputs()
    if mode=='missing_current':x['raw']['limit_up']['error']='failed'
    if mode=='missing_prior':x['prior_raw']=None
    if mode=='unknown_height':x['prior_raw']['limit_up']['stocks'][0]['limit_times_verified']=False
    if mode=='missing_quote':x['raw'].pop(sf.KEY)
    if mode=='wrong_feedback_date':x['raw'][sf.KEY]['source_date']='2026-10-28'
    v=link.calculate(**x)
    assert v['members'][1]['weak'] is None
    assert v['sectors'][0]['assessment']=='已见弱项，其余待核验'


def test_still_limit_up_and_single_role_weak():
    x=inputs();r=deepcopy(x['prior_raw']['limit_up']['stocks'][0]);x['raw']['limit_up']['stocks'].append(r);x['raw']['limit_up']['count']=2
    v=link.calculate(**x)
    assert v['members'][1]['weak'] is False
    assert v['sectors'][0]['assessment']=='局部弱项'


@pytest.mark.parametrize('mode',['fallback','wrong_industry','no_row','duplicate','one_down'])
def test_capacity_conservative_coverage(mode):
    x=inputs()
    if mode=='fallback':x['capacity']['status']='fallback_preserved'
    if mode=='wrong_industry':x['capacity']['rows'][0]['industry']='行业乙'
    if mode=='no_row':x['capacity']={'status':'not_computed'}
    if mode=='duplicate':x['capacity']['rows']*=2;x['capacity']['total_count']=x['capacity']['complete_count']=2
    if mode=='one_down':x['capacity']['rows'][0]['end_run_bars']=1
    v=link.calculate(**x)
    assert v['members'][0]['weak'] is (False if mode=='one_down' else None)
    assert v['sectors'][0]['assessment']!='多类核心同日弱项'


def test_weak_role_count_does_not_count_same_role_twice():
    x=inputs();x['roster'][2]['role']='连板核心';x['prior_raw']['limit_up']['stocks'][1]['limit_times']=2
    x['capacity']={'status':'not_computed'}
    v=link.calculate(**x)
    assert v['sectors'][0]['weak_roles']==['连板核心']
    assert v['sectors'][0]['assessment']=='已见弱项，其余待核验'


def store_roster(db,day,rows):
    conn=sqlite3.connect(db);conn.execute('CREATE TABLE IF NOT EXISTS daily_reviews(date TEXT PRIMARY KEY,step5_leaders TEXT)')
    payload={'top_leaders':[dict(stock=r['name'],stock_code=r['code'],sector=r['sector'],attribute_type=r['role']) for r in rows]}
    conn.execute('INSERT OR REPLACE INTO daily_reviews VALUES(?,?)',(day,json.dumps(payload)));conn.commit();conn.close()


def test_read_only_build_uses_latest_past_roster_and_exact_archives(tmp_path,monkeypatch):
    daily,db,days=h.market(tmp_path);x=inputs();store_roster(db,days[-3],x['roster']);store_roster(db,DAY,[])
    h.save(daily,PREV,limit=x['prior_raw']['limit_up']);h.save(daily,DAY,x['raw'][sf.KEY],x['raw']['limit_up'])
    monkeypatch.setattr(link,'_read',lambda conn,d: {'source':{ct.KEY:x['capacity'] if d==DAY else x['prior_capacity']}})
    before=db.read_bytes();v=link.build(daily,DAY,db_path=db)
    assert v['identity_date']==days[-3] and v['identity_age']==2
    assert db.read_bytes()==before
    assert v['status']=='complete'
    assert link.build(daily,DAY,db_path=db,current_raw=x['raw'])['status']=='complete'
    # 当批未落盘数据优先，但日期必须匹配。
    raw={**x['raw'],'date':DAY};raw['limit_up']={'error':'failed'}
    assert link.build(daily,DAY,db_path=db,current_raw=raw)['status']=='partial'
    raw['date']=PREV
    assert link.build(daily,DAY,db_path=db,current_raw=raw)['status']=='source_failed'


@pytest.mark.parametrize('mode',['expired','no_code','unknown_role','duplicate','wrong_suffix','backup','calendar_gap','empty_latest','corrupt_latest'])
def test_identity_calendar_failures_never_backfill_or_promote(tmp_path,mode):
    daily,db,days=h.market(tmp_path);x=inputs();rows=x['roster']
    if mode=='no_code':rows[0]['code']=None;rows[0]['name']='仅名称'
    if mode=='unknown_role':rows[0]['role']='二波龙头'
    if mode=='duplicate':rows.append(rows[0])
    if mode=='wrong_suffix':rows[0]['code']='000001.SH'
    store_roster(db,days[-7] if mode=='expired' else PREV,rows)
    if mode in ('calendar_gap','empty_latest','corrupt_latest','backup'):
        conn=sqlite3.connect(db)
        if mode=='calendar_gap':conn.execute('DELETE FROM trade_calendar WHERE date=?',(days[-3],))
        if mode=='empty_latest':conn.execute('UPDATE daily_reviews SET step5_leaders=?',(json.dumps({'top_leaders':[]}),))
        if mode=='corrupt_latest':conn.execute('UPDATE daily_reviews SET step5_leaders=?',('broken',))
        if mode=='backup':
            p=json.loads(conn.execute('SELECT step5_leaders FROM daily_reviews').fetchone()[0]);p['top_leaders'][0]['review_status']='备选'
            conn.execute('UPDATE daily_reviews SET step5_leaders=?',(json.dumps(p),))
        conn.commit();conn.close()
    v=link.build(daily,DAY,db_path=db)
    if mode=='expired':assert v['status']=='missing-data' and not v['members']
    elif mode=='empty_latest':assert v['status']=='empty' and not v['members']
    elif mode in ('no_code','unknown_role'):assert v['members'][0]['weak'] is None
    else:assert v['status']=='source_failed' and not v['members']


def test_html_source_validation_and_injection_guard(tmp_path,monkeypatch):
    monkeypatch.setattr(ct, "read_snapshot", lambda day, *args: ct.from_record(None, day))
    f=fixture('test_daily_review_html_report');a=f._load_assembler();chunks=tmp_path/'chunks';f._write_chunks(chunks,DAY)
    v=link.calculate(**inputs());v['members'][0]['name']='<script>alert(1)</script>'
    kwargs=dict(sector_core_linkage=v)
    html=a.render_report(chunks,DAY,**kwargs)
    a.validate_report(html,**kwargs)
    fragment,_=link.render_html(v,DAY)
    assert '&lt;script&gt;' in fragment and '<script>alert(1)' not in fragment
    for changed in (html.replace('MA20下方','MA20上方'),html.replace('</article>',fragment+'</article>'),html.replace(fragment,'<!--'+fragment+'-->')):
        with pytest.raises(a.ReportValidationError):a.validate_report(changed,**kwargs)
    rogue=f'<div {link.MARKER}="{DAY}">伪造</div>'
    p=chunks/f'b{DAY}_s2.html';p.write_text(p.read_text()+rogue)
    with pytest.raises(a.ReportValidationError):a.render_report(chunks,DAY,**kwargs)


def test_markdown_bounded_and_render_date_guard():
    v=link.calculate(**inputs());v['sectors']*=100
    assert len(link.render_markdown(v).encode())<6000
    with pytest.raises(ValueError):link.render_html(v,PREV)


def test_unclassified_new_limits_never_claim_exhaustive_sector_zero():
    x=inputs();x['raw']['limit_up']['stocks'][0]['industry']=''
    v=link.calculate(**x)
    assert v['status']=='partial'
    assert v['sectors'][0]['new_limit_up']==[] and v['sectors'][0]['new_unknown']==1
    assert '另1只行业未知' in link.render_html(v,DAY)[0]


def test_display_suffix_conflict_fails_before_bare_code_normalization(tmp_path):
    daily,db,days=h.market(tmp_path);rows=inputs()['roster'];rows[0]['name']='000001.SH 名称'
    store_roster(db,PREV,rows)
    assert link.build(daily,DAY,db_path=db)['status']=='source_failed'


def test_wrong_capacity_date_is_not_used():
    x=inputs();x['capacity']['trade_date']=PREV
    v=link.calculate(**x)
    assert v['members'][0]['weak'] is None


@pytest.mark.parametrize('mode',['role','name','too_many','blank_name'])
def test_invalid_historical_roster_is_rejected_on_read(tmp_path,mode):
    daily,db,days=h.market(tmp_path);rows=inputs()['roster']
    if mode=='role':rows[1]['role']=rows[0]['role']
    if mode=='name':rows[1]['name']=rows[0]['name']
    if mode=='too_many':rows*=4
    if mode=='blank_name':rows[0]['name']='   '
    store_roster(db,PREV,rows)
    v=link.build(daily,DAY,db_path=db)
    assert v['status']=='source_failed' and not v['members']


def test_report_generator_accepts_batch_without_embedded_date(tmp_path,monkeypatch):
    from generators.report import ReportGenerator
    daily,db,days=h.market(tmp_path);x=inputs();store_roster(db,PREV,x['roster'])
    h.save(daily,PREV,limit=x['prior_raw']['limit_up'])
    monkeypatch.setattr(link,'_read',lambda conn,d: {'source':{ct.KEY:x['capacity'] if d==DAY else x['prior_capacity']}})
    real_build=link.build;results=[]
    def build(daily_dir,day,**kwargs):
        assert 'date' not in kwargs['current_raw']
        v=real_build(daily_dir,day,db_path=db,**kwargs);results.append(v);return v
    monkeypatch.setattr(link,'build',build)
    generator=ReportGenerator();generator.daily_dir=daily
    markdown,_=generator.generate_post_market(DAY,x['raw'])
    assert results[0]['status']=='complete'
    assert '多类核心同日弱项' in markdown


def test_html_visual_cards_keep_three_states_and_sector_identity():
    x=inputs()
    x['roster'].append(dict(code='000006.SZ',name='另一板块核心',sector='行业乙',role='趋势中军'))
    view=link.calculate(**x)
    fragment,_=link.render_html(view,DAY);visible=_html_default_text(fragment)
    # 卡片逐一保留已保存身份；缺失角色不生成假股票或零值。
    assert fragment.count('data-core-state="weak"')==2
    assert fragment.count('data-core-state="clear"')==2
    assert fragment.count('data-core-state="unknown"')==1
    assert 'data-core-sector="行业甲"' in fragment and 'data-core-sector="行业乙"' in fragment
    assert '已核验4/5只归档核心' in visible and '另一板块核心' in visible and '000006.SZ' in visible
    assert '▼ 已核验走弱' in visible and '○ 已核验未触发' in visible and '? 待核验' in visible
    assert 'aria-label="已核验4/5只；走弱2只，未触发2只，待核验1只"' in fragment
    assert fragment.count('data-core-coverage=')==5
    assert 'flex-wrap:wrap' in fragment and 'min-width:0' in fragment and 'overflow-wrap:anywhere' in fragment
    assert '<script' not in fragment and '<svg' not in fragment and '<link' not in fragment


def test_html_one_of_five_coverage_does_not_imply_four_healthy_members():
    x=inputs();x['raw'].pop(sf.KEY)
    x['roster'].append(dict(code='000006.SZ',name='待核验核心',sector='行业乙',role='趋势中军'))
    fragment,_=link.render_html(link.calculate(**x),DAY);visible=_html_default_text(fragment)
    assert '已核验1/5只归档核心' in visible and '待核验 4只' in visible
    assert fragment.count('data-core-state="weak"')==1
    assert fragment.count('data-core-state="unknown"')==4
    assert 'data-core-state="clear"' not in fragment
    assert 'aria-label="已核验1/5只；走弱1只，未触发0只，待核验4只"' in fragment


def test_html_visual_identity_is_escaped_and_date_remains_explicit():
    view=link.calculate(**inputs())
    view['members'][0].update(name='<img src=x onerror=alert(1)>',sector='行业"<乙>')
    view['identity_date']='2025-12-31'
    fragment,_=link.render_html(view,DAY)
    assert '<img' not in fragment and '&lt;img' in fragment
    assert 'data-core-sector="行业&quot;&lt;乙&gt;"' in fragment
    assert '核心名单归档2025-12-31' in _html_default_text(fragment)
