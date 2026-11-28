"""盘中异动采样，显式 --push 时推送符合条件的钉钉短报。"""
import argparse
from datetime import date, datetime
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.intraday_events.day_summary import write_day_summary
from services.intraday_events.service import ROOT, run


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-by', required=True)
    parser.add_argument('--push', action='store_true', help='仅发送本批新触发的精简提醒')
    parser.add_argument('--scheduled-at', type=datetime.fromisoformat, help='本机定时入口的计划时刻，含上海时区')
    parser.add_argument('--summarize-date', type=date.fromisoformat, help='从指定日期本地归档生成关键位合并摘要')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--interval-minutes', type=int, choices=(5, 10), default=5, help='采样周期及相邻快照比较口径')
    parser.add_argument('--watch', action='store_true', help='当前终端按指定周期采样，到休市/收盘或样本上限停止')
    parser.add_argument('--samples', type=int, default=48, help='watch最多轮数，1—48')
    args = parser.parse_args(argv)
    if not args.input_by.strip() or not 1 <= args.samples <= 48:
        parser.error('请求者不能为空；samples必须为1—48')
    if args.push and (args.dry_run or args.interval_minutes != 10):
        parser.error('推送须使用 10 分钟实采模式')
    if args.dry_run and args.watch:
        parser.error('dry-run不能与watch合用')
    if args.summarize_date:
        if args.push or args.dry_run or args.watch or args.scheduled_at:
            parser.error('本地日期汇总不能与采样模式合用')
        try:
            path = write_day_summary(ROOT/args.summarize_date.isoformat(), args.input_by)
        except (OSError, ValueError) as exc:
            parser.exit(1, f'{exc}\n')
        print(path)
        return 0
    if args.scheduled_at and (not args.push or args.watch or args.scheduled_at.tzinfo is None):
        parser.error('计划时刻仅用于单次正式采样，须提供时区')
    from main import load_config, setup_providers
    registry = setup_providers(load_config())
    exit_code = 0
    for index in range(args.samples if args.watch else 1):
        started = time.monotonic()
        result = run(registry, input_by=args.input_by, dry_run=args.dry_run, interval_minutes=args.interval_minutes,
                     push=args.push, scheduled_at=args.scheduled_at)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)
        if result['status'] not in ('complete', 'skipped') or result.get('delivery') in ('unconfirmed', 'unconfigured'):
            exit_code = 1
        if args.interval_minutes == 10 and result.get('report_path') and not result.get('level_summary_path'):
            exit_code = 1
        if (not args.watch or result.get('reason_code') in ('outside_session', 'non_trade_day')
                or index == args.samples-1):
            break
        if result.get('reason_code') == 'too_soon':
            time.sleep(max(0, result['retry_after_seconds']))
        else:
            time.sleep(max(0, 60*args.interval_minutes-(time.monotonic()-started)))
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
