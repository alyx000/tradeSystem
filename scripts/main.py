#!/usr/bin/env python3
"""
交易系统主入口
支持命令行手动运行和定时任务调度

用法:
    # 运行盘前简报
    python main.py pre

    # 运行盘后报告
    python main.py post

    # 运行盘后报告（指定日期）
    python main.py post --date 2026-03-28

    # 启动定时调度器
    python main.py schedule

    # 检查数据源连通性
    python main.py check

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
    """初始化推送渠道"""
    from pushers import DiscordPusher, WechatPusher, MultiPusher

    multi = MultiPusher()

    push_config = config.get("push", {})

    # Discord
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

    # 企业微信
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


def cmd_pre(config: dict, target_date: str):
    """执行盘前简报"""
    logger.info(f"=== 盘前简报 {target_date} ===")

    registry = setup_providers(config)
    registry.initialize_all()

    from collectors import MarketCollector, HoldingsCollector
    from generators import ReportGenerator

    # 采集
    market_collector = MarketCollector(registry)
    market_data = market_collector.collect_pre_market()

    holdings_collector = HoldingsCollector(registry)
    holdings_collector.load()
    # 查最近3天公告
    from datetime import timedelta
    d = datetime.strptime(target_date, "%Y-%m-%d")
    start = (d - timedelta(days=3)).strftime("%Y-%m-%d")
    holdings_anns = holdings_collector.collect_holdings_announcements(start, target_date)

    # 生成报告
    generator = ReportGenerator()
    md_text, yaml_path = generator.generate_pre_market(
        date=target_date,
        market_data=market_data,
        holdings_announcements=holdings_anns,
    )

    print(md_text)
    logger.info(f"数据已保存: {yaml_path}")

    # 推送
    multi = setup_pushers(config)
    if multi._pushers:
        multi.send_report("pre_market", f"盘前简报 {target_date}", md_text)


def cmd_post(config: dict, target_date: str):
    """执行盘后报告"""
    logger.info(f"=== 盘后报告 {target_date} ===")

    registry = setup_providers(config)
    registry.initialize_all()

    from collectors import MarketCollector, HoldingsCollector
    from generators import ReportGenerator

    # 采集市场数据
    market_collector = MarketCollector(registry)
    raw_data = market_collector.collect_post_market(target_date)

    # 采集持仓数据
    holdings_collector = HoldingsCollector(registry)
    holdings_collector.load()
    holdings_data = holdings_collector.collect_holdings_data(target_date)

    # 生成报告
    generator = ReportGenerator()
    md_text, yaml_path = generator.generate_post_market(
        date=target_date,
        raw_data=raw_data,
        holdings_data=holdings_data,
    )

    print(md_text)
    logger.info(f"数据已保存: {yaml_path}")

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


def _get_prev_trade_date(registry, today: str) -> str:
    """
    向前最多查找 7 天，找到最近一个交易日（不含 today）。
    若 provider 不可用则简单回退到昨天。
    """
    for delta in range(1, 8):
        candidate = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=delta)).strftime("%Y-%m-%d")
        r = registry.call("is_trade_day", candidate)
        if r.success and r.data:
            return candidate
    # fallback
    return (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")


def cmd_evening(config: dict, target_date: str):
    """
    执行晚间任务（18:00 触发）：
    1. 溢价率回填（T-1 涨停→T 开盘溢价）
    2. 关注池行情更新 + 到价提醒
    3. Obsidian 导出当日数据
    """
    logger.info(f"=== 晚间任务 {target_date} ===")

    registry = setup_providers(config)
    registry.initialize_all()

    multi = setup_pushers(config)
    prev_date = _get_prev_trade_date(registry, target_date)

    # 1. 溢价率回填
    try:
        from collectors import PremiumCollector
        premium_collector = PremiumCollector(registry)
        premium_result = premium_collector.collect(target_date, prev_date)
        if premium_result:
            report_text = premium_collector.format_report(premium_result)
            logger.info(f"溢价率回填完成，首板高开率：{premium_result['first_board'].get('open_up_rate', '-')}")
            if multi._pushers and report_text:
                multi.send_report("post_market", f"溢价率回填 {prev_date}", report_text)
    except Exception as e:
        logger.error(f"溢价率回填失败：{e}")

    # 2. 关注池采集 + 到价提醒
    try:
        from collectors import WatchlistCollector
        wl_collector = WatchlistCollector(registry)
        wl_result = wl_collector.collect(target_date)
        if wl_result:
            report_text = wl_collector.format_report(wl_result)
            logger.info(
                f"关注池更新完成，tier1={len(wl_result.get('tier1', []))}只，"
                f"提醒={len(wl_result.get('alerts', []))}条"
            )
            if multi._pushers and report_text:
                multi.send_report("post_market", f"关注池日报 {target_date}", report_text)
    except Exception as e:
        logger.error(f"关注池采集失败：{e}")

    # 3. Obsidian 导出（仅导出 review.yaml，post-market 在 20:00 盘后任务完成后才有数据）
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

    # 盘后: 每个工作日 20:00
    scheduler.add_job(
        lambda: cmd_post(config, date.today().isoformat()),
        CronTrigger(day_of_week="mon-fri", hour=20, minute=0),
        id="post_market",
        name="盘后报告",
    )

    # 晚间: 每个工作日 18:00（溢价率回填 + 关注池 + Obsidian 导出）
    scheduler.add_job(
        lambda: cmd_evening(config, date.today().isoformat()),
        CronTrigger(day_of_week="mon-fri", hour=18, minute=0),
        id="evening",
        name="晚间任务",
    )

    logger.info("定时调度器已启动")
    logger.info("  盘前简报: 周一~周五 07:00")
    logger.info("  晚间任务: 周一~周五 18:00（溢价率回填/关注池/Obsidian）")
    logger.info("  盘后报告: 周一~周五 20:00")
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

    # pre
    pre_parser = subparsers.add_parser("pre", help="生成盘前简报")
    pre_parser.add_argument("--date", default=date.today().isoformat(), help="日期 YYYY-MM-DD")

    # post
    post_parser = subparsers.add_parser("post", help="生成盘后报告")
    post_parser.add_argument("--date", default=date.today().isoformat(), help="日期 YYYY-MM-DD")

    # holdings
    holdings_parser = subparsers.add_parser("holdings", help="持仓管理")
    holdings_parser.add_argument("--add", dest="holdings_action", action="store_const", const="add")
    holdings_parser.add_argument("--remove", dest="holdings_action", action="store_const", const="remove")
    holdings_parser.add_argument("--list", dest="holdings_action", action="store_const", const="list")
    holdings_parser.add_argument("holdings_args", nargs="*", default=[])

    # evening
    evening_parser = subparsers.add_parser("evening", help="晚间任务（溢价率回填+关注池+Obsidian导出）")
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

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    config = load_config()

    if args.command == "check":
        cmd_check(config)
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


if __name__ == "__main__":
    main()
