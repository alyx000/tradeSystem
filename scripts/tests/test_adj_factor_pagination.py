import pytest
from providers.tushare_provider import TushareProvider


def row(code, factor=1):
    return dict(ts_code=code, trade_date='20261023', adj_factor=factor)


def provider(pages):
    p = TushareProvider.__new__(TushareProvider)
    p.pro = object()
    p._initialized = True
    calls = []
    def query(api, **params):
        calls.append(params)
        return pages[len(calls)-1]
    p._query_records = query
    return p, calls


def test_pagination_reads_past_short_page_and_ends_at_empty():
    p, calls = provider([[row('A')], [row('B')], []])
    result = p.get_adj_factor('2026-10-23')
    assert result.success and len(result.data) == 2
    assert [c['offset'] for c in calls] == [0, 1, 2]
    assert all(c['limit'] == 5000 for c in calls)


@pytest.mark.parametrize('pages', [
    [[row('A'), row('A')]],
    [[row('A')], [row('A')]],
    [[dict(ts_code='A', trade_date='20261022', adj_factor=1)]],
    [[row('A', 0)]], [[row('A', float('nan'))]],
])
def test_invalid_page_never_returns_partial_success(pages):
    p, _ = provider(pages)
    result = p.get_adj_factor('2026-10-23')
    assert not result.success and result.data is None


def test_failure_after_first_page_returns_error():
    p, _ = provider([[row('A')]])
    result = p.get_adj_factor('2026-10-23')
    assert not result.success and result.data is None


def test_pagination_bound():
    p, calls = provider([[row(str(i))] for i in range(20)])
    result = p.get_adj_factor('2026-10-23')
    assert not result.success and len(calls) == 20
