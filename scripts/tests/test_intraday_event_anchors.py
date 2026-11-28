from datetime import datetime
from pathlib import Path

import pytest

from services.intraday_events.anchors import load_anchors
from services import handwritten_review as hw


@pytest.fixture
def archive(tmp_path,monkeypatch):
    class Earlier(datetime):
        @classmethod
        def now(cls,tz=None): return datetime.fromisoformat('2025-08-15T20:00:00+08:00')
    monkeypatch.setattr(hw,'datetime',Earlier)
    root=tmp_path/'archive'
    def add(day,raw,rows,header='固定对象',conditions='待核'):
        source=tmp_path/(day+'.txt'); source.write_text(raw)
        supplement=tmp_path/(day+'.md')
        supplement.write_text('## 前日锚点逐条验证\n待核\n## 固定对象跨日反馈\n'
            '| 方向／角色 | '+header+' | 反馈 |\n| --- | --- | --- |\n'+rows+
            '\n## 次日具体条件\n'+conditions+'\n## 原文待核事项\n待核\n')
        hw.import_entry(root,day,source,supplement,'USER')
    return root,add


def basic():
    return [dict(ts_code='009971.SZ',name='样本甲股'),dict(ts_code='309971.SZ',name='样本乙股'),
            dict(ts_code='309972.SZ',name='样本丙股')]


def test_monday_uses_only_friday_review_and_never_guesses_alias(archive):
    root,add=archive
    add('2025-08-05','市场锚点：样本甲股补跌\n样甲继续新高','| 情绪核心 | 样甲／样本甲股 | 待核 |')
    add('2025-08-15','负反馈的锚点：样本乙股、样本丙股','| 负反馈 | 样本乙股、样本丙股、样甲 | 待核 |')
    result=load_anchors(basic(),'2025-08-18','2025-08-15',root=root)
    assert {s['code'] for s in result['stocks']}=={'309971.SZ','309972.SZ'}
    assert result['latest_source_date']=='2025-08-15'
    assert any(r['original_name']=='样甲' for r in result['unresolved'])
    assert result['status']=='partial'
    assert result['stocks'][0]['references'][0]['original_mentions'][0]['quote']


def test_retrospective_table_alone_cannot_introduce_object(archive):
    root,add=archive
    add('2025-08-15','只有样本甲股','| 观察 | 样本乙股 | 待核 |')
    result=load_anchors(basic(),'2025-08-18','2025-08-15',root=root)
    assert not result['stocks'] and result['unresolved'][0]['reason']=='上一交易日手写原文未点名'


def test_older_original_cannot_supply_evidence_for_previous_review(archive):
    root,add=archive
    add('2025-08-05','只有样本甲股','| 观察 | 样本乙股 | 待核 |')
    add('2025-08-15','样本乙股','| 观察 | 样本甲股 | 待核 |')
    result=load_anchors(basic(),'2025-08-18','2025-08-15',root=root)
    assert not result['stocks']
    assert result['unresolved'][0]['original_name']=='样本甲股'


def test_duplicate_name_identity_remains_unresolved(archive):
    root,add=archive
    add('2025-08-15','样本甲股','| 核心 | 样本甲股 | 待核 |')
    result=load_anchors(basic()+[dict(ts_code='600000.SH',name='样本甲股')],'2025-08-18','2025-08-15',root=root)
    assert not result['stocks'] and result['unresolved']


def test_w_suffix_requires_matching_base_name_and_explicit_code(archive):
    root, add = archive
    add('2025-08-15', '样本丁股-W、其他-W', '| 核心 | 样本丁股-W `689971.SH`、其他-W `689971.SH`、样本丁股-W | 待核 |')
    result = load_anchors([dict(ts_code='689971.SH', name='样本丁股')], '2025-08-18', '2025-08-15', root=root)
    assert [s['code'] for s in result['stocks']] == ['689971.SH']
    assert {r['original_name'] for r in result['unresolved']} == {'其他-W', '样本丁股-W'}


def test_corrupt_archive_is_visible_not_silently_skipped(archive):
    root,add=archive
    add('2025-08-05','样本甲股','| 核心 | 样本甲股 | 待核 |')
    add('2025-08-15','样本乙股','| 核心 | 样本乙股 | 待核 |')
    (root/'2025-08-15'/'source.txt').write_text('损坏')
    result=load_anchors(basic(),'2025-08-18','2025-08-15',root=root)
    assert result['status']=='partial' and any('2025-08-15' in e for e in result['errors'])
    assert not result['stocks']


def test_open_after_archive_excluded_without_hindsight(archive,monkeypatch):
    root,add=archive
    class Later(datetime):
        @classmethod
        def now(cls,tz=None): return datetime.fromisoformat('2025-08-18T10:00:00+08:00')
    monkeypatch.setattr(hw,'datetime',Later)
    add('2025-08-15','样本甲股','| 核心 | 样本甲股 | 待核 |')
    result=load_anchors(basic(),'2025-08-18','2025-08-15',root=root)
    assert not result['stocks'] and any('开盘后' in e for e in result['errors'])


def test_missing_handwritten_is_not_zero(archive):
    root,add=archive
    add('2025-08-14','样本甲股','| 核心 | 样本甲股 | 待核 |')
    result=load_anchors(basic(),'2025-08-18','2025-08-15',root=root)
    assert result['status']=='partial' and result['errors'] and not result['stocks']


def test_legacy_original_name_header_is_supported(archive):
    root,add=archive
    add('2025-08-15','样本甲股','| 核心 | 样本甲股 | 待核 |',header='固定对象／原称')
    result=load_anchors(basic(),'2025-08-18','2025-08-15',root=root)
    assert result['status']=='complete' and result['stocks'][0]['name']=='样本甲股'


