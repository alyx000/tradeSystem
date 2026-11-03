"""隔离行情、数据库与推送；验证证据口径与原策略隔离。"""
from copy import deepcopy
from datetime import date, timedelta
import json
import sqlite3

import pytest

from providers.base import DataResult
from services.trend_leader import limit_pullback as L

DAY = "2026-10-30"
CODE = "600001"


def fixture():
    end = date.fromisoformat(DAY)
    dates = [(end-timedelta(days=49-i)).isoformat() for i in range(50)]
    bars = [dict(trade_date=d, ts_code=CODE+".SH", open=10., high=10.1, low=9.9,
                 close=10., pre_close=10., vol=1000., amount=10000., pct_chg=0.) for d in dates]
    bars[43].update(open=10., high=11., low=10., close=11., pct_chg=10.)
    bars[45].update(low=9., open=9.5, close=9.5)
    bars[-1].update(open=9.5, high=11.5, low=9.4, close=11.5, pct_chg=15.)
    factors = [dict(trade_date=d, adj_factor=1.) for d in dates]
    section = lambda d, present: dict(date=d, count=int(present), _source="tushare:limit_list_d",
                                    stocks=[dict(code=CODE+".SH", name="测试股票")] if present else [])
    evidence = dict(open_days=dates[-20:], current_limit=section(DAY, True),
                    history={d:section(d, d==dates[43]) for d in dates[-20:-1]},
                    candidates=[dict(code=CODE, name="测试股票")],
                    quotes={CODE:L.capture(bars, factors, CODE, DAY, "tushare:daily", "tushare:adj_factor")})
    return evidence


def test_exact_calculations_and_course_scope():
    p = L.calculate(DAY, fixture()); r = p['rows'][0]
    assert p['status'] == 'complete'
    assert r['retraced_to_pre_limit'] is True and r['regained_anchor_high'] is True
    assert r['between_bars'] == 5
    assert r['drawdown_from_anchor_high_pct'] == pytest.approx((9/11-1)*100)
    assert r['close_vs_anchor_high_pct'] == pytest.approx((11.5/11-1)*100)
    assert r['crossed_ma'] == [5,10,20,30]
    assert r['low_open'] and not r['daily_one_price']
    assert '不合并双板涨幅' in L.NOTE


@pytest.mark.parametrize('mutate', [
    lambda e:e['quotes'][CODE].update(quote_source='unknown'),
    lambda e:e['quotes'][CODE].update(code='000001'),
    lambda e:e['quotes'][CODE]['bars'][-1].update(ts_code='000001.SZ'),
    lambda e:e['quotes'][CODE]['bars'][-1].update(ts_code='600001.SZ'),
    lambda e:e['quotes'][CODE]['factors'][-1].update(ts_code='000001.SZ'),
    lambda e:e['quotes'][CODE]['bars'][-1].update(trade_date='2026-10-31'),
    lambda e:e['quotes'][CODE]['bars'].append(deepcopy(e['quotes'][CODE]['bars'][-1])),
    lambda e:e['quotes'][CODE]['factors'].append(deepcopy(e['quotes'][CODE]['factors'][-1])),
    lambda e:e['quotes'][CODE]['factors'].pop(),
    lambda e:e['quotes'][CODE]['factors'][-1].update(adj_factor=0),
    lambda e:e['quotes'][CODE]['factors'][-1].update(adj_factor=float('inf')),
    lambda e:e['quotes'][CODE]['bars'][-1].update(high=1),
    lambda e:e['quotes'][CODE]['bars'][-1].update(low=float('nan')),
    lambda e:e['quotes'][CODE]['bars'][-1].update(pre_close=True),
    lambda e:e['quotes'][CODE]['bars'][-1].update(pre_close=1),
    lambda e:e['quotes'][CODE]['bars'][-1].update(high=12),
    lambda e:e['quotes'][CODE].update(bars=e['quotes'][CODE]['bars'][-29:]),
    lambda e:e['candidates'][0].update(name='错名'),
])
def test_bad_quote_only_degrades_card(mutate):
    e = fixture(); mutate(e)
    p = L.calculate(DAY,e)
    assert p['status'] == 'partial'
    assert p['rows'][0]['status'] == 'source_failed'
    assert 'retraced_to_pre_limit' not in p['rows'][0]


