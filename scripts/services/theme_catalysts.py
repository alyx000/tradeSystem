"""对前夜重点题材的金十新闻留存逐条证据及可见时刻。"""
from datetime import datetime, timedelta
from functools import partial
import fcntl
from hashlib import sha256
from html import unescape
from html.parser import HTMLParser
import json
import re
import time
import unicodedata
from urllib.parse import urlsplit

import requests

from analyzers.tracked_theme_members import _requests
from services.board_break_intraday.data import TZ, calendar, digest
from services.handwritten_review import load_entry_before
from services.intraday_summary.state import atomic_write_text
from services.macro_flash import collector as jin10

SCHEMA = 'theme-catalysts-v1'
SLOTS = tuple(range(572, 683, 10)) + tuple(range(782, 893, 10))
ORIGINS = {
    'www.stats.gov.cn': ('国家统计局',), 'www.pbc.gov.cn': ('中国人民银行', '央行'),
    'www.miit.gov.cn': ('工业和信息化部', '工信部'), 'www.mof.gov.cn': ('财政部',),
    'www.mofcom.gov.cn': ('商务部',), 'www.cac.gov.cn': ('国家网信办',),
    'www.safe.gov.cn': ('国家外汇管理局', '外汇管理局'), 'www.csrc.gov.cn': ('中国证监会', '证监会'),
    'www.ndrc.gov.cn': ('国家发展改革委', '国家发改委'), 'www.nea.gov.cn': ('国家能源局',),
    'www.sse.com.cn': ('上海证券交易所', '上交所'), 'www.szse.cn': ('深圳证券交易所', '深交所'),
}


def stamp(value):
    ts = datetime.fromisoformat(value)
    if ts.tzinfo is None:
        raise ValueError('采集回执时间缺少时区')
    return ts.astimezone(TZ)


def selected(root, prior, day):
    cutoff = stamp(day + 'T09:30:00+08:00')
    entry, _, _ = load_entry_before(root / 'data/handwritten-review' / prior, cutoff)
    meta = entry['meta']
    if meta['date'] != prior:
        raise ValueError('手写事前选题日期错位')
    terms, basis = _requests(entry)
    return dict(date=prior, cutoff=cutoff.isoformat(), recorded_at=meta['recorded_at'],
                source_sha256=meta['source_sha256'], supplement_sha256=meta['supplement_sha256'],
                basis=basis, requests=terms)


def plain(value):
    return ' '.join(unescape(re.sub(r'<[^>]*>', ' ', value or '')).split())


def patterns(selection):
    result = {}
    for row in selection['requests']:
        name = row['requested']
        parts = unicodedata.normalize('NFKC', name).casefold().split()
        term = r'\s*'.join(map(re.escape, parts))
        if term and term[0].isascii() and term[0].isalnum():
            term = r'(?<![a-z0-9])' + term
        if term and term[-1].isascii() and term[-1].isalnum():
            term += r'(?![a-z0-9])'
        if term:
            result[name] = re.compile(term)
    return result


def safe_origin(credit, url):
    try:
        parsed = urlsplit(url)
        names = ORIGINS.get(parsed.hostname, ())
        if (parsed.scheme == 'https' and parsed.username is None and parsed.password is None
                and parsed.port in (None, 443) and parsed.path.strip('/') and credit in names):
            return names[0]
    except ValueError:
        pass
    return None


def news_kind(title, content, org):
    lead = (title or content).lstrip('【')
    if lead.startswith('金十数据整理'):
        return 'news_roundup'
    if (re.match(r'(?:港股|A股)(?:开盘|收盘|午评|收评)|(?:午盘收盘|早盘开盘|主力资金流向前十|金十图示)', lead)
            or re.match(r'(?:港股|A股|恒生|创业板指).{0,15}(?:涨|跌|走强|走高|拉升)', lead)
            or re.search(r'触达\d+cm涨停', lead[:30], re.I)):
        return 'market_observation'
    if org:
        return 'linked_institution_source'
    return 'reported_news'


