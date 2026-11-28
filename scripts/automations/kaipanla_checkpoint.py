"""Persist and resume date-scoped 开盘啦 UI evidence batches."""

from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from io import StringIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from zoneinfo import ZoneInfo


FIELDS = {
    'commodity': 'date view name category raw_pct read_at',
    'tail': 'date view unique_rows tail read_at',
    'one-view-recheck': 'date view name category raw_pct read_at',
    'group': 'date name count read_at',
    'stock': 'date name code category new price raw_pct amount read_at',
    'header': 'date total new read_at',
    'category-list': 'date ordered_categories read_at',
    'alert-raw': 'date section name code tag raw_values read_at',
    'alert-tail': 'date section unique_rows boundary read_at',
    'alert-headers': 'date title evidence read_at',
    'etf-market': 'date bucket name code price_raw change_pct_raw turnover_raw scope read_at',
    'etf-flows': ('date flow_date view bucket name code constituent_count flow_amount_raw '
                  'flow_shares_raw return_pct_raw size_raw scope disclosure_status read_at'),
    'etf-trends': 'date updated_at bucket name constituent_count duration_days interval_change_pct_raw interval_flow_raw tags read_at',
    'etf-meta': 'date view scope boundary read_at',
}
ALERT_SECTIONS = ('次日测算', '当日异动', '近期严重异动', '热门股偏离值', '监管期证券', '历史监管证券')
STOCK_VIEWS = {'百日新高按板块': ('group', 'name'), '百日新高按个股': ('stock', 'code')}
SHANGHAI = ZoneInfo('Asia/Shanghai')
PCT = re.compile(r'[+-]?[0-9]+(?:\.[0-9]+)?%')
BATCH_ID = re.compile(r'[\w][\w.-]{0,79}')


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def encode(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def load_csv(ui: Path, key: str) -> list[dict]:
    path = ui / f'{key}.csv'
    if not path.exists():
        return []
    with path.open(encoding='utf-8', newline='') as source:
        reader = csv.DictReader(source)
        if reader.fieldnames != FIELDS[key].split():
            raise ValueError(f'{path.name} 表头与采集契约不符')
        return list(reader)


def read_moment(row: dict) -> datetime:
    return datetime.fromisoformat(row['read_at'].replace('Z', '+00:00'))


def validate_rows(data: dict, fact: str) -> None:
    if not isinstance(data, dict) or set(data) - FIELDS.keys():
        raise ValueError('采集批次须由已定义的 CSV 名和记录列表组成')
    for key, rows in data.items():
        if not isinstance(rows, list):
            raise ValueError(f'{key} 必须为记录列表')
        fields = set(FIELDS[key].split())
        for row in rows:
            if not isinstance(row, dict) or set(row) != fields or not all(isinstance(v, str) for v in row.values()):
                raise ValueError(f'{key} 字段或类型不符，证券代码和原始数值均使用文本')
            stamp = read_moment(row)
            if (row['date'] != fact or stamp.utcoffset() is None
                    or stamp.astimezone(SHANGHAI).date().isoformat() < fact):
                raise ValueError(f'{key} 事实日或采集时刻无效')


def merge_records(*snapshots: dict) -> dict:
    """Collapse identical sightings while retaining conflicting raw values for review."""
    merged = defaultdict(dict)
    for records in snapshots:
        for key, rows in records.items():
            columns = FIELDS[key].split()
            if key != 'one-view-recheck':
                columns = columns[:-1]
            by_value = merged[key]
            for row in rows:
                by_value.setdefault(tuple(row[col] for col in columns), row)
    return {key: list(rows.values()) for key, rows in merged.items()}


def commodity_views(data: dict) -> tuple[set[str], set[str]]:
    registries = {row['ordered_categories'] for row in data.get('category-list', [])}
    categories = next(iter(registries)).split('、') if len(registries) == 1 else []
    expected = {'精选'}
    if categories and all(categories) and len(set(categories)) == len(categories):
        expected.update(categories)
    actual = defaultdict(set)
    for row in data.get('commodity', []):
        actual[row['view']].add((row['name'], row['category']))
    tails = defaultdict(set)
    for row in data.get('tail', []):
        if row['view'] in expected and row['tail'] == 'true':
            tails[row['view']].add(row['unique_rows'])
    complete = {
        view for view, counts in tails.items()
        if counts == {str(len(actual[view]))}
    }
    return expected, complete


def reconcile(data: dict, fact: str) -> tuple[list[dict], list[dict], int]:
    expected, complete = commodity_views(data)
    all_views_complete = len(expected) > 1 and expected == complete
    by_name = defaultdict(list)
    for row in data.get('commodity', []):
        by_name[row['name']].append(row)
    rechecks = defaultdict(list)
    for row in data.get('one-view-recheck', []):
        rechecks[(row['name'], row['category'], row['view'])].append(row)

    current, pending = [], []
    single_view_verified = 0
    for name, rows in sorted(by_name.items()):
        categories = {row['category'] for row in rows}
        category = sorted(categories)[0]
        relevant_rechecks = [
            check for view in {row['view'] for row in rows}
            for check in rechecks[(name, category, view)]
        ]
        all_rows = rows + relevant_rechecks
        values = {
            Decimal(row['raw_pct'][:-1])
            for row in all_rows if PCT.fullmatch(row['raw_pct'])
        }
        views = {row['view'] for row in rows}
        invalid_raw = any(row['raw_pct'] != '--' and not PCT.fullmatch(row['raw_pct']) for row in all_rows)
        reason = ''
        if not name or not category or len(categories) != 1 or (len(expected) > 1 and views - expected):
            reason = '品种身份或分类冲突'
        elif invalid_raw or len(values) > 1:
            reason = '同日涨幅冲突或读数格式异常'
        elif values:
            valid = next(row for row in all_rows if PCT.fullmatch(row['raw_pct']))
            raw_pct = valid['raw_pct']
            status = 'observed'
            basis = f"开盘啦·{valid['view']}同日明确涨幅"
        else:
            loaded = views & complete
            same_view_rechecked = len(views) == 1 and any(
                check['raw_pct'] == '--' and read_moment(check) > max(map(read_moment, rows))
                for check in relevant_rechecks
            )
            if len(loaded) >= 2:
                basis = '开盘啦·同日精选与分类页均未显示涨幅'
            elif all_views_complete and len(loaded) == 1 and same_view_rechecked:
                basis = '开盘啦·核对全部已加载分类及精选后，同日独有视图批量复核仍未显示涨幅'
                single_view_verified += 1
            else:
                reason = '缺少同日完整视图或批量复核证据'
            raw_pct = '--'
            status = 'source_pct_unavailable'
        if reason:
            pending.append({'date': fact, 'name': name, 'category': category, 'reason': reason})
            continue
        current.append({
            'date': fact, 'name': name, 'category': category,
            'pct': raw_pct[:-1] if raw_pct != '--' else '', 'raw_pct': raw_pct,
            'status': status, 'source': basis, 'views': '、'.join(sorted(views)),
            'read_at': '、'.join(dict.fromkeys(row['read_at'] for row in all_rows)),
        })
    return current, pending, single_view_verified


def commodity_reconcile(ui: Path, fact: str) -> tuple[list[dict], list[dict], int]:
    """Resolve only supported facts; return unverified names separately."""
    data = {key: load_csv(ui, key) for key in ('commodity', 'tail', 'category-list', 'one-view-recheck')}
    validate_rows(data, fact)
    return reconcile(merge_records(data), fact)


def atomic(path: Path, content: bytes) -> None:
    with NamedTemporaryFile(dir=path.parent, prefix='.' + path.name, delete=False) as handle:
        temp = Path(handle.name)
        try:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)


