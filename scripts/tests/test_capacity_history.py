"""板块容量历史：防换榜假改善、防缺日连线，以及只读入口验收。"""
from copy import deepcopy
from datetime import date, timedelta
import sqlite3

import pytest

from db.schema import init_schema
from services.volume_concentration import capacity_history as ch, capacity_trend as ct, repo, service

DAY = '2026-10-30'
PREV = '2026-10-29'


def record(day=DAY, specs=(('000001.SZ', '行业甲', '上方'), ('000002.SZ', '行业甲', '下方'))):
    days = [(date.fromisoformat(day) - timedelta(days=n)).isoformat() for n in reversed(range(30))]
    rows = []
    for code, industry, position in specs:
        closes = list(range(30, 60)) if position == '上方' else list(range(60, 30, -1)) if position == '下方' else [50] * 30
        bars = [dict(trade_date=d, close=c) for d, c in zip(days, closes)]
        factors = [dict(trade_date=d, adj_factor=1) for d in days]
        rows.append(dict(code=code, name='股票'+code, industry=industry, amount_billion=10,
                         **ct.analyze(bars, factors, day)))
    block = dict(schema=ct.SCHEMA, trade_date=day, status='complete', window_bars=5, ma_period=20,
                 total_count=len(rows), complete_count=len(rows), rows=rows)
    return dict(date=day, top_n=len(rows), total_amount_billion=10 * len(rows), market_total_billion=10000,
                stocks=[{k: r[k] for k in ('code', 'name', 'industry', 'amount_billion')} for r in rows],
                sector_summary=[], source={ct.KEY: block, 'market_total_source': 'test-source'})


@pytest.fixture
def conn():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    init_schema(c)
    end = date.fromisoformat(DAY)
    for n in range(90):
        d = end - timedelta(days=n)
        c.execute('INSERT INTO trade_calendar (exchange,date,is_open) VALUES (?,?,?)', ('SSE', d.isoformat(), int(d.weekday() < 5)))
    c.commit()
    yield c
    c.close()


def test_sector_coverage_uses_valid_denominator_and_unknown_is_not_zero():
    r = record(specs=(('000001', '甲', '上方'), ('000002', '甲', '下方'), ('000003', '', '持平')))
    b = r['source'][ct.KEY]
    b['rows'][1] = dict(code='000002', industry='甲', status='source_failed', reason='失败')
    b.update(status='partial', complete_count=2)
    s = ch.summarize(r, DAY)
    assert (s['sectors']['甲']['valid'], s['sectors']['甲']['total']) == (1, 2)
    assert s['sectors']['甲']['above_pct'] == 100
    assert s['sectors']['未分类']['flat'] == 1
    assert s['overall']['above_pct'] == 50
    b['rows'][0] = dict(code='000001', industry='甲', status='partial')
    b['complete_count'] = 1
    assert ch.summarize(r, DAY)['sectors']['甲']['above_pct'] is None


def test_replacement_does_not_invent_improvement_in_common_stocks():
    old = ch.summarize(record(PREV), PREV)
    new = ch.summarize(record(specs=(('000001.SZ', '行业甲', '上方'), ('000003.SZ', '行业甲', '上方'))), DAY)
    c = ch.compare(old, new, '行业甲')
    assert c['daily_delta_pp'] == 50
    assert c['common_delta_pp'] == 0
    assert (c['common_count'], c['new_count'], c['dropped_count']) == (1, 1, 1)


def test_reclassification_is_separate_and_bare_codes_match():
    old = ch.summarize(record(PREV, (('000001', '甲', '下方'), ('000002', '甲', '上方'))), PREV)
    new = ch.summarize(record(DAY, (('000001.SZ', '乙', '上方'), ('000002.SZ', '甲', '上方'))), DAY)
    for ind in (None, '甲', '乙'):
        c = ch.compare(old, new, ind)
        assert c['new_count'] == c['dropped_count'] == 0
        assert c['reclassified_count'] == 1
    assert ch.compare(old, new)['common_delta_pp'] == 0
    assert ch.compare(old, new, '乙')['common_delta_pp'] is None


