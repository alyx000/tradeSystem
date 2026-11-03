from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
import yaml

from analyzers import market_strength as ms
from analyzers import zhong_prefix_effect as z
from providers.base import DataResult

DAY = '2026-09-14'


@pytest.fixture
def evidence():
    quotes, identities, industry = [], [], {}
    names = ['中国甲', '中乙', '中丙', '中丁', '中戊', '华中公司', 'ST中甲']
    for i in range(4002):
        code = f'{i+1:06d}.SZ'
        pct = [10, 6, 5, 5, -5, 50, 10][i] if i < 7 else 0
        quotes.append(dict(ts_code=code, trade_date=DAY, close=10*(1+pct/100),
                           pre_close=10, pct_chg=pct, open=10, amount=(i+1)*1e5))
        identities.append(dict(ts_code=code, name=names[i] if i < 7 else '普通股', list_date='20000101'))
        industry[code] = dict(sw_l2='行业甲')
    st = [dict(ts_code='000007.SZ', trade_date=DAY)]
    strength = ms.calculate(quotes, identities, st, industry, DAY)
    assert strength['status'] == 'complete'
    return dict(market_strength=strength, quotes=quotes, name_snapshot_date=DAY,
                quote_source='tushare:daily', quote_fetched_at=DAY+'T20:00:00')


def test_metrics_prefix_scope_core_roles_and_ties(evidence):
    b = z.calculate(DAY, evidence)
    assert b['status'] == 'complete'
    assert b['metrics']['sample_count'] == 5
    assert b['metrics']['pct_chg_mean'] == pytest.approx(4.2)
    assert b['metrics']['pct_chg_median'] == 5
    assert b['metrics']['advance_rate'] == .8
    assert b['metrics']['strong_gain_count'] == 4
    assert b['metrics']['strong_loss_count'] == 1
    assert b['metrics']['amount_yi'] == 15
    roles = {r['code']: r['roles'] for r in b['core']}
    assert set(roles) == {'000001.SZ', '000002.SZ', '000003.SZ', '000004.SZ'}
    assert '强势前排' in roles['000004.SZ']
    assert roles['000001.SZ'] == ['强势前排']
    assert '上涨容量前排' in roles['000004.SZ']
    assert b['evidence'] == evidence
    assert z.validate(b, DAY) == b


@pytest.mark.parametrize('mode', ['name_date', 'wrong_day', 'duplicate', 'identity', 'quote_value', 'amount_source', 'minimum'])
def test_hard_failures_never_return_zero_or_core(evidence, mode):
    if mode == 'name_date': evidence['name_snapshot_date'] = '2026-09-11'
    if mode == 'wrong_day': evidence['quotes'][0]['trade_date'] = '20260911'
    if mode == 'duplicate': evidence['quotes'].append(evidence['quotes'][0])
    if mode == 'identity': evidence['market_strength']['rows'][0]['name'] = None
    if mode == 'quote_value': evidence['quotes'][0]['pct_chg'] = float('nan')
    if mode == 'amount_source': evidence['quote_source'] = 'unknown'
    if mode == 'minimum': evidence['market_strength']['coverage']['minimum_count'] = 1
    b = z.calculate(DAY, evidence)
    assert b['status'] == 'source_failed'
    assert b['metrics'] is None and b['core'] == []


def test_partial_amount_keeps_returns_but_disables_capacity(evidence):
    evidence['quotes'][0]['amount'] = None
    b = z.calculate(DAY, evidence)
    assert b['status'] == 'partial'
    assert b['metrics']['pct_chg_median'] == 5
    assert b['metrics']['amount_yi'] is None and b['metrics']['amount_share_pct'] is None
    assert all(r['roles'] == ['强势前排'] for r in b['core'])


def test_missing_members_suspend_core(evidence):
    s = evidence['market_strength']
    s['status'] = 'partial'; s['gaps'] = ['缺行情']
    c = s['coverage']; c['eligible_count'] += 1
    c['quote_coverage'] = c['sample_count']/c['eligible_count']
    c['missing_codes'] = ['600999']
    b = z.calculate(DAY, evidence)
    assert b['status'] == 'partial' and b['core'] == []
    assert b['metrics']['sample_count'] == 5


def test_empty_cohort_is_null_and_no_forced_core(evidence):
    for r in evidence['market_strength']['rows']: r['name'] = '普通股'
    b = z.calculate(DAY, evidence)
    assert b['status'] == 'complete' and b['core'] == []
    assert b['metrics']['sample_count'] == 0 and b['metrics']['advance_rate'] is None


