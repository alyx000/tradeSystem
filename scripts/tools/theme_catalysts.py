"""采集前夜重点题材的金十快讯和原发来源时间证据。"""
import argparse
from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from db.connection import get_readonly_connection
from services.board_break_intraday.data import TZ
from services.handwritten_review import valid_date
from services.theme_catalysts import collect, stamp


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', type=valid_date, default=datetime.now(TZ).date().isoformat())
    parser.add_argument('--input-by', required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--archive', action='store_true', help='只读同日宏观快讯归档，保留最迟已知时间')
    mode.add_argument('--scheduled-at', help='上海十分钟调度的带时区计划时刻')
    args = parser.parse_args(argv)
    if not args.input_by.strip():
        parser.error('--input-by 不得为空')
    root = SCRIPTS.parent
    try:
        if args.scheduled_at:
            stamp(args.scheduled_at)
        with closing(get_readonly_connection(root / 'data/trade.db')) as conn:
            result = collect(conn, root, args.date, args.input_by.strip(), archive=args.archive,
                             scheduled_at=args.scheduled_at)
    except (ValueError, KeyError, OSError, sqlite3.Error) as exc:
        print(f'题材新闻采集失败，{exc}', file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] in {'complete', 'no_selected_themes', 'skipped'} else 2


if __name__ == '__main__':
    raise SystemExit(main())