def read_batch(path: Path, fact: str) -> tuple[dict, str]:
    document = json.loads(path.read_text(encoding='utf-8'))
    payload, checksum = document['payload'], document['sha256']
    if digest(encode(payload)) != checksum or payload['date'] != fact or payload['batch_id'] != path.stem:
        raise ValueError(f'{path.name} 批次日期、身份或哈希不符')
    validate_rows(payload['rows'], fact)
    return payload, checksum


def save_batch(path: Path, fact: str, actor: str, batch_id: str, rows: dict, correction=None) -> None:
    payload = {
        'date': fact, 'input_by': actor, 'batch_id': batch_id, 'rows': rows,
        'committed_at': datetime.now(SHANGHAI).isoformat(timespec='seconds'),
    }
    if correction:
        payload['correction'] = correction
    document = {'payload': payload, 'sha256': digest(encode(payload))}
    atomic(path, encode(document) + b'\n')


def read_legacy(ui: Path, fact: str) -> dict:
    data = {key: load_csv(ui, key) for key in FIELDS if (ui / f'{key}.csv').is_file()}
    validate_rows(data, fact)
    if data:
        manifest = ui / 'SHA256SUMS'
        hashes = {}
        for line in manifest.read_text(encoding='utf-8').splitlines():
            sha, name = line.split('  ', 1)
            if name in hashes:
                raise ValueError(f'旧采集证据清单重复 {name}')
            hashes[name] = sha
        for key in data:
            name = f'{key}.csv'
            if hashes.get(name) != digest((ui / name).read_bytes()):
                raise ValueError(f'旧采集证据 {name} 的哈希不符')
    return data


