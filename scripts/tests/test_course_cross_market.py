from datetime import datetime, timedelta
import json

import pytest
import requests

from services import course_cross_market as m


def slot(day='2026-11-27', hm='10:02'):
    return datetime.fromisoformat(day + 'T' + hm + ':00+08:00')


def packet(key, spec, moment, prices=None):
    prices = prices if prices is not None else {-11: 100, -10: 100, -1: 102, 0: 102}
    if key == 'a50':
        rows = [(moment + timedelta(minutes=i)).strftime('%Y-%m-%d %H:%M') + f',0,{v},0,0,0,0,0'
                for i, v in prices.items()]
        return {'rc': 0, 'data': {'name': 'A50期指当月连续', 'market': 104, 'code': 'CN00Y', 'trends': rows}}
    meta = {'symbol': spec['symbol'], 'exchangeName': 'CME' if key == 'nq' else 'HKG',
            'instrumentType': 'FUTURE' if key == 'nq' else 'INDEX', 'dataGranularity': '1m',
            'exchangeTimezoneName': 'America/New_York' if key == 'nq' else 'Asia/Hong_Kong'}
    return {'chart': {'error': None, 'result': [{'meta': meta, 'timestamp': [
        int((moment + timedelta(minutes=i-1)).timestamp()) for i in prices],
        'indicators': {'quote': [{'close': list(prices.values())}]}}]}}


def source(key, spec, moment):
    return packet(key, spec, moment), 'f' * 64


@pytest.fixture
def market_db(monkeypatch):
    class Connection:
        def close(self):
            pass
    monkeypatch.setattr(m, 'get_readonly_connection', lambda _: Connection())
    monkeypatch.setattr(m, 'calendar', lambda _, day: {'is_open': slot(day).weekday() < 5})


def test_us_roll_observes_cme_session_and_table():
    assert m.instrument('nq', slot())['symbol'] == 'NQZ26.CME'
    assert m.instrument('nq', slot('2026-12-14'))['symbol'] == 'NQH27.CME'
    assert m.instrument('nq', slot('2026-06-12'))['expiration_date'] == '2026-06-18'
    with pytest.raises(ValueError):
        m.instrument('nq', slot('2029-01-02'))


def test_each_identity_required_in_provider_response():
    for key in m.KEYS:
        spec = m.instrument(key, slot())
        data = packet(key, spec, slot())
        if key == 'a50':
            data['data']['market'] = 1
        else:
            data['chart']['result'][0]['meta']['symbol'] = '3033.HK' if key == 'hstech' else '^IXIC'
        with pytest.raises(ValueError):
            m.decode(key, spec, data)


def test_minute_cutoff_no_future_or_stale_change():
    now = slot()
    def partial(key, spec, moment):
        return packet(key, spec, moment, {-10:100, -1:101, 1:103}), 'f'*64
    r = m.fetch_one('hstech', now, 'replay', partial, lambda: now + m.MIN, None)
    assert r['current'] is None and r['pct_change_10m'] is None
    assert r['latest_source_minute']['price'] == 101 and r['latest_source_minute']['seconds_to_slot'] == 60
    assert r['observed_during_live_window'] is False
    def malformed(key, spec, moment):
        d = packet(key,spec,moment)
        d['chart']['result'][0]['timestamp'][-1] = d['chart']['result'][0]['timestamp'][1]
        return d, 'f'*64
    assert m.fetch_one('hstech', now, 'replay', malformed, lambda: now + m.MIN, None)['status'] == 'source_failed'


def test_a50_label_evidence_partial_when_prices_differ():
    now = slot()
    def partial(key, spec, moment):
        return packet(key, spec, moment, {-11:100, -10:100, -1:101, 0:102, 1:110}), 'f'*64
    r = m.fetch_one('a50', now, 'replay', partial, lambda: now + m.MIN, None)
    assert r['current'] is None and r['pct_change_10m'] is None
    assert r['baseline']['price'] == 100
    assert [p['close'] for p in r['label_evidence']['current']] == [101,102]


def test_a50_wait_uses_only_its_thread_and_late_results_rejected():
    now = slot()
    times = [now]
    waits = []
    def wait(s):
        waits.append(s)
        times[0] = now + timedelta(seconds=s)
    r = m.fetch_one('a50', now, 'live', source, lambda: times[0], wait)
    assert waits == [60] and r['synchronous'] and r['observed_during_live_window']
    def late(*args):
        times[0] = now + 3 * m.MIN
        return source(*args)
    r = m.fetch_one('nq', now, 'live', late, lambda: times[0], wait)
    assert r['status'] == 'source_failed' and r['current'] is None


def test_calendar_slot_and_independent_archive_modes(tmp_path, market_db):
    moment = slot()
    def collect(**opts):
        return m.collect(moment, 'test', root=tmp_path, clock=lambda: moment+2*m.MIN, loader=source, **opts)
    assert collect(dry_run=True)['completed'] == 3 and not list(tmp_path.iterdir())
    first = collect()
    replay = collect(replay=True)
    assert first['path'] != replay['path']
    assert replay['completed'] == 3 and not replay['items']['a50']['observed_during_live_window']
    assert first['items']['nq']['pct_change_10m'] == 2
    assert collect()['attempt_count'] == 1
    future = slot('2026-12-14')
    assert m.collect(future, 'test', root=tmp_path, clock=lambda: moment, replay=True)['status'] == 'skipped'
    assert m.collect(moment,'test',root=tmp_path,clock=lambda:moment+3*m.MIN)['status'] == 'skipped'
    sat = slot('2026-11-28')
    assert m.collect(sat,'test',root=tmp_path,clock=lambda:sat,replay=True)['status'] == 'skipped'
    with pytest.raises(ValueError):
        m.collect(slot(hm='11:32'),'test',root=tmp_path,clock=lambda:moment)
    with pytest.raises(ValueError):
        m.collect(datetime(2026,11,27,10,2),'test',root=tmp_path,clock=lambda:moment)


