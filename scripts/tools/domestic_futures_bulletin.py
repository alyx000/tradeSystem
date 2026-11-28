"""从已保存的博易公开网页全文导入IF/IM二级行情参考。"""
import argparse
from datetime import date
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.domestic_futures_bulletin import import_file


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--web-text-file', type=Path, required=True)
    parser.add_argument('--date', type=date.fromisoformat, required=True)
    parser.add_argument('--input-by', required=True)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    try:
        r = import_file(args.web_text_file, args.date.isoformat(), args.input_by, dry_run=args.dry_run)
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        parser.exit(2, f'股指期货转载行情导入失败，{exc}\n')
    print(json.dumps(dict(status=r['status'], date=r['trade_date'], product=r['product'], path=r['path'],
                          contracts=len(r['contracts']), basis_points=r['basis_points'],
                          raw_sha256=r['source']['raw_sha256'], reused=r['reused']), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