@pytest.mark.parametrize('mutate', [
    lambda e:e['current_limit'].update(date='2026-10-29'),
    lambda e:e['current_limit'].update(_source='unknown'),
    lambda e:e['current_limit'].update(count=2),
    lambda e:e['candidates'].append(deepcopy(e['candidates'][0])),
    lambda e:e['open_days'].pop(),
])
def test_bad_common_source_fails_closed(mutate):
    e = fixture(); mutate(e)
    assert L.calculate(DAY,e)['status'] == 'source_failed'


def test_missing_history_keeps_positive_anchor_but_never_claims_complete():
    e = fixture(); e['history'].pop(e['open_days'][-2])
    p = L.calculate(DAY,e)
    assert p['status'] == 'partial' and p['rows'][0]['anchor_date']
    assert p['rows'][0]['retraced_to_pre_limit'] is True
    e['history'] = {}
    p = L.calculate(DAY,e)
    assert p['status'] == 'partial' and p['rows'][0]['anchor_date'] is None
    assert 'retraced_to_pre_limit' not in p['rows'][0]


def test_no_intermediate_bar_not_a_pit_and_no_prior_limit_not_missing():
    e = fixture()
    e['history'][e['open_days'][-2]] = {**e['current_limit'], 'date': e['open_days'][-2]}
    p = L.calculate(DAY,e)
    assert p['rows'][0]['status'] == 'partial'
    assert 'retraced_to_pre_limit' not in p['rows'][0]
    for block in e['history'].values(): block.update(count=0, stocks=[])
    p = L.calculate(DAY,e)
    assert p['status'] == 'complete' and p['rows'][0]['anchor_date'] is None


def test_qfq_handles_ex_rights_and_current_preclose():
    e = fixture(); q = e['quotes'][CODE]
    baseline = L.calculate(DAY,e)['rows'][0]
    for b,f in zip(q['bars'][:-1],q['factors'][:-1]):
        for k in ('open','close','high','low'): b[k] *= 2
        f['adj_factor'] = .5
    r = L.calculate(DAY,e)['rows'][0]
    assert r == baseline


def test_missing_known_anchor_bar_does_not_mean_no_prior_limit():
    e=fixture(); q=e['quotes'][CODE]
    d=q['bars'][43]['trade_date']; q['bars'].pop(43); q['factors'].pop(43)
    p=L.calculate(DAY,e); r=p['rows'][0]
    assert p['status']=='partial' and r['status']=='source_failed'
    assert r['anchor_date']==d and '缺少K线' in r['reason']


def test_qfq_underflow_is_contained():
    e=fixture(); factors=e['quotes'][CODE]['factors']
    for f in factors: f['adj_factor']=1e-300
    factors[-1]['adj_factor']=1e300
    p=L.calculate(DAY,e)
    assert p['status']=='partial' and p['rows'][0]['status']=='source_failed'
    assert 'retraced_to_pre_limit' not in p['rows'][0]


def test_raw_recomputation_rejects_tampering_and_html_escapes():
    e = fixture(); e['candidates'][0]['name'] = '<script>x</script>'
    e['current_limit']['stocks'][0]['name'] = '<script>x</script>'
    p = L.calculate(DAY,e)
    html, gaps = L.render_html(p,DAY)
    assert '<script>' not in html and '&lt;script&gt;' in html and not gaps
    p['rows'][0]['crossed_ma'] = []
    assert L.validate(p,DAY)['status'] == 'source_failed'


def seed(conn,e):
    conn.execute('CREATE TABLE IF NOT EXISTS trade_calendar(exchange TEXT,date TEXT,is_open INTEGER)')
    conn.execute('CREATE TABLE IF NOT EXISTS daily_market(date TEXT,raw_data TEXT)')
    conn.executemany('INSERT INTO trade_calendar(exchange,date,is_open) VALUES(?,?,?)',
                     [('SSE',d,1) for d in e['open_days']])
    conn.executemany('INSERT INTO daily_market(date,raw_data) VALUES(?,?)',
                     [(d,json.dumps({'date':d,'raw_data':{'limit_up':v}})) for d,v in e['history'].items()])
    conn.commit()


