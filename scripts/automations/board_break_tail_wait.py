"""Start the scheduled intraday scan as soon as its market-data cutoff is reached."""

import argparse
from datetime import datetime, timedelta
from functools import partial
import os
from pathlib import Path
import sys
from time import sleep
from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo("Asia/Shanghai")
CUTOFF_TIMES = {"tail": (14, 50), "open-review": (9, 40)}
MAX_WAIT = timedelta(minutes=12)  # Wake seven minutes early, with up to five minutes of jitter.
MAIN = Path(__file__).resolve().parents[1] / "main.py"


def wait_for_cutoff(clock, pause, stage="tail"):
    now = clock()
    hour, minute = CUTOFF_TIMES[stage]
    cutoff = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    while now < cutoff:
        remaining = cutoff - now
        if remaining > MAX_WAIT:
            raise RuntimeError(f"早于北京时间{cutoff - MAX_WAIT:%H:%M}，未启动计时")
        pause(min(remaining.total_seconds(), 2))
        now = clock()
        if now.date() != cutoff.date():
            raise RuntimeError("等待期间已跨日，未启动盘中扫描")
    return now


def main(argv=None):
    parser = argparse.ArgumentParser(description="按北京时间等待尾盘或开盘复核时点", allow_abbrev=False)
    parser.add_argument("--stage", choices=CUTOFF_TIMES, default="tail", help="tail 为 14:50 尾盘，open-review 为 09:40 开盘复核")
    parser.add_argument("--push", action="store_true", help="将本次正式扫描结果推送到钉钉")
    args = parser.parse_args(argv)
    clock = partial(datetime.now, SHANGHAI)
    print(f"{args.stage} 计时器启动 {clock().isoformat()}", file=sys.stderr, flush=True)
    try:
        started = wait_for_cutoff(clock, sleep, args.stage)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"交给 {args.stage} CLI {started.isoformat()}", file=sys.stderr, flush=True)
    os.execv(sys.executable, [
        sys.executable, str(MAIN), "board-break", args.stage,
        "--input-by", "USER", "--push" if args.push else "--no-push", "--json",
    ])


if __name__ == "__main__":
    raise SystemExit(main())