def checkpoint_data(day: Path, fact: str) -> tuple[dict, dict, dict]:
    journal = day / 'checkpoints'
    base = journal / '.base.json'
    if not base.exists():
        return merge_records(read_legacy(day / 'ui-resume-evidence', fact)), {}, {}
    seed, _ = read_batch(base, fact)
    parts = [seed['rows']]
    batches = {path.stem: read_batch(path, fact) for path in sorted(journal.glob('*.json')) if path != base}
    receipts = {key: checksum for key, (_, checksum) in batches.items()}
    corrections = {}
    origins = []
    for key, (payload, _) in batches.items():
        correction = payload.get('correction')
        if not correction:
            origins.append(key)
            continue
        old = correction['supersedes']
        if (old == key or old in corrections or old not in receipts
                or receipts[old] != correction['supersedes_sha256'] or not correction['reason'].strip()):
            raise ValueError(f'{key} 更正批次的旧批次关系、原因或哈希无效')
        corrections[old] = {
            'replacement': key, 'sha256': receipts[key], 'reason': correction['reason'],
            'input_by': payload['input_by'], 'committed_at': payload['committed_at'],
        }
    applied = set()
    for origin in origins:
        active = origin
        while active in corrections:
            applied.add(active)
            active = corrections[active]['replacement']
        applied.add(active)
        parts.append(batches[active][0]['rows'])
    if applied != set(batches):
        raise ValueError('更正批次关系存在循环')
    return merge_records(*parts), receipts, corrections


def valid_tails(tails: list[dict], field: str, value: str, count: int) -> bool:
    records = [row for row in tails if row[field] == value]
    return bool(records) and all(row['unique_rows'] == str(count) for row in records)


def stock_slots(data: dict, completed: set[str]) -> set[str]:
    slots = set(STOCK_VIEWS)
    for view, (key, identity) in STOCK_VIEWS.items():
        rows = data.get(key, [])
        count = len({row[identity] for row in rows if row[identity]})
        tails = [row for row in data.get('tail', []) if row['tail'] == 'true']
        if len(rows) == count and valid_tails(tails, 'view', view, count):
            completed.add(view)
    header = data.get('header', [])
    slots.add('百日新高表头')
    if len(header) == 1 and all(header[0][key].isdigit() for key in ('total', 'new')):
        completed.add('百日新高表头')
    return slots


def alert_slots(data: dict, completed: set[str]) -> set[str]:
    slots = {f'异动:{section}' for section in ALERT_SECTIONS}
    for section in ALERT_SECTIONS:
        count = sum(row['section'] == section for row in data.get('alert-raw', []))
        tails = [row for row in data.get('alert-tail', []) if row['boundary']]
        if valid_tails(tails, 'section', section, count):
            completed.add(f'异动:{section}')
    slots.add('异动:重点监控更新时间')
    if any(row['title'] == '重点监控' and row['evidence'] for row in data.get('alert-headers', [])):
        completed.add('异动:重点监控更新时间')
    return slots


