"""CLI: 每日研报速读（A股研报评级 + 美股 yfinance 评级 → Top3 → MD + 钉钉）。

  research-digest daily [--date YYYY-MM-DD] [--dry-run] [--no-llm]

- 默认推送钉钉（对齐 volume-watch / recommend 语义，无 --push）。
- --dry-run：仅打印 markdown 到 stdout，不调 Antigravity、不落盘、不推送。
- --no-llm：关闭 LLM 叙事，纯结构化（A股本就默认关，此项额外关美股叙事）。
"""
from __future__ import annotations

import argparse
import datetime
import logging
import os
import sys

logger = logging.getLogger(__name__)

US_LOOKBACK_DAYS = 5


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
        if value <= 0:
            raise ValueError(raw)
        return value
    except ValueError:
        logger.warning("[research-digest] 忽略非法环境变量 %s=%r，使用默认值 %s", name, raw, default)
        return default


def _positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return value


def _env_choice(name: str, default: str, choices: set[str]) -> str:
    raw = os.getenv(name)
    if raw in choices:
        return raw
    if raw:
        logger.warning("[research-digest] 忽略非法环境变量 %s=%r，使用默认值 %s", name, raw, default)
    return default


def register_subparser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("research-digest", help="每日研报速读（A股+美股机构评级 Top3）")
    sub = p.add_subparsers(dest="research_digest_command")
    daily = sub.add_parser("daily", help="采集 + 渲染 + 落盘 + 推钉钉")
    daily.add_argument("--date", default=None, help="A股交易日 YYYY-MM-DD（默认最近交易日，跨周末回溯）")
    daily.add_argument("--dry-run", action="store_true", help="仅打印 markdown，不调 Antigravity、不落盘、不推送")
    daily.add_argument("--no-llm", action="store_true", help="关闭 LLM 叙事，纯结构化")
    daily.add_argument("--huibo-mode", choices=["desktop_terminal", "official_api", "off"],
                       default=_env_choice("HUIBO_MODE", "desktop_terminal",
                                           {"desktop_terminal", "official_api", "off"}),
                       help="慧博深读增强模式（默认 desktop_terminal；off 关闭）")
    daily.add_argument("--huibo-window-days", type=_positive_int, default=_env_int("HUIBO_WINDOW_DAYS", 5),
                       help="慧博候选/热点趋势窗口天数")
    daily.add_argument("--huibo-reader-cap", type=_positive_int, default=_env_int("HUIBO_READER_CAP", 20),
                       help="慧博派给 Antigravity reader 的候选上限")
    daily.add_argument("--huibo-reader-concurrency", type=_positive_int,
                       default=_env_int("HUIBO_READER_CONCURRENCY", 4),
                       help="慧博 Antigravity PDF reader 并发数")
    daily.add_argument("--huibo-recommend-cap", type=_positive_int, default=_env_int("HUIBO_RECOMMEND_CAP", 2),
                       help="慧博最终推荐研报上限")
    daily.add_argument("--huibo-raw-retention-days", type=_positive_int,
                       default=_env_int("HUIBO_RAW_RETENTION_DAYS", 30),
                       help="慧博原始文件/文本保留天数（最小 1，默认 30）")
    daily.add_argument("--huibo-summary-retention-days", type=_positive_int,
                       default=_env_int("HUIBO_SUMMARY_RETENTION_DAYS", 180),
                       help="慧博结构化摘要保留天数（最小 1，默认 180）")
    daily.add_argument("--huibo-cleanup-only", action="store_true",
                       help="只执行慧博本地存储清理，不采集/不推送")

    trend = sub.add_parser("trend", help="研报覆盖·申万一级行业趋势（占比多窗口对比，只读渲染）")
    trend.add_argument("--days", type=_positive_int, default=None,
                       help="读库最近 N 份日 payload（默认 recent-n×4，留空日余量）")
    trend.add_argument("--recent-n", type=_positive_int, default=5,
                       help="趋势窗口=近 N 个有效日 vs 前 N 个有效日（默认 5）")
    trend.add_argument("--top", type=_positive_int, default=10, help="行业展示条数上限（默认 10）")
    trend.add_argument("--backfill", type=_positive_int, default=None,
                       help="先回补最近 N 个交易日中库内缺失的日（逐日 cninfo，单日失败跳过不中断）")
    trend.add_argument("--json", action="store_true", help="输出 JSON（默认 markdown 表）")


def handle_command(config: dict, args: argparse.Namespace) -> None:
    sub = getattr(args, "research_digest_command", None)
    if sub == "daily":
        _run_daily(config, args)
    elif sub == "trend":
        _run_trend(config, args)
    else:
        print("用法：python main.py research-digest daily [--date YYYY-MM-DD] [--dry-run] [--no-llm]\n"
              "     python main.py research-digest trend [--days N] [--recent-n N] [--backfill N] [--json]",
              file=sys.stderr)
        sys.exit(2)


