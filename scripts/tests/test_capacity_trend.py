from copy import deepcopy
from datetime import date, timedelta
import importlib.util
from pathlib import Path
import sqlite3

import pytest

from db.schema import init_schema
from providers.base import DataResult
from services.volume_concentration import capacity_trend as ct, repo, service


def series(values=None):
    values = list(range(100, 130)) if values is None else values
    days = []
    day = date(2026, 9, 1)
    while len(days) < len(values):
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    return ([dict(trade_date=d, close=v) for d, v in zip(days, values)],
            [dict(trade_date=d, adj_factor=1) for d in days], days[-1])


def test_up_down_flat_and_ma_direction():
    for values, position, direction, run in [
        (list(range(100, 130)), '上方', '向上', '连涨'),
        (list(range(130, 100, -1)), '下方', '向下', '连跌'),
        ([100] * 30, '持平', '持平', '连平'),
    ]:
        result = ct.analyze(*series(values))
        assert result['status'] == 'complete'
        assert result['ma20_position'] == position
        assert result['ma20_direction'] == direction
        assert result['end_run_direction'] == run
        assert result['end_run_bars'] == 5
        assert result['up_count'] + result['down_count'] + result['flat_count'] == 5


def test_mixed_window_last_streak_and_equal_close():
    result = ct.analyze(*series([100] * 25 + [101, 100, 100, 102, 103]))
    assert (result['up_count'], result['down_count'], result['flat_count']) == (3, 1, 1)
    assert result['end_run_bars'] == 2


def test_ex_right_does_not_invent_downtrend():
    bars, factors, day = series([100] * 29 + [50])
    factors[-1]['adj_factor'] = 2
    result = ct.analyze(bars, factors, day)
    assert result['ma20'] == result['close_qfq'] == 50
    assert result['ma20_direction'] == '持平'
    assert result['flat_count'] == 5


@pytest.mark.parametrize('damage', ['duplicate_bar', 'duplicate_factor', 'missing_factor',
                                   'stale', 'future', 'unordered', 'nan', 'negative', 'boolean'])
def test_bad_data_never_becomes_a_trend(damage):
    bars, factors, day = series()
    if damage == 'duplicate_bar': bars.insert(2, deepcopy(bars[2]))
    if damage == 'duplicate_factor': factors.append(deepcopy(factors[2]))
    if damage == 'missing_factor': factors.pop(2)
    if damage == 'stale': bars.pop()
    if damage == 'future': bars[-1]['trade_date'] = '2027-01-01'
    if damage == 'unordered': bars.reverse()
    if damage == 'nan': bars[3]['close'] = float('nan')
    if damage == 'negative': factors[3]['adj_factor'] = -1
    if damage == 'boolean': bars[3]['close'] = True
    result = ct.analyze(bars, factors, day)
    assert result['status'] == 'source_failed'
    assert 'up_count' not in result and 'ma20' not in result


def test_short_history_and_suspended_target_keep_unknown():
    assert ct.analyze(*series([10]*24))['status'] == 'partial'
    bars, factors, day = series()
    assert ct.analyze(bars[:-1], factors, day)['status'] == 'source_failed'


class Registry:
    def __init__(self):
        self.bars, self.factors, self.day = series()
        for bar in self.bars:
            bar['ts_code'] = '000001.SZ'
        self.calls = []
        self.fail = None

    def call(self, method, code, *args):
        self.calls.append((method, code))
        if code == '000002.SZ' or method == self.fail:
            return DataResult(data=None, source='mock', error='source unavailable')
        return DataResult(data=self.factors if method == 'get_stock_adj_factor_range' else self.bars,
                          source='mock')


STOCKS = [dict(code='000001.SZ', name='测试甲', industry='测试行业', amount_billion=10),
          dict(code='000002.SZ', name='测试乙', industry='测试行业', amount_billion=8)]


def test_collection_failure_isolated_and_ordered():
    reg = Registry()
    block = ct.collect(reg, STOCKS, reg.day)
    assert block['status'] == 'partial'
    assert (block['complete_count'], block['total_count']) == (1, 2)
    assert reg.calls == [('get_stock_daily_range', '000001.SZ'),
                         ('get_stock_adj_factor_range', '000001.SZ'),
                         ('get_stock_daily_range', '000002.SZ')]
    assert block['rows'][0]['quote_source'] == 'mock'