def match_item(raw, terms, day):
    media = jin10._parse_item(raw)
    if not media or media.date().isoformat() != day:
        return None
    info = raw['data']
    if not all(isinstance(info.get(k) or '', str) for k in ('content', 'title', 'source', 'source_link')):
        raise ValueError('金十文本或来源字段格式异常')
    title, content = plain(info.get('title')), plain(info.get('content'))
    text = unicodedata.normalize('NFKC', title + ' ' + content).casefold()
    hits = [name for name, pattern in terms.items() if pattern.search(text)]
    if not hits:
        return None
    source, link = info.get('source') or None, info.get('source_link') or None
    org = safe_origin(source, link or '')
    return dict(item_id=str(raw['id']), media_published_at=media.replace(tzinfo=TZ).isoformat(),
                kind=news_kind(title, content, org), matched_terms=hits, title=title, content=content,
                credited_source=source, credited_url=link,
                original_org=org, original_url=link if org else None, original_published_at=None,
                original_time_evidence=None, raw_sha256=digest(raw), raw_item=raw)


class PublicationMeta(HTMLParser):
    def __init__(self):
        super().__init__()
        self.candidates = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != 'meta':
            return
        fields = {k: v or '' for k, v in attrs}
        for key in ('property', 'name', 'itemprop'):
            name = fields.get(key, '').casefold()
            if name in {'article:published_time', 'datepublished', 'publishdate', 'pubdate'} and fields.get('content'):
                self.candidates.append((name, fields['content'].strip(), self.get_starttag_text()))


def original_time(version, session, clock):
    """只读取白名单机构自己署名的 HTTPS 正文；保留发布时间元标签与实际取证时刻。"""
    url = version['original_url']
    started = time.monotonic()
    with session.get(url, allow_redirects=False, stream=True, timeout=(2, 3),
                     headers={'User-Agent': 'Mozilla/5.0'}) as response:
        if response.status_code != 200 or 'html' not in response.headers.get('Content-Type', '').lower():
            raise ValueError('原机构正文未返回 HTML')
        buf = bytearray()
        for chunk in response.iter_content(8192):
            buf.extend(chunk)
            if len(buf) >= 262144 or b'</head>' in chunk.lower() or time.monotonic() - started > 6:
                break
    raw = bytes(buf[:262144])
    parser = PublicationMeta()
    # 元标签名、ISO 时间与数字均为 ASCII；不将正文内任意日期当作发布时间。
    parser.feed(raw.decode('ascii', errors='replace'))
    candidates = {}
    for name, value, tag in parser.candidates:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?', value):
            continue
        instant = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=TZ)
        candidates[instant.astimezone(TZ)] = (name, value, tag)
    if len(candidates) != 1:
        raise ValueError('机构正文发布时间缺失或冲突')
    instant, (name, value, tag) = next(iter(candidates.items()))
    if instant > stamp(version['media_published_at']):
        raise ValueError('机构正文时间晚于对应快讯，无法确认原发顺序')
    return dict(published_at=instant.isoformat(), meta_name=name, meta_value=value, raw_meta_tag=tag, url=url,
                captured_at=clock().astimezone(TZ).isoformat(), head_sha256=sha256(raw).hexdigest())


