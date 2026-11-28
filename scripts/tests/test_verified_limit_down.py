import pytest

from analyzers.verified_limit_down import verified_list, SOURCE
from analyzers.consecutive_limit_down import calculate

D = '2026-11-19'
PREV = '2026-11-18'


def facts(day, prices):
    date = day.replace('-', '')
    names, quotes, limits, st = [], [], [], []
    for i, close in enumerate(prices, 1):
        code = f'{600000+i}.SH'
        row = dict(ts_code=code, trade_date=date)
        names.append(dict(ts_code=code, name=f'股票{i}', list_date='20200101'))
        quotes.append(dict(row, close=close, pct_chg=-9.99))
        limits.append(dict(row, down_limit=9.00))
        if i == 2:
            st.append(dict(row, name='*ST样本'))
    return dict(universe=names, quotes=quotes, limits=limits, st=st, suspended=[])


def test_complete_prices_override_absent_or_truncated_board_including_st():
    days = ['2026-11-17', PREV, D]
    sections = {day: verified_list(day, **facts(day, prices))
                for day, prices in zip(days, [[10, 10, 10], [9, 9, 10], [9, 9, 9]])}
    block = calculate(D, days, sections)
    assert sections[D]['count'] == 3 and sections[D]['count_ex_st'] == 2
    assert block['status'] == 'complete'
    assert (block['count'], block['st_verified_count']) == (1, 1)
    assert all(r['start_date'] == PREV for r in block['rows'])


@pytest.mark.parametrize('field', ['quotes', 'limits', 'st'])
def test_missing_or_stale_source_cannot_become_zero(field):
    f = facts(D, [10, 10])
    f[field].pop()
    with pytest.raises(ValueError):
        verified_list(D, **f)


def test_verified_empty_and_legacy_empty_boundaries():
    empty = verified_list(PREV, **facts(PREV, [10, 10]))
    assert empty['_source'] == SOURCE and empty['count'] == 0
    t = verified_list(D, **facts(D, [9, 10]))
    assert calculate(D, [PREV,D], {PREV:empty,D:t})['count'] == 0
    legacy = dict(_source='tushare:limit_list_d', count=0, stocks=[])
    assert calculate(D, [PREV,D], {PREV:legacy,D:t})['count'] is None


def test_missing_universe_and_quote_same_code_still_fails_limit_table_reconciliation():
    f = facts(D, [9, 10])
    f['quotes'].pop()
    f['universe'].pop()
    with pytest.raises(ValueError):
        verified_list(D, **f)


def test_verified_source_label_without_coverage_cannot_prove_a_zero():
    today = verified_list(D, **facts(D, [9, 9]))
    incomplete = dict(_source=SOURCE, trade_date=PREV, count=0, stocks=[])
    assert calculate(D, [PREV, D], {D: today, PREV: incomplete})['count'] is None
