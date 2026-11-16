from __future__ import annotations

import copy
import datetime as dt
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from services.daily_leaders import readiness as R, retry, service

DATE = '2026-11-16'


@pytest.fixture
def snapshot():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript('''CREATE TABLE trade_calendar(date TEXT,is_open INTEGER,exchange TEXT);
        CREATE TABLE daily_market(date TEXT,raw_data TEXT);
        CREATE TABLE raw_interface_payloads(id INTEGER,interface_name TEXT,biz_date TEXT,
        target_date TEXT,status TEXT,row_count INTEGER,payload_json TEXT,inserted_at TEXT);''')
    conn.execute('INSERT INTO trade_calendar VALUES (?,1,"SSE")', (DATE,))
    raw = {'date': DATE}
    for key in R.DATED_BLOCKS + ('sector_industry', 'sector_concept'):
        raw[key] = {'data': [{'trade_date': '20261116', 'name': '测试', 'ts_code': '600001.SH',
                             'nums': '2', 'change_pct': 1, 'pct_chg': 1, 'up_nums': 2, 'pct_change': 1, 'net_amount': 2}], '_source': 'test'}
    env = {'date': DATE, 'generated_at': DATE+'T20:18:00', 'raw_data': raw}
    conn.execute('INSERT INTO daily_market VALUES (?,?)', (DATE, json.dumps(env)))
    prefill = {'date': DATE, 'is_trading_day': True, 'market': copy.deepcopy(raw)}
    yield conn, env, prefill
    conn.close()


def save(conn, env):
    conn.execute('UPDATE daily_market SET raw_data=?', (json.dumps(env),))


def test_ready_snapshot(snapshot):
    conn, env, prefill = snapshot
    assert R.check_post_market(conn, DATE, prefill)['status'] == 'ready'


@pytest.mark.parametrize('problem', ['missing', 'old', 'invalid', 'before_close', 'future', 'inner_old'])
def test_snapshot_not_ready(snapshot, problem):
    conn, env, prefill = snapshot
    if problem == 'missing':
        conn.execute('DELETE FROM daily_market')
    elif problem == 'invalid':
        conn.execute("UPDATE daily_market SET raw_data='[]'")
    else:
        if problem == 'old': env['date'] = '2026-11-13'
        if problem == 'inner_old': env['raw_data']['date'] = '2026-11-13'
        if problem == 'before_close': env['generated_at'] = DATE+'T10:00:00'
        if problem == 'future': env['generated_at'] = '2026-11-17T20:00:00'
        save(conn, env)
    with pytest.raises(R.DataNotReady):
        R.check_post_market(conn, DATE, prefill)


@pytest.mark.parametrize('key', R.DATED_BLOCKS + ('sector_industry', 'sector_concept'))
def test_missing_required_source_blocks_publication(snapshot, key):
    conn, env, prefill = snapshot
    del env['raw_data'][key]
    save(conn, env)
    with pytest.raises(R.DataNotReady): R.check_post_market(conn, DATE, prefill)


@pytest.mark.parametrize('problem', ['old_row', 'no_date', 'failed', 'empty', 'prefill_race'])
def test_source_integrity(snapshot, problem):
    conn, env, prefill = snapshot
    block = env['raw_data']['limit_step']
    if problem == 'old_row': block['data'][0]['trade_date'] = '20261113'
    if problem == 'no_date': del block['data'][0]['trade_date']
    if problem == 'failed': block['error'] = 'timeout'
    if problem == 'empty': block['data'] = []
    if problem == 'prefill_race': prefill['market']['limit_step']['data'][0]['nums'] = 9
    save(conn, env)
    with pytest.raises(R.DataNotReady): R.check_post_market(conn, DATE, prefill)


def test_successfully_empty_ladder_requires_dated_receipt(snapshot):
    conn, env, prefill = snapshot
    del env['raw_data']['limit_step']; prefill['market'].pop('limit_step')
    save(conn, env)
    conn.execute('INSERT INTO raw_interface_payloads VALUES (1,?,?,?,"empty",0,"[]","now")',
                 ('limit_step', DATE, DATE))
    assert R.check_post_market(conn, DATE, prefill)['status'] == 'ready'
    assert prefill['market']['limit_step']['data'] == []


def test_calendar_missing_closed_and_wrong_exchange(snapshot):
    conn, _, _ = snapshot
    conn.execute('UPDATE trade_calendar SET is_open=0')
    assert R.is_open_day(conn, DATE) is False
    conn.execute('UPDATE trade_calendar SET exchange="UNKNOWN"')
    with pytest.raises(R.DataNotReady): R.is_open_day(conn, DATE)
    conn.execute('DELETE FROM trade_calendar')
    with pytest.raises(R.DataNotReady): R.is_open_day(conn, DATE)


def registry_data():
    quotes = [{'ts_code': f'{600000+i}.SH', 'trade_date': '20261116', 'pct_chg': 10,
               'amount': 3000000, 'close': 10} for i in range(4000)]
    basics = [{'ts_code': r['ts_code'], 'name': f'样本{i}'} for i, r in enumerate(quotes)]
    industry = {r['ts_code']: {'sw_l2': '行业', 'name': basics[i]['name']} for i,r in enumerate(quotes)}
    data = {'get_market_daily_quotes': quotes, 'get_stock_basic_list': basics,
            'get_stock_sw_industry_map': industry}
    provider = SimpleNamespace(call=lambda name,*args: SimpleNamespace(success=True,data=data[name],source='test'))
    return data, provider