@pytest.mark.parametrize('state', ['partial', 'source_failed', 'fallback_preserved', 'not_computed', 'missing-data'])
def test_degraded_day_blocks_pairwise_comparison(state):
    a = ch.summarize(record(PREV), PREV)
    b = ch.summarize(record(), DAY)
    a['status'] = state
    assert ch.compare(a, b)['daily_delta_pp'] is None
    assert ch.compare(a, b)['common_count'] is None


def test_history_keeps_missing_and_old_days_and_never_bridges(conn):
    repo.save_concentration(conn, record('2026-10-27'))
    old = record('2026-10-28')
    old['source'].pop(ct.KEY)
    repo.save_concentration(conn, old)
    repo.save_concentration(conn, record())
    view = ch.build(conn, DAY, days=4)
    assert [p['status'] for p in view['history']] == ['complete', 'not_computed', 'missing-data', 'complete']
    assert view['history'][-1]['comparison']['common_count'] is None
    assert view['history'][-1]['market_change_pct'] is None
    assert '2026-10-28:not_computed' in ch.gaps(view)


@pytest.mark.parametrize('damage', ['gap', 'weekend', 'empty'])
def test_calendar_failure_preserves_daily_summary_but_blocks_history(conn, damage):
    target = DAY
    if damage == 'gap': conn.execute("DELETE FROM trade_calendar WHERE date='2026-10-25'")
    if damage == 'empty': conn.execute('DELETE FROM trade_calendar')
    if damage == 'weekend': target = '2026-10-25'
    v = ch.build(conn, target, current_record=record(target), days=10)
    assert v['calendar_status'] == 'source_failed' and not v['history']
    assert v['current']['overall']['valid'] == 2


def test_holiday_calendar_and_market_source_change(conn):
    conn.execute("UPDATE trade_calendar SET is_open=0 WHERE date='2026-10-29'")
    prev = record('2026-10-28')
    prev['market_total_billion'] = 8000
    repo.save_concentration(conn, prev)
    view = ch.build(conn, DAY, current_record=record(), days=2)
    assert [p['date'] for p in view['history']] == ['2026-10-28', DAY]
    assert view['history'][-1]['market_change_pct'] == 25
    r = record()
    r['source']['market_total_source'] = 'different'
    assert ch.build(conn, DAY, current_record=r, days=2)['history'][-1]['market_change_pct'] is None


@pytest.mark.parametrize('damage', ['duplicate', 'illegal', 'wrong_day', 'corrupt_json'])
def test_bad_snapshot_is_explicit_not_an_empty_sector(conn, damage):
    r = record()
    b = r['source'][ct.KEY]
    if damage == 'duplicate': b['rows'][1]['code'] = '000001'
    if damage == 'illegal': b['rows'][0]['code'] = 'invalid'
    if damage == 'wrong_day': b['trade_date'] = PREV
    repo.save_concentration(conn, r)
    if damage == 'corrupt_json': conn.execute("UPDATE daily_volume_concentration SET source_json='{' WHERE date=?", (DAY,))
    v = ch.build(conn, DAY, days=1)
    assert v['current']['status'] == 'source_failed'
    assert v['current']['overall'] is None


def test_fallback_visible_but_does_not_enter_comparison(conn):
    prev, current = record(PREV), record()
    failed = deepcopy(current['source'][ct.KEY])
    failed['status'], failed['complete_count'] = 'source_failed', 0
    failed['rows'] = [{**r, 'status': 'source_failed'} for r in failed['rows']]
    current['source'][ct.KEY] = ct.preserve_complete(failed, current['source'][ct.KEY])
    repo.save_concentration(conn, prev)
    view = ch.build(conn, DAY, current_record=current, days=2)
    assert view['current']['overall']['valid'] == 2
    assert view['history'][-1]['comparison']['status'] == 'not_comparable'
    rendered = str(ch.tables(view))
    assert 'fallback_preserved（本次source_failed）' in rendered


