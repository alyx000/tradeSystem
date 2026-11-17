import copy
import contextlib
import io
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch, Mock

import pandas as pd
import pytest

from tools import volume_buildup_recent as R


def bars():
    days=pd.bdate_range('2026-09-01',periods=23).strftime('%Y-%m-%d').tolist()
    rows=[dict(ts_code='x',trade_date=d,open=10.,high=11.,low=9.,close=10.5,vol=10.,adj_factor=1.) for d in days]
    rows[-3].update(open=10.,high=12.,low=9.8,close=11.,vol=15.)
    rows[-2].update(open=11.,high=13.,low=10.5,close=12.,vol=20.)
    rows[-1].update(open=12.5,high=14.,low=11.,close=11.5,vol=25.)
    return rows


def limits(rows):
    return {(x['trade_date'],x['ts_code']):x['close']+1 for x in rows}


def test_raised_yin_replaces_only_third_close_rule():
    rows=bars();e=R.shape_a(rows)
    assert e['third_bar']=='raised_yin' and e['volume_ratio']==2
    rows[-1].update(open=12.,close=12.8)
    assert R.shape_a(rows)['third_bar']=='rising_yang'


@pytest.mark.parametrize('mutation', [
    {'high':13.}, {'low':10.5}, {'open':11.5}, {'vol':20.},
    {'vol':24.99}, {'adj_factor':float('nan')}, {'low':15.}])
def test_strict_equalities_doji_volume_and_invalid_fail(mutation):
    rows=bars();rows[-1].update(mutation)
    assert R.shape_a(rows) is None


def test_first_two_must_be_yang_and_rising():
    rows=bars();rows[-2]['open']=12.5
    assert R.shape_a(rows) is None
    rows=bars();rows[-2]['close']=11.
    assert R.shape_a(rows) is None


def test_bullish_third_still_needs_rising_close():
    rows=bars();rows[-1].update(open=11.2,close=11.5)
    assert R.shape_a(rows) is None


def test_adjusted_high_low_and_individual_volume_floor():
    rows=bars()
    for k in ('open','high','low','close'):rows[-1][k]/=2
    rows[-1]['adj_factor']=2.
    assert R.shape_a(rows)['third_bar']=='raised_yin'
    rows=bars();rows[-3]['vol']=9.;rows[-1]['vol']=40.
    assert R.shape_a(rows) is None


def test_limit_verification_is_not_relaxed():
    rows=bars();lp=limits(rows)
    assert R.classify_a(rows,lp)[1]=='matched'
    lp[(rows[-2]['trade_date'],'x')]=rows[-2]['close']
    assert R.classify_a(rows,lp)[1]=='recent_limit_up'
    lp=limits(rows);del lp[(rows[-8]['trade_date'],'x')]
    assert R.classify_a(rows,lp)[1]=='limit_data_missing'


def test_short_duplicate_history_cannot_match():
    assert R.shape_a(bars()[1:]) is None
    rows=bars();rows[-1]['trade_date']=rows[-2]['trade_date']
    assert R.shape_a(rows) is None


def calendar_db():
    db=sqlite3.connect(':memory:')
    db.execute('CREATE TABLE trade_calendar(exchange TEXT,date TEXT,is_open INTEGER)')
    for d in pd.date_range('2026-10-01','2026-11-16'):
        db.execute('INSERT INTO trade_calendar VALUES(?,?,?)',('SSE',d.strftime('%Y-%m-%d'),int(d.weekday()<5)))
    return db


def test_calendar_uses_five_open_days_across_weekend():
    with calendar_db() as db:
        opened,days=R.market_days(db,'2026-11-16')
        assert days==['2026-11-10','2026-11-11','2026-11-12','2026-11-13','2026-11-16']
        db.execute("DELETE FROM trade_calendar WHERE date='2026-11-14'")
        with pytest.raises(AssertionError,match='calendar_gap'):R.market_days(db,'2026-11-16')


