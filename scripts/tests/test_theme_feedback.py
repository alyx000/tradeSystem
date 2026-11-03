from copy import deepcopy
from datetime import datetime
import importlib.util
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

import pytest
import yaml

from analyzers import theme_feedback as f
from analyzers import theme_launch as tl
from analyzers import strong_stock_feedback as sf
from scripts.tests.test_theme_launch import inputs, DAY as SOURCE, PREV, THEME, CODES

DAY = '2026-11-03'
NOW = datetime(2026, 11, 3, 20, tzinfo=ZoneInfo('Asia/Shanghai'))


def evidence():
    launch = tl.calculate(SOURCE, PREV, inputs())
    def q(code, day, op=10, close=10, pct=0):
        return dict(ts_code=code, trade_date=day, open=op, close=close, pre_close=10,
                    high=max(10, op, close), low=min(9, op, close), pct_chg=pct)
    data = dict(previous_quotes=[q(c, SOURCE) for c in CODES],
                current_quotes=[q(CODES[0], DAY, 10.5, 11, 10), q(CODES[1], DAY, 11, 12, 20), q(CODES[2], DAY, 10, 9, -10)],
                previous_factors=[dict(ts_code=c, trade_date=SOURCE, adj_factor=1) for c in CODES],
                current_factors=[dict(ts_code=c, trade_date=DAY, adj_factor=1) for c in CODES])
    feedback = sf.calculate(DAY, SOURCE, launch['evidence']['limit_up'], data,
        sources={'limit_up':dict(date=SOURCE, source='tushare:limit_list_d'),
                 'current_quotes':dict(date=DAY, source='tushare:daily', status='complete')})
    limits = dict(_source='tushare:limit_list_d', count=2, stocks=[
        dict(code=CODES[0], name='股票0', limit_times=3, limit_times_verified=True),
        dict(code=CODES[1], name='股票1', limit_times=2, limit_times_verified=True)])
    from analyzers import market_strength as ms
    strength = ms.calculate(data['current_quotes'],
        [dict(ts_code=c,name='股票'+str(i),list_date='20000101') for i,c in enumerate(CODES)],
        [dict(ts_code='600999.SH',trade_date=DAY)], {c:{'sw_l2':'行业'} for c in CODES}, DAY, min_count=3,
        sources={key:dict(status='success',source=source) for key,source in
                 [('quotes','tushare:daily'),('st','tushare:stock_st'),('identity','tushare:stock_basic:L+D+P:as_of')]})
    return dict(launch=launch, feedback=feedback, limit_up=limits, limit_up_date=DAY,market_strength=strength)


def calculate(e=None):
    return f.calculate(DAY, SOURCE, evidence() if e is None else e)


def test_fixed_cohort_board_metrics_and_recompute():
    b=calculate();r=b['rows'][0];g=r['groups']
    assert b['status']=='complete' and r['sample_count']==3
    assert g['all']['quote_count']==3 and g['all']['again_limit_count']==2
    assert g['all']['continuation_count']==2 and g['all']['positive_count']==2
    assert g['20']['sample_count']==2 and g['20']['close_mean_pct']==5
    assert g['20']['again_limit_rate_pct']==50
    assert g['30']['status']=='empty' and g['30']['again_limit_count'] is None
    assert g['30']['open_mean_pct'] is None
    assert f.validate(b,DAY)==b


def test_no_touch_factors_does_not_discard_valid_open_close():
    e=evidence();b=e['feedback']
    for s in b['details']:s['touched_previous_high']=None
    b['summary']=sf._summary(b['details']);b['status']='partial';b['errors']=['previous_factors:offline']
    out=calculate(e)
    assert out['status']=='complete' and out['rows'][0]['groups']['all']['quote_count']==3


