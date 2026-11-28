"""用同日冻结的成分与盘中已签收行情复核银行和证券。"""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
from db.connection import get_readonly_connection
from services.bank_broker_intraday import FOLDER, build, save
from services.handwritten_review import valid_date


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', required=True, type=valid_date)
    parser.add_argument('--input-by', required=True)
    parser.add_argument('--save', action='store_true')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    if not args.input_by.strip():
        parser.error('--input-by 不得为空')
    data = SCRIPTS.parent / 'data'
    try:
        with closing(get_readonly_connection(data / 'trade.db')) as conn:
            report = build(conn, data / 'reports', args.date, args.input_by.strip())
        fields = ('status', 'trade_date', 'observed_slots', 'complete_slots', 'gaps')
        summary = {k: report[k] for k in fields}
        if args.save and report['status'] != 'skipped':
            summary['write_status'] = save(data / 'reports' / FOLDER, report)
            summary['path'] = str(data / 'reports' / FOLDER / (args.date + '.json'))
            summary['html_path'] = str(data / 'reports' / FOLDER / (args.date + '.html'))
        print(json.dumps(report if args.json else summary, ensure_ascii=False))
        return 0 if report['status'] in ('skipped', 'complete') and summary.get('write_status') != 'fallback_preserved' else 2
    except (ValueError, OSError, KeyError, TypeError, AttributeError, sqlite3.Error) as exc:
        print(f'银行与证券盘中来源未核验：{exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
