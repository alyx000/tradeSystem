"""A股盘中槽位对应的外盘原始分钟证据。"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo
from time import sleep

import requests

from db.connection import get_readonly_connection
from services.board_break_intraday.data import TZ, calendar, digest, number
from services.board_break_intraday.store import lock
from services.volume_record.service import atomic_write
from utils.network_env import use_http_proxy_for_data_collection

ROOT = Path(__file__).resolve().parents[2] / 'data/reports/course-cross-market'
KEYS = ('nq', 'a50', 'hstech')
MIN = timedelta(minutes=1)
SLOTS = set(range(572, 683, 10)) | set(range(782, 893, 10))
ROLL_SOURCE = 'https://www.cmegroup.com/trading/equity-index/rolldates.html'
# US Indexes: (customary roll date, expiration day) by H/M/U/Z, from the CME table.
ROLLS = {
    2025: ((17, 21), (16, 20), (15, 19), (15, 19)),
    2026: ((16, 20), (15, 18), (14, 18), (14, 18)),
    2027: ((15, 19), (14, 18), (13, 17), (13, 17)),
    2028: ((13, 17), (12, 16), (11, 15), (11, 15)),
}
EM = 'https://push2his.eastmoney.com/api/qt/stock/trends2/get'
YAHOO = 'https://query1.finance.yahoo.com/v8/finance/chart/'


def instrument(key, slot):
    if key == 'a50':
        return dict(name='富时中国A50指数期货', symbol='104.CN00Y', exchange='SGX',
                    kind='当月连续，来源未披露对应合约月份', timezone='Asia/Singapore',
                    provider='eastmoney:trends2', unit='指数点')
    if key == 'hstech':
        return dict(name='恒生科技指数', symbol='HSTECH.HK', exchange='HKSE',
                    kind='现货价格指数', timezone='Asia/Hong_Kong',
                    provider='yahoo:chart:1m', unit='指数点')
    central = slot.astimezone(ZoneInfo('America/Chicago'))
    # Globex夜盘在17:00 CT起归入下一交易日。计算挂牌月只用于当期亚洲工作日槽。
    business_date = (central + timedelta(hours=7)).date()
    if business_date < date(2025, 1, 1):
        raise ValueError('缺少本期CME官方换月表')
    for year, quarters in ROLLS.items():
        for month, letter, (roll_day, expiry) in zip((3, 6, 9, 12), 'HMUZ', quarters):
            roll = date(year, month, roll_day)
            if business_date < roll:
                return dict(name='E-mini Nasdaq-100期货', symbol=f'NQ{letter}{year % 100:02d}.CME',
                            exchange='CME', kind='按CME惯例换月表选取的季度合约',
                            timezone='America/Chicago', provider='yahoo:chart:1m', unit='指数点',
                            cme_roll_date=roll.isoformat(), expiration_date=date(year, month, expiry).isoformat(),
                            cme_session_date_for_contract=business_date.isoformat(), roll_source=ROLL_SOURCE)
    raise ValueError('缺少本期CME官方换月及到期日表')


def get_query(key, spec, slot, first=None):
    if key == 'a50':
        return EM, dict(secid=spec['symbol'], ndays=5, iscr=0, iscca=0,
                        fields1=','.join(f'f{i}' for i in range(1, 14)),
                        fields2='f51,f52,f53,f54,f55,f56,f57,f58')
    return YAHOO + spec['symbol'], dict(period1=int(((first or slot) - 90 * MIN).timestamp()),
                                        period2=int(slot.timestamp()), interval='1m', includePrePost='true')


def fetch_url(key, url, params):
    with requests.Session() as session:
        if key == 'a50':
            session.trust_env = use_http_proxy_for_data_collection()
        response = session.get(url, params=params, timeout=(3, 6),
                               headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json',
                                        'Referer': 'https://quote.eastmoney.com/' if key == 'a50' else 'https://finance.yahoo.com/'})
        response.raise_for_status()
        return response.json(), hashlib.sha256(response.content).hexdigest()


def fetch(key, spec, slot):
    return fetch_url(key, *get_query(key, spec, slot))


def http_failure(exc):
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        status = exc.response.status_code
        return f'HTTP {status}，授权受限' if status in (401,403) else f'HTTP {status}'
    if isinstance(exc, requests.exceptions.ProxyError):
        return '代理连接失败'
    if isinstance(exc, requests.Timeout):
        return '来源请求超时'
    if isinstance(exc, requests.ConnectionError):
        if 'NameResolutionError' in repr(exc) or 'nodename nor servname' in repr(exc):
            return '来源域名解析失败'
        return '来源网络连接失败'
    return '响应内容无法解析'


def valid_points(items):
    points = {}
    for time, close, raw in items:
        if close is None or close == '-':
            continue
        point = dict(close=number(close, positive=True), raw_label=raw)
        if time in points and point != points[time]:
            raise ValueError('同分钟返回冲突行情')
        points[time] = point
    return points


def decode(key, spec, raw):
    if key == 'a50':
        d = raw.get('data')
        if (raw.get('rc') != 0 or not d or str(d.get('market')) != '104' or d.get('code') != 'CN00Y'
                or d.get('name') != 'A50期指当月连续'):
            raise ValueError('东财未返回已核验的新交所A50当月连续代码、市场与名称')
        values = []
        for row in d['trends']:
            label, _, close, *_ = row.split(',')
            t = datetime.strptime(label, '%Y-%m-%d %H:%M').replace(tzinfo=ZoneInfo(spec['timezone']))
            values.append((t, close, label))
        return valid_points(values), dict(symbol=d['code'], exchange_market=d['market'], name=d['name'],
                                         time_label='东财分时分钟标签，起止边界未披露', timezone=spec['timezone'],
                                         market_status_code=d.get('status'), vendor_delay_minutes=None)
    chart = raw['chart']
    if chart['error'] or len(chart.get('result') or []) != 1:
        raise ValueError(f"Yahoo 未返回唯一行情结果: {chart.get('error')}")
    d = chart['result'][0]
    meta = d['meta']
    kind = 'FUTURE' if key == 'nq' else 'INDEX'
    allowed = {'CME','XCME'} if key == 'nq' else {'HKG','HKSE','XHKG'}
    if (meta.get('symbol') != spec['symbol'] or meta.get('instrumentType') != kind
            or meta.get('exchangeName') not in allowed or meta.get('dataGranularity') != '1m'):
        raise ValueError('Yahoo返回品种代码、类型、市场或分钟周期不符')
    zone = meta.get('exchangeTimezoneName')
    allowed_zones = {'America/Chicago','America/New_York'} if key == 'nq' else {'Asia/Hong_Kong'}
    if zone not in allowed_zones:
        raise ValueError('Yahoo报价时区缺失或不符')
    labels = d.get('timestamp') or []
    closes = d['indicators']['quote'][0].get('close') or []
    if len(labels) != len(closes):
        raise ValueError('Yahoo时间轴与分钟价格长度不同')
    points = []
    for stamp, close in zip(labels, closes):
        if type(stamp) is not int or stamp % 60:
            continue  # 尾端可能附有最新 tick，不能解释为已收盘分钟
        end = datetime.fromtimestamp(stamp, TZ) + MIN
        points.append((end, close, stamp))
    return valid_points(points), dict(symbol=meta['symbol'], exchange_market=meta['exchangeName'],
                                     instrument_type=kind, name=meta.get('longName') or meta.get('shortName'),
                                     time_label='Yahoo 1分钟K线起始epoch，+60秒为收盘边界', timezone=zone,
                                     current_regular_period=(meta.get('currentTradingPeriod') or {}).get('regular'),
                                     vendor_delay_minutes=meta.get('exchangeDataDelayedBy'),
                                     vendor_delay_notice='Yahoo报价页标记延迟，具体延迟以源元数据为准')


def observation(points, at, key):
    p = points.get(at)
    if p is None:
        return None
    if key == 'a50':
        # 分时标签究竟左标或右标未经确认。两个可能的收盘值相同时才能确认边界价格。
        prior = points.get(at - MIN)
        if prior is None or prior['close'] != p['close']:
            return None
        return dict(price=p['close'], minute_end=at.isoformat(),
                    time_basis='相邻两个可能的分钟收盘值一致',
                    raw_labels=[prior['raw_label'], p['raw_label']])
    return dict(price=p['close'], minute_end=at.isoformat(),
                time_basis='完成分钟的收盘边界', raw_labels=[p['raw_label']])


def fetch_one(key, slot, mode, loader, clock, wait, shared=None):
    if key == 'a50' and mode == 'live':
        # 只等待东财自身线程。Yahoo两路在槽起点立即发起。
        remaining = (slot + MIN - clock()).total_seconds()
        if remaining > 0:
            wait(remaining)
    start = clock()
    item = dict(key=key, current=None, baseline=None, pct_change_10m=None,
                synchronous=False, market_state='未核验', exchange_trade_date=None, observed_during_live_window=False)
    try:
        spec = instrument(key, slot)
        url, params = get_query(key, spec, slot)
        item.update(instrument=spec, request=dict(url=url, params=params,
                    network='direct' if key == 'a50' and not use_http_proxy_for_data_collection() else 'system_configuration'),
                    requested_at=start.isoformat(), exchange_local_at_slot=slot.astimezone(ZoneInfo(spec['timezone'])).isoformat())
        if mode == 'live' and not slot <= start < slot + 3 * MIN:
            raise ValueError('发起请求时已超出本槽实采窗口')
        if shared is None:
            raw, raw_hash = loader(key, spec, slot)
            received = clock()
            item.update(response_sha256=raw_hash, received_at=received.isoformat())
            points, identity = decode(key, spec, raw)
        else:
            receipt, result = shared
            item.update(receipt)
            if result is None:
                return dict(item, status='source_failed')
            points, identity = result
            received = datetime.fromisoformat(receipt['received_at'])
        target = slot - 10 * MIN
        item.update(source_identity=identity, source_row_count=len(points))
        if mode == 'live' and not slot <= received < slot + 3 * MIN:
            raise ValueError('响应超出本槽三分钟实采窗口')
        item['observed_during_live_window'] = mode == 'live'
        for field, moment in [('current', slot), ('baseline', target)]:
            # 东财原始分时标签未证明左右边界；回看不得借用尚未结束的可能区间。
            if key != 'a50' or received >= moment + MIN:
                item[field] = observation(points, moment, key)
                if key == 'a50':
                    item.setdefault('label_evidence', {})[field] = [points[t] for t in (moment - MIN, moment) if t in points]
        usable = {t: p for t, p in points.items() if t <= slot - (MIN if key == 'a50' else timedelta(0))
                  and t >= slot - 90 * MIN}
        if usable:
            last = max(usable)
            p = usable[last]
            item['latest_source_minute'] = dict(price=p['close'], raw_label=p['raw_label'],
                source_label_or_bar_end=last.astimezone(TZ).isoformat(), seconds_to_slot=int((slot-last).total_seconds()),
                time_basis=identity['time_label'])
            # 延迟端点也保留其自身向前10分钟的观测，与目标窗覆盖分开。
            earlier = points.get(last - 10 * MIN)
            if earlier:
                item['latest_source_10m'] = dict(start_raw_label=earlier['raw_label'], end_raw_label=p['raw_label'],
                                                  start_price=earlier['close'], end_price=p['close'],
                                                  pct_change=round((p['close']/earlier['close'] - 1)*100, 6),
                                                  time_basis=identity['time_label'])
        period = identity.get('current_regular_period')
        if (isinstance(period, dict) and type(period.get('start')) is int and type(period.get('end')) is int
                and period['start'] <= slot.timestamp() < period['end']):
            item['source_session_at_slot'] = '目标落入来源给出的常规时段；临时停市未核验'
        else:
            item['source_session_at_slot'] = '来源未提供覆盖该时刻的常规时段'
        item['exchange_trade_date'] = None
        if item['current']:
            item['exchange_trade_date'] = spec.get('cme_session_date_for_contract', slot.date().isoformat())
            item['market_state'] = '目标分钟有来源行情，交易所开休市状态未独立核验'
        elif usable:
            item['market_state'] = '存在来源分钟，目标时点或标签边界未核验'
        if item['current'] and item['baseline']:
            item.update(status='complete', synchronous=True,
                        pct_change_10m=round((item['current']['price']/item['baseline']['price']-1)*100, 6))
        else:
            item.update(status='partial', gap='缺目标或严格前10分钟报价，或分钟标签边界未核验')
    except (requests.RequestException, KeyError, IndexError, TypeError, OSError, ValueError) as exc:
        gap = http_failure(exc) if isinstance(exc, requests.RequestException) else str(exc)
        item.update(status='source_failed', gap=f'{type(exc).__name__}: {gap}')
    item.setdefault('received_at', clock().isoformat())
    return item


def open_day(day, db_path):
    conn = get_readonly_connection(db_path)
    try:
        return calendar(conn, day)['is_open']
    finally:
        conn.close()


def archived(path, day, slot, mode):
    if not path.exists():
        return {}
    previous = json.loads(path.read_text())
    checksum = previous.pop('sha256')
    identity = (previous.get('schema'), previous.get('date'), previous.get('slot'), previous.get('mode'))
    if checksum != digest(previous) or identity != ('course-cross-market-v1', day, slot.isoformat(), mode):
        raise ValueError('旧槽位内容或校验值不符，保留原件')
    return dict(previous, sha256=checksum)


def has_evidence(item):
    return (any(item.get(f) for f in ('current', 'baseline', 'latest_source_minute', 'latest_source_10m'))
            or any(item.get('label_evidence', {}).values()))


def merge_evidence(old, new):
    if not has_evidence(old):
        return new
    receipt = {k:new[k] for k in ('status','gap','request','requested_at','received_at','response_sha256') if k in new}
    old_receipt = {k:old[k] for k in ('request','requested_at','received_at','response_sha256') if k in old}
    if not has_evidence(new):
        return dict(old, latest_attempt=dict(receipt, conflict=False))
    saved = dict(new)
    older_refs = dict(old.get('preserved_receipts', {}))
    def retain(field):
        saved[field] = old[field]
        older_refs.setdefault(field, old_receipt)
    conflict = False
    for field in ('current','baseline'):
        if old.get(field):
            conflict |= bool(new.get(field) and old[field] != new[field])
            retain(field)
    old_last, new_last = (item.get('latest_source_minute') or {} for item in (old,new))
    if old_last:
        before = old_last['source_label_or_bar_end']
        after = new_last.get('source_label_or_bar_end','')
        if before == after and old_last != new_last:
            conflict = True
        if before >= after:
            retain('latest_source_minute')
            if old.get('latest_source_10m'):
                if before == after and new.get('latest_source_10m') and old['latest_source_10m'] != new['latest_source_10m']:
                    conflict = True
                retain('latest_source_10m')
            elif before > after:
                saved.pop('latest_source_10m', None)
    labels = {}
    for field in ('current','baseline'):
        keep = {p['raw_label']:p for p in old.get('label_evidence', {}).get(field, [])}
        for p in new.get('label_evidence', {}).get(field, []):
            if p['raw_label'] in keep and p != keep[p['raw_label']]:
                conflict = True
            keep.setdefault(p['raw_label'], p)
        if keep:
            labels[field] = [keep[t] for t in sorted(keep)]
    if labels:
        saved['label_evidence'] = labels
        if any(old.get('label_evidence', {}).values()):
            older_refs.setdefault('label_evidence', old_receipt)
    if conflict:
        return dict(old, latest_attempt=dict(receipt, conflict=True))
    if old.get('current'):
        saved['exchange_trade_date'] = old.get('exchange_trade_date')
        saved['market_state'] = old['market_state']
    if older_refs:
        saved['preserved_receipts'] = older_refs
        saved['latest_attempt'] = dict(receipt, conflict=False)
    if saved.get('current') and saved.get('baseline'):
        saved.update(status='complete', synchronous=True,
                     pct_change_10m=round((saved['current']['price']/saved['baseline']['price']-1)*100, 6))
        saved.pop('gap',None)
    return saved


def collect(slot, input_by, *, replay=False, dry_run=False, root=ROOT, db_path=None, loader=fetch, clock=None,
            wait=sleep, shared=None):
    if slot.tzinfo is None or not input_by.strip():
        raise ValueError('槽位须带时区，请求者不能为空')
    slot = slot.astimezone(TZ)
    if slot.second or slot.microsecond or slot.hour * 60 + slot.minute not in SLOTS:
        raise ValueError('不属于既有A股盘中10分钟时钟')
    day, hhmm = slot.date().isoformat(), slot.strftime('%H%M')
    mode = 'replay' if replay else 'live'
    root = Path(root)
    folder = root/day/'replay' if replay else root/day
    path = folder/(hhmm+'.json')
    clock = clock or (lambda: datetime.now(TZ))
    started = clock()
    report = dict(schema='course-cross-market-v1', date=day, slot=slot.isoformat(), mode=mode,
                  input_by=input_by, baseline_at=(slot - 10 * MIN).isoformat(), items={})
    if replay and slot > started or not replay and not slot <= started < slot + 3 * MIN:
        return dict(report, status='skipped', gap='不在请求日期/实采窗口或回放目标尚未发生')
    if shared is None and not open_day(day, db_path):
        return dict(report, status='skipped', gap='SSE休市')
    with (nullcontext() if dry_run else lock(root, f'{day}/{mode}/{hhmm}')):
        previous = archived(path, day, slot, mode)
        prior = previous.get('items', {})
        if previous.get('status') == 'complete':
            return dict(previous, path=str(path))
        keys = [k for k in KEYS if prior.get(k, {}).get('status') != 'complete']
        with ThreadPoolExecutor(max_workers=3) as pool:
            jobs = {k: pool.submit(fetch_one, k, slot, mode, loader, clock, wait, shared.get(k, ({'gap':'本日无已核验合约请求'}, None)) if shared is not None else None) for k in keys}
            items = dict(prior)
            for key, job in jobs.items():
                items[key] = merge_evidence(prior.get(key, {}), job.result())
        completed = sum(bool(i.get('synchronous')) for i in items.values())
        has_data = any(has_evidence(i) for i in items.values())
        report.update(items=items, completed=completed, instruments=3, collected_at=clock().isoformat(),
                      status='complete' if completed == 3 else 'partial' if has_data else 'source_failed',
                      attempt_count=previous.get('attempt_count', 0) + 1)
        report['sha256'] = digest(report)
        if not dry_run:
            atomic_write(path, json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2))
    return dict(report, path=str(path) if not dry_run else None)


def request_range(key, spec, slots, transport, clock):
    first, last = min(slots), max(slots)
    url, params = get_query(key, spec, last, first=first)
    receipt = dict(request=dict(url=url, params=params, scope='replay_day',
                        network='direct' if key == 'a50' and not use_http_proxy_for_data_collection() else 'system_configuration'),
                   requested_at=clock().isoformat())
    try:
        raw, raw_hash = transport(key, url, params)
        receipt.update(response_sha256=raw_hash, received_at=clock().isoformat())
        data = decode(key, spec, raw)
        receipt['source_row_count'] = len(data[0])
        return receipt, data
    except (requests.RequestException, KeyError, IndexError, TypeError, OSError, ValueError) as exc:
        gap = http_failure(exc) if isinstance(exc, requests.RequestException) else str(exc)
        receipt.update(gap=f'{type(exc).__name__}: {gap}')
        receipt.setdefault('received_at', clock().isoformat())
        return receipt, None


def replay_day(day, input_by, *, dry_run=False, root=ROOT, db_path=None, clock=None, transport=fetch_url):
    if not input_by.strip():
        raise ValueError('请求者不能为空')
    clock = clock or (lambda: datetime.now(TZ))
    base = datetime.combine(date.fromisoformat(day), datetime.min.time(), tzinfo=TZ)
    result = dict(schema='course-cross-market-replay-day-v1', date=day, mode='replay_day', input_by=input_by)
    if clock() < base + 15 * 60 * MIN:
        return dict(result, status='skipped', gap='目标日尚未收盘')
    if not open_day(day, db_path):
        return dict(result, status='skipped', gap='SSE休市')
    root = Path(root)
    folder = root / day / 'replay'
    out = folder / 'day.json'
    slots = [base + n * MIN for n in sorted(SLOTS)]
    with (nullcontext() if dry_run else lock(root, day+'/replay/day')):
        group_slots, specs, keys_by_slot, slot_hashes = {}, {}, {}, {}
        for slot in slots:
            prior = archived(folder/(slot.strftime('%H%M')+'.json'), day, slot, 'replay')
            if prior.get('status') == 'complete':
                slot_hashes[slot.strftime('%H:%M')] = prior['sha256']
            keys_by_slot[slot] = {}
            for key in KEYS:
                if prior.get('items', {}).get(key, {}).get('status') == 'complete':
                    continue
                try:
                    spec = instrument(key, slot)
                except ValueError:
                    # 单槽服务以实际的合约表缺失原因保存此品种。
                    continue
                group = (key, spec['symbol'])
                group_slots.setdefault(group, []).append(slot)
                specs[group] = spec
                keys_by_slot[slot][key] = group
        if not group_slots and out.exists():
            old = json.loads(out.read_text())
            checksum = old.pop('sha256')
            if checksum != digest(old) or old.get('date') != day:
                raise ValueError('旧全日摘要校验失败')
            if (old.get('status') == 'complete' and len(slot_hashes) == len(slots)
                    and slot_hashes == {r['slot']:r['sha256'] for r in old.get('details',[])}):
                return dict(old, sha256=checksum, path=str(out))
        with ThreadPoolExecutor(max_workers=3) as pool:
            jobs = {g:pool.submit(request_range, g[0], specs[g], subset, transport, clock)
                    for g, subset in group_slots.items()}
            shared = {g:job.result() for g,job in jobs.items()}
        details = []
        for slot in slots:
            subset = {key:shared[g] for key,g in keys_by_slot[slot].items()}
            slot_result = collect(slot, input_by, replay=True, dry_run=dry_run, root=root, db_path=db_path,
                                  clock=clock, shared=subset)
            details.append(dict(slot=slot.strftime('%H:%M'), status=slot_result['status'],
                                completed=slot_result['completed'], sha256=slot_result['sha256']))
        completed = sum(r['completed'] for r in details)
        result.update(slots=len(slots), instruments=len(slots)*len(KEYS), completed=completed, details=details,
                      requests=[dict(key=g[0], symbol=g[1], **receipt) for g,(receipt, _) in shared.items()],
                      request_count=len(shared), collected_at=clock().isoformat(),
                      status='complete' if all(r['status']=='complete' for r in details) else
                             'source_failed' if all(r['status']=='source_failed' for r in details) else 'partial')
        result['sha256'] = digest(result)
        if not dry_run:
            atomic_write(out, json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
    return dict(result, path=None if dry_run else str(out))


def summary(r):
    fields = ('date','slot','mode','status','completed','instruments','slots','request_count','path','gap')
    return {k: v for k, v in r.items() if k in fields}