def etf_slots(data: dict, completed: set[str]) -> set[str]:
    wanted = {'涨幅榜', '申赎列', '动向', '跨境参考'}
    meta = defaultdict(list)
    for row in data.get('etf-meta', []):
        if row['scope'] and row['boundary']:
            key = '申赎列' if row['view'].endswith('申赎列') else row['view']
            meta[key].append(row)
    for key in ('涨幅榜', '申赎列'):
        if len(meta[key]) != 1:
            continue
        if key == '涨幅榜':
            rows = data.get('etf-market', [])
            counts = [sum(row['bucket'] == bucket for row in rows) for bucket in ('涨幅高', '涨幅低')]
        else:
            rows = data.get('etf-flows', [])
            counts = [
                sum(row['view'] == view and row['bucket'] == bucket for row in rows)
                for view in ('方向汇总', '申购赎回') for bucket in ('净申购', '净赎回')
            ]
        # Terminal evidence plus the full default capture count, or explicit dynamic counts.
        boundary = meta[key][0]['boundary']
        declared = re.search(r'分页条数\s*[=:：]\s*([0-9/]+)', boundary)
        if declared:
            expected = [int(v) for v in declared[1].split('/')]
        else:
            expected = [5] * len(counts)
        has_boundary = all(k == 5 for k in expected) or any(
            term in boundary for term in ('尾', '空榜', '来源未披露')
        )
        if counts == expected and all(0 <= k <= 5 for k in expected) and has_boundary:
            completed.add(f'ETF:{key}')
    if len(meta['动向']) == 1:
        boundary = meta['动向'][0]['boundary']
        counts = []
        for bucket in ('连续净申购', '连续净赎回'):
            count = re.search(rf'{bucket}(\d+)条|(\d+)条(?:{bucket}|{bucket[2:]})', boundary)
            actual = sum(row['bucket'] == bucket for row in data.get('etf-trends', []))
            counts.append(count is not None and int(count[1] or count[2]) == actual)
        if all(counts) and '尾' in boundary:
            completed.add('ETF:动向')
    if len(meta['跨境参考']) == 1:
        boundary = meta['跨境参考'][0]['boundary']
        if '已核实来源未披露' in boundary or (
            'T+2' in boundary and any('跨境T+2' in row['disclosure_status'] for row in data.get('etf-flows', []))
        ):
            completed.add('ETF:跨境参考')
    return {f'ETF:{key}' for key in wanted}


def resume_status(data: dict, receipts: dict, corrections: dict, fact: str) -> dict:
    expected, complete = commodity_views(data)
    _, pending, verified = reconcile(data, fact)
    completed = {f'商品:{view}' for view in complete}
    wanted = {f'商品:{view}' for view in expected} | {'商品:分类列表'}
    if len(expected) > 1:
        completed.add('商品:分类列表')
    wanted.update(stock_slots(data, completed))
    wanted.update(alert_slots(data, completed))
    wanted.update(etf_slots(data, completed))
    return {
        'fact_date': fact,
        'recorded_batches': receipts,
        'batch_corrections': corrections,
        'completed_slots': sorted(completed),
        'remaining_slots': sorted(wanted - completed),
        'pending_commodity': pending,
        'single_view_verified_count': verified,
        'row_counts': {key: len(rows) for key, rows in data.items()},
    }


def write_projection(day: Path, data: dict, fact: str) -> None:
    ui = day / 'ui-resume-evidence'
    ui.mkdir(exist_ok=True)
    hashes = []
    for key, rows in sorted(data.items()):
        content = StringIO(newline='')
        writer = csv.DictWriter(content, fieldnames=FIELDS[key].split())
        writer.writeheader()
        writer.writerows(rows)
        raw = content.getvalue().encode()
        name = f'{key}.csv'
        atomic(ui / name, raw)
        hashes.append(f'{digest(raw)}  {name}\n')
    atomic(ui / 'SHA256SUMS', ''.join(hashes).encode())
    _, pending, _ = reconcile(data, fact)
    content = StringIO(newline='')
    writer = csv.DictWriter(content, fieldnames=['date', 'name', 'category', 'reason'])
    writer.writeheader()
    writer.writerows(pending)
    atomic(day / 'pending-verification.csv', content.getvalue().encode())


def parse_pipe(raw: str, fact: str, current: dict) -> dict:
    """Read the pipe stream produced by the UI copy workflow."""
    sorted_summary = sorted(
        (row for row in current.get('commodity', []) if row['view'] == '精选'),
        key=lambda row: '|'.join((row['name'], row['category'], row['raw_pct'])),
    )
    result = defaultdict(list)
    for line in raw.splitlines():
        if not line.strip():
            continue
        kind, *values = line.split('|')
        if kind in {'compact', 'bulk', 'match'}:
            if kind == 'compact':
                view, category, stamp, packed = values
                for pair in packed.split('、'):
                    name, pct = pair.rsplit('@', 1)
                    add_pipe(result, 'commodity', fact, [view, name, category, pct if pct == '--' else pct + '%', stamp])
            elif kind == 'bulk':
                view, category, pct, names, stamp = values
                for name in names.split('、'):
                    add_pipe(result, 'commodity', fact, [view, name, category, pct, stamp])
            else:
                view, indices, stamp, fingerprint = values
                if not indices:
                    continue
                if not re.fullmatch(r'\d+(?:,\d+)*', indices):
                    raise ValueError('精选对照索引无效')
                group = [sorted_summary[int(i)] for i in indices.split(',')]
                packed = '\n'.join('|'.join((r['name'], r['category'], r['raw_pct'])) for r in group)
                hash_value = 2166136261
                for char in packed:
                    hash_value = ((hash_value ^ ord(char)) * 16777619) & 0xFFFFFFFF
                if f'{hash_value:08x}' != fingerprint:
                    raise ValueError('精选对照内容已变化，须重新核对索引')
                for row in group:
                    result['commodity'].append(dict(row, view=view, read_at=stamp))
        else:
            add_pipe(result, kind, fact, values)
    return dict(result)


