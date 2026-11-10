"""连板名单口径、金额单位、缺失与采集复用契约。"""
from copy import deepcopy
from datetime import datetime
import sqlite3
from unittest.mock import MagicMock, patch
from contextlib import ExitStack
from zoneinfo import ZoneInfo

import pytest

from analyzers.consecutive_pool import calculate, collect, from_archive
from providers.base import DataResult

DAY = '2026-11-10'


def evidence(day=DAY):
    stocks = [dict(code=f'{i:06d}.SZ', name=name, limit_times=height, limit_times_verified=True,
                   amount_billion=99999999)
              for i, name, height in [(1, '甲', 2), (2, '乙', 3), (3, '首板', 1), (4, '*ST排除', 7)]]
    return dict(trade_date=day, limit_up=dict(_source='tushare:limit_list_d', count=4, stocks=stocks),
                quote_source='tushare:daily', quotes=[dict(ts_code=f'{i:06d}.SZ', trade_date=day.replace('-', ''), amount=amount)
                    for i, amount in [(1, 123456), (2, 500000), (3, 123456789), (4, 1000000)]])


def test_count_and_amount_share_exact_same_pool_ignore_legacy_amount():
    e = evidence()
    p = calculate(DAY, e)
    assert p['count'] == 2 and p['amount_count'] == 2
    assert p['amount_yi'] == pytest.approx(6.23456)
    assert p['status'] == 'complete'
    assert {r['name'] for r in p['rows']} == {'甲', '乙'}
    assert e == evidence()


@pytest.mark.parametrize('mutation', ['duplicate', 'count', 'date', 'unknown-source', 'error', 'name', 'nan-height', 'bool-height'])
def test_invalid_membership_never_emits_zero(mutation):
    e = evidence()
    if mutation == 'duplicate':
        e['limit_up']['stocks'][1]['code'] = '000001'
    elif mutation == 'count':
        e['limit_up']['count'] = 5
    elif mutation == 'date':
        e['trade_date'] = '2026-11-09'
    elif mutation == 'unknown-source':
        e['limit_up']['_source'] = 'unknown'
    elif mutation == 'error':
        e['limit_up']['error'] = 'failed'
    elif mutation == 'name':
        e['limit_up']['stocks'][0]['name'] = ''
    else:
        e['limit_up']['stocks'][0]['limit_times'] = float('nan') if mutation == 'nan-height' else True
    p = calculate(DAY, e)
    assert p['count'] is None and p['amount_yi'] is None
    assert p['status'] in {'partial', 'source_failed'}


def test_n_days_m_boards_not_continuous_and_unknown_first_board_not_zero():
    e = evidence()
    e['limit_up']['stocks'][0].update(limit_times=1, limit_stat='2/3')
    assert calculate(DAY, e)['count'] == 1
    e['limit_up']['stocks'][0].pop('limit_times_verified')
    p = calculate(DAY, e)
    assert p['count'] is None and p['amount_yi'] is None and p['unknown_codes'] == ['000001.SZ']


@pytest.mark.parametrize('mutation', ['missing', 'nan', 'negative', 'bool', 'duplicate', 'date', 'unit'])
def test_amount_failure_keeps_verified_count_but_not_partial_sum(mutation):
    e = evidence()
    if mutation == 'missing': e['quotes'].pop(0)
    elif mutation == 'duplicate': e['quotes'].append(deepcopy(e['quotes'][0]))
    elif mutation == 'date': e['quotes'][0]['trade_date'] = '20261109'
    elif mutation == 'unit': e['quote_source'] = 'unknown:daily'
    else: e['quotes'][0]['amount'] = {'nan': float('nan'), 'negative': -1, 'bool': False}[mutation]
    p = calculate(DAY, e)
    assert p['count'] == 2 and p['amount_yi'] is None and p['status'] == 'partial'


def test_zero_pool_only_with_complete_membership():
    e = evidence()
    for r in e['limit_up']['stocks']: r['limit_times'] = 1
    e['quotes'] = None
    p = calculate(DAY, e)
    assert p['count'] == p['amount_yi'] == 0 and p['status'] == 'complete'
    assert from_archive({}, DAY)['count'] is None


def calendar(tmp_path, is_open=1):
    path = tmp_path/'calendar.db'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE trade_calendar (date TEXT, exchange TEXT, is_open INTEGER)')
        conn.execute('INSERT INTO trade_calendar VALUES (?, ?, ?)', (DAY, 'SSE', is_open))
    return path