def test_absent_sector_is_not_zero_percent(conn):
    repo.save_concentration(conn, record(PREV))
    view = ch.build(conn, DAY, current_record=record(specs=(('000003', '新行业', '上方'),)), days=2)
    rows = ch.tables(view)[2][2]
    dropped = next(r for r in rows if r[0] == DAY and r[1] == '行业甲')
    assert dropped[3:5] == ['未在榜', '—']


def test_readonly_html_md_share_results_and_escape(conn, tmp_path):
    malicious = '<script>x</script>|[链接](x)'
    r = record(specs=(('000001', malicious, '上方'), ('000002', '甲', '下方')))
    repo.save_concentration(conn, record(PREV))
    repo.save_concentration(conn, r)
    path = tmp_path / 'history.db'
    dest = sqlite3.connect(path)
    conn.backup(dest)
    dest.close()
    before = path.read_bytes()
    block = ct.read_snapshot(DAY, path)
    md, (html, gap) = ct.render_markdown(block), ct.render_html(block)
    assert path.read_bytes() == before
    for label in ('容量板块汇总', '容量历史变化', '板块历史变化'):
        assert label in md and label in html
    assert '50.0% / 50.0%' in html and r'50\.0% / 50\.0%' in md
    assert '<script>' not in html and '<script>' not in md
    assert '[链接](' not in md and '&lt;script&gt;' in html
    assert 'overflow-x:auto' in html
    assert gap and '缺快照' in gap
    assert ct.read_snapshot('2026-11-02', path)['history_view']['current']['overall'] is None


def test_daily_preview_and_trend_use_history_without_persisting_it(conn, monkeypatch):
    r = record()
    repo.save_concentration(conn, record(PREV))
    monkeypatch.setattr(service.collector, 'build_record', lambda *a, **k: deepcopy(r))
    monkeypatch.setattr(service.collector, 'build_gain_universe', lambda *a: [])
    monkeypatch.setattr(ct, 'collect', lambda *a: deepcopy(r['source'][ct.KEY]))
    md = service.run_daily(conn, None, DAY, persist=False, input_by='USER')
    assert '容量股变化' in md and '同股数/线上变化' not in md and '容量板块汇总' not in md
    assert repo.get_concentration(conn, DAY) is None
    service.run_daily(conn, None, DAY, input_by='USER')
    saved = repo.get_concentration(conn, DAY)
    assert 'history_view' not in saved['source'][ct.KEY]
    assert '板块历史变化' in service.run_trend(conn, DAY, days=2)


@pytest.mark.parametrize('requested', ['2026-11-01', '2026-11-02'])
def test_trend_weekend_or_missing_snapshot_has_one_observation_date(conn, requested):
    repo.save_concentration(conn, record(PREV))
    repo.save_concentration(conn, record())
    md = service.run_trend(conn, requested, days=2)
    assert DAY in md and requested not in md
    assert r'观察日期：2026\-10\-30' in md
    assert '交易日历缺失' not in md


def test_ten_day_sector_history_is_bounded_in_daily_but_complete_in_trend(conn, monkeypatch):
    specs = tuple((f'{n:06d}.SZ', f'测试行业{n}', '上方') for n in range(1, 21))
    for day in ch._open_days(conn, DAY, 10):
        repo.save_concentration(conn, record(day, specs))
    r = record(DAY, specs)
    monkeypatch.setattr(service.collector, 'build_record', lambda *a, **k: deepcopy(r))
    monkeypatch.setattr(service.collector, 'build_gain_universe', lambda *a: [])
    monkeypatch.setattr(ct, 'collect', lambda *a: deepcopy(r['source'][ct.KEY]))
    full = service.run_trend(conn, DAY, days=10)
    daily = service.run_daily(conn, None, DAY, persist=False, input_by='USER')
    assert len(full.encode('utf-8')) > 20000
    assert len(daily.encode('utf-8')) <= 18000
    assert '每类最多3只' in daily
    assert '日报历史为限长摘要' not in full
    assert '测试行业20' in full


