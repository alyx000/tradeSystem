from copy import deepcopy
from datetime import datetime
import importlib.util
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
import yaml

from analyzers import strong_stock_feedback as sf
from db.schema import init_schema
from providers.base import DataResult

PREV, DAY = '2026-10-29', '2026-10-30'
CODE = '000001.SZ'


def inputs():
    section = dict(count=1, stocks=[dict(code=CODE, name='测试甲')], _source='limit-source')
    def quote(day, opening, high, low, close, previous):
        return dict(ts_code=CODE, trade_date=day, open=opening, high=high, low=low, close=close,
                    pre_close=previous, pct_chg=(close/previous-1)*100)
    data = dict(previous_quotes=[quote(PREV, 10, 11, 10, 11, 10)],
                current_quotes=[quote(DAY, 10.8, 11, 10.4, 10.5, 11)],
                previous_factors=[dict(ts_code=CODE, trade_date=PREV, adj_factor=1)],
                current_factors=[dict(ts_code=CODE, trade_date=DAY, adj_factor=1)])
    return section, data


def compute(section=None, data=None):
    s, d = inputs()
    return sf.calculate(DAY, PREV, section if section is not None else s, data if data is not None else d)


def test_equal_high_touches_and_bearish_is_not_negative_return():
    r = compute()
    assert r['status'] == 'complete'
    assert r['details'][0]['touched_previous_high'] is True
    s, d = inputs()
    q = d['current_quotes'][0]
    q.update(open=12, high=12.1, low=11.1, close=11.5, pct_chg=(11.5/11-1)*100)
    r = compute(s,d)
    assert r['details'][0]['close_pct'] > 0 and r['details'][0]['bearish_candle']
    assert r['summary']['bearish_count'] == 1


def test_corporate_action_aligns_previous_high_and_uses_current_preclose():
    s,d = inputs()
    q=d['current_quotes'][0]
    q.update(open=5.6, high=5.7, low=5.4, close=5.5, pre_close=5.5, pct_chg=0)
    d['current_factors'][0]['adj_factor']=2
    r=compute(s,d)
    assert r['details'][0]['touched_previous_high'] is True
    assert r['details'][0]['previous_high_in_current_basis']==5.5
    assert r['details'][0]['close_pct']==0
    assert r['details'][0]['open_pct']==pytest.approx(100*(5.6/5.5-1))


def test_missing_factors_preserves_quote_feedback_with_unknown_touch():
    s,d=inputs();d['previous_factors']=[]
    r=compute(s,d)
    assert r['status']=='partial'
    assert r['summary']['quote_count']==1 and r['summary']['touch_count']==0
    assert r['summary']['touched_count'] is None and r['summary']['touched_rate_pct'] is None
    assert r['details'][0]['bearish_candle']


@pytest.mark.parametrize('damage', ['missing_quote','duplicate_quote','wrong_date','future_date','bad_suffix','conflicting_identity','nan','zero','bad_ohlc','boolean','bad_pct'])
def test_bad_current_quotes_cannot_be_zero_feedback(damage):
    s,d=inputs();q=d['current_quotes'][0]
    if damage=='missing_quote': d['current_quotes']=[]
    if damage=='duplicate_quote': d['current_quotes'].append(deepcopy(q))
    if damage=='wrong_date': q['trade_date']=PREV
    if damage=='future_date': q['trade_date']='2026-11-02'
    if damage=='bad_suffix': q['ts_code']='000001.SH'
    if damage=='conflicting_identity': q['code']='000002.SZ'
    if damage=='nan': q['close']=float('nan')
    if damage=='zero': q['pre_close']=0
    if damage=='bad_ohlc': q['high']=5
    if damage=='boolean': q['open']=True
    if damage=='bad_pct': q['pct_chg']=99
    r=compute(s,d)
    assert r['status']=='source_failed' and r['cohort_count']==1
    assert r['summary']['close_mean_pct'] is None
    assert r['details'][0]['touched_previous_high'] is None