def scheduled_slot(now, requested):
    if requested:
        plan = stamp(requested)
    else:
        plan = now.replace(minute=(now.minute // 10) * 10 + 2, second=0, microsecond=0)
    if (plan.hour * 60 + plan.minute not in SLOTS or plan.second or plan.microsecond or
            not 0 <= (now - plan).total_seconds() < 180):
        return None
    return plan


def live_batch(now, plan, terms, session, clock):
    start = plan - timedelta(minutes=15)
    page_limit = 4
    if (plan.hour, plan.minute) == (9, 32):
        start = plan.replace(hour=0, minute=0)
        page_limit = 8
    elif (plan.hour, plan.minute) == (13, 2):
        start = plan.replace(hour=11, minute=20)
        page_limit = 8
    seen = {}

    def received(page):
        actual = clock().astimezone(TZ).isoformat()
        for row in page:
            if jin10._parse_item(row) is not None:
                seen.setdefault(digest(row), actual)

    result = jin10.collect_window(start.replace(tzinfo=None), now.replace(tzinfo=None), session=session,
                                  max_pages=page_limit, max_runtime_seconds=12, on_page=received,
                                  fetch_page=partial(jin10._fetch_page, max_retries=0, timeout=(2, 3)))
    receipt = dict(kind='live', scheduled_at=plan.isoformat(), window_start=start.isoformat(),
                   window_end=now.isoformat(), source=jin10.JIN10_URL, channel=jin10.JIN10_CHANNEL,
                   source_status=result.status, pages=result.pages, raw_count=result.raw_count,
                   dropped_count=result.dropped_count, window_items=len(result.items),
                   oldest_retained_media_at=min((i['time'] for i in result.items), default=None))
    if result.error:
        receipt['error'] = '金十列表请求失败'
    rows = []
    checked_url = None
    for raw in result.items:
        version = match_item(raw, terms, plan.date().isoformat())
        if not version:
            continue
        actual = seen[version['raw_sha256']]
        if stamp(actual) < stamp(version['media_published_at']):
            raise ValueError('快讯时间晚于实际接收时间')
        if version['original_url'] and checked_url is None:
            checked_url = version['original_url']
            try:
                proof = original_time(version, session, clock)
                version.update(original_published_at=proof['published_at'], original_time_evidence=proof)
            except (ValueError, OSError, requests.RequestException) as exc:
                receipt['original_lookup_gap'] = str(exc)
        rows.append((version, actual))
    return receipt, rows


def archive_batch(root, day, terms, now):
    relative = 'data/runs/macro-flash/' + day
    directory = root / relative
    manifest = json.loads((directory / 'manifest.json').read_bytes())
    encoded = (directory / 'flash_raw.json').read_bytes()
    sha = sha256(encoded).hexdigest()
    source = manifest['files']['flash_raw']
    raw = json.loads(encoded)
    if (manifest['run_date'] != day or manifest['schema_version'] != 1 or raw['schema_version'] != 1
            or source != {'path': 'flash_raw.json', 'sha256': sha}
            or any(manifest[k] != raw[k] for k in ('window_start', 'window_end'))):
        raise ValueError('宏观快讯归档哈希、窗口或事实日不符')
    known = stamp(manifest['generated_at'])
    start, end = [datetime.fromisoformat(raw[k]).replace(tzinfo=TZ) for k in ('window_start', 'window_end')]
    if not (start < end and end.date().isoformat() == day and end <= known <= now
            and isinstance(raw['items'], list)):
        raise ValueError('宏观快讯窗口、留存时间或条目列表不符')
    receipt = dict(kind='archive', source=relative+'/flash_raw.json', raw_sha256=sha,
                   manifest=relative+'/manifest.json', archive_generated_at=known.isoformat(),
                   source_status=manifest['source_status'], window_start=start.isoformat(), window_end=end.isoformat(),
                   pages=manifest['pages'], raw_count=manifest['raw_count'], dropped_count=manifest['dropped_count'],
                   window_items=len(raw['items']))
    rows = []
    ids = set()
    for item in raw['items']:
        ts = jin10._parse_item(item)
        if (ts is None or str(item['id']) in ids or not start <= ts.replace(tzinfo=TZ) <= end):
            raise ValueError('宏观快讯归档存在非法、重复或窗口外条目')
        ids.add(str(item['id']))
        version = match_item(item, terms, day)
        if version:
            rows.append((version, known.isoformat()))
    return receipt, rows


def earlier(existing, value):
    return value if not existing or stamp(value) < stamp(existing) else existing


def save(path, day, selection, receipt, rows, actor):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix('.lock').open('a+') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if path.exists():
            report = json.loads(path.read_bytes())
            previous = report.pop('sha256')
            if digest(report) != previous or report['trade_date'] != day or report['schema'] != SCHEMA:
                raise ValueError('已有题材新闻报告的哈希、事实日或版本不符')
            if selection and report['selection'] and report['selection'] != selection:
                raise ValueError('开盘前题材版本与已归档选题不一致')
        else:
            report = dict(schema=SCHEMA, trade_date=day, selection=None, events={}, receipts={})
        if selection:
            report['selection'] = selection
        receipt['matched_items'] = len(rows) if receipt.get('source_status') != 'source_failed' or rows else None
        key = digest(receipt)
        record = report['receipts'].setdefault(key, dict(receipt, input_by=[]))
        record['input_by'] = sorted(set(record['input_by'] + [actor]))
        for version, observed_at in rows:
            ident, sha = version['item_id'], version['raw_sha256']
            event = report['events'].setdefault(ident, dict(first_seen_at=None, known_by_at=None, versions={}))
            existing = event['versions'].setdefault(sha, dict(version, first_seen_at=None, known_by_at=None, receipts=[]))
            existing.setdefault('raw_item', version['raw_item'])
            existing.setdefault('kind', version['kind'])
            if version['original_time_evidence']:
                proof = version['original_time_evidence']
                old = existing['original_time_evidence']
                if old and old['published_at'] != proof['published_at']:
                    conflict = existing.setdefault('original_time_conflicts', {})
                    conflict.setdefault(old['published_at'], old)
                    conflict.setdefault(proof['published_at'], proof)
                elif not old or stamp(proof['captured_at']) < stamp(old['captured_at']):
                    existing.update(original_time_evidence=proof, original_published_at=proof['published_at'])
                if existing.get('original_time_conflicts'):
                    existing['original_published_at'] = None
            existing['receipts'] = sorted(set(existing['receipts'] + [key]))
            for record in (existing, event):
                record['known_by_at'] = earlier(record['known_by_at'], observed_at)
                if receipt['kind'] == 'live':
                    record['first_seen_at'] = earlier(record['first_seen_at'], observed_at)
        report['coverage'] = dict(
            expected_live_slots=[f'{minute//60:02d}:{minute%60:02d}' for minute in SLOTS],
            live_slots_with_response=sorted({stamp(r['scheduled_at']).strftime('%H:%M')
                                             for r in report['receipts'].values()
                                             if r['kind'] == 'live' and r.get('pages', 0) > 0}),
            live_events=sum(bool(e['first_seen_at']) for e in report['events'].values()),
            known_events=len(report['events']))
        report['sha256'] = digest(report)
        atomic_write_text(path, json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + '\n')
        return report


def collect(conn, root, day, actor, *, archive=False, scheduled_at=None, clock=None):
    clock = clock or (lambda: datetime.now(TZ))
    now = clock().astimezone(TZ)
    path = root / 'data/reports/theme-catalysts' / (day + '.json')
    plan = None if archive else scheduled_slot(now, scheduled_at)
    if day > now.date().isoformat() or not archive and (day != now.date().isoformat() or not plan):
        return dict(status='skipped', reason='非当日实际采样时槽')
    selection, rows = None, []
    kind = 'archive' if archive else 'live'
    context = calendar(conn, day)
    if not context['is_open']:
        return dict(status='skipped', reason='SSE 休市')
    receipt = dict(kind=kind, source_status='source_failed', requested_at=now.isoformat())
    if plan:
        receipt['scheduled_at'] = plan.isoformat()
    try:
        selection = selected(root, context['previous'], day)
        terms = patterns(selection)
        if not terms:
            receipt.update(source_status='no_selected_themes')
        elif archive:
            receipt, rows = archive_batch(root, day, terms, now)
        else:
            with requests.Session() as session:
                receipt, rows = live_batch(now, plan, terms, session, clock)
    except (KeyError, ValueError, OSError, TypeError, requests.RequestException) as exc:
        receipt['error'] = str(exc)
    report = save(path, day, selection, receipt, rows, actor)
    return dict(status=receipt['source_status'], trade_date=day, path=str(path),
                matched_in_batch=receipt['matched_items'], known_events=report['coverage']['known_events'],
                live_events=report['coverage']['live_events'], error=receipt.get('error'))