@pytest.mark.parametrize('budget', [0, 100, 500, 2000, 6000])
def test_history_budget_counts_escaped_utf8_text(conn, budget):
    r = record(specs=(('000001', '行业|甲[长名字](x)', '上方'),))
    block = ct.with_history(conn, r, DAY)
    original = ct.render_markdown({k: v for k, v in block.items() if k != 'history_view'})
    bounded = ct.render_markdown(block, history_budget=budget)
    assert len(bounded.encode('utf-8')) - len(original.encode('utf-8')) <= budget + 1


def test_compact_daily_preserves_gaps_without_methodology_or_detail_tables(conn):
    repo.save_concentration(conn, dict(date=PREV, total_amount_billion=10, source={}))
    block = ct.with_history(conn, record(), DAY, days=3)
    md = ct.render_markdown(block, compact=True, history_budget=6000)
    assert '当前线上且均线向上' in md and '股票000001' in md
    assert '跨日变化未计算' in md and '1日缺快照' not in md
    assert '容量板块汇总' not in md
    assert '容量历史变化' not in md and '末端连续' not in md and '逐行身份' not in md
    assert '2026\\-10\\-28:missing' not in md
    full = ct.render_markdown(block)
    assert '容量历史变化' in full and '末端连续' in full and '逐行身份' in full
    # 极小预算只能省略板块表，不得吞掉失败提示或详情入口。
    tiny = ct.render_markdown(block, compact=True, history_budget=0)
    assert '跨日变化未计算' in tiny and 'volume\\-watch trend' in tiny


def _visible_html_text(html):
    from html.parser import HTMLParser

    class Visible(HTMLParser):
        def __init__(self):
            super().__init__()
            self.depth = 0
            self.text = []

        def handle_starttag(self, tag, attrs):
            if tag == 'details':
                assert 'open' not in dict(attrs)
                self.depth += 1

        def handle_endtag(self, tag):
            if tag == 'details':
                self.depth -= 1

        def handle_data(self, data):
            if not self.depth:
                self.text.append(data)

    parser = Visible()
    parser.feed(html)
    return ''.join(parser.text)


def test_html_default_view_is_short_but_preserves_full_evidence(conn):
    block = ct.with_history(conn, record(), DAY)
    html, gap = ct.render_html(block)
    visible = _visible_html_text(html)
    assert len(visible) < 350
    assert DAY in visible and '股票000001' in visible and '跨日变化未计算' in visible
    assert '9日缺快照' not in visible
    assert '逐行身份' not in visible and '2026-10-29' not in visible
    assert '逐行身份' in html and '2026-10-29:missing-data' in html
    assert '9日缺快照' in gap and '2026-10-29' not in gap


@pytest.mark.parametrize('status', ['partial', 'source_failed', 'fallback_preserved', 'not_computed'])
def test_compact_failure_states_remain_visible(conn, status):
    good = record()['source'][ct.KEY]
    failed = deepcopy(good)
    failed.update(status=status, complete_count=0)
    failed['rows'] = [dict(code=r['code'], name=r['name'], industry=r['industry'],
                           status='source_failed', reason='<源失败>') for r in good['rows']]
    if status == 'partial':
        failed['rows'][0] = good['rows'][0]
        failed['complete_count'] = 1
    elif status == 'fallback_preserved':
        failed['status'] = 'source_failed'
        failed = {**good, 'status': status, 'latest_attempt': failed}
    elif status == 'not_computed':
        failed = ct.from_record(None, DAY)
    html, gap = ct.render_html(failed)
    visible = _visible_html_text(html)
    md = ct.render_markdown(failed, compact=True, history_budget=0)
    if status == 'not_computed':
        assert '未计算' in visible and '未计算' in md and '0/0' not in md
    else:
        assert status in visible and status.replace('_', r'\_') in md
    if status == 'fallback_preserved':
        assert '本次刷新source_failed' in visible and '同日已有完整结果' in visible
        assert '本次缺口：000001.SZ、000002.SZ' in html
    assert gap