@pytest.mark.parametrize('mutation',[
    lambda e:e['feedback'].update(source_date=PREV),
    lambda e:e['feedback']['sources']['current_quotes'].update(date=SOURCE),
    lambda e:e['feedback']['sources']['current_quotes'].update(source=''),
    lambda e:e['feedback']['details'][0].update(name='不匹配'),
    lambda e:e['feedback']['details'][0].update(current_open=999),
    lambda e:e['feedback']['details'][0].update(current_close=10.9),
])
def test_bad_quote_provenance_preserves_limit_evidence_not_zero_returns(mutation):
    e=evidence();mutation(e);r=calculate(e)['rows'][0]
    assert r['status']=='partial' and r['groups']['all']['quote_count']==0
    assert r['groups']['all']['open_mean_pct'] is None
    assert r['groups']['all']['again_limit_count']==2
    assert r['groups']['all']['limit_checked_count']==2
    assert r['stocks'][2]['again_limit_up'] is None


def test_missing_quote_is_not_false_non_limit_or_zero_and_denominators_explicit():
    e=evidence();b=e['feedback'];s=b['details'][2]
    s.update(quote_status='source_failed',touched_previous_high=None,errors=['missing'])
    b.update(summary=sf._summary(b['details']),status='partial')
    r=calculate(e)['rows'][0];g=r['groups']['all']
    assert g['quote_count']==2 and g['close_mean_pct']==15
    assert g['again_limit_count']==2 and g['limit_checked_count']==2
    assert r['stocks'][2]['again_limit_up'] is None


@pytest.mark.parametrize('mode',['source_failed','wrong_date','duplicate','incomplete','row_date'])
def test_limit_source_failure_preserves_quotes(mode):
    e=evidence()
    if mode=='source_failed':e['limit_up']={'error':'offline'}
    if mode=='wrong_date':e['limit_up_date']=SOURCE
    if mode=='duplicate':e['limit_up']['stocks'][1]=deepcopy(e['limit_up']['stocks'][0])
    if mode=='incomplete':e['limit_up']['count']=3
    if mode=='row_date':e['limit_up']['stocks'][0]['trade_date']='20261102'
    g=calculate(e)['rows'][0]['groups']['all']
    assert g['quote_count']==3 and g['again_limit_count'] is None and g['limit_checked_count']==0


@pytest.mark.parametrize('height,verified',[(3,True),(1,False),(None,False)])
def test_repeated_limit_is_not_unverified_continuous_streak(height,verified):
    e=evidence();e['limit_up']['stocks'][1].update(limit_times=height,limit_times_verified=verified)
    r=calculate(e)['rows'][0];s=r['stocks'][1]
    assert s['again_limit_up'] is True and s['verified_continuation'] is None
    assert r['groups']['20']['continuation_checked_count']==1
    assert r['status']=='partial'


def test_current_st_name_cannot_erase_frozen_source_sample():
    e=evidence();e['limit_up']['stocks'][0]['name']='ST新名字'
    r=calculate(e)['rows'][0]
    assert r['sample_count']==3 and r['stocks'][0]['name']=='股票0'
    assert r['stocks'][0]['again_limit_up'] is True


def test_source_unknown_height_stays_unknown_despite_next_day_height():
    e=evidence();raw=e['launch']['evidence'];raw['limit_up']['stocks'][1]['limit_times_verified']=False
    e['launch']=tl.calculate(SOURCE,PREV,raw)
    s=calculate(e)['rows'][0]['stocks'][1]
    assert s['source_height'] is None and s['verified_continuation'] is None


def test_overlapping_themes_not_summed_partial_member_isolated():
    e=evidence();raw=e['launch']['evidence'];code='885002.TI'
    raw['ranking']['data'].append(dict(ts_code=code,name='题材乙',rank=2,trade_date='20261102',pct_chg=1))
    raw['membership']['themes'][code]=deepcopy(raw['membership']['themes'][THEME])
    for r in raw['membership']['themes'][code]['rows']:r['ts_code']=code
    e['launch']=tl.calculate(SOURCE,PREV,raw);b=calculate(e)
    assert [r['sample_count'] for r in b['rows']]==[3,3] and 'total_count' not in b
    raw['membership']['themes'][code]['status']='source_failed'
    e['launch']=tl.calculate(SOURCE,PREV,raw);b=calculate(e)
    assert b['status']=='partial' and b['rows'][0]['status']=='complete'
    assert b['rows'][1]['sample_count'] is None and b['rows'][1]['groups'] is None