def test_source_recomputation_rejects_fake_core_and_metrics(evidence):
    b = z.calculate(DAY, evidence)
    b['core'][0]['name'] = '假的核心'
    assert z.validate(b, DAY)['status'] == 'source_failed'
    b = z.calculate(DAY, evidence); b['metrics']['sample_count'] = 999
    assert z.validate(b, DAY)['status'] == 'source_failed'


def test_html_escapes_names_and_keeps_evidence_folded(evidence):
    evidence['market_strength']['rows'][0]['name'] = '中<script>甲|乙</script>'
    b = z.calculate(DAY, evidence)
    html, gap = z.render_html(b, DAY)
    assert gap is None and '<script>' not in html and '&lt;script&gt;' in html
    assert '<details open' not in html
    assert '中<script>甲｜乙</script>' in z.render_markdown(b, DAY)


@pytest.mark.parametrize('change', ['delete', 'duplicate', 'numeric', 'identity', 'hidden', 'outside', 'open'])
def test_official_html_source_contract(tmp_path, evidence, change):
    from scripts.tests import test_daily_review_html_report as h
    a = h._load_assembler(); h._write_chunks(tmp_path, DAY)
    b = z.calculate(DAY, evidence)
    html = a.render_report(tmp_path, DAY, zhong_prefix_effect=b)
    a.validate_report(html, zhong_prefix_effect=b)
    fragment = z.render_html(b, DAY)[0]
    altered = fragment
    if change == 'delete': altered = ''
    if change == 'duplicate': altered = fragment+fragment
    if change == 'numeric': altered = fragment.replace('样本5只', '样本9只')
    if change == 'identity': altered = fragment.replace('000001', '600999')
    if change == 'hidden': altered = '<div hidden>'+fragment+'</div>'
    if change == 'open': altered = fragment.replace('<details ', '<details open ')
    changed = html.replace(fragment, altered)
    if change == 'outside': changed = html.replace(fragment, '').replace('</article>', fragment+'</article>')
    with pytest.raises(a.ReportValidationError): a.validate_report(changed, zhong_prefix_effect=b)


def test_missing_and_handwritten_module(tmp_path):
    from scripts.tests import test_daily_review_html_report as h
    a = h._load_assembler(); h._write_chunks(tmp_path, DAY)
    b = z.missing(DAY, '旧快照未计算', 'not_computed')
    html = a.render_report(tmp_path, DAY, zhong_prefix_effect=b)
    a.validate_report(html, zhong_prefix_effect=b)
    assert '中字头赚钱效应存在缺口' in html
    p = tmp_path/f'b{DAY}_s456.html'
    p.write_text(p.read_text()+f'<div {z.MARKER}="{DAY}">伪造</div>')
    with pytest.raises(a.ReportValidationError, match='duplicate_zhong_prefix'):
        a.render_report(tmp_path, DAY, zhong_prefix_effect=b)


def test_post_envelope_roundtrip(tmp_path, evidence):
    from generators.report import ReportGenerator
    from db.dual_write import _extract_market_row, parse_post_market_envelope
    generator = ReportGenerator(); generator._ensure_dir = MagicMock(return_value=tmp_path)
    b = z.calculate(DAY, evidence)
    markdown, path = generator.generate_post_market(DAY, {z.KEY: b})
    payload = yaml.safe_load(Path(path).read_text())
    assert payload['raw_data'][z.KEY] == b
    stored = _extract_market_row(DAY, payload)
    assert parse_post_market_envelope(stored['raw_data'])['raw_data'][z.KEY] == b
    assert '中字头赚钱效应' in markdown and '中国甲' in markdown


def test_collector_reuses_batch_without_extra_provider_calls(monkeypatch, tmp_path):
    from collectors.market import MarketCollector
    monkeypatch.setattr('collectors.market.BASE_DIR', tmp_path)
    registry = MagicMock()
    registry.call.return_value = DataResult(None, 'test', error='unavailable')
    collector = MarketCollector(registry)
    collector._compute_index_ma = MagicMock()
    collector._collect_research_coverage = MagicMock(return_value=[])
    collector._rhythm_analyzer = MagicMock()
    collector._rhythm_analyzer.load_main_theme_names.return_value = []
    collector._rhythm_analyzer.analyze.return_value = []
    collect = MagicMock(return_value=z.missing(DAY, 'source failure'))
    monkeypatch.setattr(z, 'collect', collect)
    result = collector.collect_post_market(DAY)
    assert result[z.KEY] == collect.return_value
    assert collect.call_args.kwargs['market_strength'] is result['market_strength']
    assert collect.call_args.kwargs['quote_result'] is registry.call.return_value


