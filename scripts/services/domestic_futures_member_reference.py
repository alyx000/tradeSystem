"""保存同花顺公开页面上指定会员、指定交割月的一日持仓见证。"""
from contextlib import closing, nullcontext
from datetime import date, datetime, time
from hashlib import sha256
import json
from pathlib import Path
import re

from db.connection import get_readonly_connection
from services.board_break_intraday.data import TZ, calendar, digest
from services.board_break_intraday.store import lock
from services.domestic_index_futures import ROOT
from services.domestic_futures_bulletin import MAX_BYTES
from services.volume_record.service import atomic_write

SCHEMA = 'ths-futures-member-reference-v1'
ADDRESS = re.compile(
    r'(?P<member>[^()\r\n]{1,36}?)(?P<code>(?:IF|IM)\d{2}(?:0[1-9]|1[0-2]))'
    r'股指期货详情-数据中心-同花顺财经 \((?P<url>https://data\.10jqka\.com\.cn/gzqh/detail/'
    r'instrumentId/(?P=code)/partyId/(?P<party>\d{4})/)\)')
COLUMNS = ('日期', '成交量（手）', '成交增减', '多单持仓（手）', '多单增减',
           '空单持仓（手）', '空单增减', '净多持仓（手）', '净空持仓（手）')
FIELDS = ('volume', 'volume_change', 'long_position', 'long_change', 'short_position',
          'short_change', 'net_long_as_shown', 'net_short_as_shown')
COMPARE = (('volume', 'volume_change'), ('long_position', 'long_change'),
           ('short_position', 'short_change'))


def quantity(value):
    if value in ('', '-', '--', '—'):
        return None
    if not re.fullmatch(r'[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)', value):
        raise ValueError('会员表数量或增减非整数')
    return int(value.replace(',', ''))


def read_row(line):
    cells = [col.strip() for col in line.split('|')]
    if len(cells) != 9:
        raise ValueError('会员持仓表数据列数错误')
    day = date.fromisoformat(cells[0]).isoformat()
    row = dict(zip(FIELDS, (quantity(c) for c in cells[1:])))
    if any(row[key] is not None and row[key] < 0 for key, _ in COMPARE):
        raise ValueError('成交或持仓手数为负')
    if all(row[s] is None for s in ('long_position', 'short_position')):
        raise ValueError('两侧持仓数量均缺失')
    if row['long_position'] is not None and row['short_position'] is not None:
        net = row['long_position'] - row['short_position']
        for field, expected in [('net_long_as_shown', max(0, net)), ('net_short_as_shown', min(0, net))]:
            if row[field] is not None and row[field] != expected:
                raise ValueError('净持仓显示与多空持仓冲突')
    return day, row


def parse(text, day, prev_day):
    lines = text.splitlines()
    source = ADDRESS.fullmatch(lines[0]) if lines else None
    total = re.search(r'Total lines:\s*(\d+)\s*$', lines[1]) if len(lines) >= 3 else None
    if not source or not total:
        raise ValueError('缺少同花顺详情页标题、地址或网页行号信息')
    identity = source.groupdict()
    member, code = identity['member'], identity['code']
    if identity['party'] == '0016' and member != '广发期货':
        raise ValueError('会员名称与已核验的0016身份不符')
    numbered = {}
    for line in lines[2:]:
        match = re.fullmatch(r'L(\d+): ?(.*)', line)
        if not match or int(match[1]) >= int(total[1]) or int(match[1]) in numbered:
            raise ValueError('网页摘录行号无效或重复')
        numbered[int(match[1])] = match[2]
    heading = f'## {member}{code}建仓过程及持仓详情查询 (近一个月)'
    starts = [n for n,line in numbered.items() if line.strip() == heading]
    if len(starts) != 1:
        raise ValueError('缺少或重复对应会员和具体合约的明细标题')
    n = starts[0] + 1
    header_fragments = []
    while n in numbered and len(header_fragments) < 16:
        if re.fullmatch(r'\s*---(?:\s*\|\s*---){8}\s*', numbered[n]):
            break
        header_fragments.append(numbered[n])
        n += 1
    if n not in numbered or not re.fullmatch(r'\s*---(?:\s*\|\s*---){8}\s*', numbered[n]):
        raise ValueError('缺少连续的九列表头')
    columns = tuple(re.sub(r'\s+', '', c) for c in ''.join(header_fragments).split('|'))
    if columns != COLUMNS:
        raise ValueError('会员持仓列名、次序或手数单位不符')
    rows = {}
    next_n = n + 1
    while next_n in numbered and re.match(r'^\s*\d{4}-\d\d-\d\d\s*\|', numbered[next_n]):
        line = numbered[next_n].strip()
        stamp = line.split('|', 1)[0].strip()
        if stamp in (day, prev_day):
            if stamp in rows:
                raise ValueError('目标日或前一开放日存在多条持仓记录')
            _, values = read_row(line)
            rows[stamp] = dict(row=values, line_number=next_n)
        next_n += 1
    if day not in rows:
        raise ValueError('连续表格摘录内无目标日行')
    # 另一个片段再次出现目标日期，即使看似相同，也不能确定为唯一行。
    for n2, line in numbered.items():
        if n2 >= next_n and line.split('|', 1)[0].strip() in (day, prev_day):
            raise ValueError('表外另见目标日或前一开放日行，不能确认唯一性')
    row = rows[day]['row']
    prior = rows.get(prev_day)
    checked = []
    if prior:
        for key, change in COMPARE:
            if None in (row[change], row[key], prior['row'][key]):
                continue
            if row[key] - prior['row'][key] != row[change]:
                raise ValueError('已披露增减与严格上一开放日同一会员和合约的手数冲突')
            checked.append(change)
    return identity, row, dict(previous_date=prev_day, previous_row=prior, verified_changes=checked,
                               title_line=starts[0], target_line=rows[day]['line_number'])


