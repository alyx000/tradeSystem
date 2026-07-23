"""CLI: 宏观快讯采集速读(金十源)。

  macro-flash run    [--date YYYY-MM-DD] [--lookback-hours N] [--dry-run|--no-push]
                     [--force-refresh] [--repush]
  macro-flash show   [--date YYYY-MM-DD] [--json]
  macro-flash doctor [--json]

run:采集→筛选→归档(manifest/flash_raw/digest)→推钉钉;同日已 complete 幂等跳过。
show:只读展示既有归档(状态行先行);--json 输出 manifest(含 candidates,供入库确认)。
doctor:live 探测金十可达性与必需字段。入库不在本命令:走 record-notes → db add-macro。
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys

from services.macro_flash import service


def _iso_date(value: str) -> str:
    """argparse type 回调:边缘校验日期格式,免 service 层 ValueError 裸 traceback
    (且避免非法日期先白跑一段;同 value_watch / sector_crowding gate-1 先例)。"""
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"日期须为 YYYY-MM-DD 格式: {value!r}")
    return value


def register_subparser(subparsers: argparse._SubParsersAction) -> None:
    mf = subparsers.add_parser("macro-flash", help="宏观快讯采集速读(金十源:归档+钉钉)")
    sub = mf.add_subparsers(dest="macro_flash_command")

    run_p = sub.add_parser("run", help="采集→筛选→归档→推送")
    run_p.add_argument("--date", default=None, type=_iso_date,
                       help="窗口终点所在日 YYYY-MM-DD(补跑用,终点固定取该日 16:30;"
                            "周日档补跑需配 --lookback-hours 54,终点与实时档 22:00 有 5.5h 差,接受)")
    run_p.add_argument("--lookback-hours", type=int, default=service.DEFAULT_LOOKBACK_HOURS,
                       help="回溯小时数(默认 24;周日档 54)")
    run_p.add_argument("--dry-run", action="store_true", help="不写不推,仅打印速读")
    run_p.add_argument("--no-push", action="store_true", help="归档不推送(补跑防重推)")
    run_p.add_argument("--force-refresh", action="store_true",
                       help="忽略已有 complete 归档,强制重采")
    run_p.add_argument("--repush", action="store_true", help="仅重推已有 digest,不重采")

    show_p = sub.add_parser("show", help="只读展示既有归档")
    show_p.add_argument("--date", default=None, type=_iso_date, help="归档日 YYYY-MM-DD(默认今天)")
    show_p.add_argument("--json", action="store_true", help="输出 manifest JSON(含 candidates)")

    doc_p = sub.add_parser("doctor", help="live 探测金十可达性与字段")
    doc_p.add_argument("--json", action="store_true")


def handle_command(config: dict, args: argparse.Namespace) -> None:
    sub = getattr(args, "macro_flash_command", None)
    if sub == "run":
        outcome = service.run(
            config, date_str=args.date, lookback_hours=args.lookback_hours,
            dry_run=args.dry_run, no_push=args.no_push,
            force_refresh=args.force_refresh, repush=args.repush)
        if outcome.exit_code != 0:
            sys.exit(outcome.exit_code)
    elif sub == "show":
        _show(args)
    elif sub == "doctor":
        _doctor(args)
    else:
        print("用法:python main.py macro-flash run|show|doctor [选项]", file=sys.stderr)
        sys.exit(2)


def _show(args: argparse.Namespace) -> None:
    # 默认"今天"按 Asia/Shanghai 取,不随系统时区漂移(与 service 时间契约一致)
    date_str = args.date or datetime.datetime.now(service.TZ).date().isoformat()
    manifest = service.read_manifest(date_str)
    if manifest is None:
        print(f"{date_str} 无 macro-flash 归档。", file=sys.stderr)
        sys.exit(1)
    if args.json:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return
    print(f"状态 {manifest['source_status']}"
          f" · 窗口 {manifest['window_start']} → {manifest['window_end']}"
          f" · 原始 {manifest['raw_count']} · 命中 {manifest['matched_count']}"
          f" · 推送 {manifest['push_status']}")
    digest = service.BASE_DIR / date_str / "digest.md"
    if digest.exists():
        print()
        print(digest.read_text(encoding="utf-8"))


def _doctor(args: argparse.Namespace) -> None:
    report = service.doctor()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        if report["ok"]:
            print(f"✅ 金十可达:{report['rows']} 条/页,"
                  f"必需字段 {'OK' if report['required_fields_ok'] else '⚠️ 漂移'},"
                  f"样本时间 {report['sample_time']}")
        else:
            print(f"❌ 金十不可达:{report['error']}")
    if not report["ok"] or not report.get("required_fields_ok"):
        sys.exit(1)
