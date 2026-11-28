"""用本地十分钟行情回看前夜题材与点名篮子的扩散。"""
import argparse
from contextlib import closing
import fcntl
import json
from pathlib import Path
import sqlite3
import sys

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from db.connection import get_readonly_connection
from services.board_break_intraday.data import digest
from services.handwritten_review import valid_date
from services.intraday_summary.state import atomic_write_text
from services.theme_intraday_breadth import SCHEMA, build


def _coverage(result):
    items = {('batch', r['slot']) for r in result['batches']}
    for key, group in result['groups'].items():
        items.add(('group', key, digest(group['members']), digest(group['source'])))
        for p in group['series']:
            items.update((key, p['captured_at'], code) for code in p['pct_chg_by_code'])
            items.update((key, p['captured_at'], 'amount', code) for code in p.get('amount_delta_by_code_yuan', {}))
            items.update((key, p['captured_at'], 'amount-count', i) for i in range(p['amount_valid']))
            if p['full_amount_delta_yuan'] is not None:
                items.add((key, p['captured_at'], 'full-amount'))
    return items


def save(path, result):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix('.lock').open('a+') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if path.exists():
            old = json.loads(path.read_bytes())
            if (old.get('sha256') != digest({k:v for k,v in old.items() if k!='sha256'})
                    or old.get('schema') != SCHEMA or old.get('trade_date') != result['trade_date']):
                raise ValueError('已有同日归档无法核验，已保留原件')
            if not _coverage(old) <= _coverage(result):
                receipt = dict(status='fallback_preserved', input_by=result['input_by'],
                               attempted_status=result['status'], attempted_sha256=digest(result), archived_sha256=old['sha256'],
                               gaps=result['gaps'])
                atomic_write_text(path.with_suffix('.last-attempt.json'), json.dumps(receipt, ensure_ascii=False)+'\n')
                return 'fallback_preserved'
        output = dict(result, sha256=digest(result))
        atomic_write_text(path, json.dumps(output, ensure_ascii=False, allow_nan=False, indent=2)+'\n')
    return 'saved'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', required=True, type=valid_date)
    parser.add_argument('--input-by', required=True)
    parser.add_argument('--save', action='store_true', help='原子保存每日派生报告')
    parser.add_argument('--json', action='store_true', help='输出完整样本、时点及收据')
    args = parser.parse_args(argv)
    if not args.input_by.strip():
        parser.error('--input-by 不得为空')
    root = SCRIPTS.parent
    try:
        with closing(get_readonly_connection(root/'data/trade.db')) as conn:
            result = build(conn, root, args.date, args.input_by.strip())
        if args.save and result['status'] != 'skipped':
            path = root/'data/reports/theme-intraday-breadth'/(args.date+'.json')
            print(save(path, result), path, file=sys.stderr)
    except (OSError, ValueError, KeyError, sqlite3.Error) as exc:
        print(f'题材盘中扩散来源失败：{exc}', file=sys.stderr)
        return 2
    summary = dict(trade_date=args.date, status=result['status'], batches=len(result['batches']),
                   full_concepts=sum(g['kind']=='previous_session_full_concept' for g in result['groups'].values()),
                   named_samples=sum(g['kind']=='named_core_and_peers' for g in result['groups'].values()),
                   gaps=result['gaps'])
    print(json.dumps(result if args.json else summary, ensure_ascii=False, allow_nan=False))
    return 2 if result['status'] == 'source_failed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
