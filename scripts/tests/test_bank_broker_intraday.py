from datetime import datetime
from hashlib import sha256
import json

import pytest

from services import bank_broker_intraday as service
from services.board_break_intraday.data import TZ, digest

DAY = '2026-11-27'
CODES = [f'60000{i}.SH' for i in range(1, 6)]


def batch(root, clock='14:02:06', name='aa', *, mutate=None):
    at = DAY+'T'+clock+'+08:00'
    mapping = dict(zip(CODES, service.BANK + service.BROKER))
    original = {c: {'sw_l2': label} for c, label in mapping.items()}
    identity = [dict(ts_code=c, name='示例') for c in CODES]
    ctx = dict(trade_date=DAY, frozen_at=DAY+'T09:32:00+08:00', universe=CODES, industry_map=mapping,
               sources=dict(industry=dict(mapping=original, sha256=digest(original), source='tushare:index_member_all'),
                            universe=dict(rows=identity, sha256=digest(identity), source='tushare:stock_basic')))
    rows = [dict(code=c, name='示例', pre_close=100, price=100+v, amount=500,
                 quote_time=clock, quote_date=DAY, pct_chg=v) for c,v in zip(CODES,[-3,-2,1,0,4])]
    stocks = {r['code']:dict(price=r['price'], amount=r['amount'], pct_chg=r['pct_chg'], quote_at=at)
              for r in rows}
    report = dict(input_by='launchd', trade_date=DAY, captured_at=at, context=ctx,
                  snapshot=dict(captured_at=at, trade_date=DAY, interval_minutes=10, source='sina',
                                raw_rows=rows, stocks=stocks))
    if mutate:
        mutate(report)
    snap = report['snapshot']
    snap.update(raw_sha256=digest(rows), context_sha256=digest(ctx))
    folder = root/'intraday-events'/DAY/name
    folder.mkdir(parents=True)
    encoded = json.dumps(report, ensure_ascii=False).encode()
    (folder/'report.json').write_bytes(encoded)
    (folder/'receipt.json').write_text(json.dumps(dict(batch=name, input_by='launchd', trade_date=DAY,
                                                         sha256=sha256(encoded).hexdigest())))
    return folder


@pytest.fixture
def observe(monkeypatch, tmp_path):
    monkeypatch.setattr(service,'calendar',lambda conn, day: {'is_open': True})
    return lambda: service.build(None, tmp_path, DAY, 'USER', datetime(2026,11,28,tzinfo=TZ))


def test_real_slots_use_quote_time_and_frozen_equal_weight(tmp_path, observe):
    batch(tmp_path)
    result = observe()
    p = next(t for t in result['points'] if t['slot']=='14:02')
    assert result['complete_slots'] == result['observed_slots'] == 1
    assert len(result['points']) == 24 and not p['scheduled_at']
    assert (p['bank']['up'],p['bank']['expected'],p['bank']['median_pct']) == (1,4,-1.0)
    assert p['broker']['valid']==1 and p['broker_minus_bank_pp']==5
    assert p['bank']['quotes'][CODES[0]]['quote_at']==DAY+'T14:02:06+08:00'
    assert '14:02 / 14:02:06' in service.render(result,DAY)[0]


@pytest.mark.parametrize('problem',['stale','future','future_day','duplicate','missing','wrong_pct'])
def test_invalid_stock_reduces_coverage_and_blocks_difference(tmp_path, observe, problem):
    def change(r):
        snap=r['snapshot']
        quote=snap['raw_rows'][0]
        if problem in ('stale','future','future_day'):
            quote.update(quote_time='13:58:00' if problem=='stale' else '14:04:01')
            if problem=='future_day':
                quote['quote_date']='2026-11-28'
        elif problem=='duplicate':
            snap['raw_rows'].append(quote.copy())
        elif problem=='missing':
            snap['stocks'].pop(CODES[0])
        else:
            snap['stocks'][CODES[0]]['pct_chg']=90
    batch(tmp_path,mutate=change)
    p=next(t for t in observe()['points'] if t['slot']=='14:02')
    assert p['status']=='partial' and p['bank']['missing_codes']==[CODES[0]]
    assert p['bank']['valid']==3 and p['broker_minus_bank_pp'] is None