def fixture_scans():
    days=['2026-11-10','2026-11-11','2026-11-12','2026-11-13','2026-11-16']
    event=dict(code='x',name='股票',stage='A',industry='行业',start_date='2026-11-06',end_date=days[0],volume_ratio=2.1,third_bar='rising_yang')
    scans={d:dict(date=d,candidates=[],states=[dict(code='x',name='股票',A='volume_shape_not_matched',B='no_recent_limit_up')],status='complete',base_coverage={},base_exclusions={}) for d in days}
    scans[days[0]]['candidates']=[copy.deepcopy(event)]
    scans[days[1]]['candidates']=[copy.deepcopy(event)]
    daily=pd.DataFrame([dict(code='x',ts_code='x',trade_date=d,low=10.,adj_factor=1.) for d in days])
    return days,scans,daily


def test_merge_deduplicates_stock_preserves_dates_and_today_state():
    days,scans,daily=fixture_scans()
    result=R.combine(days,scans,[],daily,days)
    assert result['counts']==dict(union_observed=1,current_A=0,current_B=0,historical_only_observed=1,current_unknown_observed=0)
    row=result['rows'][0]
    assert row['occurrence_dates']==days[:2] and row['age_open_days']==3
    assert row['current_status']=='仅历史命中；今日不满足'
    assert '11-10、11-11' in R.markdown(result)
    assert len(row['events'])==2


def test_today_and_historical_missing_are_not_zero_or_exit():
    days,scans,daily=fixture_scans()
    del scans[days[2]]
    scans[days[-1]]['states'][0]['A']='target_suspended'
    result=R.combine(days,scans,[dict(date=days[2],reason='source_failed')],daily,days)
    assert result['status']=='partial' and len(result['verified_dates'])==4
    assert result['rows'][0]['current_status']=='当前待核验'
    assert '缺失日期无形态' in R.markdown(result)
    del scans[days[-1]]
    with pytest.raises(AssertionError,match='current_scan_missing'):R.combine(days,scans,[],daily,days)


def test_b_break_never_revives_and_missing_is_unknown():
    days,scans,daily=fixture_scans()
    event=dict(start_date=days[0],limit_date=days[1])
    rows=daily.to_dict('records');rows[2]['low']=9.;rows[-1]['low']=12.
    assert R.b_structure(event,rows,days,days[-1])==dict(status='结构已失效',first_broken_date=days[2])
    assert R.b_structure(event,rows[:2]+rows[3:],days,days[-1])['status']=='待核验'
    rows[2]['low']=10.
    assert R.b_structure(event,rows,days,days[-1])['status']=='未跌破原结构低点'


def fake_archive(tmp_path):
    run=tmp_path/'2026-11-16';out=run/'enhanced-v2';out.mkdir(parents=True)
    (out/'report.md').write_text('report')
    (out/'v2-screen.json').write_text('[]')
    (run/'cache').mkdir()
    pd.DataFrame([dict(ts_code='x',trade_date=run.name)]).to_pickle(run/'cache/daily.pkl')
    sources={str(p):R.sha(p) for p in (out/'v2-screen.json',run/'cache/daily.pkl')}
    report=dict(date=run.name,rule_version=R.BASE_VERSION,status='partial',source_sha256=sources)
    (out/'report.json').write_text(json.dumps(report))
    v=dict(date=run.name,status='passed',report_sha256=R.sha(out/'report.json'),markdown_sha256=R.sha(out/'report.md'))
    (out/'validation.json').write_text(json.dumps(v))
    return run,dict(source_sha256={})


def test_source_hash_and_future_cache_fail_closed(tmp_path):
    run,base=fake_archive(tmp_path)
    with patch.object(R,'verified_base',return_value=base):
        assert R.load_archive(run)['report']['date']==run.name
        (run/'enhanced-v2/v2-screen.json').write_text('[{}]')
        with pytest.raises(AssertionError,match='source_drift'):R.load_archive(run)


def test_daily_must_be_explicitly_source_bound(tmp_path):
    run,base=fake_archive(tmp_path)
    rp=run/'enhanced-v2/report.json';vp=run/'enhanced-v2/validation.json'
    report=R.read(rp);del report['source_sha256'][str(run/'cache/daily.pkl')]
    rp.write_text(json.dumps(report))
    validation=R.read(vp);validation['report_sha256']=R.sha(rp);vp.write_text(json.dumps(validation))
    with patch.object(R,'verified_base',return_value=base),pytest.raises(AssertionError,match='daily_not_bound'):
        R.load_archive(run)


