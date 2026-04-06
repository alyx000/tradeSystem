"""main.py db 子命令实现：供 AI agent 调用的标准化写入接口。"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from pathlib import Path

from .connection import get_db
from .dual_write import reconcile_daily_market, retry_pending
from .migrate import import_all, migrate

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


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
    holdings_add.add_argument("--note", default=None, help="备注")

    holdings_remove = db_sub.add_parser("holdings-remove", help="移除持仓（置 closed）")
    holdings_remove.add_argument("--code", required=True, help="股票代码")

    db_sub.add_parser("holdings-list", help="列出当前持仓")

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
              "watchlist-add|watchlist-remove|watchlist-update|watchlist-list|"
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
        "watchlist-add": _cmd_watchlist_add,
        "watchlist-remove": _cmd_watchlist_remove,
        "watchlist-update": _cmd_watchlist_update,
        "watchlist-list": _cmd_watchlist_list,
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
    raw_content = _read_raw_content(args)

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

        # 输出候选关注池条目
        candidates = []
        skipped = []
        if stocks_list:
            for stock in stocks_list:
                code = stock.get("code", "")
                name = stock.get("name", "")
                tier = stock.get("tier", "tier3_sector")
                if not code:
                    continue
                if Q.check_watchlist_exists(conn, code):
                    skipped.append({"code": code, "name": name})
                else:
                    candidates.append({"code": code, "name": name, "tier": tier, "note_id": note_id})

    att_info = f", 附件 {att_count} 个" if att_count else ""
    print(f"✅ 已录入笔记 (id={note_id}): {args.teacher} - {args.title}{att_info}")

    if stocks_list:
        # 分母仅统计含非空 code 的条目，与 candidates/skipped 一致，避免缺 code 项稀释比例
        considered = len(candidates) + len(skipped)
        new_count = len(candidates)
        if considered > 0:
            print(f"📋 候选关注池 ({new_count}/{considered}):")
            for c in candidates:
                print(f"  - {c['code']} {c['name']} [{c['tier']}] (建议加入)")
            for s in skipped:
                print(f"  - {s['code']} {s['name']} (已在关注池，跳过)")
            if candidates:
                print(
                    f"WATCHLIST_CANDIDATES: {json.dumps(candidates, ensure_ascii=False)}"
                )
        else:
            print(
                "📋 候选关注池: --stocks 中无有效股票代码（每项需含非空 code），已跳过候选统计"
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


# ── 关注池实现 ────────────────────────────────────────────────────

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