def test_retry_preserves_later_quote_and_completed_other_keys(tmp_path, market_db):
    moment = slot()
    def loader1(key,spec,at):
        if key == 'nq':
            return packet(key,spec,at,{-10:100,-2:101}), 'a'*64
        return source(key,spec,at)
    def call(loader):
        return m.collect(moment,'who',replay=True,root=tmp_path,clock=lambda: moment+2*m.MIN,loader=loader)
    first = call(loader1)
    def older(key,spec,at):
        assert key == 'nq'
        return packet(key,spec,at,{-10:100,-3:121}), 'b'*64
    second = call(older)
    assert second['items']['nq']['latest_source_minute'] == first['items']['nq']['latest_source_minute']
    def conflicting(key,spec,at):
        return packet(key,spec,at,{-10:100,-2:109}), 'c'*64
    third = call(conflicting)
    assert third['items']['nq']['latest_attempt']['conflict'] is True
    assert third['items']['a50'] == first['items']['a50']
    obj = json.loads(open(third['path']).read())
    assert m.digest({k:v for k,v in obj.items() if k != 'sha256'}) == obj['sha256']


def test_http_errors_redacted_and_per_instrument(tmp_path, market_db):
    def loader(key,spec,at):
        if key == 'nq':
            raise requests.exceptions.ProxyError('https://secret:key@host')
        return source(key,spec,at)
    r = m.collect(slot(),'test',replay=True,root=tmp_path,loader=loader,clock=lambda:slot()+2*m.MIN)
    assert r['completed'] == 2 and r['status'] == 'partial'
    assert 'secret' not in str(r) and '代理连接失败' in str(r)


def test_partial_labels_and_source_window_survive_retries(tmp_path, market_db):
    at = slot()
    def call(prices):
        def loader(key,spec,moment):
            if prices is None:
                raise requests.ConnectionError()
            return packet(key,spec,moment,prices), 'a'*64
        return m.collect(at,'test',replay=True,root=tmp_path,loader=loader,clock=lambda:at+2*m.MIN)
    first = call({0:102})
    assert first['items']['a50']['label_evidence']['current'][0]['close'] == 102
    assert call(None)['items']['a50']['label_evidence'] == first['items']['a50']['label_evidence']
    x = call({-12:99,-2:101})
    assert x['items']['a50']['latest_source_10m']['start_price'] == 99
    y = call({-2:101})
    assert y['items']['a50']['latest_source_10m'] == x['items']['a50']['latest_source_10m']


def test_batch_replay_only_three_network_requests_idempotent_and_guards(tmp_path, market_db):
    first = slot(hm='09:32')
    end = slot(hm='14:52')
    calls = []
    def transport(key,url,params):
        calls.append((key,url,params))
        prices = {}
        for offset in range(321):
            if (first + offset*m.MIN).hour*60+(first + offset*m.MIN).minute in m.SLOTS:
                for i in (-11,-10,-1,0):
                    prices[i+offset] = 100
        return packet(key,m.instrument(key,first),first,prices), 'f'*64
    def day(**opts):
        return m.replay_day('2026-11-27','test',root=tmp_path,clock=lambda:slot(hm='20:00'),
                            transport=transport,**opts)
    result = day(dry_run=True)
    assert len(calls) == 3 and result['instruments'] == 72 and result['completed'] == 72
    assert set(key for key,_,_ in calls) == set(m.KEYS)
    assert not list(tmp_path.iterdir())
    yahoo_params = next(p for key, url, p in calls if key == 'nq')
    assert yahoo_params['period1'] == int((first-90*m.MIN).timestamp()) and yahoo_params['period2'] == int(end.timestamp())
    result = day()
    assert result['slots'] == 24 and result['completed'] == 72
    early_slot = json.loads((tmp_path/'2026-11-27/replay/0932.json').read_text())
    late_slot = json.loads((tmp_path/'2026-11-27/replay/1452.json').read_text())
    for key in m.KEYS:
        assert early_slot['items'][key]['request'] == late_slot['items'][key]['request']
        assert early_slot['items'][key]['received_at'] == late_slot['items'][key]['received_at']
        assert not early_slot['items'][key]['observed_during_live_window']
    calls.clear()
    assert day()['sha256'] == result['sha256'] and calls == []
    for d,now in [('2026-11-27',end),('2026-11-28',slot('2026-11-28','20:00'))]:
        assert m.replay_day(d,'test',root=tmp_path,clock=lambda:now,transport=transport)['status'] == 'skipped'
    assert calls == []


def test_batch_failure_keeps_existing_slot_and_retry_shares_receipts(tmp_path, market_db):
    first = m.collect(slot(),'test',replay=True,root=tmp_path,clock=lambda:slot()+2*m.MIN,loader=source)
    def fail(*args):
        raise requests.exceptions.ProxyError('private-address')
    r = m.replay_day('2026-11-27','test',root=tmp_path,clock=lambda:slot(hm='20:00'),transport=fail)
    assert r['completed'] == 3 and r['status'] == 'partial' and r['request_count'] == 3
    assert 'private-address' not in str(r)
    current = json.loads((tmp_path/'2026-11-27/replay/1002.json').read_text())
    assert current['sha256'] == first['sha256']
