"""固定反馈样本与独立当日量能对照，不运行采集或业务写入。"""
from copy import deepcopy
from pathlib import Path
import re

import pytest

from analyzers import theme_feedback as f, theme_launch as tl, sector_increment as si
from scripts.tests.test_theme_feedback import calculate, evidence, DAY, SOURCE, PREV, THEME, CODES, setup, NOW, save
from scripts.tests.test_theme_launch import inputs


def current_launch(factor=2, outside=3, members=None, code=THEME):
    e = inputs()
    all_codes = CODES + ['600999.SH']
    previous = [dict(ts_code=c, trade_date=SOURCE, amount=(1 if c in CODES else 9)*1e5) for c in all_codes]
    today = [dict(ts_code=c, trade_date=DAY, amount=(factor if c in CODES else outside)*1e5) for c in all_codes]
    e['increment'] = si.calculate(today, previous,
        [dict(ts_code=c, list_date='20000101') for c in all_codes],
        {c: {'sw_l2': '行业'} for c in all_codes}, DAY, SOURCE, min_count=4)
    e.update(limit_up_date=DAY, previous_limit_up_date=SOURCE)
    e['ranking']['data'][0].update(ts_code=code, trade_date=DAY.replace('-', ''))
    member_rows = [dict(ts_code=code, con_code=c, con_name='股票'+str(CODES.index(c)), is_new='Y')
                   for c in (CODES if members is None else members)]
    e['membership'] = dict(date=DAY, fetched_at=DAY+'T20:00:00+08:00',
                          themes={code: dict(status='complete', rows=member_rows, pages=[len(member_rows), 0])})
    for row in e['indices'].values():
        row['trade_date'] = DAY
    return tl.calculate(DAY, SOURCE, e)


def context(block=None, launch=None):
    return f._continuity(calculate() if block is None else block,
                         current_launch() if launch is None else launch, DAY, DAY)


@pytest.mark.parametrize('factor,outside,market,theme', [
    (2, 3, '缩量（-25.00%）', '放量（+100.00%）'),
    (2, 12, '放量（+50.00%）', '放量（+100.00%）'),
    (.5, 12, '放量（+12.50%）', '缩量（-50.00%）'),
    (.5, 3, '缩量（-62.50%）', '缩量（-50.00%）'),
    (1, 9, '持平（+0.00%）', '持平（+0.00%）'),
])
def test_market_and_theme_directions_are_independent(factor, outside, market, theme):
    b, launch = calculate(), current_launch(factor, outside)
    before = deepcopy((b, launch))
    c = context(b, launch)
    assert market in c['market'] and theme in c['rows'][0][3] and not c['gaps']
    assert c['rows'][0][1] == '10cm 1、20cm 2、30cm 0'
    assert '固定样本3只' in c['rows'][0][2] and '收涨2/3' in c['rows'][0][2]
    assert '收阴1/3' in c['rows'][0][2] and '再次涨停2/3' in c['rows'][0][2]
    assert '连板核验2/3' in c['rows'][0][2]
    if factor == 2 and outside == 3:
        assert '+41.67个百分点' in c['rows'][0][3]
    html, gap = f.render_html(b, DAY, current_launch=launch)
    markdown = f.render_markdown(b, DAY, current_launch=launch)
    assert not gap and market in html and market in markdown
    assert theme in html and theme in markdown and (b, launch) == before


def test_current_members_do_not_replace_yesterday_sample():
    c = context(launch=current_launch(members=[CODES[0]]))
    assert '固定样本3只' in c['rows'][0][2]
    assert '10cm 1、20cm 2、30cm 0' == c['rows'][0][1]
    assert '+13.89个百分点' in c['rows'][0][3]
    assert f'固定样本日{SOURCE}' in c['dates'] and f'量能成员日{DAY}' in c['dates']


def test_markdown_keeps_context_and_board_tables_separate():
    markdown = f.render_markdown(calculate(), DAY, current_launch=current_launch())
    # GFM连续管道行属于同一张表：检查独立块与列数，避免依赖未声明的Markdown解析库。
    tables = [[line.strip('|').split('|') for line in block.splitlines()]
              for block in re.split(r'\n\s*\n', markdown) if block.startswith('|')]
    assert len(tables) == 2
    assert all(len(row) == 4 for row in tables[0])
    assert all(len(row) == 8 for row in tables[1])
    assert [cell.strip() for cell in tables[1][0][-4:]] == f.GROUP_HEADERS[-4:]
    assert [cell.strip() for cell in tables[1][2][-2:]] == ['2/3', '2/3']