def test_collect_is_read_only_and_calendar_gap_fails_closed():
    e=fixture(); conn=sqlite3.connect(':memory:'); seed(conn,e)
    before=conn.total_changes
    r=DataResult(e['current_limit'],source='tushare:limit_list_d')
    p=L.collect(conn,DAY,r,e['candidates'],e['quotes'])
    assert p['status'] == 'complete' and conn.total_changes == before
    conn.execute('DELETE FROM trade_calendar WHERE date=?',(e['open_days'][3],))
    assert L.collect(conn,DAY,r,e['candidates'],e['quotes'])['status'] == 'source_failed'


@pytest.mark.parametrize('flat',[True,False])
def test_history_envelope_and_legacy_flat_both_supported_and_wrong_date_rejected(flat):
    e=fixture(); conn=sqlite3.connect(':memory:'); seed(conn,e)
    if flat:
        for d,block in e['history'].items():
            conn.execute('UPDATE daily_market SET raw_data=? WHERE date=?',(json.dumps({'limit_up':block}),d))
    r=DataResult(e['current_limit'],source='tushare:limit_list_d')
    p=L.collect(conn,DAY,r,e['candidates'],e['quotes'])
    assert p['status']=='complete' and p['rows'][0]['anchor_date']
    anchor=p['rows'][0]['anchor_date']
    wrong={'date':'1999-01-01','raw_data':{'limit_up':e['history'][anchor]}}
    conn.execute('UPDATE daily_market SET raw_data=? WHERE date=?',(json.dumps(wrong),anchor))
    p=L.collect(conn,DAY,r,e['candidates'],e['quotes'])
    assert p['status']=='partial' and p['rows'][0]['anchor_date'] is None


def test_capture_rejects_future_or_duplicate_before_trimming():
    q=fixture()['quotes'][CODE]
    for bars,factors in ((q['bars']+[q['bars'][0]],q['factors']),
                         (q['bars'],q['factors']+[dict(trade_date='2099-01-01',adj_factor=1.)])):
        assert 'error' in L.capture(bars,factors,CODE,DAY,'tushare:daily','tushare:adj_factor')


@pytest.mark.parametrize('already_active', [False, True])
@pytest.mark.parametrize('second_quote_source', ['tushare:daily', 'unknown-fallback'])
def test_scanner_keeps_rejected_candidate_evidence_without_changing_pool_or_requests(monkeypatch, already_active, second_quote_source):
    from db.schema import init_schema
    from services.trend_leader import scanner,pool
    e=fixture(); conn=sqlite3.connect(':memory:'); conn.row_factory=sqlite3.Row
    init_schema(conn); seed(conn,e)
    if already_active:
        pool.record(conn, code=CODE, name='测试股票', sw_l2='半导体',
                    first_limit_date='2026-10-29', date='2026-10-29', signal_json={})
    class Registry:
        def __init__(self): self.calls=[]
        def call(self,key,*args):
            self.calls.append((key,args))
            values={
                'get_limit_up_list': (e['current_limit'],'tushare:limit_list_d'),
                'get_stock_sw_industry_map': ({CODE+'.SH':dict(name='测试股票',sw_l2='半导体')},'fixture'),
                'get_market_daily_changes': ([], 'fixture'),
                'get_stock_daily_range': (e['quotes'][CODE]['bars'],'tushare:daily'),
                'get_stock_adj_factor_range': (e['quotes'][CODE]['factors'],'tushare:adj_factor'),
            }
            if key not in values:
                return DataResult(None, source='fixture', error='not collected')
            data,source=values[key]
            if key == 'get_stock_daily_range' and sum(k == key for k,args in self.calls) > 1:
                source = second_quote_source
            return DataResult(deepcopy(data),source=source)
    reg=Registry()
    p=scanner.run_daily(conn,reg,DAY,sectors=['半导体'],main_line='l2')
    assert p['candidates'] == 1 and not p['entered']
    if already_active and second_quote_source == 'tushare:daily':
        assert p['limit_pullback']['rows'][0]['retraced_to_pre_limit'] is True
    else:
        assert p['limit_pullback']['rows'][0]['status'] == 'source_failed'
    if not already_active:
        assert not pool.list_pool(conn)
        assert not any(key == 'get_stock_adj_factor_range' for key, args in reg.calls)
    monkeypatch.setattr(L,'collect',lambda *a,**kw: {})
    reg2=Registry(); old=scanner.run_daily(conn,reg2,DAY,sectors=['半导体'],main_line='l2')
    assert reg.calls == reg2.calls
    assert {k:v for k,v in p.items() if k!='limit_pullback'} == {k:v for k,v in old.items() if k!='limit_pullback'}


