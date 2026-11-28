from datetime import datetime, timedelta
import json
import sqlite3

import pytest

from services import broad_etf_minutes as etf

DAY = '2026-11-27'
AT = '2026-11-27T10:02:00+08:00'


def stamp(hm):
    return datetime.fromisoformat(DAY+'T'+hm+':00+08:00')


@pytest.fixture
def run(monkeypatch, tmp_path):
    monkeypatch.setattr(etf, 'get_readonly_connection', lambda path: sqlite3.connect(':memory:'))
    monkeypatch.setattr(etf, 'calendar', lambda conn, day: {'is_open': True})

    def collect(loader, *, clock=stamp('16:00'), scheduled=None, **kwargs):
        kwargs.setdefault('benchmark_loader', lambda p, c, d: source(p, c, d, etf.ENDS, clock=clock))
        kwargs.setdefault('breadth_loader', lambda *_: {'timeseries':{},'conflicts':{}})
        return etf.collect(DAY, 'USER', loader=loader, reports=tmp_path, scheduled=scheduled,
                           clock=lambda: clock, **kwargs)
    return collect


def source(provider, code, day, ends=(), clock=None, price=2):
    caught = (clock or stamp('16:00')).isoformat()
    bars = {end: dict(source_time=stamp(end).isoformat(), open=price, high=price, low=price,
                      close=price, volume=100, volume_unit='份', amount_yuan=price*100,
                      raw=[day, end, price]) for end in ends}
    return dict(provider=provider, code=code, trade_date=day, requested_at=caught,
                received_at=caught, bars=bars, raw_sha256='a'*64, status='partial' if bars else 'source_failed')


def read(tmp_path, mode='replay'):
    return json.loads((tmp_path/'broad-etf-minutes'/DAY/(mode+'.json')).read_text())


def test_two_distinct_funds_full_grid_idempotence_and_fallback(run, tmp_path):
    calls = []
    def loader(provider, code, day):
        calls.append((provider, code))
        ends = etf.ENDS if code == '510300.SH' or provider=='eastmoney' else etf.ENDS[:2]
        return source(provider, code, day, ends)

    assert run(loader)['observed_count'] == 96
    assert sorted(calls) == [('eastmoney','512100.SH'),('tushare','510300.SH'),('tushare','512100.SH')]
    original = read(tmp_path)
    assert original['status'] == 'complete'
    assert original['funds']['512100.SH']['bars']['09:35']['provider'] == 'tushare'
    assert original['funds']['512100.SH']['bars']['15:00']['provider'] == 'eastmoney'
    assert run(lambda *_: pytest.fail('完整档不能重取'))['status'] == 'complete'
    assert read(tmp_path) == original


def test_conflicting_bar_excluded_and_prior_good_evidence_retained(run, tmp_path):
    def first(provider, code, day):
        ends = ['09:35','09:40'] if provider=='tushare' else ['09:40','09:45']
        return source(provider, code, day, ends, price=2 if provider=='tushare' else 3)
    run(first)
    old = read(tmp_path)
    assert old['observed_count'] == 4
    for code in etf.FUNDS:
        fund = old['funds'][code]
        assert fund['bars']['09:40']['close'] == 2 and fund['conflicts']['09:40'][0]['close']==3
        assert '09:40' in fund['missing_or_conflicted']
    assert run(source)['status'] == 'partial'
    now = read(tmp_path)
    assert now['funds'] == old['funds'] and len(now['attempts']) == 2


@pytest.mark.parametrize('hm,expected', [('09:32',0),('09:42',2),('11:22',22),('13:02',24),('14:52',46)])
def test_live_uses_only_finished_bars(run, tmp_path, hm, expected):
    now = stamp(hm) + timedelta(seconds=1)
    loader = lambda p,c,d: source(p,c,d,etf.ENDS,clock=now)
    result = run(loader, clock=now, scheduled=stamp(hm).isoformat())
    if not expected:
        assert result['status'] == 'skipped' and not (tmp_path/'broad-etf-minutes').exists()
        return
    assert result['observed_count'] == expected*2
    report = read(tmp_path,'live')
    bars = report['funds']['512100.SH']['bars']
    assert max(bars)==etf.ENDS[expected-1] and '15:00' not in bars
    assert not (tmp_path/'broad-etf-minutes'/DAY/'replay.json').exists()


def test_late_source_reply_never_becomes_live(run, tmp_path):
    late = stamp('10:05')
    result = run(lambda p,c,d: source(p,c,d,etf.ENDS,clock=late), clock=stamp('10:02'), scheduled=AT)
    assert result['status'] == 'source_failed'
    assert read(tmp_path, 'live')['observed_count'] == 0
    assert all('超出' in r['gap'] for sources in read(tmp_path,'live')['attempts'][0]['sources'].values() for r in sources)


def test_session_guard_and_dry_read(run, monkeypatch, tmp_path):
    failed = lambda *_: pytest.fail('不该取数')
    assert run(failed, clock=stamp('14:59'))['status'] == 'skipped'
    assert run(failed, clock=stamp('10:05'),scheduled=AT)['status'] == 'skipped'
    monkeypatch.setattr(etf,'calendar', lambda *_: {'is_open':False})
    assert run(failed)['status']=='skipped'
    assert not (tmp_path/'broad-etf-minutes').exists()


def test_archive_corruption_refused(run, tmp_path):
    run(source)
    path=tmp_path/'broad-etf-minutes'/DAY/'replay.json'
    obj=json.loads(path.read_text())
    obj['expected_count']=99
    data=json.dumps(obj)
    path.write_text(data)
    with pytest.raises(ValueError,match='哈希'):
        run(source)
    assert path.read_text()==data


