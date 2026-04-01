#!/usr/bin/env python3
"""
交易系统主入口
支持命令行手动运行和定时任务调度

用法:
    # 运行盘前简报
    python main.py pre

    # 运行盘后报告（会先执行晚间任务：溢价回填、关注池、复盘 Obsidian，再生成全日盘后数据）
    python main.py post

    # 指定日期
    python main.py post --date 2026-03-28

    # 仅重跑晚间任务（定时任务请用 post，无需单独跑 evening）
    python main.py evening

    # 关注池 / Obsidian 导出（也可通过 post、evening 间接执行）
    python main.py watchlist
    python main.py obsidian [--sync-all]

    # 启动定时调度器（工作日 07:00 pre，20:00 post；post 已含 evening）
    python main.py schedule

    # 检查数据源连通性
    python main.py check

    # 采集阶段默认会临时清除 HTTP_PROXY/HTTPS_PROXY，避免误走本机代理导致 Tushare 超时。
    # 若你必须让采集也走代理：export TRADESYSTEM_USE_HTTP_PROXY=1

    # 预拉取未来 N 天宏观日历到 tracking/calendar_auto.yaml（供盘前合并）
    python main.py prefetch-calendar [--days 14] [--from YYYY-MM-DD]

    # 更新持仓
    python main.py holdings --add 688041.SH 海光信息 200 225.0 国产AI链
    python main.py holdings --remove 688041.SH
    python main.py holdings --list
"""

import argparse
import logging
import os
import sys
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv

# 项目根目录与脚本目录（数据采集、分析等包均位于 scripts/ 下）
BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPT_DIR = Path(__file__).resolve().parent

# 确保无论从何处启动（如项目根目录执行 python -m scripts.main），均能解析
# providers、collectors、analyzers、generators 等包
_scripts = str(SCRIPT_DIR)
if _scripts not in sys.path:
    sys.path.insert(0, _scripts)

# 加载 .env
load_dotenv(SCRIPT_DIR / ".env")