def test_gap_summary_keeps_calendar_and_market_failure_separate(conn):
    view = ch.build(conn, DAY, current_record=record(), days=1)
    assert ch.gap_summary(view) is None
    view['history'][0]['market_source'] = None
    assert ch.gap_summary(view) == '两市成交额或来源缺口1日'
    view['calendar_status'] = 'source_failed'
    view['reason'] = '交易日历缺失，历史变化未计算'
    assert ch.gap_summary(view) == view['reason']


def test_stock_digest_separates_common_changes_from_entrants_and_reclassification(conn):
    from services.volume_concentration.capacity_digest import observations
    before = record(PREV, (('000001', '甲', '上方'), ('000002', '甲', '下方'),
                           ('000003', '甲', '上方'), ('000004', '甲', '下方'),
                           ('000006', '乙', '下方')))
    today = record(DAY, (('000001', '甲', '上方'), ('000002', '甲', '上方'),
                         ('000003', '甲', '下方'), ('000004', '甲', '下方'),
                         ('000005', '甲', '上方'), ('000006', '丙', '上方')))
    rows = today['source'][ct.KEY]['rows']
    rows[1]['ma20_direction'] = '向下'
    rows[3]['ma20_distance_pct'] -= 2
    repo.save_concentration(conn, before)
    block = ct.with_history(conn, today, DAY)
    lines = observations(block, limit=None)
    kept = next(x for x in lines if x.startswith('保持'))
    better = next(x for x in lines if x.startswith('改善'))
    worse = next(x for x in lines if x.startswith('走坏'))
    new = next(x for x in lines if x.startswith('新进'))
    assert '同股同分类4只' in lines[0]
    assert '000001' in kept and '000005' not in kept
    assert '000002' in better and '新站上MA20' in better and '均线向下' in better
    assert '000005' not in better and '000006' not in better
    assert '000003' in worse and '新跌破MA20' in worse and '000004' in worse and '距线' in worse
    assert '000005' in new and '000006' not in new
    # 较早缺口不能阻断已有相邻开放日比较，也不再占据推送正文。
    md = ct.render_markdown(block, compact=True)
    assert '跨日变化未计算' not in md and '历史窗口' not in md


@pytest.mark.parametrize('previous_kind', ['missing', 'old', 'corrupt', 'partial', 'fallback'])
def test_stock_digest_does_not_claim_new_changes_without_complete_adjacent_day(conn, previous_kind):
    from services.volume_concentration.capacity_digest import observations
    r = record(PREV)
    if previous_kind == 'old':
        r['source'] = {}
    elif previous_kind == 'corrupt':
        r['source'][ct.KEY] = None
    elif previous_kind in ('partial', 'fallback'):
        good = deepcopy(r['source'][ct.KEY])
        bad = r['source'][ct.KEY]
        bad['rows'][0] = {**bad['rows'][0], 'status': 'source_failed', 'reason': '缺因子'}
        bad.update(status='partial', complete_count=1)
        if previous_kind == 'fallback':
            r['source'][ct.KEY] = {**good, 'status': 'fallback_preserved', 'latest_attempt': bad}
    if previous_kind != 'missing':
        repo.save_concentration(conn, r)
    lines = observations(ct.with_history(conn, record(), DAY))
    assert any('跨日变化未计算' in line for line in lines)
    assert not any(line.startswith(('保持', '改善', '走坏', '新进')) for line in lines)