@pytest.mark.parametrize('key', ['previous_quotes','previous_factors','current_factors'])
def test_duplicate_or_missing_reference_is_partial_not_no_touch(key):
    s,d=inputs();d[key].append(deepcopy(d[key][0]))
    r=compute(s,d)
    assert r['status']=='partial' and r['summary']['touched_count'] is None


def test_partial_coverage_uses_separate_denominators():
    s,d=inputs()
    s['stocks'].append(dict(code='000002.SZ', name='测试乙'));s['count']=2
    r=compute(s,d)
    assert r['status']=='partial' and r['cohort_count']==2
    assert r['summary']['quote_count']==r['summary']['touch_count']==1
    assert r['summary']['touched_rate_pct']==100


@pytest.mark.parametrize('damage',['duplicate','count','name','source','failed'])
def test_unknown_cohort_never_becomes_empty(damage):
    s,d=inputs()
    if damage=='duplicate': s['stocks']*=2;s['count']=2
    if damage=='count': s['count']=2
    if damage=='name': s['stocks'][0]['name']=''
    if damage=='source': s['_source']=''
    if damage=='failed': s['error']='bad'
    r=compute(s,d)
    assert r['status']=='source_failed' and r['cohort_count'] is None


def test_verified_empty_and_st_exclusion():
    s,d=inputs();s['stocks'][0]['name']='*ST测试'
    r=compute(s,{})
    assert r['status']=='empty' and r['excluded_count']==1
    assert r['summary']['close_mean_pct'] is None
    assert compute(dict(count=0, stocks=[], _source='verified'), {})['status']=='empty'


def setup_collection(tmp_path):
    db=tmp_path/'db.sqlite';c=sqlite3.connect(db);init_schema(c)
    c.executemany('INSERT INTO trade_calendar (exchange,date,is_open) VALUES (?,?,?)', [('SSE', PREV,1),('SSE',DAY,1)])
    c.commit();c.close()
    s,d=inputs();directory=tmp_path/'daily';p=directory/PREV/'post-market.yaml';p.parent.mkdir(parents=True)
    p.write_text(yaml.safe_dump(dict(date=PREV,raw_data={'limit_up':s})))
    calls=[]
    def call(method,day):
        calls.append((method,day))
        key=('previous' if day==PREV else 'current')+('_quotes' if method=='get_market_daily_quotes' else '_factors')
        return DataResult(data=d[key],source='mock')
    return db,directory,SimpleNamespace(call=call),calls


def test_collect_uses_four_batch_calls_and_readonly_calendar(tmp_path):
    db,directory,reg,calls=setup_collection(tmp_path);before=db.read_bytes()
    r=sf.collect(reg,DAY,daily_dir=directory,db_path=db,now=datetime(2026,10,30,20,tzinfo=ZoneInfo('Asia/Shanghai')))
    assert r['status']=='complete' and len(calls)==4
    assert {m for m,d in calls}=={'get_market_daily_quotes','get_adj_factor'}
    assert db.read_bytes()==before


@pytest.mark.parametrize('failure',['calendar','archive','archive_date','future','intraday','holiday'])
def test_collection_gates_fail_before_quotes(tmp_path,failure):
    db,directory,reg,calls=setup_collection(tmp_path)
    now=datetime(2026,10,30,20,tzinfo=ZoneInfo('Asia/Shanghai'))
    if failure=='calendar':
        c=sqlite3.connect(db);c.execute('DELETE FROM trade_calendar WHERE date=?',(PREV,));c.commit();c.close()
    if failure=='archive': (directory/PREV/'post-market.yaml').unlink()
    if failure=='archive_date': (directory/PREV/'post-market.yaml').write_text('date: 2026-10-28')
    if failure=='future': now=datetime(2026,10,29,20,tzinfo=ZoneInfo('Asia/Shanghai'))
    if failure=='intraday': now=now.replace(hour=14)
    if failure=='holiday':
        c=sqlite3.connect(db);c.execute('UPDATE trade_calendar SET is_open=0 WHERE date=?',(DAY,));c.commit();c.close()
    r=sf.collect(reg,DAY,daily_dir=directory,db_path=db,now=now)
    assert r['status'] in {'source_failed','skipped'} and not calls