def add_pipe(result: dict, key: str, fact: str, values: list[str]) -> None:
    if key not in FIELDS or len(values) != len(FIELDS[key].split()) - 1:
        raise ValueError(f'{key} 记录列数与采集契约不符')
    result[key].append(dict(zip(FIELDS[key].split(), [fact, *values])))


def change_checkpoint(args, day: Path, fact: str) -> dict:
    if not args.input_by.strip():
        raise ValueError('请求者不能为空')
    day.mkdir(parents=True, exist_ok=True)
    with (day / '.checkpoint.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if day.parent.name == '.staging' and (day.parent.parent / fact).exists():
            raise ValueError('本事实日已有正式归档，采集批次已冻结')
        if any((day / name).exists() for name in ('delivery-receipt.md', 'delivery-receipt.txt', 'delivery-attempt.md')):
            raise ValueError('此目录已有交付记录，应使用独立的采集目录')
        data, receipts, corrections = checkpoint_data(day, fact)
        journal = day / 'checkpoints'
        journal.mkdir(exist_ok=True)
        base = journal / '.base.json'
        if not base.exists():
            save_batch(base, fact, args.input_by, '.base', data)
        if args.action == 'record':
            if not BATCH_ID.fullmatch(args.batch_id):
                raise ValueError('批次编号最多80字符，须由文字、数字、下划线、点或短横组成')
            raw = sys.stdin.read() if args.input == '-' else Path(args.input).read_text(encoding='utf-8')
            rows = parse_pipe(raw, fact, data) if args.format == 'pipe' else json.loads(raw)
            validate_rows(rows, fact)
            if not any(rows.values()):
                raise ValueError('批次无证据')
            reason = (args.reason or '').strip()
            if (args.supersedes is not None or args.reason is not None) and not (
                args.supersedes and BATCH_ID.fullmatch(args.supersedes) and 0 < len(reason) <= 500
            ):
                raise ValueError('更正须提供旧批次编号和1到500字的原因')
            correction = None
            if args.supersedes:
                old = args.supersedes
                if old == args.batch_id or old not in receipts:
                    raise ValueError('旧批次不存在或与新批次编号相同')
                correction = {'supersedes': old, 'supersedes_sha256': receipts[old], 'reason': reason}
            path = journal / f'{args.batch_id}.json'
            if path.exists():
                previous, _ = read_batch(path, fact)
                if previous['rows'] != rows or previous.get('correction') != correction:
                    raise ValueError('该批次编号已有不同证据，须使用新编号保存复核')
            else:
                if correction:
                    if old in corrections:
                        raise ValueError('旧批次已被更正，请指定当前有效批次')
                    previous, _ = read_batch(journal / f'{old}.json', fact)
                    if previous['rows'].keys() - rows.keys():
                        raise ValueError('更正须重交旧批次的全部表，包括其余记录')
                save_batch(path, fact, args.input_by, args.batch_id, rows, correction)
            data, receipts, corrections = checkpoint_data(day, fact)
        write_projection(day, data, fact)
    return resume_status(data, receipts, corrections, fact)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    for action in ('status', 'record', 'materialize'):
        command = commands.add_parser(action)
        command.add_argument('--day-dir', required=True, type=Path)
        command.add_argument('--date', required=True)
        if action != 'status':
            command.add_argument('--input-by', required=True)
        if action == 'record':
            command.add_argument('--batch-id', required=True)
            command.add_argument('--input', required=True)
            command.add_argument('--format', choices=('json', 'pipe'), default='json')
            command.add_argument('--supersedes', help='更正时填需替换的当前有效批次编号')
            command.add_argument('--reason', help='更正原因和来源依据')
    args = parser.parse_args(argv)
    try:
        fact = date.fromisoformat(args.date).isoformat()
        if fact != args.date:
            raise ValueError('日期须为 YYYY-MM-DD')
        if args.action == 'status':
            data, receipts, corrections = checkpoint_data(args.day_dir, fact)
            state = resume_status(data, receipts, corrections, fact)
        else:
            state = change_checkpoint(args, args.day_dir, fact)
        print(json.dumps(state, ensure_ascii=False, indent=2))
    except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
        print(f'断点读取或写入失败：{exc}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
