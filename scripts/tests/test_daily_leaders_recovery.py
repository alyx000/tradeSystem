"""仅用临时盘后归档、内存数据库和假源核验定向补采。"""
import datetime as dt
import json
import sqlite3
from unittest.mock import Mock

import pandas as pd
import pytest
import yaml

from providers.base import DataResult
from providers.tushare_provider import TushareProvider
from services.daily_leaders import readiness as R, recovery

DAY = '2026-11-26'
STAMP = DAY + 'T20:15:00'
NOW = dt.datetime.fromisoformat(DAY + 'T21:05:00+08:00')
GAP = {'error': 'source_failed'}


@pytest.fixture
def archive(tmp_path):
    path = tmp_path / DAY / 'post-market.yaml'
    path.parent.mkdir()
    env = {'date': DAY, 'generated_at': STAMP,
           'raw_data': {'date': DAY, 'sector_concept': GAP, 'limit_step': GAP, 'other': {'ok': 1}}}
    path.write_text(yaml.safe_dump(env))
    conn = sqlite3.connect(':memory:')
    conn.executescript('CREATE TABLE daily_market(date TEXT PRIMARY KEY, raw_data TEXT);'
                       'CREATE TABLE trade_calendar(date TEXT PRIMARY KEY, is_open INTEGER, exchange TEXT);')
    conn.execute('INSERT INTO trade_calendar VALUES (?,1,"SSE")', (DAY,))
    conn.execute('INSERT INTO daily_market VALUES (?,?)', (DAY, json.dumps(env)))
    conn.commit()
    yield conn, path
    conn.close()


def ranking():
    rows = [{'name': f'概念{i}', 'change_pct': 100-i, 'trade_date': DAY, 'ts_code': f'{i:06d}.TI'}
            for i in range(35)]
    return {'top': rows[:30], 'bottom': rows[30:][::-1], 'universe_count': 35}


def recover(archive, key='sector_concept', *, provider=None, input_by='launchd', now=lambda: NOW):
    conn, path = archive
    registry = Mock()
    registry.get_provider.return_value = provider
    issue = R.SourceNotReady('缺源', key, GAP, STAMP)
    return recovery.recover_source(conn, DAY, issue, registry, input_by=input_by,
                                   daily_dir=path.parent.parent, now=now)


def snapshots(archive):
    conn, path = archive
    return (yaml.safe_load(path.read_text())['raw_data'],
            json.loads(conn.execute('SELECT raw_data FROM daily_market').fetchone()[0])['raw_data'])


def test_only_failed_block_is_repaired_then_no_repeated_mutation(archive):
    provider = Mock()
    provider.get_sector_rankings.return_value = DataResult(data=ranking(), source='tushare:ths_daily')
    assert recover(archive, provider=provider)
    a, b = snapshots(archive)
    assert a == b and a['other'] == {'ok': 1} and a['limit_step'] == GAP
    assert a['sector_concept']['_recovery']['fact_date'] == DAY
    provider.get_sector_rankings.assert_called_once_with(DAY, 'concept', require_dated=True)
    assert not recover(archive, provider=provider)
    assert provider.get_sector_rankings.call_count == 1


@pytest.mark.parametrize('bad', ['wrong_date','no_date','invalid_pct','undated_source','empty','bad_bottom','short','duplicate','unordered'])
def test_unverified_rankings_never_replace_gap(archive, bad):
    data = ranking(); source = 'tushare:ths_daily'
    if bad == 'wrong_date': data['top'][0]['trade_date'] = '2026-11-25'
    if bad == 'no_date': del data['top'][0]['trade_date']
    if bad == 'invalid_pct': data['top'][0]['change_pct'] = float('nan')
    if bad == 'undated_source': source = 'akshare:board_concept'
    if bad == 'short': data['top'] = data['top'][:1]
    if bad == 'duplicate': data['bottom'][0] = data['top'][0]
    if bad == 'unordered': data['top'].reverse()
    if bad == 'empty': data['top'] = []
    if bad == 'bad_bottom': data['bottom'][0]['trade_date'] = '2026-11-25'
    provider = Mock(); provider.get_sector_rankings.return_value = DataResult(data=data, source=source)
    before = archive[1].read_bytes()
    assert not recover(archive, provider=provider)
    assert archive[1].read_bytes() == before and snapshots(archive)[1]['sector_concept'] == GAP


