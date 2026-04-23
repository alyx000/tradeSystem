"""main.py db 子命令实现：供 AI agent 调用的标准化写入接口。"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from pathlib import Path

import yaml

from .connection import get_db
from .dual_write import reconcile_daily_market, retry_pending
from .migrate import import_all, migrate

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _resolve_main_runtime():
    """优先使用当前进程的 ``__main__``，避免 ``python main.py`` 时再 ``import main`` 产生双模块状态。"""
    scripts_dir = Path(__file__).resolve().parent.parent
    sd = str(scripts_dir)
    if sd not in sys.path:
        sys.path.insert(0, sd)
    main_mod = sys.modules.get("__main__")
    if main_mod is not None and hasattr(main_mod, "load_config") and hasattr(main_mod, "setup_providers"):
        return main_mod
    import importlib

    return importlib.import_module("main")


def _coerce_holdings_shares(raw: object) -> tuple[int | None, str]:
    """解析 YAML 中的 shares；无法解析时返回 (None, reason)。"""
    if raw is None or raw == "":
        return 0, ""
    if isinstance(raw, bool):
        return None, "invalid_shares"
    if isinstance(raw, int):
        return (raw, "") if raw >= 0 else (None, "invalid_shares")
    if isinstance(raw, float):
        if raw < 0 or raw != int(raw):
            return None, "invalid_shares"
        return int(raw), ""
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return 0, ""
        try:
            iv = int(s)
            return (iv, "") if iv >= 0 else (None, "invalid_shares")
        except ValueError:
            try:
                fv = float(s)
                if fv < 0 or fv != int(fv):
                    return None, "invalid_shares"
                return int(fv), ""
            except ValueError:
                return None, "invalid_shares"
    return None, "invalid_shares"


def _coerce_holdings_cost(raw: object) -> tuple[float | None, str]:
    """解析 YAML 中的 cost（入库为 entry_price）；无法解析时返回 (None, reason)。"""
    if raw is None or raw == "":
        return 0.0, ""
    if isinstance(raw, bool):
        return None, "invalid_cost"
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None, "invalid_cost"
    if v < 0:
        return None, "invalid_cost"
    return v, ""


def _read_raw_content(args: argparse.Namespace) -> str | None:
    """读取 add-note 的原文内容，支持直接传参、文件和 stdin。"""
    raw_content = getattr(args, "raw_content", None)
    raw_content_file = getattr(args, "raw_content_file", None)
    if raw_content_file is None:
        return raw_content
    if raw_content_file == "-":
        return sys.stdin.read()
    path = Path(raw_content_file)
    return path.read_text(encoding="utf-8")


def register_db_subparser(subparsers: argparse._SubParsersAction) -> None:
    """在 main.py 的 subparsers 中注册 db 子命令。"""
    db_parser = subparsers.add_parser("db", help="数据库管理（查询/写入/迁移/对账）")
    db_sub = db_parser.add_subparsers(dest="db_action")

    # ── 管理命令 ──────────────────────────────────────────────────
    db_sub.add_parser("init", help="初始化数据库（创建表 + 导入历史 YAML）")
    db_sub.add_parser("sync", help="重试 pending_writes 中的失败记录")
    db_sub.add_parser("reconcile", help="对账：比对 YAML 与 DB 数据一致性")

    # ── 老师观点 ──────────────────────────────────────────────────
    add_note = db_sub.add_parser("add-note", help="录入老师观点")
    add_note.add_argument("--teacher", required=True, help="老师名称")
    add_note.add_argument("--date", required=True, help="日期 YYYY-MM-DD")
    add_note.add_argument("--title", required=True, help="标题")
    add_note.add_argument("--core-view", default=None, help="核心观点")
    add_note.add_argument("--source-type", default="text", help="来源类型: text/image/mixed")
    add_note.add_argument("--input-by", default="manual", help="录入方: manual/openclaw/copaw/cursor")
    add_note.add_argument("--tags", default=None, help="标签 JSON array")
    add_note.add_argument("--key-points", default=None, help="结构化要点 JSON array，如 '[\"要点1\",\"要点2\"]'")
    add_note.add_argument("--sectors", default=None, help="涉及板块 JSON array，如 '[\"AI\",\"锂电\"]'")
    add_note.add_argument("--position-advice", default=None, help="仓位建议")
    raw_content_group = add_note.add_mutually_exclusive_group()
    raw_content_group.add_argument("--raw-content", default=None, help="原始全文")
    raw_content_group.add_argument(
        "--raw-content-file",
        default=None,
        help="从文件读取原始全文；传 '-' 时从 stdin 读取，适合长文本/OCR/PDF 提取结果",
    )
    add_note.add_argument("--attachment", nargs="*", default=None, help="附件文件路径（可多个）")
    add_note.add_argument(
        "--stocks", default=None,
        help='提到的个股 JSON array，如 \'[{"code":"300750","name":"宁德时代","tier":"tier3_sector"}]\''
    )
    add_note.add_argument(
        "--sync-watchlist-from-stocks",
        action="store_true",
        help="将 --stocks 中尚未在关注池的标的写入 watchlist（须已由用户确认）；默认仅记笔记并输出候选",
    )

    query_notes = db_sub.add_parser("query-notes", help="搜索老师笔记")
    query_notes.add_argument("--keyword", required=True, help="搜索关键词")
    query_notes.add_argument("--teacher", default=None, help="限定老师名称")
    query_notes.add_argument("--from", dest="date_from", default=None, help="起始日期")
    query_notes.add_argument("--to", dest="date_to", default=None, help="结束日期")

    # ── 行业 / 宏观信息 ───────────────────────────────────────────
    add_industry = db_sub.add_parser("add-industry", help="录入行业板块信息")
    add_industry.add_argument("--sector", required=True, help="板块名称")
    add_industry.add_argument("--date", required=True, help="日期 YYYY-MM-DD")
    add_industry.add_argument("--content", required=True, help="内容")
    add_industry.add_argument("--info-type", default=None, help="信息类型（如 研报/政策/数据）")
    add_industry.add_argument("--source", default=None, help="来源")
    add_industry.add_argument("--confidence", default=None, help="置信度（高/中/低）")
    add_industry.add_argument("--tags", default=None, help="标签 JSON array")

    add_macro = db_sub.add_parser("add-macro", help="录入宏观经济信息")
    add_macro.add_argument("--category", required=True, help="类别（如 货币政策/财政/外贸）")
    add_macro.add_argument("--date", required=True, help="日期 YYYY-MM-DD")
    add_macro.add_argument("--title", required=True, help="标题")
    add_macro.add_argument("--content", required=True, help="内容")
    add_macro.add_argument("--source", default=None, help="来源")
    add_macro.add_argument("--impact", default=None, help="影响评估")
    add_macro.add_argument("--tags", default=None, help="标签 JSON array")

    # ── 持仓池（DB 路径）──────────────────────────────────────────
    holdings_add = db_sub.add_parser("holdings-add", help="新增持仓（写入 DB）")
    holdings_add.add_argument("--code", required=True, help="股票代码")
    holdings_add.add_argument("--name", required=True, help="股票名称")
    holdings_add.add_argument("--shares", type=int, default=None, help="持股数量")
    holdings_add.add_argument("--price", type=float, default=None, help="买入成本价")
    holdings_add.add_argument("--sector", default=None, help="所属板块")
    holdings_add.add_argument("--stop-loss", type=float, default=None, help="止损价")
    holdings_add.add_argument("--market", default="A股", help="市场（默认 A股）")
    holdings_add.add_argument("--entry-reason", default=None, help="买入原因（开仓逻辑）")
    holdings_add.add_argument("--note", default=None, help="备注（持仓期间调仓记录等）")

    holdings_remove = db_sub.add_parser("holdings-remove", help="移除持仓（置 closed）")
    holdings_remove.add_argument("--code", required=True, help="股票代码")

    db_sub.add_parser("holdings-list", help="列出当前持仓")

    holdings_refresh = db_sub.add_parser(
        "holdings-refresh",
        help="回填 SQLite 持仓现价与技术快照（需配置 Tushare 等数据源）",
    )
    holdings_refresh.add_argument("--date", required=True, help="交易日 YYYY-MM-DD")
    holdings_refresh.add_argument("--json", action="store_true", help="输出 JSON")

    holdings_import_yaml = db_sub.add_parser(
        "holdings-import-yaml",
        help="将 tracking/holdings.yaml 中的持仓导入 SQLite（upsert active）",
    )
    holdings_import_yaml.add_argument(
        "--file",
        default=None,
        help="YAML 路径（默认：仓库 tracking/holdings.yaml）",
    )

    # ── 关注池 ────────────────────────────────────────────────────
    wl_add = db_sub.add_parser("watchlist-add", help="添加到关注池")
    wl_add.add_argument("--code", required=True, help="股票代码")
    wl_add.add_argument("--name", required=True, help="股票名称")
    wl_add.add_argument("--tier", required=True,
                        choices=["tier1_core", "tier2_watch", "tier3_sector"],
                        help="关注层级")
    wl_add.add_argument("--reason", default=None, help="关注原因")
    wl_add.add_argument("--sector", default=None, help="所属板块")
    wl_add.add_argument("--note", default=None, help="备注")
    wl_add.add_argument("--source-note-id", type=int, default=None, help="来源老师笔记 ID（teacher_notes.id）")

    wl_remove = db_sub.add_parser("watchlist-remove", help="移除关注池标的（置 removed）")
    wl_remove.add_argument("--code", required=True, help="股票代码")

    wl_update = db_sub.add_parser("watchlist-update", help="更新关注池标的信息")
    wl_update.add_argument("--code", required=True, help="股票代码")
    wl_update.add_argument("--tier", default=None,
                           choices=["tier1_core", "tier2_watch", "tier3_sector"],
                           help="新层级")
    wl_update.add_argument("--status", default=None,
                           choices=["watching", "tracking", "removed"],
                           help="新状态")
    wl_update.add_argument("--note", default=None, help="备注")

    wl_list = db_sub.add_parser("watchlist-list", help="列出关注池")
    wl_list.add_argument("--tier", default=None, help="按层级过滤")
    wl_list.add_argument("--status", default="watching", help="按状态过滤（默认 watching）")

    wl_sync_note = db_sub.add_parser(
        "watchlist-sync-from-note",
        help="按 teacher_notes.mentioned_stocks 将未在池标的写入关注池（第二步确认后使用）",
    )
    wl_sync_note.add_argument("--note-id", type=int, required=True, help="老师笔记 ID（teacher_notes.id）")

    stock_resolve = db_sub.add_parser(
        "stock-resolve",
        help="通过已配置 Provider 解析证券代码/简称（供 Agent 补码/补名使用）",
    )
    resolve_group = stock_resolve.add_mutually_exclusive_group(required=True)
    resolve_group.add_argument("--code", action="append", default=None, help="股票代码，可重复传入")
    resolve_group.add_argument("--name", action="append", default=None, help="证券简称，可重复传入")
    stock_resolve.add_argument("--date", default="2000-01-01", help="名称解析时使用的占位日期，默认 2000-01-01")
    stock_resolve.add_argument("--json", action="store_true", help="输出 JSON")

    # ── 交易记录 ──────────────────────────────────────────────────
    add_trade = db_sub.add_parser("add-trade", help="录入交易记录")
    add_trade.add_argument("--code", required=True, help="股票代码")
    add_trade.add_argument("--name", required=True, help="股票名称")
    add_trade.add_argument("--direction", required=True, choices=["buy", "sell"], help="方向")
    add_trade.add_argument("--price", required=True, type=float, help="成交价格")
    add_trade.add_argument("--date", required=True, help="交易日期 YYYY-MM-DD")
    add_trade.add_argument("--shares", type=int, default=None, help="股数")
    add_trade.add_argument("--sector", default=None, help="所属板块")
    add_trade.add_argument("--reason", default=None, help="交易理由")
    add_trade.add_argument("--pnl-pct", type=float, default=None, help="盈亏百分比（卖出时）")

    # ── 投资日历 ──────────────────────────────────────────────────
    add_cal = db_sub.add_parser("add-calendar", help="手动录入日历事件")
    add_cal.add_argument("--date", required=True, help="日期 YYYY-MM-DD")
    add_cal.add_argument("--event", required=True, help="事件描述")
    add_cal.add_argument("--category", default=None, help="类别（如 财经/政策/财报）")
    add_cal.add_argument("--impact", default=None, choices=["high", "medium", "low"], help="影响级别")
    add_cal.add_argument("--note", default=None, help="备注")

    # ── 黑名单 ────────────────────────────────────────────────────
    bl_add = db_sub.add_parser("blacklist-add", help="加入黑名单")
    bl_add.add_argument("--code", required=True, help="股票代码")
    bl_add.add_argument("--name", required=True, help="股票名称")
    bl_add.add_argument("--reason", default=None, help="原因")
    bl_add.add_argument("--until", default=None, help="到期日期 YYYY-MM-DD（留空永久）")

    # ── 统一搜索 ──────────────────────────────────────────────────
    db_search = db_sub.add_parser("db-search", help="跨表搜索（老师笔记/行业/宏观）")
    db_search.add_argument("--keyword", required=True, help="搜索关键词")
    db_search.add_argument("--type", dest="search_type", default="all",
                           choices=["all", "notes", "industry", "macro"],
                           help="搜索范围（默认 all）")
    db_search.add_argument("--from", dest="date_from", default=None, help="起始日期")
    db_search.add_argument("--to", dest="date_to", default=None, help="结束日期")


def handle_db_command(args: argparse.Namespace) -> None:
    """处理 db 子命令。"""
    action = getattr(args, "db_action", None)
    if not action:
        print("用法: main.py db {init|sync|reconcile|add-note|query-notes|"
              "add-industry|add-macro|holdings-add|holdings-remove|holdings-list|"
              "holdings-refresh|holdings-import-yaml|"
              "watchlist-add|watchlist-remove|watchlist-update|watchlist-list|"
              "watchlist-sync-from-note|stock-resolve|"
              "add-trade|add-calendar|blacklist-add|db-search}")
        return

    dispatch = {
        "init": lambda _: _cmd_init(),
        "sync": lambda _: _cmd_sync(),
        "reconcile": lambda _: _cmd_reconcile(),
        "add-note": _cmd_add_note,
        "query-notes": _cmd_query_notes,
        "add-industry": _cmd_add_industry,
        "add-macro": _cmd_add_macro,
        "holdings-add": _cmd_holdings_add,
        "holdings-remove": _cmd_holdings_remove,
        "holdings-list": _cmd_holdings_list,
        "holdings-refresh": _cmd_holdings_refresh,
        "holdings-import-yaml": _cmd_holdings_import_yaml,
        "watchlist-add": _cmd_watchlist_add,
        "watchlist-remove": _cmd_watchlist_remove,
        "watchlist-update": _cmd_watchlist_update,
        "watchlist-list": _cmd_watchlist_list,
        "watchlist-sync-from-note": _cmd_watchlist_sync_from_note,
        "stock-resolve": _cmd_stock_resolve,
        "add-trade": _cmd_add_trade,
        "add-calendar": _cmd_add_calendar,
        "blacklist-add": _cmd_blacklist_add,
        "db-search": _cmd_db_search,
    }
    fn = dispatch.get(action)
    if fn:
        fn(args)
    else:
        print(f"未知子命令: {action}")


# ── 管理命令实现 ──────────────────────────────────────────────────

def _cmd_init() -> None:
    with get_db() as conn:
        migrate(conn)
        results = import_all(conn)
    print(f"数据库初始化完成: {results}")


def _cmd_sync() -> None:
    succeeded, failed = retry_pending()
    print(f"重试完成: 成功 {succeeded}, 失败 {failed}")


def _cmd_reconcile() -> None:
    diffs = reconcile_daily_market()
    if not diffs:
        print("✅ YAML 与 DB 数据一致")
    else:
        print(f"⚠️ 发现 {len(diffs)} 处不一致:")
        for d in diffs:
            print(f"  {d}")


# ── 老师观点实现 ──────────────────────────────────────────────────

def _cmd_add_note(args: argparse.Namespace) -> None:
    from . import queries as Q

    stocks_list: list[dict] = []
    if args.stocks:
        stocks_list = json.loads(args.stocks)
        try:
            Q.validate_mentioned_stocks_entries(stocks_list)
        except ValueError as err:
            print(f"❌ {err}", file=sys.stderr)
            sys.exit(1)
    raw_content = _read_raw_content(args)
    sync_from_stocks = getattr(args, "sync_watchlist_from_stocks", False)

    with get_db() as conn:
        migrate(conn)
        teacher_id = Q.get_or_create_teacher(conn, args.teacher)

        kwargs: dict = {
            "date": args.date,
            "title": args.title,
            "source_type": args.source_type,
            "input_by": args.input_by,
        }
        if args.core_view:
            kwargs["core_view"] = args.core_view
        if raw_content:
            kwargs["raw_content"] = raw_content
        if args.tags:
            kwargs["tags"] = json.loads(args.tags)
        if args.key_points:
            kwargs["key_points"] = json.loads(args.key_points)
        if args.sectors:
            kwargs["sectors"] = json.loads(args.sectors)
        if args.position_advice:
            kwargs["position_advice"] = args.position_advice
        if stocks_list:
            kwargs["mentioned_stocks"] = stocks_list

        note_id = Q.insert_teacher_note(conn, teacher_id=teacher_id, **kwargs)

        att_count = 0
        if args.attachment:
            for att_path in args.attachment:
                src = Path(att_path)
                if src.exists():
                    dest_dir = PROJECT_ROOT / "data" / "attachments" / args.date
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    dest = dest_dir / src.name
                    shutil.copy2(src, dest)
                    rel_path = str(dest.relative_to(PROJECT_ROOT))
                    Q.insert_attachment(conn, note_id, rel_path)
                    att_count += 1
                else:
                    logger.warning("附件不存在，跳过: %s", att_path)

        candidates: list[dict] = []
        skipped: list[dict] = []
        if stocks_list:
            if sync_from_stocks:
                sync_result = Q.sync_watchlist_from_mentioned_stocks(
                    conn,
                    note_id=note_id,
                    note_date=args.date,
                    title=args.title,
                    teacher_name=args.teacher,
                    stocks=stocks_list,
                )
                for a in sync_result["added"]:
                    candidates.append({
                        "code": a["code"],
                        "name": a["name"],
                        "tier": a["tier"],
                        "note_id": note_id,
                        "watchlist_id": a["watchlist_id"],
                    })
                for s in sync_result["skipped"]:
                    skipped.append({"code": s["code"], "name": s["name"]})
            else:
                for stock in stocks_list:
                    code = stock.get("code", "")
                    name = stock.get("name", "")
                    tier = Q.normalize_watchlist_tier(stock.get("tier"))
                    if not code:
                        continue
                    if Q.check_watchlist_exists(conn, code):
                        skipped.append({"code": code, "name": name})
                    else:
                        candidates.append({"code": code, "name": name, "tier": tier, "note_id": note_id})

    att_info = f", 附件 {att_count} 个" if att_count else ""
    print(f"✅ 已录入笔记 (id={note_id}): {args.teacher} - {args.title}{att_info}")

    if stocks_list:
        considered = len(candidates) + len(skipped)
        new_count = len(candidates)
        if considered > 0:
            if sync_from_stocks:
                print(f"📋 关注池同步 ({new_count} 新增 / {considered} 统计):")
                for c in candidates:
                    wid = c.get("watchlist_id")
                    extra = f", watchlist id={wid}" if wid is not None else ""
                    print(f"  - {c['code']} {c['name']} [{c['tier']}] (已加入{extra})")
                for s in skipped:
                    print(f"  - {s['code']} {s['name']} (已在关注池，跳过)")
            else:
                print(f"📋 候选关注池 ({new_count}/{considered}):")
                for c in candidates:
                    print(f"  - {c['code']} {c['name']} [{c['tier']}] (建议加入)")
                for s in skipped:
                    print(f"  - {s['code']} {s['name']} (已在关注池，跳过)")
            if candidates:
                cand_out = [
                    {k: v for k, v in c.items() if k != "watchlist_id"}
                    for c in candidates
                ]
                print(f"WATCHLIST_CANDIDATES: {json.dumps(cand_out, ensure_ascii=False)}")
        else:
            hint = (
                "关注池同步"
                if sync_from_stocks
                else "候选关注池"
            )
            print(
                f"📋 {hint}: --stocks 中无有效股票代码（每项需含非空 code），已跳过"
            )


def _cmd_query_notes(args: argparse.Namespace) -> None:
    from . import queries as Q

    with get_db() as conn:
        migrate(conn)
        results = Q.search_teacher_notes(
            conn, args.keyword,
            teacher_name=args.teacher,
            date_from=args.date_from,
            date_to=args.date_to,
        )

    if not results:
        print(f"未找到包含「{args.keyword}」的笔记")
        return

    print(f"找到 {len(results)} 条结果:")
    for r in results:
        print(f"  [{r['date']}] {r.get('teacher_name', '?')} - {r['title']}")
        if r.get("core_view"):
            preview = r["core_view"][:80]
            print(f"    观点: {preview}...")


# ── 行业 / 宏观实现 ───────────────────────────────────────────────

def _cmd_add_industry(args: argparse.Namespace) -> None:
    from . import queries as Q

    kwargs: dict = {
        "date": args.date,
        "sector_name": args.sector,
        "content": args.content,
    }
    if args.info_type:
        kwargs["info_type"] = args.info_type
    if args.source:
        kwargs["source"] = args.source
    if args.confidence:
        kwargs["confidence"] = args.confidence
    if args.tags:
        kwargs["tags"] = json.loads(args.tags)

    with get_db() as conn:
        migrate(conn)
        info_id = Q.insert_industry_info(conn, **kwargs)

    print(f"✅ 已录入行业信息 (id={info_id}): {args.sector} [{args.date}]")


def _cmd_add_macro(args: argparse.Namespace) -> None:
    from . import queries as Q

    kwargs: dict = {
        "date": args.date,
        "category": args.category,
        "title": args.title,
        "content": args.content,
    }
    if args.source:
        kwargs["source"] = args.source
    if args.impact:
        kwargs["impact_assessment"] = args.impact
    if args.tags:
        kwargs["tags"] = json.loads(args.tags)

    with get_db() as conn:
        migrate(conn)
        info_id = Q.insert_macro_info(conn, **kwargs)

    print(f"✅ 已录入宏观信息 (id={info_id}): {args.title} [{args.date}]")


# ── 持仓池实现（DB 路径）─────────────────────────────────────────

def _cmd_holdings_add(args: argparse.Namespace) -> None:
    from . import queries as Q

    kwargs: dict = {
        "stock_code": args.code,
        "stock_name": args.name,
        "market": args.market,
        "status": "active",
    }
    if args.shares is not None:
        kwargs["shares"] = args.shares
    if args.price is not None:
        kwargs["entry_price"] = args.price
    if args.sector:
        kwargs["sector"] = args.sector
    if args.stop_loss is not None:
        kwargs["stop_loss"] = args.stop_loss
    if args.entry_reason:
        kwargs["entry_reason"] = args.entry_reason
    if args.note:
        kwargs["note"] = args.note

    with get_db() as conn:
        migrate(conn)
        hid = Q.upsert_holding(conn, **kwargs)

    print(f"✅ 已添加持仓 (id={hid}): {args.name} ({args.code})")


def _cmd_holdings_remove(args: argparse.Namespace) -> None:
    from . import queries as Q

    with get_db() as conn:
        migrate(conn)
        closed = Q.close_active_holdings_by_code(conn, args.code)
        if not closed:
            print(f"⚠️ 未找到持仓: {args.code}")
            return

    print(f"✅ 已移除持仓: {args.code}（共 {closed} 条置为 closed）")


def _cmd_holdings_list(args: argparse.Namespace) -> None:
    from . import queries as Q

    with get_db() as conn:
        migrate(conn)
        holdings = Q.get_holdings(conn, status="active")

    if not holdings:
        print("当前无持仓")
        return

    print(f"当前持仓 ({len(holdings)} 只):")
    for h in holdings:
        shares_str = f"{h['shares']}股" if h.get("shares") else "-"
        price_str = f"成本 {h['entry_price']}" if h.get("entry_price") else "-"
        sl_str = f"止损 {h['stop_loss']}" if h.get("stop_loss") else ""
        print(f"  {h['stock_code']} {h['stock_name']} | {shares_str} | "
              f"{price_str} | {h.get('sector', '-')} {sl_str}".rstrip())
        reason = (h.get("entry_reason") or "").strip()
        note = (h.get("note") or "").strip()
        if reason:
            print(f"    买入原因: {reason[:80]}{'…' if len(reason) > 80 else ''}")
        if note:
            print(f"    备注:     {note[:80]}{'…' if len(note) > 80 else ''}")


def _cmd_holdings_refresh(args: argparse.Namespace) -> None:
    """回填持仓现价（与旧 `main.py holdings --refresh` 行为一致）。"""
    main_mod = _resolve_main_runtime()
    from collectors.holdings import HoldingsCollector
    from utils.network_env import without_standard_http_proxy

    config = main_mod.load_config()
    date_str = args.date
    with without_standard_http_proxy():
        registry = main_mod.setup_providers(config)
        registry.initialize_all()
        hc = HoldingsCollector(registry=registry)
        hc.load()
        result = hc.refresh_sqlite_quotes(date_str)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    print(
        f"SQLite 持仓快照回填 {result['date']}: "
        f"快照更新 {result['updated']} 条, 当前价更新 {result['current_price_updated']} 条, "
        f"失败 {result['failed']} 条, 跳过 {result['skipped']} 条"
    )
    for item in result["items"]:
        status = item.get("status")
        if status == "updated":
            print(
                f"  [updated] {item['code']} {item['name']} | "
                f"收盘 {item.get('close')} | 盈亏 {item.get('pnl_pct')}% | "
                f"换手 {item.get('turnover_rate')}% | "
                f"MA5/10/20 {item.get('ma5')}/{item.get('ma10')}/{item.get('ma20')} | "
                f"量能 {item.get('volume_vs_ma5')} | "
                f"{'已更新当前价' if item.get('current_price_updated') else '仅写快照'}"
            )
        elif status == "error":
            print(f"  [error] {item['code']} {item['name']} | {item.get('error')}")
        else:
            print(f"  [skip] {item['code']} {item['name']} | {item.get('reason')}")


def _cmd_holdings_import_yaml(args: argparse.Namespace) -> None:
    from . import queries as Q

    path = Path(args.file).expanduser() if args.file else PROJECT_ROOT / "tracking" / "holdings.yaml"
    if not path.is_file():
        print(f"❌ 文件不存在: {path}")
        return
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        print(f"❌ 文件非 UTF-8 或编码损坏，无法读取: {path}\n{e}")
        return
    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        print(f"❌ YAML 解析失败: {e}")
        return
    if parsed is None:
        parsed = {}
    if not isinstance(parsed, dict):
        print(f"⚠️ YAML 根须为映射（对象），当前为 {type(parsed).__name__}，跳过")
        return
    data = parsed
    raw_holdings = data.get("holdings")
    if raw_holdings is None:
        rows: list = []
    elif isinstance(raw_holdings, list):
        rows = raw_holdings
    else:
        print(f"⚠️ holdings 须为序列，当前为 {type(raw_holdings).__name__}，跳过")
        return
    if not rows:
        print("⚠️ YAML 中无 holdings 条目，跳过")
        return
    imported = 0
    skip_reasons: dict[str, int] = {}
    with get_db() as conn:
        migrate(conn)
        for h in rows:
            if not isinstance(h, dict):
                skip_reasons["not_mapping"] = skip_reasons.get("not_mapping", 0) + 1
                continue
            code = (h.get("code") or "").strip()
            if not code:
                skip_reasons["empty_code"] = skip_reasons.get("empty_code", 0) + 1
                continue
            shares, sr = _coerce_holdings_shares(h.get("shares"))
            if shares is None:
                key = sr or "invalid_shares"
                skip_reasons[key] = skip_reasons.get(key, 0) + 1
                continue
            cost, cr = _coerce_holdings_cost(h.get("cost"))
            if cost is None:
                key = cr or "invalid_cost"
                skip_reasons[key] = skip_reasons.get(key, 0) + 1
                continue
            entry_reason = (h.get("entry_reason") or h.get("reason") or "").strip() or None
            note_val = (h.get("note") or "").strip() or None
            Q.upsert_holding(
                conn,
                stock_code=code,
                stock_name=(h.get("name") or "").strip() or code,
                shares=shares,
                entry_price=cost,
                sector=(h.get("sector") or "").strip() or None,
                entry_reason=entry_reason,
                note=note_val,
                status="active",
            )
            imported += 1
    skipped = sum(skip_reasons.values())
    msg = f"✅ 已从 {path} 导入 {imported} 条持仓到 SQLite（upsert active）"
    if skipped:
        detail = ", ".join(f"{k}={v}" for k, v in sorted(skip_reasons.items()))
        msg += f"，跳过 {skipped} 条（{detail}）"
    print(msg)


# ── 关注池实现 ────────────────────────────────────────────────────

def _cmd_watchlist_sync_from_note(args: argparse.Namespace) -> None:
    from . import queries as Q

    with get_db() as conn:
        migrate(conn)
        row = Q.get_teacher_note_by_id(conn, args.note_id)
        if not row:
            print(f"❌ 笔记不存在: id={args.note_id}")
            return
        raw_ms = row.get("mentioned_stocks")
        if not raw_ms:
            print("⚠️ 该笔记无 mentioned_stocks，跳过关注池同步")
            return
        if isinstance(raw_ms, str):
            stocks_list = json.loads(raw_ms)
        elif isinstance(raw_ms, (list, dict)):
            stocks_list = raw_ms if isinstance(raw_ms, list) else [raw_ms]
        else:
            print("⚠️ mentioned_stocks 格式无效")
            return
        if not isinstance(stocks_list, list):
            print("⚠️ mentioned_stocks 须为 JSON 数组")
            return
        try:
            Q.validate_mentioned_stocks_entries(stocks_list)
        except ValueError as err:
            print(f"❌ {err}", file=sys.stderr)
            return
        sync_result = Q.sync_watchlist_from_mentioned_stocks(
            conn,
            note_id=args.note_id,
            note_date=row["date"],
            title=row["title"],
            teacher_name=row.get("teacher_name"),
            stocks=stocks_list,
        )

    candidates = []
    skipped = []
    for a in sync_result["added"]:
        candidates.append({
            "code": a["code"],
            "name": a["name"],
            "tier": a["tier"],
            "note_id": args.note_id,
            "watchlist_id": a["watchlist_id"],
        })
    for s in sync_result["skipped"]:
        skipped.append({"code": s["code"], "name": s["name"]})

    considered = len(candidates) + len(skipped)
    if considered > 0:
        print(f"📋 关注池同步（来自笔记 #{args.note_id}）({len(candidates)} 新增 / {considered} 统计):")
        for c in candidates:
            print(
                f"  - {c['code']} {c['name']} [{c['tier']}] "
                f"(已加入, watchlist id={c['watchlist_id']})"
            )
        for s in skipped:
            print(f"  - {s['code']} {s['name']} (已在关注池，跳过)")
        if candidates:
            cand_out = [{k: v for k, v in c.items() if k != "watchlist_id"} for c in candidates]
            print(f"WATCHLIST_CANDIDATES: {json.dumps(cand_out, ensure_ascii=False)}")
    else:
        print(
            "📋 关注池同步: mentioned_stocks 中无有效股票代码（每项需含非空 code），已跳过"
        )


def _cmd_stock_resolve(args: argparse.Namespace) -> None:
    from services.stock_resolver import resolve_stock_codes, resolve_stock_names
    from utils.network_env import without_standard_http_proxy

    main_mod = _resolve_main_runtime()
    config = main_mod.load_config()
    with without_standard_http_proxy():
        registry = main_mod.setup_providers(config)
        registry.initialize_all()
        if args.code:
            result = resolve_stock_codes(registry, args.code)
        else:
            result = resolve_stock_names(registry, args.name, args.date)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    mode_label = "代码补名称" if result["mode"] == "code" else "名称补代码"
    print(f"解析模式: {mode_label}")
    if result.get("error"):
        print(f"数据源错误: {result['error']}")

    if result["resolved"]:
        print(f"已解析 {len(result['resolved'])} 条:")
        for item in result["resolved"]:
            print(f"  - {item['query']} -> {item['code']} {item['name']}")
    if result["ambiguous"]:
        print(f"存在歧义 {len(result['ambiguous'])} 条:")
        for item in result["ambiguous"]:
            candidates = " / ".join(f"{c['code']} {c['name']}" for c in item["candidates"])
            print(f"  - {item['query']}: {candidates}")
    if result["not_found"]:
        print(f"未命中 {len(result['not_found'])} 条:")
        for item in result["not_found"]:
            print(f"  - {item['query']} ({item['reason']})")
    if not result["resolved"] and not result["ambiguous"] and not result["not_found"]:
        print("无待解析输入")


def _cmd_watchlist_add(args: argparse.Namespace) -> None:
    from . import queries as Q

    import datetime
    kwargs: dict = {
        "stock_code": args.code,
        "stock_name": args.name,
        "tier": args.tier,
        "add_date": datetime.date.today().isoformat(),
    }
    if args.reason:
        kwargs["add_reason"] = args.reason
    if args.sector:
        kwargs["sector"] = args.sector
    if args.note:
        kwargs["note"] = args.note
    if args.source_note_id:
        kwargs["source_note_id"] = args.source_note_id

    with get_db() as conn:
        migrate(conn)
        wid = Q.insert_watchlist(conn, **kwargs)

    src_info = f", 来源笔记 #{args.source_note_id}" if args.source_note_id else ""
    print(f"✅ 已添加到关注池 (id={wid}): {args.name} ({args.code}) [{args.tier}]{src_info}")


def _cmd_watchlist_remove(args: argparse.Namespace) -> None:
    from . import queries as Q

    with get_db() as conn:
        migrate(conn)
        rows = conn.execute(
            "SELECT id FROM watchlist WHERE stock_code = ? AND status != 'removed'",
            (args.code,),
        ).fetchall()
        if not rows:
            print(f"⚠️ 未在关注池中找到: {args.code}")
            return
        for row in rows:
            Q.update_watchlist_item(conn, row["id"], status="removed")

    print(f"✅ 已从关注池移除: {args.code}")


def _cmd_watchlist_update(args: argparse.Namespace) -> None:
    from . import queries as Q

    with get_db() as conn:
        migrate(conn)
        rows = conn.execute(
            "SELECT id FROM watchlist WHERE stock_code = ? AND status != 'removed'",
            (args.code,),
        ).fetchall()
        if not rows:
            print(f"⚠️ 未在关注池中找到: {args.code}")
            return

        kwargs: dict = {}
        if args.tier:
            kwargs["tier"] = args.tier
        if args.status:
            kwargs["status"] = args.status
        if args.note:
            kwargs["note"] = args.note

        if not kwargs:
            print("⚠️ 未指定任何更新字段（--tier / --status / --note）")
            return

        for row in rows:
            Q.update_watchlist_item(conn, row["id"], **kwargs)

    changes = ", ".join(f"{k}={v}" for k, v in kwargs.items())
    print(f"✅ 已更新关注池 {args.code}: {changes}")


def _cmd_watchlist_list(args: argparse.Namespace) -> None:
    from . import queries as Q

    with get_db() as conn:
        migrate(conn)
        items = Q.get_watchlist(conn, tier=args.tier, status=args.status)

    if not items:
        print(f"关注池为空（tier={args.tier}, status={args.status}）")
        return

    print(f"关注池 ({len(items)} 只, status={args.status}):")
    for it in items:
        tier_str = it.get("tier", "-")
        sector_str = it.get("sector", "-")
        reason_str = it.get("add_reason", "")
        print(f"  [{tier_str}] {it['stock_code']} {it['stock_name']} | "
              f"{sector_str} | {reason_str}")


# ── 交易记录实现 ──────────────────────────────────────────────────

def _cmd_add_trade(args: argparse.Namespace) -> None:
    from . import queries as Q

    kwargs: dict = {
        "date": args.date,
        "stock_code": args.code,
        "stock_name": args.name,
        "direction": args.direction,
        "price": args.price,
    }
    if args.shares is not None:
        kwargs["shares"] = args.shares
    if args.sector:
        kwargs["sector"] = args.sector
    if args.reason:
        kwargs["entry_reason" if args.direction == "buy" else "exit_reason"] = args.reason
    if args.pnl_pct is not None:
        kwargs["pnl_pct"] = args.pnl_pct

    with get_db() as conn:
        migrate(conn)
        tid = Q.insert_trade(conn, **kwargs)

    direction_cn = "买入" if args.direction == "buy" else "卖出"
    print(f"✅ 已录入交易 (id={tid}): {direction_cn} {args.name} ({args.code}) @{args.price}")


# ── 日历事件实现 ──────────────────────────────────────────────────

def _cmd_add_calendar(args: argparse.Namespace) -> None:
    from . import queries as Q

    kwargs: dict = {
        "date": args.date,
        "event": args.event,
    }
    if args.category:
        kwargs["category"] = args.category
    if args.impact:
        kwargs["impact"] = args.impact
    if args.note:
        kwargs["note"] = args.note

    with get_db() as conn:
        migrate(conn)
        eid = Q.insert_calendar_event(conn, **kwargs)

    print(f"✅ 已录入日历事件 (id={eid}): {args.event} [{args.date}]")


# ── 黑名单实现 ────────────────────────────────────────────────────

def _cmd_blacklist_add(args: argparse.Namespace) -> None:
    from . import queries as Q

    with get_db() as conn:
        migrate(conn)
        bid = Q.insert_blacklist(
            conn,
            stock_code=args.code,
            stock_name=args.name,
            reason=args.reason,
            until=args.until,
        )

    print(f"✅ 已加入黑名单 (id={bid}): {args.name} ({args.code})")


# ── 统一搜索实现 ──────────────────────────────────────────────────

def _cmd_db_search(args: argparse.Namespace) -> None:
    from . import queries as Q

    type_map = {
        "all": None,
        "notes": ["teacher_notes"],
        "industry": ["industry_info"],
        "macro": ["macro_info"],
    }
    search_types = type_map.get(args.search_type)

    with get_db() as conn:
        migrate(conn)
        results = Q.unified_search(
            conn, args.keyword,
            types=search_types,
            date_from=args.date_from,
            date_to=args.date_to,
        )

    total = sum(len(v) for v in results.values())
    if total == 0:
        print(f"未找到包含「{args.keyword}」的记录")
        return

    print(f"共找到 {total} 条结果:")
    for src, rows in results.items():
        if not rows:
            continue
        src_cn = {"teacher_notes": "老师笔记", "industry_info": "行业信息",
                  "macro_info": "宏观信息"}.get(src, src)
        print(f"\n[{src_cn}] {len(rows)} 条:")
        for r in rows[:5]:
            title = r.get("title") or r.get("sector_name") or r.get("content", "")[:30]
            date = r.get("date", "")
            print(f"  [{date}] {title}")
        if len(rows) > 5:
            print(f"  ... 还有 {len(rows) - 5} 条")
