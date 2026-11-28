"""从严格上一开放日的已知证据冻结核心股及同题材对照；不读取观察日行情。"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timezone
import json
from pathlib import Path
import re
import sqlite3

from analyzers import market_strength, theme_launch, tracked_theme_members
from analyzers.strong_stock_feedback import _code
from services.board_break_intraday.data import TZ, calendar, digest, limit_rows
from services.handwritten_review import DEFAULT_ARCHIVE, load_entry_before
from utils.limit_identity import verified_streak

RULE_VERSION = 'preopen-core-theme-v2'
MAX_PEERS = 2
MAX_THEME_MEMBERS = 300
SOURCE_ERRORS = (OSError, KeyError, TypeError, ValueError, AttributeError, sqlite3.Error)


def _time(value, *, naive_zone):
    stamp = datetime.fromisoformat(value)
    return stamp.replace(tzinfo=naive_zone) if stamp.tzinfo is None else stamp


def _previous_market(conn, prior, cutoff):
    row = conn.execute("SELECT raw_data,created_at FROM daily_market WHERE date=?", (prior,)).fetchone()
    if not row or _time(row[1], naive_zone=timezone.utc) >= cutoff:
        raise ValueError('上一开放日盘后归档在截止前不可核验')
    envelope = json.loads(row[0])
    raw = envelope['raw_data']
    if envelope['date'] != prior or raw['date'] != prior:
        raise ValueError('盘后归档日期不匹配')
    captured = _time(envelope['generated_at'], naive_zone=TZ)
    if captured.astimezone(TZ).date().isoformat() != prior or captured >= cutoff:
        raise ValueError('盘后来源生成时间与事实日不符')
    strength = raw['market_strength']
    verified = market_strength.view(strength, prior)
    if verified['status'] not in ('complete', 'partial'):
        raise ValueError('盘后逐股身份未通过核验')
    for key in ('identity', 'quotes', 'st'):
        source = strength['sources'][key]
        stamp = _time(source['fetched_at'], naive_zone=TZ)
        if source['status'] != 'success' or stamp.astimezone(TZ).date().isoformat() != prior or stamp >= cutoff:
            raise ValueError('盘后逐股身份的事实日、采集时点或状态错误')
    by_code = {}
    by_name = defaultdict(list)
    for item in strength['rows']:
        name = item.get('name')
        code = _code(item['code'])
        if not name or code in by_code:
            raise ValueError('逐股身份缺名称或代码重复')
        by_code[code] = name
        by_name[name].append(code)
    source = dict(date=prior, generated_at=captured.isoformat(), identity_sources={
        k: strength['sources'][k] for k in ('identity', 'quotes', 'st')},
        identity_sha256=digest(sorted(by_code.items())))
    return raw, by_code, by_name, source


def _subsection(text, heading):
    match = re.search(r'^#{3,6}\s+' + heading + r'[^\n]*\n(.*?)(?=^#{2,6}\s|\Z)', text, re.M | re.S)
    return match.group(1) if match else ''


def _affirmed(raw, label):
    """只取保留／新增等明确措辞之后的句段，剔除删除、移出等反向句段。"""
    valid = []
    for line in raw.splitlines():
        if not re.match(r'^\s*[-*]?\s*' + label + r'\s*[：:]', line):
            continue
        mode = bool(re.search(r'[：:]\s*\d+\s*只核心[（(]', line))
        for text in re.split(r'(删除|移出|剔除|不再|新增|保留|确定|补充|精简为)', line):
            if text in {'删除', '移出', '剔除', '不再'}:
                mode = False
            elif text in {'新增', '保留', '确定', '补充', '精简为'}:
                mode = True
            elif mode:
                valid.append(text)
    return ' '.join(valid)


def _manual_groups(section, by_name, tags):
    """复合组内只认明确子标题或证券旁的细题材说明，风格／防守／轮动不连边。"""
    groups = defaultdict(list)
    current = None
    generic = re.compile(r'其他|其余|杂项|混沌|散点|防守|轮动|其[它他]|分化')
    role = re.compile(r'首板|连板|涨停|炸板|断板|反包|中军|领涨|负反馈|异动|点名')
    for line in _subsection(section, '题材分组').splitlines():
        title = re.fullmatch(r'\*\*\d+[.、]\s*(.*?)\*\*', line.strip())
        if title:
            label = re.split(r'[⬆⬇↕↑↓]', title.group(1), 1)[0].strip()
            current = None if generic.search(label) or re.search(r'[与、/／＋+]', label) else label.split('（')[0]
            continue
        bullet = re.match(r'^- \*\*(.*?)\*\*[：:]\s*(.*)$', line)
        if not bullet:
            continue
        heading = bullet.group(1).split('（')[0].strip()
        # 如“贵金属、太空光电与医药／传媒”内有明确“医药”“传媒”两行，各自为组。
        group = current if role.search(heading) else (
            heading if re.fullmatch(r'[\w ]{1,18}', heading) and not generic.search(heading) else None)
        for cell in re.finditer(r'\*\*([^*]+)\*\*(?:[（(]([^（）()]*)[）)])?', bullet.group(2)):
            parts = cell.group(1).split()
            if not parts or len(by_name.get(parts[0], [])) != 1:
                continue
            name = parts[0]
            labels = {group} if group else set()
            # 只承认个股自己行内的说明；不把同一行的其他票附注张冠李戴。
            labels.update(tag for tag in tags if tag in (cell.group(2) or ''))
            for label in sorted(labels):
                if name not in groups[label]:
                    groups[label].append(name)
    return groups


def _explicit_peers(cell, by_name):
    """仅解析末列明确的“与甲、乙对照”，避免把形容词、竞品或市场点评当作成员。"""
    match = re.search(r'与([^；;，。|]+(?:[，,][^；;。|]*)?)对照', cell)
    if not match:
        return []
    body = re.sub(r'[(（][^()（）]*[)）]', '', match.group(1))
    return [name.strip() for name in re.split(r'[、／/及和]', body) if len(by_name.get(name.strip(), [])) == 1]


def _explicit_name(name, statement):
    separators = r'\s、，,（）()／/；;。:：及和'
    return re.search(rf'(?:^|[{separators}]){re.escape(name)}(?=$|[{separators}])', statement) is not None


def _canonical_name(name, code, by_name):
    if by_name.get(name) == [code]:
        return name
    # 显式证券代码一致时兼容科创企业-W的行情简称，不猜同音或类似名称。
    if code.startswith('688') and code.endswith('.SH') and name.endswith('-W') and by_name.get(name[:-2]) == [code]:
        return name[:-2]
    return None


def _handwritten(entry, by_name):
    fixed = entry['sections']['固定对象跨日反馈']
    raw = entry['raw']
    l1 = _affirmed(raw, r'(?:L1\s*)?情绪核心')
    l2 = _affirmed(raw, r'(?:L2\s*)?[「“]?最[」”]?')
    cores = {}
    announced = {}
    stock_tags = {}
    for name in re.findall(r'^-\s+\*\*([^*]+)\*\*', _subsection(fixed, '情绪核心'), re.M):
        if _explicit_name(name, l1) and len(by_name.get(name, [])) == 1:
            cores[by_name[name][0]] = ['手写L1情绪核心']
    table = False
    for line in fixed.splitlines():
        if not line.startswith('|'):
            table = False
            continue
        cells = [c.strip() for c in line.strip('|').split('|')]
        if cells[:3] == ['优先级', '观察组', '核心股票']:
            table = '对照' in cells[-1]
            continue
        if table and len(cells) >= 4:
            identity = re.fullmatch(r'([^\s|]+)\s+`?(\d{6}\.(?:SH|SZ|BJ))`?', cells[2])
            if identity:
                name, code = identity.groups()
                if _canonical_name(name, code, by_name):
                    announced[code] = _explicit_peers(cells[-1], by_name)
                    stock_tags[name] = [tag.split()[0] for tag in re.split(r'[／/]', cells[1])
                                        if tag.strip() and re.fullmatch(r'[\w ]{2,16}', tag)
                                        and not re.search(r'情绪|反包|异动|监[控管]|核心|最高|连板', tag)]
                    if _explicit_name(name, l2):
                        cores.setdefault(code, []).append('手写确认L2最票')
    for line in entry['sections'].get('次日具体条件', '').splitlines():
        cells = [part.strip() for part in line.strip('|').split('|')]
        identity = re.fullmatch(r'(?:[\w-]+\s+)?([^\s|]+)\s+`?(\d{6}\.(?:SH|SZ|BJ))`?', cells[0])
        if not identity or by_name.get(identity[1]) != [identity[2]]:
            continue
        for match in re.finditer(r'同看([^；;。|]+)', ' '.join(cells[1:])):
            body = re.sub(r'[（(][^（）()]*[）)]', '', match[1])
            for name in re.split(r'[、／/及和]', body):
                name = re.split(r'竞价|联动|能否|观察|走势|[ ，,；;。]', name.strip(), 1)[0]
                if len(by_name.get(name, [])) == 1 and name not in announced.setdefault(identity[2], []):
                    announced[identity[2]].append(name)
    groups = _manual_groups(fixed, by_name, {tag for labels in stock_tags.values() for tag in labels})
    for name, labels in stock_tags.items():
        for label in labels:
            if name not in groups[label]:
                groups[label].append(name)
    return cores, announced, groups


def _manual_leaders(conn, prior, cutoff, by_code):
    row = conn.execute('SELECT step5_leaders,updated_at FROM daily_reviews WHERE date=?', (prior,)).fetchone()
    if not row:
        raise ValueError('前一开放日人工最票来源缺失')
    updated = _time(row[1], naive_zone=timezone.utc)
    if updated >= cutoff or updated.astimezone(TZ).date().isoformat() < prior:
        raise ValueError('人工最票修改时点无法证明开盘前已确定')
    leaders = json.loads(row[0])['top_leaders']
    if not isinstance(leaders, list):
        raise ValueError('人工最票名单结构错误')
    codes = []
    for item in leaders:
        code = _code(item.get('stock_code') or item.get('code'))
        stated = item['stock']
        same_name = by_code.get(code) == stated or (
            code.startswith('688') and code.endswith('.SH') and isinstance(stated, str)
            and stated.endswith('-W') and by_code.get(code) == stated[:-2])
        if not same_name or code in codes:
            raise ValueError('人工最票名称代码不匹配或重复')
        codes.append(code)
    return codes, dict(date=prior, updated_at=updated.isoformat(), sha256=digest(leaders))


def _theme_groups(raw, prior, cutoff):
    block = raw['theme_launch']
    evidence = block['evidence']
    if (block['trade_date'] != prior or evidence['membership_source'] != theme_launch.SOURCE
            or block['membership_date'] != prior):
        raise ValueError('题材成员来源或日期不匹配')
    snapshot = evidence['membership']
    stamp = datetime.fromisoformat(snapshot['fetched_at'])
    if stamp.tzinfo is None or stamp >= cutoff or stamp.astimezone(TZ).date().isoformat() != prior:
        raise ValueError('题材快照不在事前时点')
    groups, gaps, broad = [], [], []
    for theme in theme_launch.rankings(evidence['ranking'], prior):
        try:
            members = theme_launch._members(snapshot, theme['code'], prior)
        except SOURCE_ERRORS as exc:
            gaps.append(f"{theme['name']}成员未核验：{exc}")
            continue
        if len(members) <= MAX_THEME_MEMBERS:
            groups.append((theme, members))
        else:
            broad.append(theme['code'])
    return groups, dict(date=prior, fetched_at=snapshot['fetched_at'], sha256=digest(snapshot),
                        excluded_broad_count=len(broad)), gaps


def _tracked_groups(raw, prior, cutoff, previous):
    block = raw[tracked_theme_members.KEY]
    if not tracked_theme_members._preservable(block, prior, previous):
        raise ValueError('自选细题材来源时间或状态未核验')
    selection = block['selection']
    selected = datetime.fromisoformat(selection['recorded_at'])
    if (selection['date'] != previous or selected.tzinfo is None or
            selected >= datetime.fromisoformat(prior+'T09:30:00+08:00')):
        raise ValueError('细题材选择晚于事实日开盘')
    if block.get('catalog') is not None and block.get('catalog_source') == tracked_theme_members.CATALOG:
        catalog = tracked_theme_members._catalog_rows(block['catalog'], prior)
    else:
        catalog = tracked_theme_members._daily_identities(raw, prior)
    groups, receipts, gaps = [], {}, []
    for item in block['rows']:
        code, name = item.get('code'), item.get('name')
        if item.get('status') != 'complete' or not code or catalog.get(code) != name:
            gaps.append(f'{name or code}前夜细题材身份或成员未核验')
            continue
        snap, members = tracked_theme_members._stored({tracked_theme_members.KEY: block}, code, prior)
        if not snap or datetime.fromisoformat(snap['fetched_at']) >= cutoff or item.get('count') != len(members):
            gaps.append(f'{name}前夜细题材成员收据不符')
            continue
        receipts[code] = dict(fetched_at=snap['fetched_at'], sha256=digest(snap), count=len(members))
        if len(members) <= MAX_THEME_MEMBERS:
            groups.append((dict(code=code, name=name), members))
    return groups, dict(date=prior, selection_sha256=digest(selection), snapshots=receipts), gaps


def _volume_concepts(conn, prior, cutoff, by_code):
    """保存的是T-1成交额前50与当时热门概念的实际交集；仅供明确点名的两股对照。"""
    row = conn.execute('SELECT gain_universe_json,source_json,created_at,updated_at FROM '
                       'daily_volume_concentration WHERE date=?', (prior,)).fetchone()
    if not row:
        raise ValueError('前日成交榜题材标签缺失')
    source = json.loads(row[1])
    health = source['gain_concept']
    audit = source['audit']
    times = [_time(row[3] or row[2], naive_zone=timezone.utc),
             _time(audit['collected_at'], naive_zone=timezone.utc)]
    if (any(t >= cutoff or t.astimezone(TZ).date().isoformat() != prior for t in times)
            or health.get('status') not in ('complete', 'healthy_sparse')
            or health.get('caliber') != 'top15_hot_concept_intersection'):
        raise ValueError('成交榜题材采集状态或时点未核验')
    rows = json.loads(row[0])
    if len(rows) != health['universe_count'] or len({r['code'] for r in rows}) != len(rows):
        raise ValueError('成交榜题材样本身份重复或分母不符')
    tags = {}
    for item in rows:
        code = _code(item['code'])
        labels = item.get('concepts')
        if by_code.get(code) == item['name'] and isinstance(labels, list):
            tags[code] = [x for x in labels if isinstance(x, str) and x and not
                          re.search(r'持股|同花顺.*\d+|成份|成分|沪股通|深股通|融资融券|MSCI|富时', x)]
    return tags, dict(date=prior, collected_at=times[1].isoformat(), updated_at=times[0].isoformat(),
                      sha256=digest(tags), caliber=health['caliber'])


def build_universe(conn, day, *, archive_root=DEFAULT_ARCHIVE, cutoff_time=time(9, 30)):
    """返回开盘前已有凭据的有界取样名单；调用方首次存证后以同日已冻结名单为准。"""
    if cutoff_time not in (time(9, 15), time(9, 30)):
        raise ValueError("选样截止须是竞价开始或连续竞价开盘")
    cutoff = datetime.combine(date.fromisoformat(day), cutoff_time, tzinfo=TZ)
    src, gaps = {}, []
    output = dict(schema='course-intraday-universe-v1', rule_version=RULE_VERSION, status='source_failed', trade_date=day,
                  as_of_date=None, cutoff_at=cutoff.isoformat(), members=[], source=src, gaps=gaps)
    def done():
        output['sha256'] = digest(output)
        return output

    try:
        cal = calendar(conn, day)
        prior = cal['previous']
        output['as_of_date'] = prior
        if not cal['is_open']:
            output['status'] = 'skipped'
            return done()
        raw, by_code, by_name, src['identity'] = _previous_market(conn, prior, cutoff)
    except SOURCE_ERRORS as exc:
        gaps.append(str(exc))
        return done()

    cores, proposed, manual_groups = {}, {}, {}
    try:
        entry, _, _ = load_entry_before(Path(archive_root) / prior, cutoff)
        meta = entry['meta']
        if datetime.fromisoformat(meta['recorded_at']).astimezone(TZ).date().isoformat() < prior:
            raise ValueError('前夜手写稿记录早于其事实日')
        cores, proposed, manual_groups = _handwritten(entry, by_name)
        src['handwritten'] = {k: meta[k] for k in ('recorded_at', 'source_sha256', 'supplement_sha256')}
        src['handwritten'].update(date=prior, revision=meta.get('revision', 0))
        if not cores:
            gaps.append('未从事前手写原文及分层表同时核验人工核心')
    except SOURCE_ERRORS as exc:
        gaps.append(f'手写分层：{exc}')
    try:
        manual, src['manual_leaders'] = _manual_leaders(conn, prior, cutoff, by_code)
        for code in manual:
            cores.setdefault(code, []).append('正式复盘人工最票')
    except SOURCE_ERRORS as exc:
        gaps.append(str(exc))
    if not cores:
        gaps.append('无开盘前核验的核心，未建立对照组')
        return done()
    try:
        themes, src['theme_snapshot'], skipped = _theme_groups(raw, prior, cutoff)
        gaps.extend(skipped)
    except SOURCE_ERRORS as exc:
        themes = []
        gaps.append(f'题材历史快照：{exc}')

    theme_refs = {t['code']: 'theme_snapshot' for t, _ in themes}
    if tracked_theme_members.KEY in raw:
        try:
            tracked, src['tracked_themes'], skipped = _tracked_groups(raw, prior, cutoff, cal['history'][-2])
            gaps.extend(skipped)
            for item, group in tracked:
                if item['code'] not in theme_refs:
                    theme_refs[item['code']] = 'tracked_themes'
                    themes.append((item, group))
        except SOURCE_ERRORS as exc:
            gaps.append(f'前夜跟踪细题材：{exc}')
    try:
        tags, src['volume_concepts'] = _volume_concepts(conn, prior, cutoff, by_code)
    except SOURCE_ERRORS:
        tags = {}
    bounds = []
    if themes:
        try:
            rows = limit_rows(raw, prior)
            provider = raw['limit_up']['_source']
            for row in rows:
                count, _ = verified_streak(row, provider)
                if count is not None and by_code.get(row['code']) == row.get('name'):
                    bounds.append((row['code'], count))
            bounds.sort(key=lambda item: (-item[1], item[0]))
            src['limit_up'] = dict(date=prior, provider=provider, sha256=digest(rows),
                                   generated_at=src['identity']['generated_at'])
        except SOURCE_ERRORS as exc:
            gaps.append(f'昨日涨停对照：{exc}')

    members = {}
    for code, roles in cores.items():
        refs = ['identity']
        if any(r.startswith('手写') for r in roles):
            refs.append('handwritten')
        if '正式复盘人工最票' in roles:
            refs.append('manual_leaders')
        members[code] = dict(code=code, name=by_code[code], role='core', roles=roles,
                             source_refs=refs, theme_groups=[], comparisons=[])
    # 唯一取样顺序是昨日已定的对照顺序及既有快照内的昨日涨停身位，绝不参考今天走势。
    for code in cores:
        core = members[code]
        name = core['name']
        picks = {}
        for peer in proposed.get(code, []):
            other = by_name[peer][0]
            shared = [tag for tag in tags.get(code, []) if tag in tags.get(other, [])]
            if shared and peer != name:
                picks[other] = ('前夜点名且同成交榜细题材', shared[0], 'volume_concepts')
                continue
            matches = [label for label, names in manual_groups.items() if name in names and peer in names]
            if matches and peer != name:
                picks[by_name[peer][0]] = ('前夜点名且同手写题材组', matches[0], 'handwritten')
        for theme, group in themes:
            if group.get(code) != name:
                continue
            for other, _ in bounds:
                if other != code and other in by_code and group.get(other) == by_code[other]:
                    picks.setdefault(other, ('同题材昨日涨停身位对照', theme['name'], theme_refs[theme['code']]))
        for other, (basis, label, ref) in list(picks.items())[:MAX_PEERS]:
            if label not in core['theme_groups']:
                core['theme_groups'].append(label)
            peer = members.setdefault(other, dict(code=other, name=by_code[other], role='peer',
                                      roles=[], source_refs=['identity'], theme_groups=[], comparisons=[]))
            if ref not in peer['source_refs']:
                peer['source_refs'].append(ref)
            if label not in peer['theme_groups']:
                peer['theme_groups'].append(label)
            if basis not in peer['roles']:
                peer['roles'].append(basis)
            if ref == 'volume_concepts' and 'handwritten' not in peer['source_refs']:
                peer['source_refs'].append('handwritten')
            if ref in ('theme_snapshot', 'tracked_themes') and 'limit_up' not in peer['source_refs']:
                peer['source_refs'].append('limit_up')
            core['comparisons'].append(dict(code=other, theme=label, basis=basis, source=ref))
        if not core['comparisons']:
            gaps.append(f'{name}按事前同题材取样规则未选出对照')
    output['members'] = list(members.values())
    output['status'] = 'partial' if gaps else 'complete'
    return done()