def test_targeted_ladder_recovery_resumes_after_yaml_only_publication(archive):
    provider = Mock()
    rows = [{'ts_code': '600001.SH', 'nums': 2, 'trade_date': '20261126'}]
    provider.get_limit_step.return_value = DataResult(data=rows, source='tushare:limit_step')
    # Simulate an interrupted commit after the verified block reached the file.
    assert recover(archive, 'limit_step', provider=provider)
    conn,path = archive
    db = json.loads(conn.execute('SELECT raw_data FROM daily_market').fetchone()[0])
    db['raw_data'].update(limit_step=GAP, db_only='preserved')
    conn.execute('UPDATE daily_market SET raw_data=?', (json.dumps(db),))
    conn.commit()
    before = path.read_bytes()
    assert recover(archive, 'limit_step', provider=None)
    assert before == path.read_bytes()
    assert snapshots(archive)[1]['db_only'] == 'preserved'
    assert snapshots(archive)[1]['limit_step']['data'] == rows
    provider.get_limit_step.assert_called_once_with(DAY)


@pytest.mark.parametrize('race', ['target_file','target_db','version','other_file','other_db','closed','deadline'])
def test_compare_before_publication(archive, race):
    conn, path = archive
    clock = [NOW]
    def fetch(*args, **kwargs):
        if race in ('target_file','version','other_file'):
            env = yaml.safe_load(path.read_text())
            if race == 'version': env['generated_at'] = DAY + 'T21:03:00'
            else: env['raw_data']['sector_concept' if race == 'target_file' else 'other'] = {'newer': 2}
            path.write_text(yaml.safe_dump(env))
        if race in ('target_db','other_db'):
            env = json.loads(conn.execute('SELECT raw_data FROM daily_market').fetchone()[0])
            env['raw_data']['sector_concept' if race == 'target_db' else 'other'] = {'newer': 2}
            conn.execute('UPDATE daily_market SET raw_data=?', (json.dumps(env),)); conn.commit()
        if race == 'closed':
            conn.execute('UPDATE trade_calendar SET is_open=0'); conn.commit()
        if race == 'deadline': clock[0] = NOW.replace(hour=23)
        return DataResult(data=ranking(), source='tushare:ths_daily')
    provider = Mock(); provider.get_sector_rankings.side_effect = fetch
    if race == 'deadline':
        with pytest.raises(TimeoutError): recover(archive, provider=provider, now=lambda:clock[0])
        assert snapshots(archive)[0]['sector_concept'] == GAP
    else:
        assert recover(archive, provider=provider) == race.startswith('other')
        a, b = snapshots(archive)
        if race.endswith('_db'): assert b['other' if race == 'other_db' else 'sector_concept'] == {'newer':2}
        if race.endswith('_file'): assert a['other' if race == 'other_file' else 'sector_concept'] == {'newer':2}


def test_manual_input_never_fetches(archive):
    provider = Mock()
    assert not recover(archive, input_by='USER', provider=provider)
    provider.initialize.assert_not_called()


@pytest.mark.parametrize('problem', [None, 'wrong_day', 'small', 'duplicate', 'missing_catalog_member', 'nan'])
def test_provider_verifies_whole_dated_universe_before_ranking(problem):
    p = TushareProvider.__new__(TushareProvider)
    data = ranking()
    scope = data['top'] + data['bottom']
    p.get_concept_catalog_snapshot = Mock(return_value=DataResult(data={'rows': scope}, source='catalog'))
    rows = [{'ts_code': r['ts_code'], 'trade_date': '20261126', 'pct_change': str(r['change_pct'])} for r in scope]
    if problem == 'small': rows = rows[:1]
    if problem == 'missing_catalog_member': rows.pop()
    if problem == 'duplicate': rows[-1] = rows[0]
    if problem == 'wrong_day': rows[-1]['trade_date'] = '20261125'
    if problem == 'nan': rows[-1]['pct_change'] = float('nan')
    p.pro=Mock(); p.pro.ths_daily.return_value = pd.DataFrame(rows)
    result = p.get_sector_rankings(DAY, 'concept', require_dated=True)
    assert result.success is (problem is None)
    if result.success:
        assert result.data['universe_count'] == 35
        assert result.data['top'][0]['change_pct'] == 100
        assert result.data['bottom'][0]['change_pct'] == 66