def test_empty_source_sample_not_source_failure():
    e=evidence();raw=e['launch']['evidence'];raw['limit_up'].update(count=0,stocks=[])
    e['launch']=tl.calculate(SOURCE,PREV,raw)
    b=calculate(e);g=b['rows'][0]['groups']['all']
    assert b['status']=='complete' and g['sample_count']==0 and g['again_limit_count'] is None
    assert g['open_mean_pct'] is None
    raw['limit_up']={'error':'failed'};e['launch']=tl.calculate(SOURCE,PREV,raw)
    b=calculate(e)
    assert b['status']=='source_failed' and not b['rows']


def test_evidence_tamper_and_wrong_day_fail_closed():
    b=calculate();b['rows'][0]['groups']['all']['open_mean_pct']=999
    assert f.validate(b,DAY)['status']=='source_failed'
    assert f.validate(calculate(),SOURCE)['status']=='source_failed'
    b=f.missing(DAY,'missing');b.update(fake_metric=999)
    assert '999' not in f.render_html(b,DAY)[0]


def save(root,day,raw):
    p=root/day/'post-market.yaml';p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(yaml.safe_dump(dict(date=day,raw_data=raw),allow_unicode=True))


def setup(tmp_path):
    db=tmp_path/'calendar.db'
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE trade_calendar(date TEXT,exchange TEXT,is_open INT)')
        c.executemany('INSERT INTO trade_calendar VALUES (?,"SSE",?)',[(PREV,1),('2026-10-31',0),('2026-11-01',0),(SOURCE,1),(DAY,1)])
    e=evidence();raw=dict(date=DAY,strong_stock_feedback=e['feedback'],limit_up=e['limit_up'],market_strength=e['market_strength'])
    save(tmp_path,SOURCE,{tl.KEY:e['launch']});save(tmp_path,DAY,raw)
    return db,raw


