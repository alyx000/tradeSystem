from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
import sqlite3
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
import yaml

from analyzers.consecutive_limit_down import calculate, collect, render
from collectors.market import MarketCollector
from generators.report import ReportGenerator

DAY = '2026-11-16'
DAYS = ['2026-11-11', '2026-11-12', '2026-11-13', DAY]
NOW = datetime(2026, 11, 16, 20, tzinfo=ZoneInfo('Asia/Shanghai'))


def section(*codes, source='tushare:limit_list_d'):
    rows = [dict(code=code, name='测试' + code, pct_chg=-10) for code in codes]
    return dict(count=len(rows), stocks=rows, _source=source)


def sources():
    return {DAYS[0]: section(), DAYS[1]: section('600001.SH'),
            DAYS[2]: section('600001.SH', '000001.SZ'),
            DAY: section('600001.SH', '000001.SZ', '300001.SZ')}


def test_counts_streak_start_and_weekend():
    block = calculate(DAY, DAYS, sources())
    assert block['count'] == block['verified_count'] == 2
    assert block['market_count'] is None and block['status'] == 'partial'
    rows = {r['code']: r for r in block['rows']}
    assert (rows['600001.SH']['days'], rows['600001.SH']['start_date']) == (3, DAYS[1])
    assert rows['000001.SZ']['days'] == 2
    assert rows['600001.SH']['boundary']['trade_date'] == DAYS[0]


@pytest.mark.parametrize('bad', [None, {'error': 'down'}, {}, {'stocks': [], 'count': 0}])
def test_missing_previous_is_unknown_not_zero(bad):
    evidence = sources()
    evidence[DAYS[-2]] = bad
    block = calculate(DAY, DAYS, evidence)
    assert block['count'] is None and block['verified_count'] == 0
    assert len(block['unknown_rows']) == 3


def test_older_gap_does_not_erase_known_count_or_invent_start():
    evidence = sources()
    evidence[DAYS[1]] = None
    block = calculate(DAY, DAYS, evidence)
    assert block['count'] == 2
    assert all(r['days'] == 2 and not r['days_exact'] and r['start_date'] is None for r in block['rows'])


def test_window_left_edge_is_lower_bound():
    block = calculate(DAY, DAYS[-2:], sources())
    assert block['count'] == 2
    assert all(not r['days_exact'] for r in block['rows'])


def test_provider_switch_and_bare_code_join():
    evidence = sources()
    evidence[DAYS[-2]] = section('600001', source='akshare:stock_zt_pool_dtgc_em')
    block = calculate(DAY, DAYS, evidence)
    assert block['verified_count'] == 1 and block['count'] is None
    assert {r['code'] for r in block['unknown_rows']} == {'000001.SZ', '300001.SZ'}


@pytest.mark.parametrize('mutation', ['duplicate', 'count', 'date', 'name', 'status', 'source'])
def test_invalid_current_fails_closed(mutation):
    evidence = sources()
    current = evidence[DAY]
    if mutation == 'duplicate':
        current['stocks'].append(deepcopy(current['stocks'][0]))
        current['count'] += 1
    elif mutation == 'count':
        current['count'] = 9
    elif mutation == 'date':
        current['stocks'][0]['trade_date'] = '20261113'
    elif mutation == 'name':
        current['stocks'][0]['name'] = ''
    elif mutation == 'status':
        current['status'] = 'partial'
    else:
        current['_source'] = 'unverified'
    block = calculate(DAY, DAYS, evidence)
    assert block['status'] == 'source_failed' and block['count'] is None
    assert block['verified_count'] is None
    assert '0只' not in '\n'.join(render(block))


def test_st_separate_b_share_delisted_excluded():
    evidence = {d: section('600001.SH', '000001.SZ', '200001.SZ', '600002.SH') for d in DAYS}
    for s in evidence.values():
        s['stocks'][1]['name'] = '*ST测试'
        s['stocks'][3]['name'] = '退市测试'
    block = calculate(DAY, DAYS, evidence)
    assert block['count'] == 1 and block['st_verified_count'] == 1
    assert len(block['rows']) == 2


def test_valid_empty_is_zero_in_source_scope_only():
    block = calculate(DAY, DAYS, {DAY: section()})
    assert block['count'] == 0 and block['market_count'] is None
    assert block['status'] == 'partial'


