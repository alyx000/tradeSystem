"""补采指定日期区间连板池数量/成交额，仅更新对应盘后事实块并标准双写。"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.consecutive_pool_history import backfill


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start-date', required=True)
    parser.add_argument('--end-date', required=True)
    parser.add_argument('--input-by', required=True)
    args = parser.parse_args()
    if not args.input_by.strip():
        parser.error('--input-by不能为空')
    from main import load_config, setup_providers
    from utils.network_env import without_standard_http_proxy
    with without_standard_http_proxy():
        registry = setup_providers(load_config())
        registry.initialize_all()
        result = backfill(registry, args.start_date, args.end_date, input_by=args.input_by)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] == 'complete' else 2


if __name__ == '__main__':
    raise SystemExit(main())