def test_base_integrity_contract_snapshot_and_artifact_drift(tmp_path):
    run=tmp_path/'runs/2026-11-16';run.mkdir(parents=True)
    contract=tmp_path/'recurring-contract.md';contract.write_text('old contract')
    digest=R.sha(contract)
    report=dict(date=run.name,status='partial',source_sha256={str(contract):digest})
    (run/'report.json').write_text(json.dumps(report))
    (run/'validation.json').write_text(json.dumps(dict(status='passed',report_sha256=R.sha(run/'report.json'))))
    for name in ('report.md','comparison.json','comparison.md','full-screen.csv','evaluate.py'):
        (run/name).write_text('original')
    receipt=dict(market_date=run.name,delivery_ready=True,artifact_sha256={p.name:R.sha(p) for p in run.iterdir()})
    (run/'run-receipt.json').write_text(json.dumps(receipt))
    assert R.verified_base(run)==report
    history=tmp_path/'contract-history';history.mkdir();(history/(digest+'.md')).write_text(contract.read_text())
    contract.write_text('new authorized contract')
    assert R.verified_base(run)==report
    (run/'comparison.md').write_text('changed')
    with pytest.raises(AssertionError,match='comparison.md'):R.verified_base(run)
    (run/'comparison.md').write_text('original');(history/(digest+'.md')).write_text('damaged')
    with pytest.raises(AssertionError):R.verified_base(run)


def test_full_report_relocates_all_relative_links(tmp_path):
    old=tmp_path/'old';new=old/'recent-shapes'
    text='[来源](comparison.md) ![图](charts/volume.png) [远程](https://example.org/a) [锚点](#x)'
    actual=R.relocate_links(text,old,new)
    assert '(../comparison.md)' in actual and '(../charts/volume.png)' in actual
    assert '(https://example.org/a)' in actual and '(#x)' in actual


def filter_fixture(stage='A'):
    days,scans,daily=fixture_scans()
    scans[days[-1]]['candidates']=[dict(scans[days[0]]['candidates'][0],stage=stage,limit_date=days[1])]
    result=R.combine(days,scans,[],daily,days)
    daily['close']=10.
    return result,daily,{(days[-1],'x'):9.}


@pytest.mark.parametrize('stage',['A','B'])
def test_today_limit_down_removes_current_and_historical_hits(stage):
    result,daily,prices=filter_fixture(stage)
    prices[(result['date'],'x')]=10.
    result=R.final_universe(result,daily,prices)
    assert result['rows']==[] and result['counts']['union_observed']==0
    assert result['counts']['current_'+stage]==0
    assert result['excluded_limit_down'][0]['occurrence_dates'][:2]==result['days'][:2]
    assert result['excluded_limit_down'][0]['limit_down_check']['close']==10.


def test_intraday_touch_and_past_limit_down_do_not_exclude_today():
    result,daily,prices=filter_fixture()
    daily['low']=9.;prices[(result['days'][0],'x')]=10.
    result=R.final_universe(result,daily,prices)
    assert result['counts']['union_observed']==1
    assert result['rows'][0]['limit_down_check']['status']=='clear'


@pytest.mark.parametrize('floor',[None,float('nan'),11.])
def test_unverified_today_limit_cannot_enter_candidate_list(floor):
    result,daily,prices=filter_fixture()
    prices[(result['date'],'x')]=floor
    result=R.final_universe(result,daily,prices)
    assert result['rows']==[] and result['status']=='partial'
    assert result['counts']['pending_limit_down']==1
    assert result['counts']['excluded_limit_down']==0
    assert '暂不列入名单' in R.markdown(result,compact=True)


def test_missing_today_close_stays_pending():
    result,daily,prices=filter_fixture()
    result=R.final_universe(result,daily[daily.trade_date!=result['date']],prices)
    assert len(result['pending_limit_down'])==1 and not result['rows']


@pytest.mark.parametrize('code',['920289.BJ','832000.BJ','430001.BJ'])
def test_beijing_excluded_from_both_current_and_history(code):
    result,daily,prices=filter_fixture('B')
    result['rows'][0]['code']=code
    result=R.final_universe(result,daily,prices)
    assert result['counts']['excluded_beijing']==1 and result['counts']['current_B']==0
    assert result['rows']==[] and result['pending_limit_down']==[]