@pytest.fixture
def setup_archive(tmp_path):
    db = tmp_path/'test.db'
    conn = sqlite3.connect(db)
    conn.execute('CREATE TABLE trade_calendar (exchange TEXT, date TEXT, is_open INTEGER)')
    end = date.fromisoformat(DAY)
    for i in range(61):
        d = end - timedelta(days=i)
        conn.execute('INSERT INTO trade_calendar VALUES (?,?,?)', ('SSE', str(d), int(d.weekday() < 5)))
    conn.commit()
    conn.close()
    daily = tmp_path/'daily'
    for d, s in sources().items():
        path = daily/d/'post-market.yaml'
        path.parent.mkdir(parents=True)
        path.write_text(yaml.safe_dump(dict(date=d, raw_data=dict(limit_down=s))))
    return db, daily


def test_collect_calendar_and_archive(setup_archive):
    db, daily = setup_archive
    block = collect(DAY, limit_down=sources()[DAY], daily_dir=daily, db_path=db, now=NOW)
    assert block['count'] == 2
    assert DAYS[-2] == block['calendar_days'][-2]


def test_corrupt_older_fields_preserve_confirmed_count(setup_archive):
    db, daily = setup_archive
    path = daily/DAYS[1]/'post-market.yaml'
    payload = yaml.safe_load(path.read_text())
    payload['raw_data']['limit_down']['_source'] = []
    path.write_text(yaml.safe_dump(payload))
    block = collect(DAY, limit_down=sources()[DAY], daily_dir=daily, db_path=db, now=NOW)
    assert block['count'] == 2 and block['status'] == 'partial'
    assert all(not r['days_exact'] for r in block['rows'])


@pytest.mark.parametrize('bad', [None, [], 'bad'])
def test_invalid_current_row_isolated(setup_archive, bad):
    db, daily = setup_archive
    current = section('600001.SH')
    current['stocks'] = [bad]
    block = collect(DAY, limit_down=current, daily_dir=daily, db_path=db, now=NOW)
    assert block['status'] == 'source_failed' and block['count'] is None


@pytest.mark.parametrize('gap', ['whole_directory', 'wrong_date', 'calendar'])
def test_collect_never_skips_missing_open_day(setup_archive, gap):
    db, daily = setup_archive
    path = daily/DAYS[-2]/'post-market.yaml'
    if gap == 'whole_directory':
        path.unlink()
        path.parent.rmdir()
    elif gap == 'wrong_date':
        path.write_text(yaml.safe_dump(dict(date=DAYS[1], raw_data=dict(limit_down=section()))))
    else:
        with sqlite3.connect(db) as conn:
            conn.execute('DELETE FROM trade_calendar WHERE date=?', (DAYS[-2],))
    block = collect(DAY, limit_down=sources()[DAY], daily_dir=daily, db_path=db, now=NOW)
    assert block['count'] is None


def test_time_and_closed_day_guards(setup_archive):
    db, daily = setup_archive
    assert collect(DAY, limit_down=section(), daily_dir=daily, db_path=db, now=NOW.replace(hour=14))['status'] == 'skipped'
    assert collect('2026-11-15', limit_down=section(), daily_dir=daily, db_path=db, now=NOW)['status'] == 'skipped'


def test_collector_report_and_envelope(setup_archive, tmp_path):
    db, daily = setup_archive
    raw = dict(limit_down=sources()[DAY])
    def scoped(day, **kwargs):
        return collect(day, **kwargs, db_path=db, now=NOW)
    with patch('collectors.market.BASE_DIR', daily.parent), patch('analyzers.consecutive_limit_down.collect', side_effect=scoped):
        MarketCollector(MagicMock())._enrich_limit_down_structure(raw, DAY)
    assert raw['consecutive_limit_down']['count'] == 2
    assert raw['limit_down']['consecutive_down_count_ex_st'] == 2
    assert raw['limit_down']['stocks'][2]['consecutive_down'] == 1
    generator = ReportGenerator()
    generator.daily_dir = tmp_path/'output'
    report, path = generator.generate_post_market(DAY, raw)
    assert '连续跌停个股数（非ST，≥2日）：2只' in report
    assert '600001.SH' in report and '来源范围内' in report
    assert yaml.safe_load(Path(path).read_text())['raw_data']['consecutive_limit_down']['count'] == 2