def test_missing_feedback_does_not_claim_existing_verified_results():
    html, gap = f.render_html(None, DAY)
    assert '保留已核验反馈' not in html + gap and '暂不能判断' in html


def test_drop_from_top_ten_keeps_feedback_without_weakness_claim():
    c = context(launch=current_launch(code='885999.TI'))
    assert '固定样本3只' in c['rows'][0][2] and '再次涨停2/3' in c['rows'][0][2]
    assert '未收录或榜单未取得，不据此判转弱' in c['rows'][0][3]
    assert c['gaps'] and '缩量' in c['market']


@pytest.mark.parametrize('mode', ['missing', 'wrong_day', 'wrong_previous', 'tampered', 'renamed', 'members_failed'])
def test_failed_context_keeps_feedback_and_exposes_gaps(mode):
    b, launch = calculate(), current_launch()
    if mode == 'missing':
        launch = None
    if mode == 'wrong_day':
        launch['trade_date'] = SOURCE
    if mode == 'wrong_previous':
        raw = launch['evidence']
        raw['previous_limit_up_date'] = PREV
        launch = tl.calculate(DAY, PREV, raw)
    if mode == 'tampered':
        launch['rows'][0]['amount']['change_pct'] = 999
    if mode == 'renamed':
        raw = launch['evidence']; raw['ranking']['data'][0]['name'] = '新名称'
        launch = tl.calculate(DAY, SOURCE, raw)
    if mode == 'members_failed':
        raw = launch['evidence']; raw['membership']['themes'][THEME]['status'] = 'source_failed'
        launch = tl.calculate(DAY, SOURCE, raw)
    html, gap = f.render_html(b, DAY, current_launch=launch)
    assert '固定样本3只' in html and '再次涨停2/3' in html
    assert '量能背景对照不完整' in gap and '+999' not in html
    assert '题材放量' not in html


def test_partial_market_does_not_publish_partial_total_as_full_direction():
    launch = current_launch(); raw = launch['evidence']
    raw['increment'].update(status='partial', gaps=['上市宇宙缺行情'])
    launch = tl.calculate(DAY, SOURCE, raw)
    assert launch['status'] == 'partial' and launch['rows'][0]['amount']['share_delta_pp'] is None
    c = context(launch=launch)
    assert c['market'] == '沪深市场成交额：未完整核验'
    assert '题材放量（+100.00%）' in c['rows'][0][3] and '份额变化未计算' in c['rows'][0][3]
    assert c['gaps']


def test_coverage_denominators_stay_separate():
    e = evidence(); b = e['feedback']; stock = b['details'][2]
    stock.update(quote_status='source_failed', touched_previous_high=None, errors=['missing'])
    b.update(summary=f.sf._summary(b['details']), status='partial')
    c = context(calculate(e))
    assert '固定样本3只；行情2/3' in c['rows'][0][2]
    assert '收涨2/2' in c['rows'][0][2] and '再次涨停2/2' in c['rows'][0][2]
    assert '连板核验2/2' in c['rows'][0][2]


def test_empty_samples_have_no_invented_zero_return():
    e = evidence(); raw = e['launch']['evidence']; raw['limit_up'].update(count=0, stocks=[])
    e['launch'] = tl.calculate(SOURCE, PREV, raw)
    c = context(calculate(e))
    assert c['rows'][0][1] == '10cm 0、20cm 0、30cm 0'
    assert c['rows'][0][2] == '昨日已核验涨停样本0只，无次日样本反馈'


def test_zero_prior_theme_amount_does_not_become_infinite_or_zero_percent():
    launch = current_launch(); raw = launch['evidence']
    # 使用计算入口构造合法的零成交题材（市场仍有正成交额）。
    all_codes = CODES + ['600999.SH']
    raw['increment'] = si.calculate(
        [dict(ts_code=c, trade_date=DAY, amount=1e5) for c in all_codes],
        [dict(ts_code=c, trade_date=SOURCE, amount=0 if c in CODES else 9e5) for c in all_codes],
        [dict(ts_code=c, list_date='20000101') for c in all_codes],
        {c: {'sw_l2': '行业'} for c in all_codes}, DAY, SOURCE, min_count=4)
    c = context(launch=tl.calculate(DAY, SOURCE, raw))
    assert '题材未计算' in c['rows'][0][3] and c['gaps']