@pytest.mark.parametrize('wait,push,input_by,should_repair', [
    (True,True,'launchd',True), (True,True,'USER',False), (False,True,'launchd',False),
    (False,False,'USER',False),
])
def test_cli_only_scheduled_attempt_recovers_and_rebuilds_prefill(monkeypatch, wait, push, input_by, should_repair):
    from argparse import Namespace
    from cli import daily_leaders as cli
    from api.routes import review
    from api import deps
    conn = Mock()
    args = Namespace(date=DAY, wait_ready=wait, push=push, input_by=input_by, no_llm=True, max_candidates=10)
    registry = Mock()
    prefill = Mock(side_effect=[{'revision': 1}, {'revision': 2}])
    propose = Mock(side_effect=[R.SourceNotReady('来源缺失', 'limit_step', GAP, STAMP), {'ok': True}])
    repair = Mock(return_value=True)
    send = Mock(return_value=True)
    monkeypatch.setattr(cli, 'run_when_ready', lambda f,*a,**k: f())
    monkeypatch.setattr(cli, 'check_window', Mock())
    monkeypatch.setattr(cli, 'is_open_day', lambda *a: True)
    monkeypatch.setattr(cli, 'get_connection', lambda: conn)
    monkeypatch.setattr(deps, 'get_provider_registry', lambda: registry)
    monkeypatch.setattr(review, 'get_prefill', prefill)
    monkeypatch.setattr(cli.service, 'propose', propose)
    monkeypatch.setattr(recovery, 'recover_source', repair)
    monkeypatch.setattr(cli.renderer, 'render_markdown', Mock(return_value='稿'))
    monkeypatch.setattr(cli, '_push_to_dingtalk', send)
    if should_repair:
        cli._handle_propose(args)
        repair.assert_called_once()
        assert propose.call_count == prefill.call_count == 2
        assert propose.call_args.args[2] == {'revision': 2}
        assert propose.call_args.kwargs['require_ready'] is True
        send.assert_called_once()
    else:
        with pytest.raises(SystemExit) as err:
            cli._handle_propose(args)
        assert err.value.code == 75
        repair.assert_not_called()
        send.assert_not_called()


@pytest.mark.parametrize('problem', [None, 'missing_active', 'unknown_flag', 'short_catalog', 'past_date'])
def test_industry_recovery_only_requires_current_published_l2_codes(monkeypatch, problem):
    from providers import tushare_provider as tp

    class SessionDateTime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz else NOW.replace(tzinfo=None)

    monkeypatch.setattr(tp, 'datetime', SessionDateTime)
    p = TushareProvider.__new__(TushareProvider)
    p.pro = Mock()
    # 124 个已发布行业、10 个历史退役码，行情只给已发布行业。
    catalog = [{'index_code': f'{801000+i:06d}.SI', 'is_pub': str(int(i<124))} for i in range(134)]
    rows = [{'ts_code': r['index_code'], 'name': f'行业{i}', 'pct_change': 124-i, 'trade_date': '20261126'}
            for i,r in enumerate(catalog[:124])]
    if problem == 'missing_active':
        rows.pop()
    if problem == 'unknown_flag':
        catalog[-1]['is_pub'] = None
    if problem == 'short_catalog':
        catalog.pop()
    p.pro.index_classify.return_value = pd.DataFrame(catalog)
    p.pro.sw_daily.return_value = pd.DataFrame(rows)
    result = p.get_sector_rankings('2026-11-25' if problem == 'past_date' else DAY, 'industry', require_dated=True)
    assert result.success is (problem is None)
    if result.success:
        assert result.data['universe_count'] == 124
        assert len(result.data['top']) == 30 and len(result.data['bottom']) == 5


def test_post_rerun_keeps_only_verified_recovered_block(archive):
    from generators.report import _retain_recovered_sources

    provider = Mock()
    provider.get_sector_rankings.return_value = DataResult(data=ranking(), source='tushare:ths_daily')
    assert recover(archive, provider=provider)
    archived = snapshots(archive)[0]
    saved = archived['sector_concept']
    assert _retain_recovered_sources(archived, {'sector_concept': GAP}, DAY) == {'sector_concept': saved}
    assert not _retain_recovered_sources(archived, archived, DAY)
    assert not _retain_recovered_sources(archived, {}, '2026-11-25')


def test_concept_catalog_requires_qualified_identities_and_closed_pagination(monkeypatch):
    from providers import tushare_provider as tp

    class FixedDateTime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz else NOW.replace(tzinfo=None)

    monkeypatch.setattr(tp, 'datetime', FixedDateTime)
    p = TushareProvider.__new__(TushareProvider)
    p.pro = Mock()
    row = {'ts_code': '880001.TI', 'name': '示例', 'exchange': 'A', 'type': 'N'}
    p.pro.query.side_effect = [pd.DataFrame([row]), pd.DataFrame()]
    result = p.get_concept_catalog_snapshot(DAY)
    assert result.success and result.data['pages'] == [1, 0]
    assert [call.kwargs['offset'] for call in p.pro.query.call_args_list] == [0, 1]
    p.pro.query.side_effect = [pd.DataFrame([row]), pd.DataFrame([row])]
    assert not p.get_concept_catalog_snapshot(DAY).success
    assert not p.get_concept_catalog_snapshot('2026-11-25').success