def test_missing_sector_source_never_claims_zero_denominator(tmp_path, observe):
    def change(r):
        ctx=r['context']
        ctx['industry_map'][CODES[0]]='农业'
        evidence=ctx['sources']['industry']
        evidence['mapping'][CODES[0]]={'sw_l2':'农业'}
        evidence['sha256']=digest(evidence['mapping'])
    batch(tmp_path,mutate=change)
    p=next(t for t in observe()['points'] if t['slot']=='14:02')
    assert p['bank']['expected'] is None and p['bank']['missing_industries']==[service.BANK[0]]
    assert p['broker_minus_bank_pp'] is None


def test_bad_receipt_duplicate_or_changed_frozen_groups(tmp_path, observe):
    folder=batch(tmp_path)
    batch(tmp_path,'14:02:10','bb')
    report=observe()
    assert report['observed_slots']==0 and report['points'][18]['gap']=='重复批次'
    (folder/'report.json').write_text('tamper')
    report=observe()
    assert report['status']=='partial' and any('哈希' in g for g in report['gaps'])
    batch(tmp_path,'14:12:03','cc',mutate=lambda r:r['context'].update(frozen_at=DAY+'T14:09:00+08:00'))
    assert observe()['observed_slots']==0


def test_refuse_future_freeze(tmp_path, observe):
    batch(tmp_path,mutate=lambda r:r['context'].update(frozen_at=DAY+'T14:20:00+08:00'))
    assert observe()['status']=='source_failed'


def test_preserves_previous_valid_batch_when_sources_go_missing(tmp_path, observe):
    folder=batch(tmp_path)
    full=observe()
    target=tmp_path/service.FOLDER
    assert service.save(target,full)=='saved'
    day_file=target/(DAY+'.json')
    signed=day_file.read_bytes()
    html_path=day_file.with_suffix('.html')
    preview=html_path.read_text()
    assert f'<title>{DAY} 银行与证券盘中强弱</title>' in preview
    assert f'href="{DAY}.json"' in preview and f'data-items="24" open' in preview
    assert preview.count('data-bank-broker-intraday=')==1 and '14:02 / 14:02:06' in preview
    html_path.unlink()
    (folder/'report.json').write_text('broken')
    assert service.save(target,observe())=='fallback_preserved'
    assert signed==day_file.read_bytes()
    old=service.load_review(tmp_path,DAY)
    assert old['latest_attempt']=='fallback_preserved' and service.render(old,DAY)[1]
    assert html_path.read_text()==service.standalone(old)
    service.save(target,full)
    assert html_path.read_text()==preview
    assert 'latest_attempt' not in service.load_review(tmp_path,DAY)
    inconsistent=json.loads(day_file.read_bytes())
    inconsistent.pop('sha256')
    inconsistent['points'][18]['bank']['up']=99
    day_file.write_text(json.dumps(dict(inconsistent,sha256=digest(inconsistent))))
    assert service.load_review(tmp_path,DAY)['status']=='source_failed'
    day_file.write_bytes(signed)
    day_file.write_text(day_file.read_text().replace('14:02:06', '14:03:00'))
    assert service.load_review(tmp_path,DAY)['status']=='source_failed'


def test_official_assembler_owns_bank_card_in_section_two(tmp_path, observe):
    from tests.test_daily_review_html_report import _load_assembler, _write_chunks
    batch(tmp_path)
    result=observe()
    module=_load_assembler()
    from analyzers.sector_increment import failed
    day=DAY
    sector=failed(day,'本项输入缺失，独立日内对照仍可用')
    sector['bank_broker_intraday']=result
    _write_chunks(tmp_path,day)
    html=module.render_report(tmp_path,day,sector_increment=sector,
                              capacity_trend_db_path=tmp_path/'empty.db', daily_dir=tmp_path/'daily',
                              emotion_history_dir=tmp_path/'emotion', fx_history_dir=tmp_path/'fx')
    assert html.count('data-bank-broker-intraday=')==1
    assert html.index('id="s2"') < html.index('data-bank-broker-intraday=') < html.index('id="s3"')
    assert '14:02 / 14:02:06' in html and '+5.00' in html
    module.validate_report(html,sector_increment=sector)
    with pytest.raises(module.ReportValidationError,match='sector_increment_source_mismatch'):
        module.validate_report(html.replace('+5.00</td>','+99.00</td>'),sector_increment=sector)


def test_skips_unclosed_day_and_holiday(monkeypatch,tmp_path):
    monkeypatch.setattr(service,'calendar',lambda *a:{'is_open':False})
    for at in (datetime(2026,11,27,14,tzinfo=TZ),datetime(2026,11,28,tzinfo=TZ)):
        assert service.build(None,tmp_path,DAY,'USER',at)['status']=='skipped'
