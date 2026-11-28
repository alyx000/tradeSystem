"""采集前一开放日核心与板块跟随股的全天分钟，保留逐股来源与缺口。"""
import argparse
from datetime import datetime
import json
import sqlite3
from pathlib import Path
import sys

from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))
from services.board_break_intraday.data import TZ
from services.course_core_minutes import capture_day, capture_with_previous, summary
from utils.network_env import without_standard_http_proxy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=datetime.now(TZ).date().isoformat())
    parser.add_argument("--input-by", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--retry-previous", action="store_true", help="当日完成后再补严格前一开放日已有日档")
    group.add_argument("--expected-prior-universe-sha", help="显式更正名单规则版本，核验原摘要并留存原档")
    args = parser.parse_args()
    load_dotenv(SCRIPT_DIR / ".env")
    try:
        with without_standard_http_proxy():
            if args.retry_previous:
                result = capture_with_previous(args.date, args.input_by, dry_run=args.dry_run)
            else:
                result = summary(capture_day(args.date, args.input_by, dry_run=args.dry_run,
                    expected_prior_universe_sha=args.expected_prior_universe_sha))
    except (ValueError, OSError, RuntimeError, sqlite3.Error) as exc:
        result = dict(status="source_failed", trade_date=args.date, gaps=[str(exc)])
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.json else None))
    return 0 if result["status"] in ("complete", "skipped") else 2


if __name__ == "__main__":
    raise SystemExit(main())
