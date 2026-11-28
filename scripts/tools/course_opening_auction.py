"""采集前夜核心和同题材昨日涨停样本的09:25最终开盘集合竞价成交。"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys

from dotenv import load_dotenv

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
from services.board_break_intraday.data import TZ
from services.course_opening_auction import capture, summary
from utils.network_env import without_standard_http_proxy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=datetime.now(TZ).date().isoformat())
    parser.add_argument("--input-by", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    load_dotenv(SCRIPTS / ".env")
    try:
        with without_standard_http_proxy():
            result = summary(capture(args.date, args.input_by, dry_run=args.dry_run))
    except (ValueError, OSError, RuntimeError, sqlite3.Error) as exc:
        result = dict(trade_date=args.date, status="source_failed", gaps=[str(exc)])
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.json else None))
    return 0 if result["status"] in ("complete","skipped") else 2


if __name__ == "__main__":
    raise SystemExit(main())