def test_factor_failure_and_identity_mismatch():
    reg = Registry()
    reg.fail = 'get_stock_adj_factor_range'
    assert ct.collect(reg, STOCKS[:1], reg.day)['status'] == 'source_failed'
    reg.fail = None
    reg.bars[0]['ts_code'] = '600000.SH'
    assert ct.collect(reg, STOCKS[:1], reg.day)['status'] == 'source_failed'


@pytest.mark.parametrize('source,fields', [
    ('bars', {'ts_code': '000001.SH'}),
    ('factors', {'ts_code': '000001.SH'}),
    ('bars', {'ts_code': '000001.SZ', 'code': '000001.SH'}),
    ('bars', {'ts_code': '000001.INVALID'}),
    ('bars', {'ts_code': None}),
    ('bars', {}),
])
def test_market_suffix_and_missing_quote_identity_fail_closed(source, fields):
    reg = Registry()
    row = getattr(reg, source)[0]
    row.pop('ts_code', None)
    row.update(fields)
    block = ct.collect(reg, STOCKS[:1], reg.day)
    assert block['status'] == 'source_failed'
    assert block['rows'][0]['status'] == 'source_failed'
    assert 'ma20' not in block['rows'][0]


def test_identity_normalization_and_factor_contract_without_code():
    reg = Registry()
    reg.bars[0]['ts_code'] = ' 000001.sz '
    reg.bars[1]['ts_code'] = '000001'
    assert all('ts_code' not in f for f in reg.factors)
    block = ct.collect(reg, STOCKS[:1], reg.day)
    assert block['status'] == 'complete'
    assert block['rows'][0]['identity_basis'] == 'single_stock_request_with_optional_row_check'


def test_preservation_explicit_same_day_same_universe_only():
    reg = Registry()
    good = ct.collect(reg, STOCKS[:1], reg.day)
    reg.fail = 'get_stock_adj_factor_range'
    bad = ct.collect(reg, STOCKS[:1], reg.day)
    saved = ct.preserve_complete(bad, good)
    assert saved['status'] == 'fallback_preserved'
    assert saved['latest_attempt']['status'] == 'source_failed'
    assert saved['rows'] == good['rows']
    assert ct.preserve_complete(bad, saved)['latest_attempt'] == bad
    different = {**good, 'trade_date': '2000-01-01'}
    assert ct.preserve_complete(bad, different) == bad
    different = deepcopy(good)
    different['rows'][0]['industry'] = '新分类'
    assert ct.preserve_complete(bad, different) == bad


def test_daily_persist_dry_run_and_refresh_receipt(monkeypatch):
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    reg = Registry()
    def record(*args, **kwargs):
        return dict(date=reg.day, top_n=1, total_amount_billion=10, market_total_billion=100,
                    stocks=deepcopy(STOCKS[:1]), sector_summary=[], source={})
    monkeypatch.setattr(service.collector, 'build_record', record)
    monkeypatch.setattr(service.collector, 'build_gain_universe', lambda *a: [])
    preview = service.run_daily(conn, reg, reg.day, persist=False)
    assert 'MA20方向' in preview
    assert repo.get_concentration(conn, reg.day) is None
    service.run_daily(conn, reg, reg.day, input_by=' USER ')
    first_record = repo.get_concentration(conn, reg.day)
    first = ct.from_record(first_record, reg.day)
    assert first['status'] == 'complete'
    assert first['audit']['input_by'] == 'USER'
    assert first_record['source']['audit'] == first['audit']
    assert first['audit']['started_at'] <= first['audit']['collected_at']
    reg.fail = 'get_stock_adj_factor_range'
    output = service.run_daily(conn, reg, reg.day, input_by='launchd')
    assert r'fallback\_preserved' in output and r'本次刷新source\_failed' in output
    refreshed = repo.get_concentration(conn, reg.day)
    saved = ct.from_record(refreshed, reg.day)
    assert saved['latest_attempt']['complete_count'] == 0
    assert saved['audit'] == first['audit']
    assert saved['latest_attempt']['audit']['input_by'] == 'launchd'
    assert refreshed['source']['audit'] == saved['latest_attempt']['audit']
    conn.close()


