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
import hashlib
import json
import re
import sys

from services.macro_flash import collector, service


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _iso_date(value: str) -> str:
    """argparse type 回调:严格校验 YYYY-MM-DD(regex 挡掉 3.11+ 的紧凑/周日期形式),
    返回规范化 isoformat;免 service 层 ValueError 裸 traceback 与归档路径漂移。"""
    if not _DATE_RE.match(value):
        raise argparse.ArgumentTypeError(f"日期须为 YYYY-MM-DD 格式: {value!r}")
    try:
        return datetime.date.fromisoformat(value).isoformat()
    except ValueError:
        raise argparse.ArgumentTypeError(f"非法日期: {value!r}")


def _positive_int(value: str) -> int:
    """argparse type 回调:回溯小时数须为正整数,挡掉 0/负数导致的窗口反转。"""
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"须为整数: {value!r}")
    if n <= 0:
        raise argparse.ArgumentTypeError(f"回溯小时数须为正: {value!r}")
    return n


def register_subparser(subparsers: argparse._SubParsersAction) -> None:
    mf = subparsers.add_parser("macro-flash", help="宏观快讯采集速读(金十源:归档+钉钉)")
    sub = mf.add_subparsers(dest="macro_flash_command")

    run_p = sub.add_parser("run", help="采集→筛选→归档→推送")
    run_p.add_argument("--date", default=None, type=_iso_date,
                       help="窗口终点所在日 YYYY-MM-DD(补跑用,终点固定取该日 20:00;"
                            "周日档补跑需配 --lookback-hours 54,终点与实时档 22:00 有 2h 差,接受)")
    run_p.add_argument("--lookback-hours", type=_positive_int, default=service.DEFAULT_LOOKBACK_HOURS,
                       help="回溯小时数(默认 24;周日档 54)")
    mode = run_p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="不写不推,仅打印速读")
    mode.add_argument("--no-push", action="store_true", help="归档不推送(补跑防重推)")
    mode.add_argument("--repush", action="store_true", help="仅重推已有 digest,不重采")
    run_p.add_argument("--force-refresh", action="store_true",
                       help="忽略已有 complete 归档,强制重采(不可与 --repush 同用)")

    show_p = sub.add_parser("show", help="只读展示既有归档")
    show_p.add_argument("--date", default=None, type=_iso_date, help="归档日 YYYY-MM-DD(默认今天)")
    show_p.add_argument("--json", action="store_true", help="输出 manifest JSON(含 candidates)")

    doc_p = sub.add_parser("doctor", help="live 探测金十可达性与字段")
    doc_p.add_argument("--json", action="store_true")


def handle_command(config: dict, args: argparse.Namespace) -> None:
    sub = getattr(args, "macro_flash_command", None)
    if sub == "run":
        if args.repush and args.force_refresh:
            print("--repush 与 --force-refresh 互斥(重推既有 vs 强制重采)", file=sys.stderr)
            sys.exit(2)
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
    print(f"状态 {manifest.get('source_status')}"
          f" · 窗口 {manifest.get('window_start')} → {manifest.get('window_end')}"
          f" · 原始 {manifest.get('raw_count', '-')} · 命中 {manifest.get('matched_count', '-')}"
          f" · 推送 {manifest.get('push_status')}")
    if manifest.get("error"):
        print(f"错误:{manifest['error']}")
    source_status = manifest.get("source_status")
    digest_rec = (manifest.get("files") or {}).get("digest") or {}
    digest_path = service.BASE_DIR / date_str / "digest.md"
    # 仅 complete 归档的正文可作为当期有效快讯供人工/Agent 确认入库;
    # 任何非 complete(source_failed/truncated/stalled/drift/run_error)可能是失败伪装成空
    # 或部分不完整,只给状态+警告,不展示正文,避免误确认。
    if source_status != collector.STATUS_COMPLETE:
        print(f"(归档状态 {source_status} 非 complete,正文可能失败伪装成空或不完整,不展示;"
              f"用 --json 查 manifest,或 macro-flash run --force-refresh 重采)", file=sys.stderr)
    elif digest_rec.get("sha256") and digest_path.exists():
        body = digest_path.read_text(encoding="utf-8")
        if hashlib.sha256(body.encode("utf-8")).hexdigest() == digest_rec["sha256"]:
            print()
            print(body)
        else:
            print("(digest.md 与 manifest sha 不符,疑似撕裂/篡改,不展示正文;用 --json 查 manifest)",
                  file=sys.stderr)
    else:
        print("(该归档无有效 digest 记录,仅展示状态)", file=sys.stderr)


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