def test_microcap_exact_close_label_and_actual_observation(run, tmp_path):
    refs=tmp_path/'tdx-index-5m'
    refs.mkdir()
    source_rows={end:dict(close='101', source_time=DAY+' '+end, captured_at=caught.isoformat())
                 for end, caught in [('09:35',stamp('09:36')),('09:40',stamp('10:50')),('09:45',stamp('09:46'))]}
    obj=dict(schema='tdx-index-ohlc-5m-v1', source='tdx:index_5min', trade_date=DAY, timezone='Asia/Shanghai',
             bar_time='收盘时刻', indices={'880823.TDX':dict(bars=source_rows, conflicts={'09:45':{'captured_at':stamp('09:55').isoformat()}})})
    (refs/(DAY+'.json')).write_text(json.dumps(dict(obj, sha256=etf.digest(obj))))
    run(lambda p,c,d:source(p,c,d,etf.ENDS,clock=stamp('10:02')),clock=stamp('10:02'),scheduled=AT)
    result=read(tmp_path,'live')
    assert result['funds']['512100.SH']['paired_microcap_ends']==['09:35']
    assert result['funds']['512100.SH']['benchmark_code']=='000852.SH'
    assert result['funds']['510300.SH']['benchmark_code']=='000300.SH'
    assert result['microcap_reference']['code']=='880823.TDX'


def test_benchmark_comparison_uses_exact_bar_and_preserves_failure_and_conflict(run, tmp_path):
    def fallback(p, c, d):
        return source(p,c,d, ['09:35','09:40'] if c=='000852.SH' else ['09:35'], price=3)
    run(lambda p,c,d:source(p,c,d,['09:35','09:40']), benchmark_loader=fallback)
    first=read(tmp_path)
    assert first['funds']['512100.SH']['paired_benchmark_ends']==['09:35','09:40']
    assert first['funds']['510300.SH']['paired_benchmark_ends']==['09:35']
    run(source,benchmark_loader=lambda p,c,d:source(p,c,d,['09:40'],price=4))
    revised=read(tmp_path)
    assert revised['funds']['512100.SH']['paired_benchmark_ends']==['09:35']
    assert revised['benchmark_references']['000852.SH']['bars']['09:40']['close']==3
    assert revised['benchmark_references']['000852.SH']['conflicts']['09:40'][0]['close']==4


def test_breadth_time_difference_and_disputed_batches(run, tmp_path):
    def breadth(*_):
        return dict(timeseries={'09:42':dict(captured_at=stamp('09:42').isoformat(), source_sha256='sha'),
                                '09:52':dict(captured_at=stamp('09:52').isoformat(), source_sha256='sha2')},
                    conflicts=['09:52'])
    result=run(lambda p,c,d:source(p,c,d,['09:40','09:50']), breadth_loader=breadth)
    assert result['market_breadth_slots']==1
    paired=read(tmp_path)['funds']['512100.SH']['nearby_market_breadth']
    assert list(paired)==['09:40'] and paired['09:40']['clock_difference_seconds']==120
    assert paired['09:40']['batch_sha256']=='sha'


def test_secondary_breadth_failure_keeps_fund_bars_and_previous_evidence(run, tmp_path):
    point = dict(captured_at=stamp('09:42').isoformat(), source_sha256='sha', valid=5384)
    run(lambda p,c,d:source(p,c,d,['09:40']), breadth_loader=lambda *_:dict(timeseries={'09:42':point}))
    def broken(*_):
        raise OSError('广度暂不可读')
    run(lambda p,c,d:source(p,c,d,['09:45']), breadth_loader=broken)
    report=read(tmp_path)
    assert report['funds']['512100.SH']['observed_count']==2
    assert report['market_breadth']['timeseries']['09:42']==point
    assert report['market_breadth']['status']=='source_failed'


def test_old_market_point_gains_verified_receipt_hash():
    old=dict(slot='09:42', source_sha256='raw', captured_at=stamp('09:42').isoformat())
    fresh=dict(old,receipt_sha256='receipt')
    result=etf._combine_breadth({'timeseries':{'09:42':old}}, {'timeseries':{'09:42':fresh}})
    assert result['timeseries']['09:42']['receipt_sha256']=='receipt'
    assert not result['conflicts']


def test_earlier_live_clock_leaves_later_archive_untouched(run, tmp_path):
    late=stamp('14:02') + timedelta(seconds=15)
    def market_loader(p,c,d):
        return source(p,c,d,etf.ENDS,clock=late)
    def breadth(_,day,until):
        return dict(through=until.isoformat(), timeseries={'14:02':dict(source_sha256='sha', valid=1,
                    captured_at=stamp('14:02').isoformat())}, conflicts={})
    run(market_loader,clock=late,scheduled=stamp('14:02').isoformat(),breadth_loader=breadth)
    path=tmp_path/'broad-etf-minutes'/DAY/'live.json'
    content=path.read_bytes()
    fail=lambda *_:pytest.fail('更早调用不应取数')
    for hm in ['10:02','14:02']:
        result=run(fail, clock=stamp(hm), scheduled=stamp(hm).isoformat(),
                   benchmark_loader=fail, breadth_loader=fail)
        assert result['status']=='skipped' and result['through']=='14:00'
        assert path.read_bytes()==content
    assert json.loads(content)['market_breadth']['through']==late.isoformat()
