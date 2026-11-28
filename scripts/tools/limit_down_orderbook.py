"""从指定开放日原始十分钟行情及逐股参考限价复核跌停盘口观察。"""
import argparse
from datetime import date
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.intraday_events.limit_down_book import replay_day


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', required=True, type=date.fromisoformat)
    parser.add_argument('--input-by', required=True)
    args = parser.parse_args()
    if not args.input_by.strip():
        parser.error('请求者不能为空')
    try:
        receipt = replay_day(args.date.isoformat(), args.input_by.strip())
    except (ValueError, OSError) as exc:
        parser.exit(1, str(exc)+'\n')
    print(json.dumps(receipt, ensure_ascii=False))
    return int(receipt['status'] != 'complete')


if __name__ == '__main__':
    raise SystemExit(main())