def test_late_amendment_preserves_old_anchor_without_adopting_new(archive,monkeypatch,tmp_path):
    root,add=archive
    add('2025-08-15','样本甲股、样本乙股','| 核心 | 样本甲股 | 待核 |')
    before=hw.load_entry(root/'2025-08-15')
    class Later(datetime):
        @classmethod
        def now(cls,tz=None): return datetime.fromisoformat('2025-08-18T10:00:00+08:00')
    monkeypatch.setattr(hw,'datetime',Later)
    text=(root/'2025-08-15'/'supplement.md').read_text().replace('| 样本甲股 |','| 样本甲股、样本乙股 |')
    amended=tmp_path/'new.md'; amended.write_text(text)
    hw.amend_entry(root,'2025-08-15',amended,before['meta']['supplement_sha256'],'补充观察','USER')
    result=load_anchors(basic(),'2025-08-18','2025-08-15',root=root)
    assert [s['name'] for s in result['stocks']]==['样本甲股']
    assert result['sources'][0]['revision']==0
    assert result['sources'][0]['ignored_late_revisions']==[1]
    assert any('沿用事前版本0' in e for e in result['errors'])


def test_new_object_tables_check_code_and_ignore_adjacent_sector_table(archive):
    root, add = archive
    rows = """
| 板块 | 今日证据 | 观察 |
| --- | --- | --- |
| 样本丙股 | 无 | 无 |

| 对象（名称及已核验代码） | 用户原角色／来源 |
| --- | --- |
| 样本甲股009971.SZ；样本乙股（代码待核） | 核心 |

| 优先级 | 观察组 | 核心股票 |
| --- | --- | --- |
| 01 | 测试组 | 样本丙股 009971.SZ |
"""
    add('2025-08-15', '样本甲股、样本乙股和样本丙股', rows)
    result = load_anchors(basic(), '2025-08-18', '2025-08-15', root=root)
    assert {row['name'] for row in result['stocks']} == {'样本甲股', '样本乙股'}
    assert [(row['original_name'], row['reason']) for row in result['unresolved']] == [('样本丙股', '名称代码不一致')]


@pytest.mark.parametrize('quote', ['', '`'])
def test_same_review_confirmed_target_is_traceable_without_original_mention(archive, quote):
    root, add = archive
    add('2025-08-15', '根据配图生成复盘', f'| 核心 | 样本甲股{quote}009971.SZ{quote}、样本乙股309971.SZ | 跟踪 |',
        conditions=f'| ID／对象 | 盘中 |\n| --- | --- |\n| F0815-01 样本甲股 {quote}009971.SZ{quote} | 同看样本乙股309971.SZ |')
    result = load_anchors(basic(), '2025-08-18', '2025-08-15', root=root)
    assert [s['name'] for s in result['stocks']] == ['样本甲股']
    stock = result['stocks'][0]
    assert stock['source'] == 'handwritten_review_supplement'
    assert stock['references'][0]['next_session_target'] and not stock['references'][0]['original_mentions']
    assert [s['original_name'] for s in result['unresolved']] == ['样本乙股']


def test_only_explicit_next_session_levels_bind_to_their_object(archive):
    root, add = archive
    conditions = """#### 明日锚点｜8月18日
观察 **上证（MA5=4102.75／4090 缺口）、科创50（MA5=985.25 / MA20=993.50）**。
08-20 观察指数，创业板站稳 2455 点。
港股的平均股价跌破 20 点。
核对 **科创50 能否守住 08-15 前低 960.25**（对照值 970.80）、**中证1000 守住 5190 风险区**、**创业板重返 2130 之上**。
科创50 守住 08-20 前低 950 点。
#### 重点个股
| ID／对象 | 竞价 | 盘中 |
| --- | --- | --- |
| F0815-01 样本甲股 `009971.SZ` | 对比 17.40 元；同看样本乙股(52.50) | 核站稳 18 元；留意样本乙股突破 53 元 |
| F0815-02 样本乙股 `309971.SZ` | 观察竞价 | 若样本甲股跌破 18 元，观察样本乙股的承接 |
| F0815-03 样本丙股 `309972.SZ` | 对比 72.45 元；结合 08-15 夜盘 | 站稳 75 元（仅 08-20 核验） |
#### 市场与延续条件
| F0815-02 指数 | 创业板站稳 MA5(2225)并试探 2250 | 成交额 12345 亿 |
| F0815-03 宽基／08-18 | 科创50 止跌收复 975 | 科创50 有效失守 940 |
#### 未来一周
创业板站稳 2800 点。
"""
    add('2025-08-15', '样本甲股、样本乙股、样本丙股；上证历史 4020', '| 核心 | 样本甲股、样本乙股、样本丙股 | 待核 |', conditions=conditions)
    result = load_anchors(basic(), '2025-08-18', '2025-08-15', root=root)
    levels = result['key_levels']
    assert {(r['code'], r['level']) for r in levels} == {
        ('000001.SH', 4102.75), ('000001.SH', 4090), ('000688.SH', 985.25),
        ('000688.SH', 993.50), ('399006.SZ', 2225), ('399006.SZ', 2250),
        ('009971.SZ', 17.40), ('009971.SZ', 18), ('309972.SZ', 72.45),
        ('000688.SH', 960.25), ('000688.SH', 975), ('000688.SH', 940),
        ('000852.SH', 5190), ('399006.SZ', 2130)}
    assert all(r['source_date']=='2025-08-15' and r['supplement_sha256']==result['sources'][0]['supplement_sha256']
               and r['quotes'] for r in levels)