def test_read_only_old_missing_and_wrong_date_snapshots(tmp_path, monkeypatch):
    path = tmp_path/'test.db'
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    reg = Registry()
    record = dict(date=reg.day, total_amount_billion=10, source={})
    repo.save_concentration(conn, record)
    monkeypatch.setenv('TRADE_DB_PATH', str(path))
    # get_readonly_connection 的默认路径在导入时绑定，显式替换工厂保证测试隔离。
    from db import connection
    original = connection.get_readonly_connection
    monkeypatch.setattr(connection, 'get_readonly_connection', lambda: original(path))
    assert ct.read_snapshot(reg.day)['status'] == 'not_computed'
    record['source'][ct.KEY] = ct.collect(reg, STOCKS[:1], reg.day)
    repo.save_concentration(conn, record)
    before = path.read_bytes()
    assert ct.read_snapshot(reg.day)['status'] == 'complete'
    assert ct.read_snapshot('2026-10-31')['status'] == 'not_computed'
    assert path.read_bytes() == before
    record['source'][ct.KEY]['trade_date'] = '2026-10-31'
    assert ct.from_record(record, reg.day)['status'] == 'source_failed'
    conn.close()


def test_render_escapes_source_and_preserves_missing_state():
    reg = Registry()
    stocks = deepcopy(STOCKS)
    stocks[0]['name'] = '<script>x</script>|甲'
    block = ct.collect(reg, stocks, reg.day)
    html, gap = ct.render_html(block)
    assert '<script>' not in html and '&lt;script&gt;' in html
    assert '000001.SZ' in html and 'source_failed' in html and gap
    assert '\\|' in ct.render_markdown(block)
    html, gap = ct.render_html(ct.from_record(None, reg.day))
    assert '未计算' in html and '0/0' not in html and gap


def test_markdown_source_text_cannot_create_links_or_html():
    reg = Registry()
    payload = '[点击](https://example.invalid) <script>x</script> **粗体** `代码` \\ |\n换行'
    stocks = deepcopy(STOCKS)
    stocks[0]['name'] = stocks[0]['industry'] = payload
    block = ct.collect(reg, stocks, reg.day)
    block['rows'][1]['reason'] = payload
    markdown = ct.render_markdown(block)
    assert '<script>' not in markdown and '&lt;script&gt;' in markdown
    assert '[点击](' not in markdown and r'\[点击\]\(' in markdown
    assert '**粗体**' not in markdown and r'\*\*粗体\*\*' in markdown
    assert r'\`代码\`' in markdown and r'\|' in markdown
    # 未计算/不可读分支的原因也要走同一转义口径。
    unavailable = {'status': 'source_failed', 'trade_date': reg.day, 'reason': payload, 'rows': []}
    assert '[点击](' not in ct.render_markdown(unavailable)


@pytest.mark.parametrize('damage', ['missing_metric', 'bad_count', 'bad_window', 'bad_status', 'bad_source', 'bad_block',
                                   'string_distance', 'bool_distance'])
def test_corrupt_snapshot_cannot_crash_or_render_complete(damage):
    reg = Registry()
    block = ct.collect(reg, STOCKS[:1], reg.day)
    record = {'source': {ct.KEY: block}}
    if damage == 'missing_metric': del block['rows'][0]['ma20']
    if damage == 'bad_count': block['rows'][0]['up_count'] = 0
    if damage == 'bad_window': block['rows'][0]['window_dates'] = []
    if damage == 'bad_status': block['status'] = 'partial'
    if damage == 'bad_source': record['source'] = []
    if damage == 'bad_block': record['source'][ct.KEY] = []
    if damage == 'string_distance': block['rows'][0]['ma20_distance_pct'] = '1.2'
    if damage == 'bool_distance': block['rows'][0]['ma20_distance_pct'] = True
    result = ct.from_record(record, reg.day)
    assert result['status'] == 'source_failed'
    assert '0/0' not in ct.render_html(result)[0]