def _run_daily(config: dict, args: argparse.Namespace) -> None:
    from main import setup_providers
    from services.research_digest import huibo, narrator, run_daily_digest
    from utils.trade_date import get_prev_trade_date

    raw_dir = os.getenv("HUIBO_RAW_DIR", "data/reports/huibo/raw")
    summary_dir = os.getenv("HUIBO_SUMMARY_DIR", "data/reports/huibo/summaries")

    if args.huibo_cleanup_only:
        result = huibo.cleanup_storage(
            raw_dir,
            summary_dir,
            raw_retention_days=args.huibo_raw_retention_days,
            summary_retention_days=args.huibo_summary_retention_days,
            dry_run=args.dry_run,
        )
        action = "将清理" if args.dry_run else "已清理"
        print(f"[research-digest] 慧博 {action}: raw={len(result.raw_files)} summary={len(result.summary_files)}")
        return

    registry = setup_providers(config)
    registry.initialize_all()  # 必须：register 不自动初始化，漏了 provider 调用全返 not_initialized

    # A股交易日：显式 --date 优先；否则取最近交易日（周一/节后自动回溯）
    a_date = args.date or get_prev_trade_date(registry, datetime.date.today().isoformat())

    # dry-run 也不调 Antigravity（对齐 recommend/volume-watch）
    no_llm = bool(args.no_llm or args.dry_run)
    llm_runner = None if no_llm else narrator.build_antigravity_runner()
    huibo_source = huibo.build_source_from_env(args.huibo_mode)
    huibo_llm_runner = None if no_llm else huibo.build_role_runner(llm_runner)

    digest = run_daily_digest(
        registry, a_date,
        no_llm=no_llm,
        llm_runner=llm_runner,
        us_lookback_days=US_LOOKBACK_DAYS,
        huibo_mode=args.huibo_mode,
        huibo_source=huibo_source,
        huibo_summary_dir=summary_dir,
        huibo_window_days=args.huibo_window_days,
        huibo_reader_cap=args.huibo_reader_cap,
        huibo_reader_concurrency=args.huibo_reader_concurrency,
        huibo_recommend_cap=args.huibo_recommend_cap,
        huibo_llm_runner=huibo_llm_runner,
    )

    if args.dry_run:
        print(digest.markdown)
        logger.info("[research-digest] dry-run 完成（未落盘 / 未推送）")
        return

    from services.research_digest.renderer import write_md
    path = write_md(digest.markdown, a_date)
    logger.info("[research-digest] MD 落盘: %s", path)
    _push_to_dingtalk(digest.title, digest.markdown)
    try:
        cleanup = huibo.cleanup_storage(
            raw_dir,
            summary_dir,
            raw_retention_days=args.huibo_raw_retention_days,
            summary_retention_days=args.huibo_summary_retention_days,
            dry_run=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[research-digest] 慧博清理失败，已忽略: %s", exc)
    else:
        logger.info("[research-digest] 慧博清理完成 raw=%d summary=%d",
                    len(cleanup.raw_files), len(cleanup.summary_files))


def _run_trend(config: dict, args: argparse.Namespace) -> None:
    """研报覆盖·行业趋势：CLI 只做 wiring（provider/conn/参数）+ 打印；
    回补与聚合编排在 services.research_digest.trend（与 earnings-digest 的 service 分层对齐）。
    """
    import json as json_lib

    from main import setup_providers
    from db.connection import get_connection
    from services.research_digest import trend as trend_mod

    registry = setup_providers(config)
    registry.initialize_all()
    conn = get_connection()

    if args.backfill:
        stats = trend_mod.backfill_missing_payloads(conn, registry, args.backfill)
        print(f"[research-digest trend] 回补完成：新采 {stats['done']} / 已有跳过 {stats['skipped']}"
              f" / 失败 {stats['failed']}（目标 {stats['scanned']} 个交易日）")
        if stats["scanned"] < args.backfill:
            print(f"[research-digest trend] ⚠ 交易日历仅覆盖 {stats['scanned']}/{args.backfill} 个"
                  "目标交易日（日历缺失时先跑一次盘后采集或检查数据源），未覆盖日期未回补")

    result = trend_mod.run_trend_report(conn, registry, days=args.days, recent_n=args.recent_n)
    if args.json:
        print(json_lib.dumps(result, ensure_ascii=False, indent=1))
    else:
        print(trend_mod.render_trend_md(result, top_cap=args.top))


def _push_to_dingtalk(title: str, markdown: str) -> None:
    """走 DingTalkPusher 推送；token/secret 由 pusher.initialize() 从 env 读（CLI 直接实例化，不碰 multi.py）。"""
    from pushers.dingtalk_pusher import DingTalkPusher

    pusher = DingTalkPusher(config={})
    if not pusher.initialize():
        logger.error("[research-digest] DingTalk pusher 未启用（缺 env DINGTALK_WEBHOOK_TOKEN/SECRET），跳过推送")
        return
    ok = pusher.send_markdown(title=title, content=markdown)
    logger.info("[research-digest] 推送 %s", "成功" if ok else "失败")
