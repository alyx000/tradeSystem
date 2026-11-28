from datetime import datetime
import json
from pathlib import Path
import sqlite3

import pytest

from services import domestic_futures_member_reference as m

DAY, PREV = '2026-11-27','2026-11-26'
TITLE = '广发期货IF2612股指期货详情-数据中心-同花顺财经 (https://data.10jqka.com.cn/gzqh/detail/instrumentId/IF2612/partyId/0016/)'
TABLE = ['## 广发期货IF2612建仓过程及持仓详情查询 (近一个月)',
         '日期 | 成交量','（手） | 成交增减 | 多单持仓','（手） | 多单增减 | 空单持仓',
         '（手） | 空单增减 | 净多持仓','（手） | 净空持仓','（手）',
         '--- | --- | --- | --- | --- | --- | --- | --- | ---']
TODAY = '2026-11-27 | 7526 | +4636 | 5298 | +830 | 5039 | +498 | +259 |'
BEFORE = '2026-11-26 | 2890 | -595 | 4468 | -29 | 4541 | -121 | | -73'
EARLY = '2026-11-25 | 3485 | +452 | 4497 | -76 | 4662 | +101 | | -165'


def text(rows=(TODAY, BEFORE), start=100, wrapper='turn0', table=TABLE):
    return TITLE+'\n'+f'【{wrapper}】 Total lines: 970\n'+'\n'.join(
        f'L{start+i}: {line}' for i,line in enumerate([*table,*rows]))+'\n'


@pytest.fixture
def importer(tmp_path, monkeypatch):
    monkeypatch.setattr(m,'get_readonly_connection',lambda _:sqlite3.connect(':memory:'))
    monkeypatch.setattr(m,'calendar',lambda *_:dict(is_open=True,previous=PREV))
    def run(document, **kw):
        path = tmp_path / 'web.txt'
        path.write_text(document)
        return m.import_file(path,DAY,'USER',root=tmp_path/'reports',
                             clock=lambda:datetime(2026,11,28,14,tzinfo=m.TZ),**kw)
    return run


def test_trimmed_page_and_three_daily_deltas(importer):
    first = importer(text())
    assert first['identity']['party']=='0016' and first['row']['short_change']==498
    assert first['verified_changes']==['volume_change','long_change','short_change']
    stored = json.loads(Path(first['path']).read_bytes())
    assert stored.pop('sha256')==m.digest(stored)
    assert stored['sources'][0]['http_received_at'] is None
    assert stored['sources'][0]['known_by_at'].startswith('2026-11-28')


def test_same_target_bootstrap_then_enrich_changed_wrapper_and_newer_days(importer):
    out = importer(text((TODAY,)))
    assert not out['verified_changes']
    two = importer(text((TODAY,BEFORE),start=200,wrapper='turnOther'))
    assert len(two['sources'])==2 and len(two['verified_changes'])==3
    before_bytes = Path(two['path']).read_bytes()
    later = '2026-11-30 | 6012 | -1514 | 5000 | -298 | 4000 | -1039 | +1000 |'
    final = importer(text((later,TODAY,BEFORE,EARLY),start=45,wrapper='other'))
    assert final['reused'] and Path(final['path']).read_bytes()==before_bytes
    assert importer(text((TODAY,)))['reused']


@pytest.mark.parametrize('alter', [
    lambda t:t.replace('data.10jqka.com.cn','data.10jqka.com.cn.evil.net'),
    lambda t:t.replace('IF2612/partyId','IM2612/partyId'),
    lambda t:t.replace('## 广发期货','## 其他会员'),
    lambda t:t.replace('广发期货','其他会员'),
    lambda t:t.replace('L103:','L230:'),
    lambda t:t.replace('多单持仓','成交量'),
    lambda t:t.replace('（手）','（元）'),
    lambda t:t.replace('+830','+801'),
    lambda t:t.replace('+259','+111'),
    lambda t:t.replace('L109:', 'L108:'),
    lambda t:t.replace('L109: '+BEFORE, 'L109: '+TODAY),
])
def test_bad_scope_units_duplicates_or_deltas(alter):
    with pytest.raises(ValueError):
        m.parse(alter(text()),DAY,PREV)


def test_missing_positions_stay_unknown_and_do_not_create_changes():
    row = '2026-11-27 | 7526 | | | | 5039 | +498 | |'
    _,facts,evidence=m.parse(text((row,BEFORE)),DAY,PREV)
    assert facts['long_position'] is None and facts['volume_change'] is None
    assert evidence['verified_changes']==['short_change']


def test_wrong_prior_open_day_and_midday_rejected(importer,monkeypatch):
    _,_,v=m.parse(text((TODAY,EARLY)),DAY,PREV)
    assert v['previous_row'] is None and not v['verified_changes']
    monkeypatch.setattr(m,'calendar',lambda *_:dict(is_open=False,previous=PREV))
    with pytest.raises(ValueError):importer(text())


def test_corrupt_or_conflicting_reference_preserved(importer):
    out = importer(text())
    p=Path(out['path'])
    baseline=p.read_bytes()
    different = text((TODAY.replace('5298','5299').replace('+259','+260').replace('+830','+831'),BEFORE))
    with pytest.raises(ValueError):importer(different)
    assert p.read_bytes()==baseline
    obj=json.loads(baseline)
    obj['sources'][0]['raw_text']+='wrong'
    p.write_text(json.dumps(obj))
    with pytest.raises(ValueError):importer(text())



def test_two_earlier_rows_separated_by_excerpt_gap_rejected():
    with pytest.raises(ValueError):
        m.parse(text()+'L135: '+BEFORE.replace('4468','4000')+'\n', DAY, PREV)


def test_prior_fields_enriched_across_two_independent_receipts(importer):
    first = '2026-11-26 | 2890 | -595 | 4468 | -29 | | | |'
    second = '2026-11-26 | 2890 | -595 | | | 4541 | -121 | |'
    a=importer(text((TODAY,first)))
    assert a['verified_changes']==['volume_change','long_change']
    b=importer(text((TODAY,second)))
    assert b['verified_changes']==['volume_change','long_change','short_change']
    assert len(b['sources'])==2 and importer(text((TODAY,BEFORE)))['reused']
    altered = second.replace('4541 | -121','4541 | -120')
    with pytest.raises(ValueError):
        importer(text((TODAY,altered)))