def test_formal_assembler_injects_into_sector_and_ops(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('capacity_review_fixture', Path(__file__).with_name('test_daily_review_html_report.py'))
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    assembler = fixture._load_assembler()
    # 使用正式模板 fixture，避免在测试中复制组装器实现。
    fixture._write_chunks(tmp_path)
    monkeypatch.setattr(ct, 'read_snapshot', lambda day: ct.from_record(None, day))
    html = assembler.render_report(tmp_path, fixture.DATE)
    assert html.count('data-capacity-trend="v1"') == 1
    assert '成交榜容量趋势：未计算' in html
    assembler.validate_report(html)

    reg = Registry()
    target = date.fromisoformat(fixture.DATE)
    dates = []
    while len(dates) < 30:
        if target.weekday() < 5:
            dates.append(target.isoformat())
        target -= timedelta(days=1)
    for b, f, day in zip(reg.bars, reg.factors, reversed(dates)):
        b['trade_date'] = f['trade_date'] = day
    stocks = [{**STOCKS[0], 'code': f'{i:06d}.SZ'} for i in range(1, 21)]
    block = ct.collect(reg, stocks, fixture.DATE)
    monkeypatch.setattr(ct, 'read_snapshot', lambda day: block)
    html = assembler.render_report(tmp_path, fixture.DATE)
    assert '000020.SZ' in html and 'partial' in html
    assert html.index('data-capacity-trend="v1"') < html.index('id="s3"')
    assembler.validate_report(html)

    # 显式库路径必须穿过render_report；不能因导入时默认库而混入另一份同日数据。
    chosen = tmp_path / 'selected.db'
    calls = []
    def selected_snapshot(day, db_path=None):
        calls.append((day, db_path))
        return block if db_path == chosen else ct.from_record(None, day)
    monkeypatch.setattr(ct, 'read_snapshot', selected_snapshot)
    selected_html = assembler.render_report(tmp_path, fixture.DATE, capacity_trend_db_path=chosen)
    assert calls == [(fixture.DATE, chosen)]
    assert '000020.SZ' in selected_html

    path = tmp_path / f'b{fixture.DATE}_s2.html'
    path.write_text(path.read_text() + '<p data-capacity-trend="v1">伪造明细</p>')
    with pytest.raises(assembler.ReportValidationError, match='成交榜容量趋势由组装器只读生成'):
        assembler.render_report(tmp_path, fixture.DATE)


def test_explicit_database_wins_over_default_database(tmp_path, monkeypatch):
    reg = Registry()
    default, selected = tmp_path/'default.db', tmp_path/'selected.db'
    for path, name in ((default, '默认库股票'), (selected, '指定库股票')):
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        init_schema(conn)
        block = ct.collect(reg, [{**STOCKS[0], 'name': name}], reg.day)
        repo.save_concentration(conn, dict(date=reg.day, total_amount_billion=10, source={ct.KEY: block}))
        conn.close()
    from db import connection
    monkeypatch.setattr(connection, '_DEFAULT_DB_PATH', default)
    before = {p: p.read_bytes() for p in (default, selected)}
    assert ct.read_snapshot(reg.day)['rows'][0]['name'] == '默认库股票'
    assert ct.read_snapshot(reg.day, selected)['rows'][0]['name'] == '指定库股票'
    assert all(p.read_bytes() == before[p] for p in before)

    spec = importlib.util.spec_from_file_location('capacity_cli_fixture', Path(__file__).with_name('test_daily_review_html_report.py'))
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    chunks = tmp_path/'chunks'
    fixture._write_chunks(chunks, reg.day)
    output = tmp_path/'review.html'
    monkeypatch.setenv('TRADE_DB_PATH', str(default))
    result = fixture._run_cli([str(chunks), reg.day, '--trade-db', str(selected), '--output', str(output)])
    assert result.returncode == 0, result.stdout + result.stderr
    html = output.read_text()
    assert '指定库股票' in html and '默认库股票' not in html
    assert all(p.read_bytes() == before[p] for p in before)