from utils.network_env import without_standard_http_proxy

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(SCRIPT_DIR / "trade_system.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("main")


def load_config() -> dict:
    config_path = SCRIPT_DIR / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def setup_providers(config: dict):
    """初始化数据源注册中心"""
    from providers import ProviderRegistry, TushareProvider, AkshareProvider

    registry = ProviderRegistry()

    # Tushare
    ts_config = config.get("providers", {}).get("tushare", {})
    if ts_config.get("enabled"):
        ts = TushareProvider({
            "token": os.getenv("TUSHARE_TOKEN", ""),
            **ts_config,
        })
        ts.priority = ts_config.get("priority", 1)
        registry.register(ts)

    # AkShare
    ak_config = config.get("providers", {}).get("akshare", {})
    if ak_config.get("enabled"):
        ak = AkshareProvider(ak_config)
        ak.priority = ak_config.get("priority", 2)
        registry.register(ak)

    return registry


def setup_pushers(config: dict):
    """
    初始化推送渠道
    
    配置职责边界:
    - .env 文件：仅用于存储敏感 token 和 Webhook URL（DISCORD_WEBHOOK_*, WECHAT_WEBHOOK）
    - config.yaml：用于存储非敏感配置（频道映射、开关状态等）
    """
    from pushers import DiscordPusher, WechatPusher, QQBotPusher, MultiPusher

    multi = MultiPusher()

    push_config = config.get("push", {})

    # Discord
    # Webhook URL 从 .env 加载，频道映射从 config.yaml 加载
    dc_config = push_config.get("discord", {})
    if dc_config.get("enabled"):
        dc = DiscordPusher({
            "webhook_pre": os.getenv("DISCORD_WEBHOOK_PRE", ""),
            "webhook_post": os.getenv("DISCORD_WEBHOOK_POST", ""),
            "webhook_alert": os.getenv("DISCORD_WEBHOOK_ALERT", ""),
            "channels": dc_config.get("channels", {}),
        })
        dc.initialize()
        multi.register(dc)

    # QQ Bot
    # 配置全部来自 config.yaml（无敏感信息）
    qq_config = push_config.get("qq", {})
    if qq_config.get("enabled"):
        qq = QQBotPusher({
            "channels": qq_config.get("channels", {}),
        })
        qq.initialize()
        multi.register(qq)

    # 企业微信
    # Webhook URL 从 .env 加载
    wx_config = push_config.get("wechat", {})
    if wx_config.get("enabled"):
        wx = WechatPusher({
            "webhook_url": os.getenv("WECHAT_WEBHOOK", ""),
        })
        wx.initialize()
        multi.register(wx)

    return multi


def cmd_check(config: dict):
    """检查数据源连通性"""
    logger.info("=== 数据源连通性检查 ===")
    with without_standard_http_proxy():
        registry = setup_providers(config)
        results = registry.initialize_all()
    for name, ok in results.items():
        status = "OK" if ok else "FAIL"
        print(f"  {name}: {status}")

    # 检查推送
    logger.info("=== 推送渠道检查 ===")
    multi = setup_pushers(config)
    if not multi._pushers:
        print("  未配置任何推送渠道")
    else:
        for p in multi._pushers:
            print(f"  {p.name}: {'OK' if p.enabled else 'FAIL'}")


def cmd_prefetch_calendar(config: dict, days: int, from_date: Optional[str]):
    """预拉取宏观日历并写入 tracking/calendar_auto.yaml"""
    logger.info(f"=== 预拉取宏观日历 days={days} from={from_date or 'today'} ===")
    with without_standard_http_proxy():
        registry = setup_providers(config)
        registry.initialize_all()

        from collectors.market import prefetch_calendar

        n_fetch, n_total = prefetch_calendar(
            registry,
            days=days,
            from_date=from_date,
            base_dir=BASE_DIR,
        )
    print(f"预拉取完成：API 返回 {n_fetch} 条，calendar_auto.yaml 共 {n_total} 条事件")
    logger.info(f"calendar_auto 已更新：拉取 {n_fetch} 条，文件合计 {n_total} 条")


def cmd_pre(config: dict, target_date: str):
    """执行盘前简报"""
    logger.info(f"=== 盘前简报 {target_date} ===")

    with without_standard_http_proxy():
        registry = setup_providers(config)
        registry.initialize_all()

        from collectors import MarketCollector, HoldingsCollector, WatchlistCollector
        from generators import ReportGenerator
        from utils.trade_date import get_prev_trade_date

        prev_date = get_prev_trade_date(registry, target_date)
        prev_prev_date = get_prev_trade_date(registry, prev_date) if prev_date else None

        # 采集
        market_collector = MarketCollector(registry)
        market_data = market_collector.collect_pre_market(
            target_date=target_date,
            prev_trade_date=prev_date,
            prev_prev_trade_date=prev_prev_date,
        )

        # SQLite：上一交易日盘面摘要 + 昨日复盘结论（无则跳过）
        try:
            from db.connection import get_connection
            from db import queries as DbQ

            conn = get_connection()
            try:
                snap_row = DbQ.get_prev_daily_market(conn, target_date)
                if snap_row:
                    market_data["prev_session_snapshot"] = {
                        "date": snap_row.get("date"),
                        "sh_index_close": snap_row.get("sh_index_close"),
                        "sh_index_change_pct": snap_row.get("sh_index_change_pct"),
                        "total_amount": snap_row.get("total_amount"),
                        "limit_up_count": snap_row.get("limit_up_count"),
                        "limit_down_count": snap_row.get("limit_down_count"),
                        "seal_rate": snap_row.get("seal_rate"),
                        "broken_rate": snap_row.get("broken_rate"),
                        "northbound_net": snap_row.get("northbound_net"),
                    }
                if prev_date:
                    rev = DbQ.get_daily_review(conn, prev_date)
                    blurbs = DbQ.extract_review_conclusion_lines(rev)
                    if blurbs:
                        market_data["prev_review_conclusion"] = blurbs
            finally:
                conn.close()
        except Exception as e:
            logger.warning("盘前简报 DB 补充（T-1 盘面/复盘摘要）失败: %s", e)

        holdings_collector = HoldingsCollector(registry)
        holdings_collector.load()
        # 查最近3天公告
        d = datetime.strptime(target_date, "%Y-%m-%d")
        start = (d - timedelta(days=3)).strftime("%Y-%m-%d")
        holdings_anns = holdings_collector.collect_holdings_announcements(start, target_date)

        watchlist_collector = WatchlistCollector(registry)
        watchlist_anns = watchlist_collector.collect_watchlist_announcements(start, target_date)

        # 采集信息面（互动易/研报/个股新闻）
        holdings_info = {}
        watchlist_info = {}
        try:
            holdings_info = holdings_collector.collect_stock_info(target_date)
        except Exception as e:
            logger.warning(f"持仓信息面采集失败: {e}")
        try:
            watchlist_info = watchlist_collector.collect_watchlist_info(target_date)
        except Exception as e:
            logger.warning(f"关注池信息面采集失败: {e}")

        # 生成报告
        generator = ReportGenerator()
        md_text, yaml_path = generator.generate_pre_market(
            date=target_date,
            market_data=market_data,
            holdings_announcements=holdings_anns,
            watchlist_announcements=watchlist_anns,
            news=market_data.get("news", []),
            calendar_events=market_data.get("calendar_events", []),
            holdings_info=holdings_info,
            watchlist_info=watchlist_info,
        )

    print(md_text)
    logger.info(f"数据已保存: {yaml_path}")

    # 推送
    multi = setup_pushers(config)
    if multi._pushers:
        multi.send_report("pre_market", f"盘前简报 {target_date}", md_text)


def cmd_post(config: dict, target_date: str):
    """执行盘后报告：先晚间任务（溢价/关注池/复盘 Obsidian），再全日盘后采集与推送。"""
    cmd_evening(config, target_date)

    logger.info(f"=== 盘后报告 {target_date} ===")

    with without_standard_http_proxy():
        registry = setup_providers(config)
        registry.initialize_all()

        from collectors import MarketCollector, HoldingsCollector, WatchlistCollector
        from generators import ReportGenerator

        # 采集市场数据
        market_collector = MarketCollector(registry)
        raw_data = market_collector.collect_post_market(target_date)

        # 采集持仓数据 + 盘后公告
        holdings_collector = HoldingsCollector(registry)
        holdings_collector.load()
        holdings_data = holdings_collector.collect_holdings_data(target_date)
        try:
            holdings_data = holdings_collector.enrich_with_ma(holdings_data, target_date)
        except Exception as e:
            logger.warning(f"持仓均线/板块数据补充失败: {e}")
        holdings_summary = HoldingsCollector.compute_summary(holdings_data)

        holdings_anns = {}
        try:
            holdings_anns = holdings_collector.collect_holdings_announcements(target_date, target_date)
        except Exception as e:
            logger.warning(f"持仓盘后公告采集失败: {e}")

        watchlist_data = {}
        try:
            wl_collector = WatchlistCollector(registry)
            watchlist_data = wl_collector.get_watchlist_summary()
        except Exception as e:
            logger.warning(f"关注池盘后数据加载失败: {e}")

        # 交叉检查：持仓与关注池重复
        holdings_codes = set(h.get("code", "") for h in holdings_data if "error" not in h)
        wl_codes = set(s.get("code", "") for s in watchlist_data.get("tier1", []))
        overlap = holdings_codes & wl_codes
        if overlap:
            logger.info(f"持仓与关注池 tier1 重叠: {overlap}")

        # 生成报告
        generator = ReportGenerator()
        md_text, yaml_path = generator.generate_post_market(
            date=target_date,
            raw_data=raw_data,
            holdings_data=holdings_data,
            holdings_announcements=holdings_anns,
            watchlist_data=watchlist_data,
            holdings_summary=holdings_summary,
        )

    print(md_text)
    logger.info(f"数据已保存: {yaml_path}")

    try:
        from pathlib import Path

        from db.dual_write import sync_daily_market_to_db

        pm = Path(yaml_path)
        if pm.is_file():
            with pm.open(encoding="utf-8") as f:
                envelope = yaml.safe_load(f) or {}
            if sync_daily_market_to_db(target_date, envelope):
                logger.info("daily_market 已同步到 SQLite: %s", target_date)
            else:
                logger.warning("daily_market 同步失败（已记入 pending_writes）: %s", target_date)
    except Exception as e:
        logger.warning("daily_market 同步跳过: %s", e)

    # 推送
    multi = setup_pushers(config)
    if multi._pushers:
        multi.send_report("post_market", f"盘后数据报告 {target_date}", md_text)

    # 盘后数据写入完成后，同步导出到 Obsidian（post-market.yaml 此时已存在）
    try:
        from generators.obsidian_export import ObsidianExporter
        exporter = ObsidianExporter()
        post_path = exporter.export_post_market(target_date)
        if post_path:
            logger.info(f"Obsidian 导出完成（盘后数据）：{post_path}")
    except Exception as e:
        logger.warning(f"Obsidian 盘后数据导出失败：{e}")


def cmd_holdings(config: dict, args):
    """持仓管理"""
    from collectors import HoldingsCollector
    hc = HoldingsCollector()
    hc.load()

    if args.holdings_action == "list":
        holdings = hc.load()
        if not holdings:
            print("当前无持仓")
        else:
            print(f"当前持仓 ({len(holdings)} 只):")
            for h in holdings:
                print(f"  {h['code']} {h['name']} | {h.get('shares', 0)}股 | "
                      f"成本: {h.get('cost', '-')} | 板块: {h.get('sector', '-')}")

    elif args.holdings_action == "add":
        if len(args.holdings_args) < 2:
            print("用法: python main.py holdings --add <代码> <名称> [股数] [成本] [板块]")
            return
        stock = {
            "code": args.holdings_args[0],
            "name": args.holdings_args[1],
            "shares": int(args.holdings_args[2]) if len(args.holdings_args) > 2 else 0,
            "cost": float(args.holdings_args[3]) if len(args.holdings_args) > 3 else 0,
            "sector": args.holdings_args[4] if len(args.holdings_args) > 4 else "",
        }
        hc.add_stock(stock)
        print(f"已添加: {stock['name']} ({stock['code']})")

    elif args.holdings_action == "remove":
        if not args.holdings_args:
            print("用法: python main.py holdings --remove <代码>")
            return
        code = args.holdings_args[0]
        hc.remove_stock(code)
        print(f"已移除: {code}")


def cmd_evening(config: dict, target_date: str):
    """
    晚间任务（由 post 在定时流程中自动先执行，也可手动单独运行）：
    1. 溢价率回填（T-1 涨停→T 开盘溢价）
    2. 关注池行情更新 + 到价提醒
    3. Obsidian 导出 review.yaml（post-market 在随后 post 阶段导出）
    """
    logger.info(f"=== 晚间任务 {target_date} ===")

    premium_report_text: Optional[str] = None
    premium_prev_date: Optional[str] = None
    wl_report_text: Optional[str] = None
    wl_alerts: list = []

    with without_standard_http_proxy():
        registry = setup_providers(config)
        registry.initialize_all()

        from utils.trade_date import get_prev_trade_date

        prev_date = get_prev_trade_date(registry, target_date)

        # 1. 溢价率回填
        try:
            from collectors import PremiumCollector
            premium_collector = PremiumCollector(registry)
            premium_result = premium_collector.collect(target_date, prev_date)
            if premium_result:
                premium_report_text = premium_collector.format_report(premium_result)
                premium_prev_date = prev_date
                logger.info(
                    f"溢价率回填完成，首板高开率："
                    f"{premium_result['first_board'].get('open_up_rate', '-')}"
                )
        except Exception as e:
            logger.error(f"溢价率回填失败：{e}")

        # 2. 关注池采集 + 到价提醒
        try:
            from collectors import WatchlistCollector
            wl_collector = WatchlistCollector(registry)
            wl_result = wl_collector.collect(target_date)
            if wl_result:
                wl_report_text = wl_collector.format_report(wl_result)
                logger.info(
                    f"关注池更新完成，tier1={len(wl_result.get('tier1', []))}只，"
                    f"提醒={len(wl_result.get('alerts', []))}条"
                )
                wl_alerts = wl_result.get("alerts", []) or []
        except Exception as e:
            logger.error(f"关注池采集失败：{e}")

    multi = setup_pushers(config)
    if multi._pushers and premium_report_text and premium_prev_date:
        multi.send_report("post_market", f"溢价率回填 {premium_prev_date}", premium_report_text)
    if multi._pushers and wl_report_text:
        multi.send_report("post_market", f"关注池日报 {target_date}", wl_report_text)
    if multi._pushers and wl_alerts:
        alert_lines = [f"⚠ 关注池到价提醒 {target_date}"]
        for a in wl_alerts:
            alert_lines.append(f"• {a.get('message', '(无消息)')}")
        multi.send_alert("\n".join(alert_lines))

    # 3. Obsidian 导出 review.yaml（post-market 在同次 post 流程后半段导出）
    try:
        from generators.obsidian_export import ObsidianExporter
        exporter = ObsidianExporter()
        review_path = exporter.export_daily_review(target_date)
        if review_path:
            logger.info(f"Obsidian 导出完成（复盘）：{review_path}")
        else:
            logger.info(f"Obsidian 复盘导出跳过：{target_date}/review.yaml 不存在或尚未填写")
    except Exception as e:
        logger.error(f"Obsidian 导出失败：{e}")


def cmd_watchlist(config: dict, target_date: str):
    """手动触发关注池采集 + 到价提醒"""
    logger.info(f"=== 关注池采集 {target_date} ===")

    with without_standard_http_proxy():
        registry = setup_providers(config)
        registry.initialize_all()

        from collectors import WatchlistCollector
        wl_collector = WatchlistCollector(registry)
        result = wl_collector.collect(target_date)
        report = wl_collector.format_report(result)
    print(report)

    multi = setup_pushers(config)
    if multi._pushers and report:
        multi.send_report("post_market", f"关注池日报 {target_date}", report)


def cmd_obsidian(config: dict, target_date: str, sync_all: bool = False):
    """手动触发 Obsidian 导出"""
    from generators.obsidian_export import ObsidianExporter
    exporter = ObsidianExporter()

    if sync_all:
        daily_dir = exporter.ts_dir / "daily"
        if not daily_dir.exists():
            logger.error(f"daily 目录不存在：{daily_dir}")
            return
        dates = sorted([d.name for d in daily_dir.iterdir() if d.is_dir() and d.name != "example"])
        logger.info(f"全量同步 {len(dates)} 个日期...")
        for d in dates:
            exporter.export_all(d)
        logger.info("全量同步完成")
    else:
        results = exporter.export_all(target_date)
        exported = {k: v for k, v in results.items() if v and k != "date"}
        if exported:
            logger.info(f"导出完成：{exported}")
        else:
            logger.warning(f"未找到可导出的文件（{target_date}）")


def cmd_schedule(config: dict):
    """启动定时调度器"""
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        print("请安装 apscheduler: pip install apscheduler")
        return

    scheduler = BlockingScheduler(timezone="Asia/Shanghai")

    # 盘前: 每个工作日 07:00
    scheduler.add_job(
        lambda: cmd_pre(config, date.today().isoformat()),
        CronTrigger(day_of_week="mon-fri", hour=7, minute=0),
        id="pre_market",
        name="盘前简报",
    )

    # 盘后: 每个工作日 20:00（内含晚间任务：溢价回填/关注池/复盘 Obsidian）
    scheduler.add_job(
        lambda: cmd_post(config, date.today().isoformat()),
        CronTrigger(day_of_week="mon-fri", hour=20, minute=0),
        id="post_market",
        name="盘后报告",
    )

    logger.info("定时调度器已启动")
    logger.info("  盘前简报: 周一~周五 07:00")
    logger.info("  盘后报告: 周一~周五 20:00（含溢价回填/关注池/复盘与全日盘后）")
    logger.info("按 Ctrl+C 停止")

    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("调度器已停止")


def main():
    parser = argparse.ArgumentParser(description="交易系统数据采集与报告")
    subparsers = parser.add_subparsers(dest="command")

    # check
    subparsers.add_parser("check", help="检查数据源连通性")

    # prefetch-calendar
    prefetch_parser = subparsers.add_parser(
        "prefetch-calendar",
        help="预拉取未来多日宏观日历到 tracking/calendar_auto.yaml",
    )
    prefetch_parser.add_argument("--days", type=int, default=14, help="从起始日起连续拉取的天数（默认 14）")
    prefetch_parser.add_argument("--from", dest="from_date", default=None, help="起始日 YYYY-MM-DD（默认今天）")

    # pre
    pre_parser = subparsers.add_parser("pre", help="生成盘前简报")
    pre_parser.add_argument("--date", default=date.today().isoformat(), help="日期 YYYY-MM-DD")

    # post
    post_parser = subparsers.add_parser("post", help="生成盘后报告（含晚间任务）")
    post_parser.add_argument("--date", default=date.today().isoformat(), help="日期 YYYY-MM-DD")

    # holdings
    holdings_parser = subparsers.add_parser("holdings", help="持仓管理")
    holdings_parser.add_argument("--add", dest="holdings_action", action="store_const", const="add")
    holdings_parser.add_argument("--remove", dest="holdings_action", action="store_const", const="remove")
    holdings_parser.add_argument("--list", dest="holdings_action", action="store_const", const="list")
    holdings_parser.add_argument("holdings_args", nargs="*", default=[])

    # evening
    evening_parser = subparsers.add_parser("evening", help="仅晚间任务（定时请用 post；post 会自动先执行本流程）")
    evening_parser.add_argument("--date", default=date.today().isoformat(), help="日期 YYYY-MM-DD")

    # watchlist
    watchlist_parser = subparsers.add_parser("watchlist", help="关注池行情更新 + 到价提醒")
    watchlist_parser.add_argument("--date", default=date.today().isoformat(), help="日期 YYYY-MM-DD")

    # obsidian
    obsidian_parser = subparsers.add_parser("obsidian", help="导出数据到 Obsidian Vault")
    obsidian_parser.add_argument("--date", default=date.today().isoformat(), help="日期 YYYY-MM-DD")
    obsidian_parser.add_argument("--sync-all", action="store_true", help="全量同步所有日期")

    # schedule
    subparsers.add_parser("schedule", help="启动定时调度器")

    # db
    from db.cli import register_db_subparser
    register_db_subparser(subparsers)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    config = load_config()

    if args.command == "check":
        cmd_check(config)
    elif args.command == "prefetch-calendar":
        cmd_prefetch_calendar(config, args.days, args.from_date)
    elif args.command == "pre":
        cmd_pre(config, args.date)
    elif args.command == "post":
        cmd_post(config, args.date)
    elif args.command == "holdings":
        if not args.holdings_action:
            args.holdings_action = "list"
        cmd_holdings(config, args)
    elif args.command == "evening":
        cmd_evening(config, args.date)
    elif args.command == "watchlist":
        cmd_watchlist(config, args.date)
    elif args.command == "obsidian":
        cmd_obsidian(config, args.date, sync_all=args.sync_all)
    elif args.command == "schedule":
        cmd_schedule(config)
    elif args.command == "db":
        from db.cli import handle_db_command
        handle_db_command(args)


if __name__ == "__main__":
    main()