def test_collection_calendar_clock_and_historical_name_gates(tmp_path, evidence):
    import sqlite3
    from datetime import date, timedelta
    db = tmp_path/'calendar.db'; conn = sqlite3.connect(db)
    conn.execute('CREATE TABLE trade_calendar(date TEXT, exchange TEXT, is_open INTEGER)')
    start = date(2026, 8, 25)
    for i in range(21):
        d = start+timedelta(days=i)
        conn.execute('INSERT INTO trade_calendar VALUES(?,?,?)', (d.isoformat(), 'SSE', int(d.weekday() < 5)))
    conn.commit()
    quote = DataResult(evidence['quotes'], 'tushare:daily')
    def run(day=DAY, hour=20, clock_day=14):
        return z.collect(day, market_strength=evidence['market_strength'], quote_result=quote,
                         now=datetime(2026, 9, clock_day, hour, tzinfo=ZoneInfo('Asia/Shanghai')), db_path=db)
    assert run()['status'] == 'complete'
    assert run(hour=15)['status'] == 'skipped'
    assert run(clock_day=15)['status'] == 'source_failed'
    conn.execute("DELETE FROM trade_calendar WHERE date='2026-09-10'"); conn.commit(); conn.close()
    assert run()['status'] == 'source_failed'


def test_other_stock_missing_does_not_disable_complete_zhong_core(evidence):
    s = evidence['market_strength']; s['status'] = 'partial'; s['gaps'] = ['缺行情']
    c = s['coverage']; c['eligible_count'] += 1
    c['quote_coverage'] = c['sample_count']/c['eligible_count']
    c['missing_codes'] = ['600999.SH']; c['missing_names'] = {'600999.SH': '其他公司'}
    b = z.calculate(DAY, evidence)
    assert b['status'] == 'partial' and len(b['core']) == 4
    assert b['metrics']['eligible_count'] == 5
    c['missing_names']['600999.SH'] = '中国缺失'
    b = z.calculate(DAY, evidence)
    assert b['status'] == 'partial' and b['core'] == []
    assert b['metrics']['eligible_count'] == 6


def test_all_decline_never_forces_core_and_open_gap_remains_partial(evidence):
    for row, quote in zip(evidence['market_strength']['rows'][:5], evidence['quotes'][:5]):
        row['pct_chg'] = -1
        quote.update(pct_chg=-1, close=9.9)
    market, sectors = ms._aggregate(evidence['market_strength']['rows'])
    evidence['market_strength'].update(market=market, sectors=sectors)
    evidence['quotes'][0]['open'] = None
    b = z.calculate(DAY, evidence)
    assert b['status'] == 'partial' and b['core'] == []
    assert b['metrics']['intraday_count'] == 4
    assert b['metrics']['advance_rate'] == 0


def test_historical_review_reads_saved_snapshot_and_closed_day_context(tmp_path, evidence):
    import sqlite3
    b = z.calculate(DAY, evidence)
    folder = tmp_path/DAY; folder.mkdir()
    (folder/'post-market.yaml').write_text(yaml.safe_dump({'date': DAY, 'raw_data': {z.KEY: b}}, allow_unicode=True))
    db = tmp_path/'calendar.db'; conn = sqlite3.connect(db)
    conn.execute('CREATE TABLE trade_calendar(date TEXT, exchange TEXT, is_open INTEGER)')
    conn.executemany('INSERT INTO trade_calendar VALUES(?,?,?)', [(DAY,'SSE',1), ('2026-09-15','SSE',0)])
    conn.commit(); conn.close()
    v = z.load_for_review(tmp_path, '2026-09-15', db)
    html, gap = z.render_html(v, '2026-09-15')
    assert gap is None and '事实日：2026-09-14' in html and '报告日：2026-09-15' in html
    assert '中国甲' in html
    v['review_context']['calendar'][-1][1] = 1
    assert z.render_html(v, '2026-09-15')[1]


@pytest.mark.parametrize('report_day', [DAY, '2026-09-15'])
def test_review_loader_rejects_other_exchange_calendar(tmp_path, report_day):
    import sqlite3
    db = tmp_path/'calendar.db'; conn = sqlite3.connect(db)
    conn.execute('CREATE TABLE trade_calendar(date TEXT, exchange TEXT, is_open INTEGER)')
    conn.executemany('INSERT INTO trade_calendar VALUES(?,?,?)', [(DAY,'OTHER',1), ('2026-09-15','OTHER',0)])
    conn.commit(); conn.close()
    assert z.load_for_review(tmp_path, report_day, db)['status'] == 'source_failed'
