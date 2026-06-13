"""CLI: 业绩预告/快报速报（全市场采集存档 + 次日缺口验证 + MD 落盘 + 钉钉）。

  earnings-digest daily [--date YYYY-MM-DD] [--dry-run] [--lookback-days N]

- 默认推送钉钉（对齐 research-digest / volume-watch 语义，无 --push）。
- --dry-run：仅打印 markdown 到 stdout，不落盘 MD、不推送；**采集落库照常执行**
  （单次权威取数即存档，落库幂等且有审计，跳过反而破坏「推送=存档同批」语义）。
- --lookback-days：手动补采用（连续漏跑 >3 天后扩窗），通过 env 传递给 provider。
- 阈值可调项走 env：EARNINGS_DIGEST_MIN_PROFIT_WAN（Top 榜净利中值阈值，默认 5000 万）、
  EARNINGS_DIGEST_GAP_THRESHOLD_PCT（缺口阈值，默认 2.0）。
"""
from __future__ import annotations

import argparse
import datetime
import logging
import os
import sys

logger = logging.getLogger(__name__)


def _positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return value


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
        if value > 0:
            return value
        raise ValueError(raw)
    except ValueError:
        logger.warning("[earnings-digest] 忽略非法环境变量 %s=%r，使用默认值 %s", name, raw, default)
        return default


def register_subparser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("earnings-digest", help="业绩预告/快报速报（全市场+缺口验证）")
    sub = p.add_subparsers(dest="earnings_digest_command")
    daily = sub.add_parser("daily", help="采集存档 + 缺口验证 + 渲染 + 落盘 + 推钉钉")
    daily.add_argument("--date", default=None, help="目标日 YYYY-MM-DD（默认今天）")
    daily.add_argument("--dry-run", action="store_true",
                       help="仅打印 markdown，不落盘 MD、不推送（采集落库照常）")
    daily.add_argument("--lookback-days", type=_positive_int, default=None,
                       help="覆盖公告回看窗口天数（手动补采用，默认 3）")
    daily.add_argument("--no-consensus", action="store_true",
                       help="关闭口径三券商一致预期（每股 2 次外网调用，加速/排障用）")


def handle_command(config: dict, args: argparse.Namespace) -> None:
    sub = getattr(args, "earnings_digest_command", None)
    if sub == "daily":
        _run_daily(config, args)
    else:
        print("用法：python main.py earnings-digest daily [--date YYYY-MM-DD] [--dry-run] [--lookback-days N]",
              file=sys.stderr)
        sys.exit(2)


def _run_daily(config: dict, args: argparse.Namespace) -> None:
    from main import setup_providers
    from services.earnings_digest import collector, run_daily_digest
    from services.earnings_digest.gap_check import DEFAULT_GAP_THRESHOLD_PCT
    from services.earnings_digest.renderer import (
        DEFAULT_MIN_PROFIT_WAN, resolve_report_dir, write_md,
    )
    from utils.network_env import without_standard_http_proxy

    if args.lookback_days:
        # provider 读取顺序：config["earnings_lookback_days"] > env。config.yaml 当前
        # 未配该键，故 env 桥接生效；若将来在 config.yaml 配置默认值，此 CLI 覆盖会
        # 失效——届时需改为 config override 而非 env
        os.environ["EARNINGS_LOOKBACK_DAYS"] = str(args.lookback_days)

    target_date = args.date or datetime.date.today().isoformat()
    # 采集阶段（forecast/express/quotes/industry/consensus 全走 provider）必须在
    # without_standard_http_proxy() 内：仓库强约定，provider HTTP 调用「在调用时」读
    # os.environ，代理环境（launchd source 的 env 带 HTTP_PROXY）下不隔离会失败/超时
    # （对齐 cmd_post / sector_correlation）。MD 落盘与钉钉推送（国内端点直连）留在外。
    with without_standard_http_proxy():
        registry = setup_providers(config)
        registry.initialize_all()  # 必须：register 不自动初始化，漏了 provider 调用全返 not_initialized
        result = run_daily_digest(
            registry,
            target_date,
            # 审计来源标注：launchd 无参触发（自动化）记 openclaw；显式 --date / dry-run /
            # lookback-days（手动补采）视为人工操作记 manual（对齐仓库 input_by 取值惯例）
            input_by="manual" if args.date or args.dry_run or args.lookback_days else "openclaw",
            min_profit_wan=_env_float("EARNINGS_DIGEST_MIN_PROFIT_WAN", DEFAULT_MIN_PROFIT_WAN),
            gap_threshold_pct=_env_float("EARNINGS_DIGEST_GAP_THRESHOLD_PCT", DEFAULT_GAP_THRESHOLD_PCT),
            enable_consensus=not args.no_consensus,
        )

    print(f"[earnings-digest] {target_date} stats={result.stats}")

    if not result.has_content:
        return  # 空窗口日不推送（详情已在 stats 行与 service 日志中）

    if args.dry_run:
        print(result.markdown)
        logger.info("[earnings-digest] dry-run 完成（未落盘 MD / 未推送 / 不落已推标记）")
        return

    path = write_md(result.markdown, target_date)
    logger.info("[earnings-digest] MD 落盘: %s", path)
    if _push_to_dingtalk(result.title, result.markdown):
        # 推送成功才落已推标记：失败不落 → 重试仍能补推（同日重跑幂等兜底，详见 collector）
        collector.record_pushed(
            target_date, result.pushed_announcement_keys,
            result.pushed_gap_keys, resolve_report_dir(),
            warning_keys=result.pushed_warning_keys)


def _push_to_dingtalk(title: str, markdown: str) -> bool:
    """走 DingTalkPusher 推送；token/secret 由 pusher.initialize() 从 env 读（对齐 research-digest）。

    返回是否推送成功（用于决定是否落已推标记；未启用/失败均返 False，保证重试可补推）。
    """
    from pushers.dingtalk_pusher import DingTalkPusher

    pusher = DingTalkPusher(config={})
    if not pusher.initialize():
        logger.error("[earnings-digest] DingTalk pusher 未启用（缺 env DINGTALK_WEBHOOK_TOKEN/SECRET），跳过推送")
        return False
    ok = pusher.send_markdown(title=title, content=markdown)
    logger.info("[earnings-digest] 推送 %s", "成功" if ok else "失败")
    return bool(ok)
