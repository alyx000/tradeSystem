"""同代码的题材成员简称、涨停简称和可核分板计数。"""
from copy import deepcopy

import pytest

from analyzers import theme_feedback as feedback, theme_launch as launch
from scripts.tests.test_theme_feedback import DAY as NEXT, evidence as feedback_evidence
from scripts.tests.test_theme_launch import CODES, DAY, PREV, THEME, inputs


def member(evidence, index):
    return evidence['membership']['themes'][THEME]['rows'][index]


def calculate(evidence):
    return launch.calculate(DAY, PREV, evidence)


def test_same_code_width_and_horizontal_space_recover_full_counts_and_height():
    e = inputs()
    member(e, 0)['con_name'] = '深　桑\u00a0达A'
    e['limit_up']['stocks'][0]['name'] = '深桑达Ａ'
    e['previous_limit_up']['stocks'][0]['name'] = '深 桑 达A'
    block = calculate(e); row = block['rows'][0]
    assert row['counts'] == {'10':1, '20':2, '30':0, '10+20':3} and row['status'] == 'complete'
    assert row['limit_stocks'][0]['name'] == '深桑达Ａ' and row['lead_10cm'][0]['height'] == 2
    assert launch.validate(block, DAY) == block
    assert e['limit_up']['stocks'][0]['name'] == '深桑达Ａ'


@pytest.mark.parametrize('member_name,limit_name', [('裕太微', '裕太微-U'), ('企业-W', '企业'),
    ('ST甲', '甲'), ('XD甲', '甲'), ('企业Ａ', '企业a'), ('企业①', '企业1'), ('Ａ\nＢ','AB')])
def test_substantive_name_keeps_per_stock_evidence_and_board_lower_bound(member_name,limit_name):
    e = inputs(); member(e,1)['con_name'] = member_name; e['limit_up']['stocks'][1]['name'] = limit_name
    b = calculate(e); row = b['rows'][0]; coverage = row['count_coverage']
    assert row['counts'] is None and row['status'] == 'partial'
    assert coverage['boards'] == {'10': {'confirmed':1,'unresolved':0},'20':{'confirmed':1,'unresolved':1},
                                 '30':{'confirmed':0,'unresolved':0},'10+20':{'confirmed':2,'unresolved':1}}
    assert coverage['name_conflicts'] == [dict(code=CODES[1],member_name=member_name,limit_name=limit_name,board=20)]
    assert {s['code'] for s in row['limit_stocks']} == {CODES[0], CODES[2]}
    assert row['lead_10cm'][0]['code'] == CODES[0] and row['amount'] is not None
    assert launch.count_text(row,'20') == '至少1（1只待核）' and launch.count_text(row,'30') == '0'
    assert '至少1（1只待核）' in launch.render_html(b, DAY)[0] and CODES[1] in launch.render_markdown(b, DAY)


def test_no_verified_candidate_does_not_become_zero_or_complete_feedback():
    e = inputs(); member(e,0)['con_name']='其他'; member(e,1)['con_name']='其他乙'; member(e,2)['con_name']='其他丙'
    block = calculate(e); row = block['rows'][0]
    assert row['limit_stocks'] == [] and row['counts'] is None
    assert launch.count_text(row,'10') == '未定（1只待核）' and row['lead_unknown'] == [CODES[0]]
    assert '已核验0只' not in launch._cells(row)[7]
    current = feedback_evidence();current['launch'] = block
    checked = feedback.calculate(NEXT, DAY, current)['rows'][0]
    assert checked['sample_count'] is None and checked['groups'] is None
    assert '固定样本未核全' in checked['gaps'][0]


@pytest.mark.parametrize('name', ['ＳＴ甲','＊ＳＴ甲','Ｓ＊ＳＴ甲'])
def test_wide_st_prefix_stays_out_of_non_st_counts(name):
    e = inputs(); e['limit_up']['stocks'][0]['name'] = name; member(e,0)['con_name'] = launch._name_key(name)
    r = calculate(e)['rows'][0]
    assert r['counts']['10'] == 0 and not r['lead_10cm'] and CODES[0] not in [s['code'] for s in r['limit_stocks']]


def test_equal_name_on_different_code_never_creates_membership():
    e = inputs(); member(e,0).update(con_code='600987.SH'); r = calculate(e)['rows'][0]
    assert r['counts']['10'] == 0


def test_legacy_gate_verifies_original_before_read_only_projection():
    e = inputs();member(e,1)['con_name'] = '股 票１'
    legacy = launch.calculate(DAY, PREV, e, schema=launch.LEGACY_SCHEMA); original = deepcopy(legacy)
    assert launch.validate(legacy, DAY) == original and legacy['rows'][0]['counts'] is None
    projected, fact = launch._view(legacy, DAY)
    assert fact == DAY and projected['rows'][0]['counts']['20'] == 2 and legacy == original
    legacy['rows'][0]['counts'] = {'20':2}
    assert launch._view(legacy, DAY)[0]['status'] == 'source_failed'
    broken = deepcopy(projected); broken['rows'][0]['counts']['10'] = 999
    assert launch.validate(broken, DAY)['status'] == 'source_failed'


def test_substantive_prior_name_blocks_height_only():
    e = inputs();e['previous_limit_up']['stocks'][0]['name'] += '-W'
    row = calculate(e)['rows'][0]
    assert row['counts']['10'] == 1 and row['lead_10cm'] == [] and row['lead_unknown'] == [CODES[0]]


def test_next_day_build_uses_validated_legacy_members_without_changing_archive(tmp_path):
    from scripts.tests.test_theme_feedback import NOW, save, setup

    db, raw = setup(tmp_path)
    e = inputs()
    member(e, 1)['con_name'] = '股 票１'
    legacy = launch.calculate(DAY, PREV, e, schema=launch.LEGACY_SCHEMA)
    assert legacy['rows'][0]['counts'] is None
    save(tmp_path, DAY, {launch.KEY:legacy})
    archive = tmp_path / DAY / 'post-market.yaml'
    before = archive.read_bytes()
    result = feedback.build(tmp_path, NEXT, current_raw=raw, db_path=db, now=NOW)
    assert result['rows'][0]['groups']['all']['sample_count'] == 3
    assert result['rows'][0]['groups']['all']['quote_count'] == 3
    assert result['evidence']['launch']['schema'] == launch.SCHEMA
    assert archive.read_bytes() == before
