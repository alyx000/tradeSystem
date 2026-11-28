"""归档A股盘中同槽的NQ、SGX A50与恒生科技现货分钟证据。"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
from services.course_cross_market import TZ, collect, replay_day, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scheduled-at', help='定时槽位，ISO 8601并带时区')
    parser.add_argument('--date', help='上海日期 YYYY-MM-DD')
    parser.add_argument('--slot', help='上海槽位 HH:MM')
    parser.add_argument('--input-by', required=True)
    parser.add_argument('--replay', action='store_true')
    parser.add_argument('--replay-day', action='store_true', help='收盘后按日共享三路源响应，回放所有槽位')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    try:
        if args.replay_day:
            if not args.date or args.slot or args.scheduled_at or args.replay:
                parser.error('--replay-day 只与 --date 配合')
            result = replay_day(args.date, args.input_by, dry_run=args.dry_run)
        elif args.scheduled_at and not (args.date or args.slot):
            slot = datetime.fromisoformat(args.scheduled_at)
        elif args.date and args.slot and not args.scheduled_at:
            slot = datetime.strptime(args.date+' '+args.slot, '%Y-%m-%d %H:%M').replace(tzinfo=TZ)
        else:
            parser.error('请使用 --scheduled-at 或同时使用 --date 和 --slot')
        if not args.replay_day:
            result = collect(slot, args.input_by, replay=args.replay, dry_run=args.dry_run)
    except (ValueError, OSError, sqlite3.Error, KeyError) as exc:
        result = dict(status='source_failed', gap=f'{type(exc).__name__}: {exc}')
    print(json.dumps(result if args.json else summary(result), ensure_ascii=False, indent=2 if args.json else None))
    return 0 if result['status'] in ('complete','skipped') else 2


if __name__ == '__main__':
    raise SystemExit(main())