def test_collection_guards_and_evidence_recalculation(tmp_path):
    e = evidence()
    db = calendar(tmp_path)
    kwargs = dict(limit_up=e['limit_up'], quote_result=DataResult(data=e['quotes'], source='tushare:daily'), db_path=db)
    assert collect(DAY, **kwargs, now=datetime(2026, 11, 10, 15, tzinfo=ZoneInfo('Asia/Shanghai')))['status'] == 'skipped'
    p = collect(DAY, **kwargs, now=datetime(2026, 11, 10, 20, tzinfo=ZoneInfo('Asia/Shanghai')))
    assert p['status'] == 'complete'
    p.update(count=99, amount_yi=99999)
    got = from_archive(dict(date=DAY, raw_data={'consecutive_pool': p}), DAY)
    assert got['count'] == 2 and got['amount_yi'] == pytest.approx(6.23456)
    with sqlite3.connect(db) as conn: conn.execute('UPDATE trade_calendar SET is_open=0')
    assert collect(DAY, **kwargs)['status'] == 'skipped'


def test_old_archive_readonly_recalculation_with_missing_amount():
    e = evidence()
    p = dict(date=DAY, raw_data={'limit_up': e['limit_up']})
    assert from_archive(p, DAY)['count'] == 2
    assert from_archive(p, DAY)['amount_yi'] is None
    p['raw_data']['zhong_prefix_effect'] = dict(trade_date=DAY, evidence=e)
    before = deepcopy(p)
    assert from_archive(p, DAY)['amount_yi'] == pytest.approx(6.23456)
    assert p == before
    p['raw_data']['consecutive_pool'] = None
    assert from_archive(p, DAY)['status'] == 'source_failed'  # explicit corruption must not fallback


@pytest.mark.parametrize('bad', [None, [], 'bad'])
def test_bad_old_amount_evidence_preserves_count(bad):
    p = dict(date=DAY, raw_data=dict(limit_up=evidence()['limit_up'],
             zhong_prefix_effect=dict(trade_date=DAY, evidence=bad)))
    result = from_archive(p, DAY)
    assert result['status'] == 'partial' and result['count'] == 2 and result['amount_yi'] is None


def test_post_reuses_existing_quote_call_and_report_renders(tmp_path):
    from collectors.market import MarketCollector
    from generators.report import ReportGenerator
    e = evidence()
    def call(method, *args, **kwargs):
        if method == 'get_limit_up_list': return DataResult(data=deepcopy(e['limit_up']), source='tushare:limit_list_d')
        if method == 'get_market_daily_quotes': return DataResult(data=e['quotes'], source='tushare:daily')
        return DataResult(data=None, source='test', error='not configured')
    reg = MagicMock()
    reg.call.side_effect = call
    real_collect = collect
    def scoped(day, **kwargs):
        return real_collect(day, **kwargs, db_path=db, now=datetime(2026, 11, 10, 20, tzinfo=ZoneInfo('Asia/Shanghai')))
    db = calendar(tmp_path)
    with ExitStack() as stack:
        stack.enter_context(patch('collectors.market.BASE_DIR', tmp_path))
        stack.enter_context(patch('analyzers.consecutive_pool.collect', side_effect=scoped))
        # 邻接统计各有独立采集与日历，隔离外部调用及生产数据库。
        for target in ['analyzers.low_price_effect.collect_low_price_effect', 'analyzers.ipo_effect.collect_ipo_effect',
                       'analyzers.market_strength.collect', 'analyzers.zhong_prefix_effect.collect',
                       'analyzers.sector_increment.collect', 'analyzers.theme_launch.collect',
                       'analyzers.board_break_feedback.collect_board_break_feedback',
                       'analyzers.strong_stock_feedback.collect',
                       'services.sector_adjustment_risk.collector.collect']:
            stack.enter_context(patch(target, return_value={}))
        stack.enter_context(patch('analyzers.StyleAnalyzer'))
        stack.enter_context(patch('analyzers.NodeSignalAnalyzer'))
        c = MarketCollector(reg)
        c._get_broken_board_count = MagicMock(return_value=0)
        c._rhythm_analyzer = MagicMock()
        c._rhythm_analyzer.analyze.return_value = []
        raw = c.collect_post_market(DAY)
    assert raw['consecutive_pool']['amount_yi'] == pytest.approx(6.23456)
    assert sum(c.args[0] == 'get_market_daily_quotes' for c in reg.call.call_args_list) == 1
    generator = ReportGenerator()
    generator.daily_dir = tmp_path/'output'
    # 报告写到测试目录；最小原始事实避免其他模块历史统计。
    report, path = generator.generate_post_market(DAY, {'consecutive_pool': raw['consecutive_pool']})
    assert str(tmp_path) in path
    assert '连板股票池（2只）（6.23亿元）' in report