def test_stock_digest_new_break_prioritized_and_omitted_changes_available_in_full(conn):
    from services.volume_concentration.capacity_digest import observations
    specs = tuple((f'{n:06d}', '甲', '下方') for n in range(1, 6))
    before, today = record(PREV, specs), record(DAY, specs)
    before['source'][ct.KEY]['rows'][-1]['ma20_position'] = '上方'
    for row in today['source'][ct.KEY]['rows']:
        row['ma20_distance_pct'] -= 2
    repo.save_concentration(conn, before)
    block = ct.with_history(conn, today, DAY)
    brief = next(x for x in observations(block) if x.startswith('走坏'))
    assert brief.index('000005') < brief.index('000001')
    assert '另有2只' in brief and '000004' not in brief
    full = ct.render_markdown(block)
    assert '完整个股变化' in full and '股票000004（000004）：距线' in full
    html = ct.render_html(block)[0]
    assert '股票000004（000004）：距线' in html


def test_stock_digest_escapes_stock_names_and_ignores_invisible_rounding_changes(conn):
    before, today = record(PREV), record()
    today['source'][ct.KEY]['rows'][0]['name'] = '<script>[链接](x)</script>'
    row = today['source'][ct.KEY]['rows'][1]
    row['ma20_distance_pct'] = round(row['ma20_distance_pct'], 2) - 0.000001
    before['source'][ct.KEY]['rows'][1]['ma20_distance_pct'] = round(row['ma20_distance_pct'], 2)
    repo.save_concentration(conn, before)
    block = ct.with_history(conn, today, DAY)
    md = ct.render_markdown(block, compact=True)
    html = ct.render_html(block)[0]
    assert '<script>' not in html and '<script>' not in md and '[链接](' not in md
    assert '&lt;script&gt;' in html and r'\[链接\]\(' in md
    assert '无新跌破' in md


def test_sector_digest_names_real_changes_without_treating_replacements_as_improvement(conn):
    from services.volume_concentration.capacity_digest import sector_observations
    repo.save_concentration(conn, record(PREV, (('000001', '甲', '下方'), ('000002', '甲', '下方'),
                                               ('000003', '乙', '下方'), ('000006', '退出组', '上方'))))
    today = record(DAY, (('000001', '甲', '上方'), ('000002', '甲', '下方'),
                         ('000004', '乙', '上方'), ('000005', '新增组', '上方')))
    today['source'][ct.KEY]['rows'][0]['ma20_direction'] = '向下'
    block = ct.with_history(conn, today, DAY)
    lines = sector_observations(block, limit=None)
    first = next(x for x in lines if x.startswith('甲：'))
    assert '同股2只' in first and '线上0→1只' in first and '均线向上0→0只' in first
    assert '股票000001（000001）：新站上' in first
    replaced = next(x for x in lines if x.startswith('乙：'))
    assert '在榜1→1只' in replaced and '无同股可比' in replaced and '当前线上1/1' in replaced
    assert '无同股可比' in next(x for x in lines if x.startswith('新增组：'))
    assert '退出成交榜不等于板块走弱' in next(x for x in lines if x.startswith('退出组：'))
    brief = sector_observations(block, limit=1)
    assert brief[0] == first and '另有3个板块' in brief[1]
    assert '完整板块变化' in ct.render_html(block)[0]
    assert '完整板块变化' in ct.render_markdown(block)


def test_sector_digest_preserves_offsetting_changes_and_reclassification(conn):
    from services.volume_concentration.capacity_digest import sector_observations
    repo.save_concentration(conn, record(PREV, (('000001', '甲', '上方'), ('000002', '甲', '下方'),
                                               ('000003', '甲', '下方'))))
    block = ct.with_history(conn, record(DAY, (('000001', '甲', '下方'), ('000002', '甲', '上方'),
                                              ('000003', '乙', '上方'))), DAY)
    lines = sector_observations(block, limit=None)
    line = next(x for x in lines if x.startswith('甲：'))
    assert '同股2只' in line and '线上1→1只' in line
    assert '股票000001（000001）：新跌破' in line and '股票000002（000002）：新站上' in line
    assert '在榜3→2只' in line and '改类不计入' in line
    assert '无同股可比' in next(x for x in lines if x.startswith('乙：'))


