"""盘中观察或收盘后回放宽基 ETF 自身的五分钟价量。"""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

from dotenv import load_dotenv

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
from services.broad_etf_minutes import _stamp, collect
from utils.network_env import without_standard_http_proxy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--scheduled-at')
    group.add_argument('--replay-day', action='store_true')
    parser.add_argument('--date')
    parser.add_argument('--input-by', required=True)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if bool(args.date) != args.replay_day:
        parser.error('--date 必须且仅与 --replay-day 合用')
    load_dotenv(SCRIPTS / '.env')
    try:
        day = args.date if args.replay_day else _stamp(args.scheduled_at).date().isoformat()
        with without_standard_http_proxy():
            result = collect(day, args.input_by, scheduled=args.scheduled_at, dry_run=args.dry_run)
    except (ValueError, KeyError, OSError, RuntimeError, sqlite3.Error) as exc:
        result = dict(status='source_failed', gap=str(exc))
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] in ('complete', 'skipped') else 2


if __name__ == '__main__':
    raise SystemExit(main())
