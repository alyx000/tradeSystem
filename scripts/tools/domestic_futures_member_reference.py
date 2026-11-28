"""导入指定交易日的公开单会员股指期货持仓参考。"""
import argparse
from datetime import date
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.domestic_futures_member_reference import import_file


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--web-text-file', type=Path, required=True)
    parser.add_argument('--date', type=date.fromisoformat, required=True)
    parser.add_argument('--input-by', required=True)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    try:
        result = import_file(args.web_text_file, args.date.isoformat(), args.input_by, dry_run=args.dry_run)
    except (OSError, ValueError, KeyError, sqlite3.Error) as exc:
        parser.exit(2, f'会员持仓参考导入失败，{exc}\n')
    print(json.dumps({k: result[k] for k in ('trade_date','identity','row','verified_changes','path','reused')},ensure_ascii=False))


if __name__ == '__main__':
    main()
