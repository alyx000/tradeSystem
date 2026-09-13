"""停牌核验必须与同日原始事实绑定，不能掩盖真正的失败。"""
import copy
import json
import sqlite3

import pytest

from services.emotion_leader.suspensions import (
    MISSING_QUOTE, load_suspensions, reconcile_suspensions, reconcile_report_file,
)
from services.review_feedback_trends import _core_point, render_feedback_trends

DAY = '2026-09-11'
CODE = '605577.SH'


def database():
    conn = sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE raw_interface_payloads (id INTEGER,interface_name TEXT,biz_date TEXT,target_date TEXT,provider TEXT,status TEXT,row_count INTEGER,payload_json TEXT)')
    return conn


def insert(conn, *, status='success', **changes):
    item = dict(ts_code=CODE, code=CODE, trade_date='20260911', suspend_type='S', suspend_timing=None)
    item.update(changes)
    payload = dict(biz_date=DAY, interface_name='regulatory_suspend', provider='tushare:suspend_d', params={'trade_date':'20260911'}, rows=[item])
    conn.execute('INSERT INTO raw_interface_payloads VALUES (1,?,?,?,?,?,?,?)', ('regulatory_suspend',DAY,DAY,'tushare:suspend_d',status,1,json.dumps(payload)))


def report():
    return dict(date=DAY, status='partial', active=[dict(code=CODE,name='龙版传媒',metric_as_of=DAY,metric_status='source_failed',metric_error=MISSING_QUOTE,current_state='未计算')], archived=[dict(code='000001.SZ',metric_status='cached_archived')],promoted_today=[],
                summary=dict(active_count=1,today_limit_up_count=0,today_limit_down_count=0,new_peak_count=0),
                source_errors=[f'{CODE}:{MISSING_QUOTE}'],missing_dates=[],coverage=dict(expected_open_days=64,loaded_limit_days=64))


def proof():
    c=database(); insert(c)
    return load_suspensions(c,DAY)


def test_verified_suspension_is_na_with_provenance_and_no_archive_mutation():
    original=report(); before=copy.deepcopy(original)
    result=reconcile_suspensions(original,proof())
    assert original==before
    assert result['status']=='ok' and result['source_errors']==[]
    row=result['active'][0]
    assert row['metric_status']=='suspended' and row['current_state']=='停牌'
    assert row['suspension_evidence']['raw_payload_id']==1
    assert 'today_pct_chg' not in row and 'metric_error' not in row
    assert result['summary']['suspended_count']==1
    point=_core_point(result,DAY)
    assert point['status']=='ok' and point['sample']==0 and point['total']==1
    assert point['suspended']==1 and point['missing']==0
    assert 'peak' not in point['values']
    html,_=render_feedback_trends({'core':[point]},DAY)
    assert '龙版传媒' in html and '核验证据' in html and '全天停牌' in html
    assert reconcile_suspensions(result,proof())==result


@pytest.mark.parametrize('change', [dict(trade_date='20260910'),dict(suspend_type='R'),dict(suspend_timing='09:30-10:30'),dict(ts_code='000001.SZ'),dict(code='000001.SZ')])
def test_wrong_day_resumption_intraday_or_conflicting_identity_does_not_exempt(change):
    c=database();insert(c,**change)
    p=report()
    assert reconcile_suspensions(p,load_suspensions(c,DAY))==p


def test_failed_latest_snapshot_no_fallback_and_absent_table():
    c=database();insert(c)
    c.execute("INSERT INTO raw_interface_payloads VALUES (2,'regulatory_suspend',?,?, 'tushare:suspend_d','failed',0,'{}')",(DAY,DAY))
    assert load_suspensions(c,DAY)=={}
    assert load_suspensions(sqlite3.connect(':memory:'),DAY)=={}


@pytest.mark.parametrize('change', [dict(metric_error='复权因子缺失或与行情错位'),dict(metric_as_of='2026-09-10'),dict(today_pct_chg=0),dict(current_close_qfq=18.67),dict(current_state='涨停'),dict(new_peak_today=True)])
def test_other_failures_stale_metrics_and_conflicting_trading_facts_not_hidden(change):
    p=report();p['active'][0].update(change)
    assert reconcile_suspensions(p,proof())==p


@pytest.mark.parametrize('reason', ['history','error','unknown_partial','whole_failure','other_stock'])
def test_unrelated_gaps_remain_partial_or_failed(reason):
    p=report()
    if reason=='history':p['coverage']['loaded_limit_days']=63
    elif reason=='error':p['source_errors'].append('行业缺失')
    elif reason=='unknown_partial':p['source_errors']=[]
    elif reason=='whole_failure':p['status']='source_failed'
    else:
        p['active'].append(dict(code='000002.SZ',metric_status='source_failed'))
        p['summary']['active_count']=2
    result=reconcile_suspensions(p,proof())
    assert result['status']==p['status']


def test_reopening_does_not_carry_old_evidence_and_invalid_file_is_safe(tmp_path):
    p=report();p['date']='2026-09-14'
    assert reconcile_suspensions(p,proof())==p
    assert reconcile_report_file(p,tmp_path/'absent.db',p['date'])==p


def test_daily_service_uses_same_readonly_suspension_reconciliation(monkeypatch):
    from services.emotion_leader import service
    c=database();insert(c)
    p=report()
    monkeypatch.setattr(service,'load_history',lambda *a,**kw:dict(coverage=p['coverage'],missing_dates=[],errors=[],target_ok=True))
    monkeypatch.setattr(service,'discover_lifecycles',lambda *a,**kw:dict(promoted=[dict(code=CODE,name='龙版传媒',launch_date='2026-09-01',last_limit_up_date='2026-09-08')],candidates=[],trade_dates=['2026-09-08',DAY],current_limit_up_codes=set(),current_down_codes=set(),height_breakthrough=dict(status='none',leaders=[])))
    monkeypatch.setattr(service,'_industry_map',lambda r:({},None,''))
    monkeypatch.setattr(service,'fetch_metrics',lambda *a:dict(metric_status='source_failed',metric_error=MISSING_QUOTE))
    result=service.run_daily(c,None,DAY)
    assert result['status']=='ok' and result['summary']['suspended_count']==1
    assert result['active'][0]['current_state']=='停牌'


@pytest.mark.parametrize('reason',['failed','resumed','db_absent'])
def test_saved_suspension_is_revoked_when_latest_evidence_disappears(reason,tmp_path):
    saved=reconcile_suspensions(report(),proof())
    if reason=='db_absent':
        result=reconcile_report_file(saved,tmp_path/'missing.db',DAY)
    else:
        c=database();insert(c,suspend_type='R' if reason=='resumed' else 'S',status='failed' if reason=='failed' else 'success')
        result=reconcile_suspensions(saved,load_suspensions(c,DAY))
    assert result['status']=='partial'
    assert result['active'][0]['metric_status']=='source_failed'
    assert result['summary']['suspended_count']==0 and result['summary']['unresolved_count']==1
    assert result['source_errors']==[f'{CODE}:{MISSING_QUOTE}']
    assert _core_point(result,DAY)['status']=='partial'


def test_unexplained_partial_never_upgrades_on_repeated_checks_or_evidence_recovery():
    p=report();p['source_errors']=[]
    once=reconcile_suspensions(p,proof())
    twice=reconcile_suspensions(once,proof())
    assert once==twice and twice['status']=='partial'
    revoked=reconcile_suspensions(twice,{})
    recovered=reconcile_suspensions(revoked,proof())
    assert recovered['status']=='partial' and recovered['summary']['suspended_count']==1