@pytest.mark.parametrize('damage', ['missing', 'calendar', 'partial', 'fallback'])
def test_sector_digest_never_reports_unchanged_when_comparison_unavailable(conn, damage):
    from services.volume_concentration.capacity_digest import sector_observations
    if damage != 'missing':
        repo.save_concentration(conn, record(PREV))
    block = ct.with_history(conn, record(), DAY)
    if damage == 'calendar':
        block['history_view']['calendar_status'] = 'source_failed'
    elif damage in ('partial', 'fallback'):
        block['status'] = 'partial' if damage == 'partial' else 'fallback_preserved'
    assert sector_observations(block) == ['板块变化未计算：相邻开放日证据不完整。']


def test_sector_digest_keeps_unknowns_separate_and_escapes_industry_names(conn):
    from services.volume_concentration.capacity_digest import sector_observations
    name = '<script>[板块](x)</script>'
    specs = (('000001', name, '下方'), ('000002', '', '上方'))
    repo.save_concentration(conn, record(PREV, specs))
    block = ct.with_history(conn, record(DAY, specs), DAY)
    lines = sector_observations(block)
    assert '1只仍在线下且均线向下' in lines[0]
    assert lines[-1] == '未分类：在榜1→1只，不纳入板块变化。'
    html = ct.render_html(block)[0]
    md = ct.render_markdown(block, compact=True)
    assert '<script>' not in html and '<script>' not in md
    assert '[板块](' not in md and '&lt;script&gt;' in html


def test_fresh_database_daily_cli_populates_calendar_via_existing_guard(tmp_path, monkeypatch, capsys):
    """不得直接插入日历：验证正式CLI冷启动已有的日历维护链。"""
    import argparse
    from types import SimpleNamespace
    import main
    from cli import volume_watch
    from providers.base import DataResult
    from db.migrate import migrate
    path = tmp_path / 'fresh.db'
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    migrate(c)  # 走真实空库初始化，连同版本号建立；裸init_schema不是CLI冷启动契约。
    assert c.execute('SELECT COUNT(*) FROM trade_calendar').fetchone()[0] == 0
    c.close()

    def connect():
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        return c

    calls = []
    def call(method, target):
        calls.append((method, target))
        assert method == 'get_trade_calendar'
        start = date(2026, 1, 1)
        days = [start + timedelta(days=n) for n in range(365)]
        return DataResult(data=[{'cal_date': d.strftime('%Y%m%d'), 'is_open': int(d.weekday() < 5)} for d in days], source='calendar-fixture')

    r = record()
    registry = SimpleNamespace(initialize_all=lambda: None, call=call)
    monkeypatch.setattr(main, 'setup_providers', lambda config: registry)
    monkeypatch.setattr(volume_watch, 'get_connection', connect)
    monkeypatch.setattr(service.collector, 'build_record', lambda *a, **k: deepcopy(r))
    monkeypatch.setattr(service.collector, 'build_gain_universe', lambda *a: [])
    monkeypatch.setattr(ct, 'collect', lambda *a: deepcopy(r['source'][ct.KEY]))
    volume_watch._run_daily({}, argparse.Namespace(date=DAY, input_by='USER', dry_run=False, no_push=True, refetch=False))
    assert calls == [('get_trade_calendar', '2026-06-15')]
    with connect() as c:
        assert c.execute('SELECT COUNT(*) FROM trade_calendar').fetchone()[0] == 365
        v = ch.build(c, DAY)
        assert v['calendar_status'] == 'complete'
        assert len(v['history']) == 10 and v['history'][-1]['status'] == 'complete'
    assert '交易日历缺失' not in capsys.readouterr().out
