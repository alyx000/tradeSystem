"""验证课内分钟观察的事前身份、截止时点及同题材边界。"""
import json
import sqlite3
from datetime import datetime

import pytest

from services import course_intraday_universe as universe

DAY = '2026-11-27'
PRIOR = '2026-11-26'
CUTOFF = datetime.fromisoformat(DAY + 'T09:30:00+08:00')


def sample():
    return dict(
        raw='- L1 情绪核心：删除甲旧、甲，保留甲新、乙。\n- L2 「最」：确定丙。',
        sections={'固定对象跨日反馈': '''
#### 情绪核心（L1，人工）
- **甲**（误入）
- **甲新**（保留）
- **甲旧**（已删除）
- **乙**（保留）
#### 题材分组（L3）
**1. 行业甲**
- **涨停**：**丙 2板**、**丁**，**戊 4板**。
**2. 行业乙**
- **涨停**：**己**。
#### 最（L2）
| 优先级 | 观察组 | 核心股票 | 观察重点与同组对照 |
| --- | --- | --- | --- |
| 01 | 行业甲 | 丙 000004.SZ | 与丁(5.10)、己(5板)对照 |
| 02 | 行业甲 | 戊 000006.SZ | 与丁(5.10)对照 |
'''}, meta=dict(date=PRIOR, recorded_at=PRIOR+'T22:00:00+08:00',
                 source_sha256='s', supplement_sha256='t'))


NAMES = ['甲', '甲新', '乙', '丙', '丁', '戊', '己', '甲旧']
CODES = {f'{i:06}.SZ': name for i, name in enumerate(NAMES, 1)}
BY_NAME = {name: [code] for code, name in CODES.items()}


def test_roles_require_positive_raw_and_explicit_group():
    cores, proposed, groups = universe._handwritten(sample(), BY_NAME)
    assert list(cores) == ['000002.SZ', '000003.SZ', '000004.SZ']
    assert proposed['000004.SZ'] == ['丁', '己']
    assert groups['行业甲'] == ['丙', '丁', '戊'] and groups['行业乙'] == ['己']
    assert not universe._explicit_name('甲', '甲新、乙')


def database(stamp='2026-11-26 15:00:00'):
    conn = sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE daily_reviews (date TEXT,step5_leaders TEXT,updated_at TEXT)')
    conn.execute('INSERT INTO daily_reviews VALUES (?,?,?)', (PRIOR, json.dumps({
        'top_leaders': [dict(stock='丙', stock_code='000004')]}), stamp))
    return conn


def test_manual_leaders_strict_cutoff_and_exact_identity():
    codes, ref = universe._manual_leaders(database(), PRIOR, CUTOFF, CODES)
    assert codes == ['000004.SZ'] and ref['updated_at'].endswith('+00:00')
    with pytest.raises(ValueError, match='时点'):
        universe._manual_leaders(database('2026-11-27 01:30:00'), PRIOR, CUTOFF, CODES)
    with pytest.raises(ValueError, match='名称代码'):
        universe._manual_leaders(database(), PRIOR, CUTOFF, {'000004.SZ': '丙旧称'})


def test_universe_does_not_promote_mentions_or_cross_groups(monkeypatch, tmp_path):
    monkeypatch.setattr(universe, 'calendar', lambda conn, day: dict(is_open=True, previous=PRIOR))
    def market(conn, prior, cutoff):
        assert prior == PRIOR and cutoff == CUTOFF
        return {}, CODES, BY_NAME, dict(generated_at=PRIOR+'T20:00:00+08:00')
    monkeypatch.setattr(universe, '_previous_market', market)
    monkeypatch.setattr(universe, 'load_entry_before', lambda path, cutoff: (sample(), sample(), []))
    monkeypatch.setattr(universe, '_theme_groups', lambda *args: ([], {'date': PRIOR}, []))
    result = universe.build_universe(database(), DAY, archive_root=tmp_path)
    assert result['status'] == 'partial'
    assert result['members'][-1]['code'] == '000005.SZ'
    assert [row['role'] for row in result['members']] == ['core', 'core', 'core', 'peer']
    assert result['members'][2]['comparisons'][0]['code'] == '000005.SZ'
    assert '000007.SZ' not in [r['code'] for r in result['members']]
    assert '000006.SZ' not in [r['code'] for r in result['members']]
    assert universe.digest({k: v for k, v in result.items() if k != 'sha256'}) == result['sha256']


def test_market_missing_or_late_receipt_fails_closed():
    conn = sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE daily_market (date TEXT,raw_data TEXT,created_at TEXT)')
    with pytest.raises(ValueError, match='截止前'):
        universe._previous_market(conn, PRIOR, CUTOFF)
    conn.execute('INSERT INTO daily_market VALUES (?,?,?)', (PRIOR, '{}', '2026-11-27 01:31:00'))
    with pytest.raises(ValueError, match='截止前'):
        universe._previous_market(conn, PRIOR, CUTOFF)
    conn.execute("UPDATE daily_market SET created_at='2026-11-26 12:00:00',raw_data=?", (json.dumps(
        dict(date=PRIOR, raw_data=dict(date=PRIOR), generated_at=DAY+'T09:29:00')),))
    with pytest.raises(ValueError, match='事实日不符'):
        universe._previous_market(conn, PRIOR, CUTOFF)


