"""CLI: 趋势主升漏斗扫描（盘后 EOD 只读观察清单 + 持久化观察池）。

  trend-leader daily [--date YYYY-MM-DD] [--sectors '["半导体",...]'] [--top-k N]
                     [--main-line hybrid|l2|l2+concept] [--no-llm] [--dry-run] [--no-push]
  trend-leader pool  [--status active|exited] [--json]

daily：涨停∩主线 → 首次涨停加速+缓涨 → 入池/维护/退池 → 渲染 → 推钉钉。
  · 裸 daily：落池 + 推送。
  · --no-push：落池 + 仅打印（软上线/排障）。
  · --dry-run：**内存副本**跑，不落池、不推送（历史校准安全，不污染真实池）。
pool：只读看池（--json 输出结构化；默认 Markdown 表）。

红线：只读观察清单，标 [判断]，不出价位、不写计划层。
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import sqlite3
import sys

from db.connection import get_connection
from services.trend_leader import constants as C
from services.trend_leader import pool, renderer, scanner

logger = logging.getLogger(__name__)


def _positive_int(raw: str) -> int:
    """--top-k 必须为正整数：否则 [:top_k] 切片会静默扩成「全部除末尾」等异常主线池。"""
    try:
        v = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("必须为正整数") from exc
    if v <= 0:
        raise argparse.ArgumentTypeError("必须为正整数")
    return v


def register_subparser(subparsers: argparse._SubParsersAction) -> None:
    tl = subparsers.add_parser("trend-leader", help="趋势主升漏斗扫描（观察清单 / 看池）")
    sub = tl.add_subparsers(dest="trend_leader_command")

    daily = sub.add_parser("daily", help="涨停∩主线→检测→入池/退池→渲染→推钉钉")
    daily.add_argument("--date", default=None, help="交易日 YYYY-MM-DD（默认今天）")
    daily.add_argument("--sectors", default=None,
                       help='手工主线板块 JSON 数组，∪ 自动 Top-K（如 \'["半导体","玻璃玻纤"]\'）')
    daily.add_argument("--top-k", type=_positive_int, default=C.DEFAULT_TOP_K_SECTORS,
                       help=f"自动主线取成交额集中度 Top-K 申万二级（正整数，默认 {C.DEFAULT_TOP_K_SECTORS}）")
    daily.add_argument("--main-line", default="hybrid", choices=["hybrid", "l2", "l2+concept"],
                       help=("主线口径：hybrid=申万二级∪同花顺概念并用LLM过滤概念（默认）；"
                             "l2=仅申万二级；l2+concept=机械∪同花顺概念资金净流入 Top-M 分支"))
    daily.add_argument("--top-concepts", type=_positive_int, default=C.DEFAULT_TOP_CONCEPTS,
                       help=f"概念分支取资金净流入 Top-M（正整数，默认 {C.DEFAULT_TOP_CONCEPTS}，hybrid/l2+concept 生效）")
    daily.add_argument("--no-llm", action="store_true",
                       help="hybrid 模式下禁用 LLM 概念过滤，降级为确定性 l2+concept")
    daily.add_argument("--dry-run", action="store_true",
                       help="内存副本跑，不落池/不推送（历史校准用）")
    daily.add_argument("--no-push", action="store_true", help="落池但仅打印，不推送")

    p = sub.add_parser("pool", help="只读看观察池")
    p.add_argument("--status", default=None, choices=["active", "exited"],
                   help="按状态过滤（默认全部）")
    p.add_argument("--json", action="store_true", help="输出 JSON")


def handle_command(config: dict, args: argparse.Namespace) -> None:
    sub = getattr(args, "trend_leader_command", None)
    if sub == "daily":
        _run_daily(config, args)
    elif sub == "pool":
        _run_pool(config, args)
    else:
        print("用法：python main.py trend-leader daily|pool [...]", file=sys.stderr)
        sys.exit(2)


def _today() -> str:
    return datetime.date.today().isoformat()


def _parse_sectors(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    try:
        val = json.loads(raw)
    except json.JSONDecodeError:
        print(f"--sectors 不是合法 JSON：{raw}", file=sys.stderr)
        sys.exit(2)
    if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
        print("--sectors 必须是字符串 JSON 数组，如 '[\"半导体\"]'", file=sys.stderr)
        sys.exit(2)
    return val


def _run_daily(config: dict, args: argparse.Namespace) -> None:
    from main import setup_providers

    date = args.date or _today()
    sectors = _parse_sectors(args.sectors)
    mainline_llm_runner = _mainline_llm_runner(args)
    registry = setup_providers(config)
    registry.initialize_all()  # register 不自动初始化，漏了所有 provider 调用返 provider_not_initialized

    real_conn = get_connection()
    try:
        if args.dry_run:
            # dry-run：把真实库 backup 到内存副本再跑，pool 写操作落到丢弃的内存库 → 真无副作用。
            # 不用 scanner persist=False：scanner 有 read-after-write（Pass1 record 后 Pass2 须读到新池、
            # renderer 再读一次），no-op 写会让 dry-run 读到陈旧池、与真实跑不一致。
            # 也不能靠 rollback：pool.py 每次写内部已 commit。故用「可写但丢弃」的内存副本隔离。
            # 刻意不套非交易日守卫：dry-run 不落池/不推送，非交易日也无污染面；且守卫的日历预取会写库，
            # 套在真实库上会破坏 dry-run「真无副作用」契约（codex 门2 finding，2026-06-21）。
            mem = sqlite3.connect(":memory:")
            mem.row_factory = sqlite3.Row
            real_conn.backup(mem)
            try:
                summary = scanner.run_daily(mem, registry, date, sectors=sectors, top_k=args.top_k,
                                        main_line=args.main_line, top_concepts=args.top_concepts,
                                        mainline_llm_runner=mainline_llm_runner)
                md = renderer.render_daily(mem, summary)
            finally:
                mem.close()
        else:
            # 裸跑/no-push 都会落池（no-push 仅不推送、仍写池），非交易日必须守卫，否则降级源
            # 会用上一交易日榜单按当日落池（裸跑还会误推送）。守卫复用 utils.trade_date.is_non_trading_day。
            # 刻意不套 without_standard_http_proxy()（codex 反驳）：本文件 scanner 的 provider 调用历来
            # 也在 no-proxy 外且生产可达，只给守卫套会自相矛盾；周末经 weekday 兜底必拦，fail-open 与
            # main.py 一致。给整个 scanner 套 no-proxy 属覆盖全模块的独立加固，不在本守卫范围。
            from utils.trade_date import is_non_trading_day
            if is_non_trading_day(real_conn, registry, date):
                logger.warning("⚠️ %s 为非交易日（周末/法定假日），跳过趋势主升扫描（不落池、不推送）", date)
                return
            summary = scanner.run_daily(real_conn, registry, date, sectors=sectors, top_k=args.top_k,
                                        main_line=args.main_line, top_concepts=args.top_concepts,
                                        mainline_llm_runner=mainline_llm_runner)
            md = renderer.render_daily(real_conn, summary)
    finally:
        real_conn.close()

    print(md)  # 始终打印（校准/排障）
    if args.dry_run:
        logger.info("[trend-leader daily] dry-run（内存副本，未落池/未推送）完成")
        return
    if args.no_push:
        logger.info("[trend-leader daily] --no-push：已落池，未推送")
        return
    _push_to_dingtalk(f"趋势主升观察清单 · {date}", md)


def _mainline_llm_runner(args: argparse.Namespace):
    """Build optional Antigravity runner for hybrid mainline filtering."""
    if getattr(args, "no_llm", False) or getattr(args, "main_line", "hybrid") != "hybrid":
        return None
    try:
        from services.research_digest.narrator import build_antigravity_runner
        return build_antigravity_runner()
    except Exception as exc:  # noqa: BLE001 - trend-leader must degrade if LLM wiring is unavailable.
        logger.warning("[trend-leader] mainline LLM runner 初始化失败，降级确定性概念主线: %s", exc)
        return None


def _run_pool(config: dict, args: argparse.Namespace) -> None:
    conn = get_connection()
    try:
        rows = pool.list_pool(conn, status=args.status)
    finally:
        conn.close()
    if args.json:
        # rows 已是 pool 层清洗过的 dict（last_signal 已解析、内部原始列不外泄），直接序列化
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print(renderer.render_pool(rows))


def _push_to_dingtalk(title: str, markdown: str) -> None:
    """走 DingTalkPusher 推送；token/secret 由 pusher.initialize() 从 env 读。"""
    from pushers.dingtalk_pusher import DingTalkPusher

    pusher = DingTalkPusher(config={})
    if not pusher.initialize():
        logger.error("[trend-leader] DingTalk pusher 未启用（缺 env DINGTALK_WEBHOOK_TOKEN/SECRET），跳过推送")
        return
    ok = pusher.send_markdown(title=title, content=markdown)
    logger.info("[trend-leader] 推送 %s", "成功" if ok else "失败")