def test_html_and_markdown_escape_names_and_limit_only_visible_summary():
    e = evidence(); raw = e['launch']['evidence']
    launch = current_launch(); current = launch['evidence']
    for data, day in [(raw, SOURCE), (current, DAY)]:
        for i in range(1, 5):
            code = f'88500{i+1}.TI'
            data['ranking']['data'].append(dict(ts_code=code, name=f'题材{i}<>&|', rank=i+1, trade_date=day.replace('-', ''), pct_chg=1))
            member = deepcopy(data['membership']['themes'][THEME])
            for stock in member['rows']: stock['ts_code'] = code
            data['membership']['themes'][code] = member
    e['launch'] = tl.calculate(SOURCE, PREV, raw)
    b, launch = calculate(e), tl.calculate(DAY, SOURCE, current)
    html, gap = f.render_html(b, DAY, current_launch=launch)
    visible = html.split('<details')[0]
    assert not gap and '另2个题材' in visible and '题材4' not in visible and '题材4' in html
    assert '题材1&lt;&gt;&amp;|' in html and '题材1<>' not in html
    assert '题材1<>&｜' in f.render_markdown(b, DAY, current_launch=launch)


@pytest.mark.parametrize('change', ['numeric', 'direction', 'date', 'hidden', 'dropped', 'different_input'])
def test_official_report_guards_combined_sources(tmp_path, change):
    from scripts.tests import test_daily_review_html_report as h
    a = h._load_assembler(); h._write_chunks(tmp_path, DAY)
    b, launch = calculate(), current_launch()
    html = a.render_report(tmp_path, DAY, theme_feedback=b, theme_launch=launch)
    a.validate_report(html, theme_feedback=b, theme_launch=launch)
    fragment = f.render_html(b, DAY, current_launch=launch)[0]
    if change == 'numeric': other = fragment.replace('+41.67个百分点', '+99.99个百分点')
    if change == 'direction': other = fragment.replace('沪深市场成交额：缩量', '沪深市场成交额：放量')
    if change == 'date': other = fragment.replace(f'量能成员日{DAY}', f'量能成员日{SOURCE}')
    if change == 'hidden': other = '<div hidden>'+fragment+'</div>'
    if change == 'dropped': other = fragment.replace('题材放量（+100.00%）', '')
    if change == 'different_input':
        other = fragment
        launch = current_launch(.5, 12)
    changed = html.replace(fragment, other)
    with pytest.raises(a.ReportValidationError):
        a.validate_report(changed, theme_feedback=b, theme_launch=launch)


def test_closed_day_projects_both_sources_to_same_fact_day(tmp_path):
    import sqlite3
    db, raw = setup(tmp_path); raw[tl.KEY] = current_launch(); save(tmp_path, DAY, raw)
    with sqlite3.connect(db) as conn:
        conn.execute('INSERT INTO trade_calendar VALUES ("2026-11-04","SSE",0)')
    report_day = '2026-11-04'
    b = f.load_for_review(tmp_path, report_day, db, now=NOW.replace(day=4))
    launch = tl.load_for_review(tmp_path, report_day, db)
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    html, gap = f.render_html(b, report_day, current_launch=launch)
    assert not gap and '休市展示2026-11-03' in html
    assert '量能成员日2026-11-03' in html and '沪深市场成交额：缩量（-25.00%）' in html
    assert before == {p: p.read_bytes() for p in before}


def test_daily_report_uses_current_batch_amounts_and_does_not_persist_summary(tmp_path, monkeypatch):
    import yaml
    from datetime import datetime
    from generators.report import ReportGenerator
    db, raw = setup(tmp_path); raw[tl.KEY] = current_launch()
    monkeypatch.setenv('TRADE_DB_PATH', str(db))
    monkeypatch.setattr('db.connection._DEFAULT_DB_PATH', db)
    monkeypatch.setattr(f, 'datetime', type('Clock', (datetime,), {'now': classmethod(lambda cls, tz=None: NOW)}))
    save(tmp_path, DAY, {**raw, tl.KEY: current_launch(.5, 12)})
    generator = ReportGenerator(); generator.daily_dir = tmp_path
    markdown, path = generator.generate_post_market(DAY, raw)
    assert '板块延续与量能背景' in markdown and '沪深市场成交额：缩量（-25.00%）' in markdown
    saved = yaml.safe_load(Path(path).read_text())['raw_data']
    assert saved[tl.KEY] == raw[tl.KEY] and 'theme_feedback' not in saved and 'theme_continuity' not in saved
