from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from services import theme_catalysts as svc


DAY = '2026-11-27'
TERM = {'requests': [{'requested': 'PCB'}]}
URL = 'https://www.miit.gov.cn/x/article.html'


def dt(clock):
    return svc.stamp(DAY + 'T' + clock + '+08:00')


def item(content='港股PCB概念走强', ident='item1', time='10:01:00', **extra):
    return {'id': ident, 'time': DAY + ' ' + time, 'data': dict(content=content, **extra)}


def row(raw=None):
    return svc.match_item(raw or item(), svc.patterns(TERM), DAY)


def batch(kind, at='10:02:00', status='complete'):
    return dict(kind=kind, scheduled_at=dt(at).isoformat(), pages=1, source_status=status)


def test_literal_terms_sources_and_media_observations():
    assert row()['kind'] == 'market_observation'
    assert row(item('XPCB2')) is None
    assert row(item('港股ＰＣＢ概念上涨'))['matched_terms'] == ['PCB']
    official = row(item('PCB产业会议举行', source='工信部', source_link=URL))
    assert official['original_org'] == '工业和信息化部' and official['original_url'] == URL
    assert official['original_published_at'] is None
    reported = row(item(source='工信微报', source_link='https://mp.weixin.qq.com/s?x'))
    assert reported['credited_source'] == '工信微报' and reported['credited_url']
    assert reported['original_org'] is None
    for url in ('https://www.miit.gov.cn.attacker.invalid/x', 'https://user@www.miit.gov.cn/x',
                'http://www.miit.gov.cn/x', 'https://www.miit.gov.cn:8123/x'):
        assert svc.safe_origin('工信部', url) is None


def test_retry_archive_first_and_out_of_order_actual_receipts(tmp_path):
    path = tmp_path / (DAY + '.json')
    def put(t, typ, value=None, who='USER'):
        return svc.save(path, DAY, TERM, batch(typ, t), [(value or row(), dt(t).isoformat())], who)
    put('20:00:25', 'archive')
    archive_only = json.loads(path.read_bytes())['events']['item1']
    assert archive_only['first_seen_at'] is None
    assert archive_only['known_by_at'] == dt('20:00:25').isoformat()
    put('10:12:19', 'live')
    put('10:02:04', 'live')
    put('10:02:04', 'live', who='launchd')
    updated = row(item('PCB 盘中快讯修正'))
    report = put('10:22:12', 'live', updated)
    event = report['events']['item1']
    assert len(report['receipts']) == 4 and len(event['versions']) == 2
    assert event['first_seen_at'] == event['known_by_at'] == dt('10:02:04').isoformat()
    assert event['versions'][updated['raw_sha256']]['first_seen_at'] == dt('10:22:12').isoformat()
    assert svc.digest(updated['raw_item']) == updated['raw_sha256']
    report = svc.save(path, DAY, None, batch('live', '10:32:01', 'source_failed'), [], 'USER')
    assert report['events']['item1'] == event
    with pytest.raises(ValueError, match='选题'):
        svc.save(path, DAY, {'requests': []}, batch('archive'), [], 'USER')
    path.write_text(path.read_text().replace('港股', '美股'))
    with pytest.raises(ValueError, match='哈希'):
        put('10:32:03', 'live')


def test_live_observation_is_per_valid_original_version(monkeypatch):
    valid = item()
    malformed = item('')
    older = item('旧边界', 'old', time='09:40:00')
    pages = iter([[malformed, item('其他消息', 'other', time='10:00:00')], [valid, older]])
    def fetch(session, anchor, sleep_fn, **kwargs):
        assert kwargs == dict(max_retries=0, timeout=(2, 3))
        return next(pages)
    monkeypatch.setattr(svc.jin10, '_fetch_page', fetch)
    clocks = iter([dt('10:02:03'), dt('10:02:04')])
    receipt, rows = svc.live_batch(dt('10:02:02'), dt('10:02:00'), svc.patterns(TERM), None, lambda: next(clocks))
    assert receipt['pages'] == 2 and len(rows) == 1
    assert rows[0][1] == dt('10:02:04').isoformat()
    assert rows[0][0]['media_published_at'] == dt('10:01:00').isoformat()
    assert receipt['dropped_count'] == 1