def test_previous_open_does_not_skip_a_calendar_gap(tmp_path):
    db,directory,reg,calls=setup_collection(tmp_path)
    c=sqlite3.connect(db)
    c.execute('UPDATE trade_calendar SET date=? WHERE date=?',('2026-10-26',PREV));c.commit()
    with pytest.raises(ValueError,match='缺口'): sf._previous_open(c,DAY)
    c.close()


def test_generator_archives_full_block_and_dual_write_envelope(tmp_path):
    from generators.report import ReportGenerator
    from db.dual_write import _extract_market_row,parse_post_market_envelope
    g=ReportGenerator();g._ensure_dir=MagicMock(return_value=tmp_path)
    block=compute()
    text,path=g.generate_post_market(date=DAY,raw_data={sf.KEY:block})
    envelope=yaml.safe_load(Path(path).read_text())
    assert envelope['raw_data'][sf.KEY]==block
    stored=_extract_market_row(DAY,envelope)
    assert parse_post_market_envelope(stored['raw_data'])['raw_data'][sf.KEY]==block
    assert '昨日涨停股次日反馈' in text


def test_post_collection_calls_new_analyzer_without_changing_old_feedback(monkeypatch,tmp_path):
    from collectors.market import MarketCollector
    monkeypatch.setattr('collectors.market.BASE_DIR',tmp_path)
    registry=MagicMock()
    registry.call.return_value=DataResult(None,'test',error='unavailable')
    expected=compute();collect=MagicMock(return_value=expected)
    monkeypatch.setattr(sf,'collect',collect)
    old=dict(status='partial',outcome_date=DAY,details=[])
    monkeypatch.setattr('analyzers.board_break_feedback.collect_board_break_feedback',MagicMock(return_value=old))
    collector=MarketCollector(registry)
    collector._compute_index_ma=MagicMock()
    collector._collect_research_coverage=MagicMock(return_value=[])
    collector._rhythm_analyzer=MagicMock()
    collector._rhythm_analyzer.load_main_theme_names.return_value=[]
    collector._rhythm_analyzer.analyze.return_value=[]
    result=collector.collect_post_market(DAY)
    assert result[sf.KEY] is expected and result['board_break_feedback'] is old
    collect.assert_called_once_with(registry,DAY,daily_dir=tmp_path/'daily')


def test_renderer_is_readonly_and_escapes_sources(tmp_path):
    s,d=inputs();s['stocks'][0]['name']='<script>x</script>|[link](url)'
    block=compute(s,d);html,gap=sf.render_html(block,DAY);md=sf.render_markdown(block,DAY)
    assert '<script>' not in html and '&lt;script&gt;' in html and gap is None
    assert '[link](' not in md
    p=tmp_path/DAY/'post-market.yaml';p.parent.mkdir();p.write_text(yaml.safe_dump(dict(date=DAY,raw_data={sf.KEY:block})))
    before=p.read_bytes();assert sf.load_snapshot(tmp_path,DAY)==block;assert p.read_bytes()==before
    assert sf.load_snapshot(tmp_path,PREV)['status']=='not_computed'


@pytest.mark.parametrize('damage',['summary','details','touch','date','nan','state'])
def test_corrupt_snapshot_is_not_rendered_as_complete(damage):
    b=compute()
    if damage=='summary': b['summary']['touch_count']=999
    if damage=='details': b['details']=[None]
    if damage=='touch': b['details'][0]['touched_previous_high']='yes'
    if damage=='date': b['trade_date']=PREV
    if damage=='nan': b['details'][0]['open_pct']=float('nan')
    if damage=='state': b['status']='empty'
    html,gap=sf.render_html(b,DAY)
    assert 'source_failed' in html and gap