def validate_old(obj, day, previous_day, identity):
    signature = obj.pop('sha256')
    if signature != digest(obj) or obj.get('schema') != SCHEMA or obj.get('trade_date') != day:
        raise ValueError('旧会员参考签名或事实日错误')
    if not obj.get('sources') or type(obj.get('best_source')) is not int or not 0 <= obj['best_source'] < len(obj['sources']):
        raise ValueError('旧会员参考缺来源记录')
    verified = set()
    for receipt in obj['sources']:
        raw = receipt['raw_text']
        if sha256(raw.encode()).hexdigest() != receipt['raw_sha256']:
            raise ValueError('旧网页摘录哈希不符')
        old_id, row, evidence = parse(raw,day,previous_day)
        if (old_id != identity or obj['identity'] != identity or row != obj['row']
                or receipt['evidence'] != evidence):
            raise ValueError('旧会员、合约、目标行或前日证据不符')
        verified.update(evidence['verified_changes'])
    if obj['verified_changes'] != [change for _,change in COMPARE if change in verified]:
        raise ValueError('旧增减核验状态不符')
    return signature


def import_file(path, day, actor, *, root=ROOT, db_path=None, clock=None, dry_run=False):
    if not actor.strip():
        raise ValueError('请求者不能为空')
    now = (clock or (lambda: datetime.now(TZ)))().astimezone(TZ)
    if now < datetime.combine(date.fromisoformat(day), time(16), tzinfo=TZ):
        raise ValueError('目标交易日尚未完成')
    path, root = Path(path), Path(root)
    if not 0 < path.stat().st_size <= MAX_BYTES:
        raise ValueError('网页摘录为空或超过大小上限')
    wire = path.read_bytes()
    text = wire.decode('utf-8')
    with closing(get_readonly_connection(db_path)) as conn:
        market_day = calendar(conn, day)
    if not market_day['is_open']:
        raise ValueError('非SSE开放日')
    previous = market_day['previous']
    identity, row, evidence = parse(text, day, previous)
    code, party = identity['code'], identity['party']
    file_name = code + '-' + party
    out = root / 'references' / day / 'members' / (file_name + '.json')
    receipt = dict(raw_text=text, raw_sha256=sha256(wire).hexdigest(), evidence=evidence,
                   known_by_at=now.isoformat(), http_received_at=None, input_by=actor.strip())
    with (nullcontext() if dry_run else lock(root, day+'/reference/member/'+file_name)):
        if out.exists():
            obj = json.loads(out.read_bytes())
            signature = validate_old(obj, day, previous, identity)
            if row != obj['row']:
                raise ValueError('同会员同合约目标日原文与已存数值冲突，保留旧档')
            if evidence['previous_row']:
                new_row = evidence['previous_row']['row']
                for old in obj['sources']:
                    prior = old['evidence']['previous_row']
                    if prior and any(v is not None and new_row[k] is not None and v != new_row[k]
                                     for k,v in prior['row'].items()):
                        raise ValueError('上一开放日会员原始行出现冲突，保留旧档')
            if set(evidence['verified_changes']).issubset(obj['verified_changes']):
                return dict(obj, sha256=signature, path=str(out), reused=True)
        else:
            obj = dict(schema=SCHEMA, trade_date=day, identity=identity, row=row, sources=[],
                       scope='同花顺单一会员、单一挂牌合约的公开日持仓；单位手。',
                       acquisition='手工取得公开网页摘录，导入时间为本机可核验的取得上界')
        obj['sources'].append(receipt)
        obj['best_source'] = max(range(len(obj['sources'])), key=lambda i:len(obj['sources'][i]['evidence']['verified_changes']))
        all_checked = set(obj.get('verified_changes', [])) | set(evidence['verified_changes'])
        obj['verified_changes'] = [change for _,change in COMPARE if change in all_checked]
        obj['sha256'] = digest(obj)
        if not dry_run:
            atomic_write(out, json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False))
        return dict(obj, path=None if dry_run else str(out), reused=False)