@pytest.mark.parametrize('problem', [None, 'old', 'no_date', 'short', 'duplicate', 'nan',
                                    'negative', 'identity', 'coverage', 'industry', 'failed'])
def test_quote_readiness(problem):
    data, provider = registry_data()
    quotes = data['get_market_daily_quotes']
    if problem == 'old': quotes[0]['trade_date'] = '20261113'
    if problem == 'no_date': quotes[0].pop('trade_date')
    if problem == 'short': quotes.pop()
    if problem == 'duplicate': quotes[-1] = quotes[0]
    if problem == 'nan': quotes[0]['pct_chg'] = float('nan')
    if problem == 'negative': quotes[0]['amount'] = -1
    if problem == 'identity': data['get_stock_basic_list'][0]['name'] = ''
    if problem == 'coverage':
        data['get_stock_basic_list'].extend({'ts_code':f'{700000+i}.SH','name':'extra'} for i in range(100))
    if problem == 'industry': data['get_stock_sw_industry_map'] = {}
    if problem == 'failed': data['get_market_daily_quotes'] = None
    checked = R.CheckedRegistry(provider)
    prefill = service.attach_market_quotes({}, DATE, checked)
    if problem:
        with pytest.raises(R.DataNotReady): checked.validate(DATE, prefill)
    else:
        assert checked.validate(DATE, prefill)['quote_count'] == 4000


def test_service_stops_before_llm_and_report_write(snapshot, tmp_path, monkeypatch):
    conn, env, prefill = snapshot
    data, provider = registry_data(); data['get_market_daily_quotes'] = []
    llm = Mock(); writer = Mock()
    monkeypatch.setattr(service, 'enrich_with_llm_reason', llm)
    monkeypatch.setattr(service, 'write_proposal', writer)
    with pytest.raises(R.DataNotReady):
        service.propose(conn, DATE, prefill, registry=provider, require_ready=True, output_root=tmp_path)
    llm.assert_not_called(); writer.assert_not_called()


def test_retry_late_arrival_and_no_delivery_retry():
    attempt = Mock(side_effect=[R.DataNotReady('missing'), R.DataNotReady('old'), {'ready': True}])
    sleep = Mock()
    now = lambda: dt.datetime(2026,11,16,21,tzinfo=R.SHANGHAI)
    assert retry.run_when_ready(attempt, DATE, wait=True, now=now, sleep=sleep) == {'ready': True}
    assert attempt.call_count == 3 and sleep.call_count == 2
    attempt = Mock(side_effect=RuntimeError('unexpected failure'))
    with pytest.raises(RuntimeError): retry.run_when_ready(attempt, DATE, wait=True, now=now, sleep=sleep)
    assert attempt.call_count == 1


def test_retry_deadline_and_date_rollover():
    clock = [dt.datetime(2026,11,16,22,59,tzinfo=R.SHANGHAI)]
    def sleep(seconds): clock[0] += dt.timedelta(seconds=seconds)
    attempt = Mock(side_effect=R.DataNotReady('missing'))
    with pytest.raises(TimeoutError):
        retry.run_when_ready(attempt, DATE, wait=True, now=lambda: clock[0], sleep=sleep)
    assert attempt.call_count == 1
    for value in [dt.datetime(2026,11,17,0,tzinfo=R.SHANGHAI), dt.datetime(2026,11,16,20,tzinfo=R.SHANGHAI)]:
        with pytest.raises(TimeoutError): retry.check_window(DATE, value)


def test_manual_push_does_not_wait():
    attempt = Mock(side_effect=R.DataNotReady('missing')); sleep = Mock()
    with pytest.raises(R.DataNotReady): retry.run_when_ready(attempt, DATE, sleep=sleep)
    assert attempt.call_count == 1; sleep.assert_not_called()


def test_inconsistent_prefill_calendar_cannot_publish_skip_report(snapshot):
    conn, _, prefill = snapshot
    prefill['is_trading_day'] = False
    with pytest.raises(R.DataNotReady): R.check_post_market(conn, DATE, prefill)


@pytest.mark.parametrize('key,field,value', [('limit_step','nums',None),
 ('limit_step','nums',1.5), ('concept_moneyflow_ths','net_amount',None),
 ('limit_cpt_list','pct_chg',float('nan'))])
def test_invalid_critical_source_fields(snapshot,key,field,value):
    conn,env,prefill = snapshot
    env['raw_data'][key]['data'][0][field]=value
    prefill['market']=copy.deepcopy(env['raw_data']);save(conn,env)
    with pytest.raises(R.DataNotReady): R.check_post_market(conn, DATE, prefill)


@pytest.mark.parametrize('key', ['sector_industry', 'sector_concept'])
@pytest.mark.parametrize('row', [{}, {'name':'行业'}, {'name':'  ','change_pct':1},
                                {'name':'行业','change_pct':float('nan')},
                                {'name':'行业','change_pct':False}])
def test_sector_rankings_must_have_valid_names_and_changes(snapshot,key,row):
    conn,env,prefill = snapshot
    env['raw_data'][key]['data']=[row]
    prefill['market']=copy.deepcopy(env['raw_data']);save(conn,env)
    with pytest.raises(R.DataNotReady): R.check_post_market(conn, DATE, prefill)