def test_review_roundtrip_and_bad_card_does_not_remove_old_evidence(tmp_path):
    from services.trend_leader import review_evidence as R
    from tests.test_trend_review_evidence import payload
    p=payload(); day=p['trade_date']
    # 标准入口封存今日形态；旧模块使用自己的事实日，错日新卡单独失败。
    p['limit_pullback']=L.calculate(DAY,fixture())
    checked=R.validate(p,day)
    assert checked['status'] == 'complete'
    if day!=DAY: assert checked['limit_pullback']['status'] == 'not_computed'
    html,gaps=R.render(checked,day)
    assert '涨停回撤与再涨停证据' in html
    assert '放量回踩与股东户数' in html


@pytest.mark.parametrize('bad',[None,7,'unknown',{},[None]])
def test_bad_gap_structure_only_degrades_new_card(bad,tmp_path):
    from services.trend_leader import review_evidence as R
    from tests.test_trend_review_evidence import payload
    p=payload(); day=p['trade_date']; original=deepcopy(p)
    p['limit_pullback']={**L.missing(day,'fixture'),'gaps':bad}
    checked=R.validate(p,day)
    assert checked['status']==original['status']
    assert checked['observations']==original['observations'] and checked['cards']==original['cards']
    assert checked['limit_pullback']['status']=='source_failed'
    R.write(tmp_path,day,p)
    assert '放量回踩与股东户数' in R.render(p,day)[0]


@pytest.mark.parametrize('value',[float('nan'),float('inf'),object()])
def test_bad_source_serialization_does_not_break_review_write(value,tmp_path):
    from services.trend_leader import review_evidence as R
    e=fixture(); p=L.calculate(DAY,e)
    p['evidence']['quotes'][CODE]['bars'][0]['close']=value
    summary=dict(date=DAY,launch_pullbacks=[],research_cards=[],
                 research_coverage=dict(eligible=0,collected=0,not_collected=[]),limit_pullback=p)
    checked=R.build(summary,'fixture')
    assert checked['status']=='complete' and checked['limit_pullback']['status']=='source_failed'
    (tmp_path/f'{DAY}.md').write_text('fixture'); R.write(tmp_path,DAY,checked)
    assert R.load(tmp_path,DAY)['status']=='complete'


def test_formal_assembler_source_check_and_saved_roundtrip(tmp_path):
    from services.trend_leader import review_evidence as R
    from tests.test_daily_review_html_report import _load_assembler, _write_chunks
    summary=dict(date=DAY,launch_pullbacks=[],research_cards=[],
                 research_coverage=dict(eligible=0,collected=0,not_collected=[]),
                 limit_pullback=L.calculate(DAY,fixture()))
    p=R.build(summary,'fixture')
    (tmp_path/f'{DAY}.md').write_text('fixture')
    R.write(tmp_path,DAY,p)
    assert R.load(tmp_path,DAY)['limit_pullback']['rows'][0]['retraced_to_pre_limit'] is True
    module=_load_assembler(); _write_chunks(tmp_path,DAY)
    html=module.render_report(tmp_path,DAY,trend_review_evidence=p)
    module.validate_report(html,trend_review_evidence=p)
    with pytest.raises(module.ReportValidationError):
        module.validate_report(html.replace('相对锚点最高价最大回撤','伪造的回撤'),trend_review_evidence=p)


def test_empty_sample_keeps_discovery_failure_separate_from_real_empty():
    e=fixture(); e['candidates']=[]
    p=L.calculate(DAY,e)
    assert p['status']=='complete'
    assert '本次主线门内无当日涨停观察对象' in L.render_html(p,DAY)[0]
    e['upstream_gaps']=['sw_map']
    p=L.calculate(DAY,e)
    assert p['status']=='partial'
    assert '尚无可展示的已核验证据' in L.render_html(p,DAY)[0]
