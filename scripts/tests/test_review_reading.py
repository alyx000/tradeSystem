"""阅读层不能把不完整反馈简化为完整数量或未经核验的子集。"""
from copy import deepcopy

from analyzers import theme_feedback as feedback
from analyzers import zhong_prefix_effect as zhong
from scripts.tests.test_theme_feedback import calculate, evidence, DAY, SOURCE, PREV
from scripts.tests.test_zhong_prefix_effect import evidence as zhong_evidence, DAY as ZHONG_DAY


def visible(block):
    return feedback.render_html(block, DAY)[0].split('<details')[0]


def test_complete_table_uses_fixed_cohort_and_preserves_evidence():
    block = calculate()
    before = deepcopy(block)
    html, _ = feedback.render_html(block, DAY)
    top, detail = html.split('<details', 1)
    assert '其中再涨停' in top and 'width:66.6667%' in top
    assert '<td>3</td>' in top and '<td>2</td>' in top
    assert '板块延续与量能背景' not in top
    assert '板块延续与量能背景' in detail and '开盘均值/中位' in detail
    assert block == before


def test_partial_quotes_hide_bar_and_do_not_claim_verified_subset():
    e = evidence()
    source = e['feedback']
    source['details'][2].update(quote_status='source_failed', touched_previous_high=None, errors=['missing'])
    source.update(summary=feedback.sf._summary(source['details']), status='partial')
    top = visible(calculate(e))
    assert '已核验2只，覆盖2/3' in top
    assert 'theme-feedback-track' not in top and '其中再涨停' not in top


def test_failed_limits_preserve_quote_bar_but_not_limit_zero():
    e = evidence()
    e['limit_up'] = {'error': 'offline'}
    top = visible(calculate(e))
    assert 'width:66.6667%' in top
    assert '待核（覆盖0/3）' in top and '其中再涨停' not in top


def test_empty_cohort_and_missing_cohort_are_distinct():
    e = evidence()
    raw = e['launch']['evidence']
    raw['limit_up'].update(count=0, stocks=[])
    e['launch'] = feedback.tl.calculate(SOURCE, PREV, raw)
    top = visible(calculate(e))
    assert '<td>0</td>' in top and '无样本' in top and 'theme-feedback-track' not in top
    top = visible(None)
    assert '暂不能判断' in top and '<td>0</td>' not in top


def test_subsets_require_stock_level_proof_not_counts_alone():
    row = calculate()['rows'][0]
    row['stocks'][0]['close_pct'] = -1
    assert '其中再涨停' not in feedback._compact_feedback_table([row])
    row = calculate()['rows'][0]
    row['stocks'][0]['code'] = row['stocks'][1]['code']
    assert '其中再涨停' not in feedback._compact_feedback_table([row])


def test_all_rows_are_visible_in_source_order_without_cross_theme_total():
    row = calculate()['rows'][0]
    rows = [dict(deepcopy(row), name=f'题材{i}') for i in range(10)]
    top = feedback._compact_feedback_table(rows)
    assert all(f'题材{i}' in top for i in range(10))
    assert top.index('题材0') < top.index('题材9')
    assert '题材不相加' in top and '合计' not in top


def test_style_market_denominator_visible(zhong_evidence):
    block = zhong.calculate(ZHONG_DAY, zhong_evidence)
    top = zhong.render_html(block, ZHONG_DAY)[0].split('<details')[0]
    assert '剔ST、退市及B股' in top
    assert '中字头4/5' in top and '市场基准5/4001' in top
