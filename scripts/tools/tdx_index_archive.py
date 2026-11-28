#!/usr/bin/env python3
"""只补指定已收盘开放日的两个通达信指数5分钟档案。"""
import argparse
from datetime import date
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from providers.tdx_provider import TdxProvider
from services.tdx_index_archive import run_archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, type=date.fromisoformat)
    parser.add_argument("--input-by", required=True)
    args = parser.parse_args()
    result = run_archive(TdxProvider(), day=str(args.date), input_by=args.input_by)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] in {"complete","non_trade_day"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