def test_launch_guard_and_lunch_lookback(monkeypatch):
    assert svc.scheduled_slot(dt('10:05:00'), dt('10:02:00').isoformat()) is None
    assert svc.scheduled_slot(dt('10:01:59'), dt('10:02:00').isoformat()) is None
    assert svc.scheduled_slot(dt('11:32:02'), None) is None
    assert svc.scheduled_slot(dt('09:34:59'), None) == dt('09:32:00')
    assert len(svc.SLOTS) == 24
    windows = []
    def collect(start, end, **kwargs):
        windows.append((start, end, kwargs['max_pages']))
        return svc.jin10.CollectResult(status=svc.jin10.STATUS_TRUNCATED)
    monkeypatch.setattr(svc.jin10, 'collect_window', collect)
    for t in ('09:32:00', '13:02:00', '14:12:00'):
        plan = dt(t)
        receipt, rows = svc.live_batch(plan, plan, {}, None, lambda: plan)
        assert not rows and receipt['source_status'] == 'partial_window_truncated'
    assert [(start.strftime('%H:%M'), pages) for start, _, pages in windows] == [('00:00', 8), ('11:20', 8), ('13:57', 4)]


def archive(root, items, day=DAY):
    folder = root / 'data/runs/macro-flash' / day
    folder.mkdir(parents=True)
    end = DAY + ' 20:00:02'
    raw = dict(schema_version=1, window_start=DAY+' 00:00:00', window_end=end, items=items)
    encoded = json.dumps(raw).encode()
    (folder / 'flash_raw.json').write_bytes(encoded)
    manifest = dict(raw, run_date=day, generated_at=dt('20:00:25').isoformat(),
                    source_status='complete', raw_count=len(items), pages=1, dropped_count=0,
                    files={'flash_raw': dict(path='flash_raw.json', sha256=svc.sha256(encoded).hexdigest())})
    manifest.pop('items')
    (folder / 'manifest.json').write_text(json.dumps(manifest))
    return folder


def test_historical_receipt_hash_duplicates_and_event_dates(tmp_path):
    now = dt('20:05:00')
    folder = archive(tmp_path, [item()])
    receipt, rows = svc.archive_batch(tmp_path, DAY, svc.patterns(TERM), now)
    assert receipt['archive_generated_at'] == rows[0][1] == dt('20:00:25').isoformat()
    assert rows[0][0]['media_published_at'] == dt('10:01:00').isoformat()
    with pytest.raises(ValueError, match='窗口'):
        svc.archive_batch(tmp_path, DAY, {}, dt('12:00:00'))
    (folder / 'flash_raw.json').write_text('{}')
    with pytest.raises((KeyError, ValueError)):
        svc.archive_batch(tmp_path, DAY, {}, now)
    archive(tmp_path / 'other', [item(), item()])
    with pytest.raises(ValueError, match='重复'):
        svc.archive_batch(tmp_path / 'other', DAY, {}, now)


def test_authoritative_page_metadata_not_media_clock():
    class Response:
        headers = {'Content-Type': 'text/html'}
        status_code = 200
        html = '<meta name="PubDate" content="2026-11-27 09:11:30">'
        def iter_content(self, n):
            yield self.html.encode()
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
    class Session:
        def get(self, url, **kwargs):
            assert url == URL and kwargs['allow_redirects'] is False
            return Response()
    version = row(item(source='工信部', source_link=URL))
    proof = svc.original_time(version, Session(), lambda: dt('10:02:04'))
    assert proof['published_at'] == dt('09:11:30').isoformat()
    assert proof['captured_at'] == dt('10:02:04').isoformat()
    Response.html = '<meta name="lastmodified" content="2026-11-27 09:33:00">'
    with pytest.raises(ValueError, match='缺失'):
        svc.original_time(version, Session(), lambda: dt('10:02:04'))
    Response.html = '<meta name="PubDate" content="2026-11-27 10:12:00">'
    with pytest.raises(ValueError, match='晚于'):
        svc.original_time(version, Session(), lambda: dt('10:02:04'))


def test_early_calendar_close_and_missing_selection_do_not_fetch(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, 'calendar', lambda conn, d: {'is_open': False})
    assert svc.collect(None, tmp_path, DAY, 'USER', archive=True, clock=lambda: dt('20:05:00'))['status'] == 'skipped'
    assert not list(tmp_path.iterdir())
    monkeypatch.setattr(svc, 'calendar', lambda conn, d: {'is_open': True, 'previous':'2026-11-26'})
    result = svc.collect(None, tmp_path, DAY, 'USER', archive=True, clock=lambda: dt('20:05:00'))
    assert result['status'] == 'source_failed' and result['matched_in_batch'] is None
    assert Path(result['path']).is_file()