def test_calendar_is_required():
    result = universe.build_universe(sqlite3.connect(':memory:'), DAY)
    assert result['status'] == 'source_failed' and result['members'] == []


def test_membership_source_and_bounds(monkeypatch):
    def snapshot(date):
        return dict(trade_date=date, membership_date=date, evidence={
            'membership_source': universe.theme_launch.SOURCE, 'ranking': {},
            'membership': {'date': date, 'fetched_at': date+'T17:00:00+08:00'}})
    monkeypatch.setattr(universe.theme_launch, 'rankings', lambda data, date: [
        {'name': '精确题材', 'code': 'one'}, {'name': '大概念', 'code': 'many'}])
    monkeypatch.setattr(universe.theme_launch, '_members', lambda data, code, date:
                        {'a': '甲'} if code == 'one' else {i: str(i) for i in range(301)})
    groups, ref, gaps = universe._theme_groups({'theme_launch': snapshot(PRIOR)}, PRIOR, CUTOFF)
    assert [g['code'] for g, _ in groups] == ['one'] and not gaps
    with pytest.raises(ValueError):
        universe._theme_groups({'theme_launch': snapshot(DAY)}, PRIOR, CUTOFF)


def test_mixed_handwritten_groups_keep_explicit_subheadings_only():
    section = '''
#### 题材分组与新增观察
**6. 其他／散点／混沌轮动**
- **医药（同组）**：**甲新 +1%**、**丙**。
- **传媒**：**甲**、**乙 +2%**。
- **连板**：**丁**。
**7. 算力／水产与冰雪**
- **首板**：**戊**（6亿，冰雪装备）、**己**（3亿，水产）。
'''
    groups = universe._manual_groups(section, BY_NAME, {'冰雪', '水产'})
    assert groups == {'医药': ['甲新', '丙'], '传媒': ['甲', '乙'], '冰雪': ['戊'], '水产': ['己']}


def test_exact_theme_snapshot_must_exclude_cutoff():
    block = dict(trade_date=PRIOR, membership_date=PRIOR, evidence=dict(
        membership_source=universe.theme_launch.SOURCE, ranking={}, membership={
            'date': PRIOR, 'fetched_at': CUTOFF.isoformat()}))
    with pytest.raises(ValueError, match='事前时点'):
        universe._theme_groups({'theme_launch': block}, PRIOR, CUTOFF)


def test_volume_comparison_cannot_use_later_tag_or_domain_style():
    conn = sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE daily_volume_concentration (date, gain_universe_json,source_json,created_at,updated_at)')
    stamp = PRIOR+'T22:10:00+08:00'
    source = dict(gain_concept=dict(status='complete', caliber='top15_hot_concept_intersection', universe_count=1),
                  audit=dict(collected_at=stamp))
    conn.execute('INSERT INTO daily_volume_concentration VALUES(?,?,?,?,?)',
                 (PRIOR, json.dumps([dict(code='000001.SZ', name='甲', concepts=['窄题材', '融资融券'])]),
                  json.dumps(source), stamp, stamp))
    tags, _ = universe._volume_concepts(conn, PRIOR, CUTOFF, CODES)
    assert tags == {'000001.SZ': ['窄题材']}
    conn.execute('UPDATE daily_volume_concentration SET updated_at=?', (CUTOFF.isoformat(),))
    with pytest.raises(ValueError):
        universe._volume_concepts(conn, PRIOR, CUTOFF, CODES)


def test_tracked_narrow_theme_verifies_real_pagination_hash_and_selection_cutoff():
    tracker = universe.tracked_theme_members
    concept = '885001.TI'
    older = '2026-11-25'
    stamp = PRIOR+'T18:10:00+08:00'
    snapshot = dict(date=PRIOR, fetched_at=stamp, themes={concept: dict(status='complete', pages=[2, 0],
                    rows=[dict(con_code=c, con_name=CODES[c], ts_code=concept, is_new='Y')
                          for c in list(CODES)[:2]])})
    catalog = dict(date=PRIOR, fetched_at=stamp, pages=[1, 0],
                   rows=[dict(ts_code=concept, name='明确细题材', type='N', exchange='A')])
    block = dict(schema=tracker.SCHEMA, status='complete', trade_date=PRIOR, previous_date=older,
                 selection=dict(date=older, recorded_at=older+'T19:00:00+08:00'),
                 membership_source=universe.theme_launch.SOURCE, catalog_source=tracker.CATALOG,
                 snapshots={concept: snapshot}, catalog=catalog,
                 rows=[dict(code=concept, name='明确细题材', count=2, status='complete')])
    block['sha256'] = tracker._checksum(block)
    raw = {tracker.KEY: block}
    groups, receipts, missing = universe._tracked_groups(raw, PRIOR, CUTOFF, older)
    assert groups[0][1]['000001.SZ'] == '甲' and not missing
    assert receipts['snapshots'][concept]['fetched_at'] == stamp
    block['rows'][0]['name'] = '其他题材'
    with pytest.raises(ValueError):
        universe._tracked_groups(raw, PRIOR, CUTOFF, older)
    block['selection']['recorded_at'] = PRIOR+'T09:30:00+08:00'
    block['sha256'] = tracker._checksum(block)
    with pytest.raises(ValueError):
        universe._tracked_groups(raw, PRIOR, CUTOFF, older)