def test_mobile_one_line_per_stock_and_old_details_only_in_full_report():
    result,daily,prices=filter_fixture()
    second=copy.deepcopy(result['rows'][0]);second.update(code='y',name='历史股票',current_stages=[],last_date=result['days'][0])
    result['rows'].append(second)
    result['counts'].update(union_observed=2,historical_only_observed=1)
    phone=R.markdown(result,compact=True)
    assert phone.count('- 股票 x｜')==1 and phone.count('- 历史股票 y｜')==1
    assert '今天仍满足（1）' in phone and '仅历史出现（1）' in phone
    assert '|形态日|' not in phone and '原严格' not in phone and '当日量价形态不满足' not in phone
    assert '|形态日|' in R.markdown(result)


def test_future_daily_rejected_even_when_hash_bound(tmp_path):
    run,base=fake_archive(tmp_path)
    cache=run/'cache/daily.pkl'
    pd.DataFrame([dict(ts_code='x',trade_date='2026-11-17')]).to_pickle(cache)
    rp=run/'enhanced-v2/report.json';vp=run/'enhanced-v2/validation.json'
    r=json.loads(rp.read_text());r['source_sha256'][str(cache)]=R.sha(cache);rp.write_text(json.dumps(r))
    v=json.loads(vp.read_text());v['report_sha256']=R.sha(rp);vp.write_text(json.dumps(v))
    with patch.object(R,'verified_base',return_value=base),pytest.raises(AssertionError,match='future_or_stale_daily'):
        R.load_archive(run)


def test_missing_limit_for_new_yin_candidate_is_exposed(tmp_path):
    rows=bars();day=rows[-1]['trade_date']
    source=tmp_path/'industries.pkl';pd.to_pickle({},source)
    archive=dict(report=dict(date=day,status='complete',source_sha256={str(source):R.sha(source)},
                             candidates=[],base_coverage={},base_exclusions={}),
                 states=[dict(code='x',name='股票',A='volume_shape_not_matched',B='no_recent_limit_up')],
                 daily=pd.DataFrame(rows),limits={})
    result=R.replay(archive)
    assert result['status']=='partial' and result['candidates']==[]
    assert result['uncomputed_A']==[dict(code='x',name='股票',reason='limit_data_missing')]


def test_current_match_preserves_past_occurrences_and_B_uses_new_event_status():
    days,scans,daily=fixture_scans()
    scans[days[-1]]['candidates']=[dict(scans[days[0]]['candidates'][0],stage='B',limit_date=days[1])]
    scans[days[-1]]['states'][0]['B']='matched'
    result=R.combine(days,scans,[],daily,days)
    assert result['rows'][0]['current_stages']==['B']
    assert result['rows'][0]['stages']==['A','B']
    assert result['counts']['historical_only_observed']==0


@pytest.mark.parametrize('unknown_stage',['A','B'])
def test_other_stage_unknown_cannot_be_presented_as_rejected(unknown_stage):
    days,scans,daily=fixture_scans()
    scans[days[-1]]['states'][0][unknown_stage]='limit_data_missing'
    result=R.combine(days,scans,[],daily,days)
    assert result['rows'][0]['current_status']=='当前待核验'
    assert result['rows'][0]['current_reasons'][unknown_stage]=='limit_data_missing'
    assert result['counts']['current_unknown_observed']==1
    assert '涨停价缺失待核验' in R.markdown(result)


def test_source_collection_failure_excludes_entire_day(tmp_path):
    days,scans,daily=fixture_scans()
    scans[days[1]]['candidates']=[]  # The failed first date has the sole candidate.
    run=tmp_path/'runs'/days[-1];out=run/'enhanced-v2';out.mkdir(parents=True)
    (out/'report.md').write_text('old report')
    reports={d:{'date':d} for d in days}
    archives={d:dict(report=reports[d],daily=daily,sources={}) for d in days}
    # Reproduce a file disappearing after load_archive, before source collection.
    archives[days[0]]['sources']={str(tmp_path/'gone/recurring-contract.md'):'not_found'}
    with patch.object(R.sqlite3,'connect',return_value=calendar_db()),patch.object(R,'market_days',return_value=(days,days)),patch.object(R,'load_archive',side_effect=lambda p:archives[p.name]),patch.object(R,'replay',side_effect=lambda a:scans[a['report']['date']]):
        result,_=R.prepare(run,reports[days[-1]],'USER')
    assert days[0] not in result['verified_dates']
    assert result['counts']['union_observed']==0
    assert result['gaps'][0]['date']==days[0]