def test_formal_assembler_injects_only_same_day_and_rejects_handwritten_block(tmp_path):
    spec=importlib.util.spec_from_file_location('feedback_fixture',Path(__file__).with_name('test_daily_review_html_report.py'))
    fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
    fixture._write_chunks(tmp_path,DAY);assembler=fixture._load_assembler()
    html=assembler.render_report(tmp_path,DAY,strong_stock_feedback=compute())
    assert html.count('data-strong-stock-feedback="v1"')==1 and '测试甲' in html
    assembler.validate_report(html,strong_stock_feedback=compute())
    html=assembler.render_report(tmp_path,DAY,strong_stock_feedback=sf._base(PREV))
    assert '快照内容或统计口径损坏' in html
    p=tmp_path/f'b{DAY}_s456.html';p.write_text(p.read_text()+'<div data-strong-stock-feedback="v1">fake</div>')
    with pytest.raises(assembler.ReportValidationError): assembler.render_report(tmp_path,DAY)


@pytest.mark.parametrize('damage', ['unit_error', 'derived_overflow'])
def test_bad_open_high_magnitude_is_a_quote_gap(damage):
    s,d=inputs();q=d['current_quotes'][0]
    if damage=='unit_error':
        q.update(open=1000000,high=1000000,low=10,close=11,pre_close=11,pct_chg=0)
    else:
        q.update(open=1e308,high=1e308,low=1e-308,close=1e-308,pre_close=1e-308,pct_chg=0)
    r=compute(s,d)
    assert r['status']=='source_failed'
    assert r['summary']['quote_count']==0 and r['summary']['touched_rate_pct'] is None


def test_provider_empty_error_cannot_be_reinterpreted_as_verified_zero(tmp_path):
    db,directory,registry,calls=setup_collection(tmp_path)
    section=dict(count=0,stocks=[],_source='tushare:limit_list_d',error='无涨停数据')
    (directory/PREV/'post-market.yaml').write_text(yaml.safe_dump(dict(date=PREV,raw_data={'limit_up':section})))
    result=sf.collect(registry,DAY,daily_dir=directory,db_path=db,now=datetime(2026,10,30,17,tzinfo=ZoneInfo('Asia/Shanghai')))
    assert result['status']=='source_failed' and result['cohort_count'] is None
    assert not calls


@pytest.mark.parametrize('damage',['remove','duplicate','number','name','open','metadata','extra_row','comment_copy','script_copy'])
def test_formal_validator_rejects_mutated_feedback(tmp_path,damage):
    spec=importlib.util.spec_from_file_location('feedback_fixture',Path(__file__).with_name('test_daily_review_html_report.py'))
    fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
    fixture._write_chunks(tmp_path,DAY);assembler=fixture._load_assembler()
    block=compute();fragment=sf.render_html(block,DAY)[0]
    html=assembler.render_report(tmp_path,DAY,strong_stock_feedback=block)
    assert fragment in html
    replacement=fragment
    if damage=='remove': replacement=''
    if damage=='duplicate': replacement=fragment+fragment
    if damage=='number': replacement=fragment.replace('100.00%','99.00%')
    if damage=='name': replacement=fragment.replace('测试甲','另一个名字')
    if damage=='open': replacement=fragment.replace('<details ','<details open ')
    if damage=='metadata': replacement=fragment.replace('data-items="1"','data-items="2"')
    if damage=='extra_row': replacement=fragment.replace('</tbody>','<tr><td>extra</td></tr></tbody>')
    if damage=='comment_copy': replacement=fragment.replace('测试甲','错误名字')+'<!--'+fragment+'-->'
    if damage=='script_copy': replacement=fragment.replace('测试甲','错误名字')+'<script type="application/json">'+fragment+'</script>'
    with pytest.raises(assembler.ReportValidationError):
        assembler.validate_report(html.replace(fragment,replacement),strong_stock_feedback=block)