def test_build_readonly_current_memory_and_prior_theme_frozen(tmp_path):
    db,raw=setup(tmp_path)
    before={p:p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    b=f.build(tmp_path,DAY,db_path=db,now=NOW)
    assert b['status']=='complete'
    raw[tl.KEY]={'error':'today_members_changed'}
    assert f.build(tmp_path,DAY,current_raw=raw,db_path=db,now=NOW)==b
    raw['limit_up']={'error':'new_failed_attempt'}
    assert f.build(tmp_path,DAY,current_raw=raw,db_path=db,now=NOW)['status']=='partial'
    assert before=={p:p.read_bytes() for p in before}


def test_calendar_gap_nonadjacent_source_before_close_future(tmp_path):
    db,raw=setup(tmp_path)
    assert f.build(tmp_path,DAY,db_path=db,now=NOW.replace(hour=15))['status']=='skipped'
    assert f.build(tmp_path,'2026-11-04',db_path=db,now=NOW)['status']=='skipped'
    with sqlite3.connect(db) as c:c.execute('DELETE FROM trade_calendar WHERE date="2026-11-01"')
    assert f.build(tmp_path,DAY,db_path=db,now=NOW)['status']=='source_failed'


def test_old_archive_missing_launch_and_closed_day_projection(tmp_path):
    db,raw=setup(tmp_path)
    with sqlite3.connect(db) as c:c.execute('INSERT INTO trade_calendar VALUES ("2026-11-04","SSE",0)')
    b=f.load_for_review(tmp_path,'2026-11-04',db,now=NOW.replace(day=4))
    html,gap=f.render_html(b,'2026-11-04')
    assert '量能背景对照不完整' in gap and 'data-as-of="2026-11-03"' in html and '休市展示' in html
    save(tmp_path,SOURCE,{'limit_up':raw['limit_up']})
    assert f.build(tmp_path,DAY,db_path=db,now=NOW)['status']=='not_computed'


def test_html_escaping_and_plain_missing_summary():
    e=evidence();raw=e['launch']['evidence'];raw['ranking']['data'][0]['name']='<script>题材|甲</script>'
    e['launch']=tl.calculate(SOURCE,PREV,raw)
    html,_=f.render_html(calculate(e),DAY)
    assert '<script>题材' not in html and '&lt;script&gt;' in html
    assert '题材｜甲' in f.render_markdown(calculate(e),DAY)
    html,gap=f.render_html(None,DAY)
    assert '暂不能判断' in html and '0只' not in html and gap
    assert 'not_computed' not in html.split('<details')[0]


@pytest.mark.parametrize('change',['delete','duplicate','numeric','identity','comment','hidden','outside','open'])
def test_official_html_guards_source_and_structure(tmp_path, change):
    from scripts.tests import test_daily_review_html_report as h
    a=h._load_assembler();h._write_chunks(tmp_path,DAY);b=calculate()
    html=a.render_report(tmp_path,DAY,theme_feedback=b)
    a.validate_report(html,theme_feedback=b)
    fragment,_=f.render_html(b,DAY)
    if change=='delete':changed=html.replace(fragment,'')
    if change=='duplicate':changed=html.replace('</article>',fragment+'</article>')
    if change=='numeric':changed=html.replace(fragment,fragment.replace('固定样本3只','固定样本9只'))
    if change=='identity':changed=html.replace(fragment,fragment.replace(CODES[0],'600999.SH'))
    if change=='comment':changed=html.replace(fragment,'<!--'+fragment+'-->')
    if change=='hidden':changed=html.replace(fragment,'<div hidden>'+fragment+'</div>')
    if change=='outside':changed=html.replace(fragment,'').replace('</article>',fragment+'</article>')
    if change=='open':changed=html.replace(fragment,fragment.replace('<details ','<details open '))
    with pytest.raises(a.ReportValidationError):a.validate_report(changed,theme_feedback=b)


def test_official_html_rejects_handwritten_module_and_accepts_missing(tmp_path):
    from scripts.tests import test_daily_review_html_report as h
    a=h._load_assembler();h._write_chunks(tmp_path,DAY);b=f.missing(DAY,'missing')
    html=a.render_report(tmp_path,DAY,theme_feedback=b)
    a.validate_report(html,theme_feedback=b)
    assert '同题材次日反馈存在缺口' in html
    s2=tmp_path/f'b{DAY}_s2.html';s2.write_text(s2.read_text()+f'<div {f.MARKER}="{DAY}">伪造</div>')
    with pytest.raises(a.ReportValidationError,match='duplicate_theme_feedback'):
        a.render_report(tmp_path,DAY,theme_feedback=b)


def test_report_entry_uses_current_memory_without_persisting_derived_block(tmp_path,monkeypatch):
    from generators.report import ReportGenerator
    db,raw=setup(tmp_path);monkeypatch.setenv('TRADE_DB_PATH',str(db))
    monkeypatch.setattr('db.connection._DEFAULT_DB_PATH',db)
    monkeypatch.setattr(f,'datetime',type('Clock',(datetime,),{'now':classmethod(lambda cls,tz=None:NOW)}))
    gen=ReportGenerator();gen.daily_dir=tmp_path
    # 原来的盘后归档故意失败，当批内存结果应覆盖它参与报表，不能读旧块替代。
    save(tmp_path,DAY,{'date':DAY,'strong_stock_feedback':{'error':'stale'}})
    markdown,path=gen.generate_post_market(DAY,raw)
    assert '同题材次日反馈' in markdown and '题材甲' in markdown and '| 3 | 3/3 |' in markdown
    saved=yaml.safe_load(Path(path).read_text())
    assert saved['raw_data']['strong_stock_feedback']==raw['strong_stock_feedback']
    assert 'theme_feedback' not in saved['raw_data']


@pytest.mark.parametrize('mode',['akshare','no_non_st','st_name','wrong_non_st_date','unverified_non_st_source'])
def test_absence_needs_confirmed_non_st_and_supported_source_coverage(mode):
    e=evidence()
    if mode=='akshare':e['limit_up']['_source']='akshare:stock_zt_pool_em'
    if mode=='no_non_st':e.pop('market_strength')
    if mode=='st_name':e['market_strength']['rows'][2]['name']='ST股票2'
    if mode=='wrong_non_st_date':e['market_strength']['trade_date']=SOURCE
    if mode=='unverified_non_st_source':e['market_strength']['sources']['st']['source']='unknown'
    b=calculate(e);s=b['rows'][0]['stocks'][2];g=b['rows'][0]['groups']['all']
    assert b['status']=='partial' and s['again_limit_up'] is None and s['verified_continuation'] is None
    assert g['again_limit_count']==2 and g['limit_checked_count']==2 and g['quote_count']==3


@pytest.mark.parametrize('where',['section','row'])
def test_explicit_prior_limit_date_conflict_rejects_cohort(where):
    e=evidence();raw=e['launch']['evidence'];section=raw['limit_up']
    (section if where=='section' else section['stocks'][0])['trade_date']='20261030'
    e['launch']=tl.calculate(SOURCE,PREV,raw)
    assert calculate(e)['status']=='source_failed' and calculate(e)['rows']==[]


@pytest.mark.parametrize('where',['quote_source','prior_receipt','prior_source','current_source'])
def test_unknown_sources_not_certified(where):
    e=evidence()
    if where=='quote_source':e['feedback']['sources']['current_quotes']['source']='unverified'
    if where=='prior_receipt':e['feedback']['sources']['limit_up']['source']='wrong-source'
    if where=='prior_source':
        raw=e['launch']['evidence'];raw['limit_up']['_source']='unverified';e['launch']=tl.calculate(SOURCE,PREV,raw)
    if where=='current_source':e['limit_up']['_source']='unverified'
    b=calculate(e)
    assert b['status']!='complete'
    if where in {'quote_source','prior_receipt'}:assert b['rows'][0]['groups']['all']['quote_count']==0
    if where=='current_source':assert b['rows'][0]['groups']['all']['again_limit_count'] is None
    if where=='prior_source':assert not b['rows']


def test_missing_member_date_never_borrowed_from_expected_source():
    html,_=f.render_html(f.missing(DAY,'成员源失败',previous=SOURCE),DAY)
    assert '预期来源日：2026-11-02' in html and '成员快照日：未取得' in html


@pytest.mark.parametrize('bad',[None,[],{'st':None},{'st':[]},{'st':'bad'}])
def test_bad_auxiliary_identity_receipts_only_disable_negative_inference(bad):
    e=evidence();e['market_strength']['sources']=bad
    b=calculate(e);g=b['rows'][0]['groups']['all']
    assert b['status']=='partial' and g['quote_count']==3
    assert g['again_limit_count']==2 and g['limit_checked_count']==2
    assert b['rows'][0]['stocks'][2]['again_limit_up'] is None


def test_feedback_uses_current_adjusted_reference_not_source_close():
    e=evidence();s=e['feedback']['details'][1]
    s.update(current_open=5.5,current_close=6,pre_close=5)
    # 例如发生除权，报价和昨收同比缩放；开收盘涨跌仍保持10%和20%。
    b=calculate(e);s=b['rows'][0]['stocks'][1]
    assert b['status']=='complete' and s['open_pct']==pytest.approx(10) and s['close_pct']==20
