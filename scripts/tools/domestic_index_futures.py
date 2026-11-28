"""中金所IF、IM各月日行情、会员持仓增减与收盘基差。"""
import argparse
from datetime import date
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from services.domestic_index_futures import collect


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date',type=date.fromisoformat,required=True)
    parser.add_argument('--input-by',required=True)
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args()
    try:
        result=collect(args.date.isoformat(),args.input_by,dry_run=args.dry_run)
    except (KeyError,ValueError,OSError,sqlite3.Error) as exc:
        print(f'境内期指日档失败，{exc}',file=sys.stderr)
        return 2
    print(json.dumps(dict(date=result['date'],status=result['status'],reason=result.get('reason'),path=result.get('path'),
                          coverage={k:v['coverage'] for k,v in result.get('products',{}).items()}),ensure_ascii=False))
    return 0 if result['status'] in ('complete','skipped') else 2


if __name__=='__main__':
    raise SystemExit(main())
