"""数据管理 CRUD 路由：老师观点、持仓、关注池、黑名单、行业/宏观、日历、交易记录、市场行情。"""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import date as _date
from pathlib import Path
from typing import Any, Optional

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_db_conn
from api.market_enrich import enrich_daily_market_row
from db import queries as Q
from services.holding_signals import build_holding_signals
from services.teacher_note_service import (
    TeacherNoteProvenanceConflict,
    create_teacher_note_idempotent,
)
from services.trend_leader import pool as tl_pool
from services.trend_leader.signals import signal_hits as tl_signal_hits
from services.volume_concentration import service as vc_service

_REPO_ROOT = Path(__file__).resolve().parents[3]

router = APIRouter(prefix="/api", tags=["crud"])


def _apply_market_ma5w_fallback(conn: sqlite3.Connection, row: dict | None) -> dict | None:
    if not row:
        return row
    if row.get("sh_above_ma5w") is not None and row.get("sz_above_ma5w") is not None:
        return row
    flags = Q.compute_ma5w_flags_from_history(
        conn,
        target_date=str(row.get("date") or ""),
        sh_close=row.get("sh_index_close"),
        sz_close=row.get("sz_index_close"),
    )
    for key, value in flags.items():
        if row.get(key) is None and value is not None:
            row[key] = value
    return row


def _sanitize_non_finite(value: Any) -> Any:
    """将 NaN/Inf 递归转换为 None，避免 JSON 序列化失败。"""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _sanitize_non_finite(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_non_finite(v) for v in value]
    return value


# ── Trend Leader（趋势主升观察池，只读） ──────────────────────────

# 只读响应白名单：只放非价位字段 + 信号 chip + 非价位归因（entry_trigger/branch_concepts）。
# 红线「不含价位」：raw last_signal 里含 near_ma5.ma5 / overheat.ma5 / trend.ma10（均线价位）
# 与 vol 等内部明细，**绝不外泄**——与 renderer 一致（renderer 只出 chip、不出数值，门2 codex 拦）。
_TREND_LEADER_PUBLIC_FIELDS = (
    "code", "name", "sw_l2", "first_limit_date", "entered_date",
    "last_seen_date", "days_in_pool", "status", "exit_date", "exit_reason",
)


def _trend_leader_public_row(r: dict) -> dict:
    """池行 → 只读 DTO：白名单非价位列 + 归因 + signal_hits（Pass1 行=null）。"""
    sig = r.get("last_signal") if isinstance(r.get("last_signal"), dict) else {}
    out = {k: r.get(k) for k in _TREND_LEADER_PUBLIC_FIELDS}
    out["entry_trigger"] = sig.get("entry_trigger")          # 涨停 / 双创15%加速（事件，非价位）
    # branch_concepts 归一为 **纯字符串列表**：脏值/漂移成非 list（前端 .join 崩，round-1）或
    # list 内含 dict（如 [{"ma5":15.2}] 会把价位明细透出 API，绕过只查顶层 key 的红线，round-3）。
    # 只保留 str 元素：非价位、不崩、红线无条件成立。
    bc = sig.get("branch_concepts")
    out["branch_concepts"] = [x for x in bc if isinstance(x, str)] if isinstance(bc, list) else []
    out["signal_hits"] = tl_signal_hits(r.get("last_signal"))
    return out


@router.get("/trend-leaders")
def list_trend_leaders(
    status: Optional[str] = Query(None, pattern="^(active|exited)$"),
    conn: sqlite3.Connection = Depends(get_db_conn),
):
    """趋势主升观察池（盘后只读 [判断]）：active/exited/全部。

    仅返回白名单字段（非价位）：基础列 + 归因 entry_trigger/branch_concepts + signal_hits
    （在池信号 chip 布尔，Pass1 未维护行=null）。**不外泄 raw last_signal**——其内含 MA5/MA10/vol
    等均线价位明细，违红线「不含价位」（与 renderer 只出 chip 一致）。
    """
    rows = tl_pool.list_pool(conn, status=status)
    return [_trend_leader_public_row(r) for r in rows]


# ── Teachers / Notes ──────────────────────────────────────────

@router.get("/teachers")
def list_teachers(conn: sqlite3.Connection = Depends(get_db_conn)):
    return Q.list_teachers(conn)


# 列表预览截断长度：足够 KnowledgeWorkbench 等列表页展示摘要，又远小于全文。
_RAW_CONTENT_PREVIEW_LEN = 200


def _strip_heavy_fields(notes: list[dict]) -> list[dict]:
    """从列表笔记中移除 raw_content（单条最大数十 KB、占列表 payload ~82%），
    显著缩小响应体积；全文仅在前端展开时经详情端点 GET /teacher-notes/{id} 按需返回。

    剔除前保留两个轻量派生字段：
    - has_raw_content：是否存在全文，供前端决定是否渲染「原始观点全文」入口；
    - raw_content_preview：全文前 200 字摘要，供 KnowledgeWorkbench 等列表页预览
      （部分老师笔记仅录入 raw_content 无 core_view，需要它兜底预览，否则只剩标题）。

    注：此处仍读取 raw_content 以计算预览，未改为显式轻字段 SELECT。理由：① 列表 limit≤500
    行封顶，实测整条列表请求 <0.1s（含全文读取），读取非瓶颈，被消除的是 ~3.1MB 出站序列化/传输；
    ② 关键词分支的 LIKE 匹配本就要读 raw_content；③ 该表 schema 持续演进，显式列清单易漂移。
    """
    for note in notes:
        raw = note.get("raw_content") or ""
        note["has_raw_content"] = bool(raw)
        note["raw_content_preview"] = raw[:_RAW_CONTENT_PREVIEW_LEN] or None
        note.pop("raw_content", None)
    return notes


def _attach_note_attachments(conn: sqlite3.Connection, notes: list[dict]) -> list[dict]:
    """为笔记列表批量附加 attachments 字段。"""
    if not notes:
        return notes
    ids = [n["id"] for n in notes]
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT note_id, file_path, file_type, description FROM note_attachments "
        f"WHERE note_id IN ({placeholders}) ORDER BY id",
        ids,
    ).fetchall()
    att_map: dict[int, list[dict]] = {}
    for r in rows:
        att_map.setdefault(r["note_id"], []).append({
            "file_path": r["file_path"],
            "file_type": r["file_type"],
            "description": r["description"],
            "url": f"/attachments/{r['file_path'].removeprefix('data/attachments/').lstrip('/')}",
        })
    for note in notes:
        note["attachments"] = att_map.get(note["id"], [])
    return notes


@router.get("/teacher-notes")
def list_notes(
    teacher: Optional[str] = None,
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    keyword: Optional[str] = None,
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(get_db_conn),
):
    if keyword:
        notes = Q.search_teacher_notes(
            conn,
            keyword,
            teacher_name=teacher,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            offset=offset,
        )
        return _attach_note_attachments(conn, _strip_heavy_fields(notes))
    sql = "SELECT n.*, t.name as teacher_name FROM teacher_notes n JOIN teachers t ON n.teacher_id = t.id WHERE 1=1"
    params: list[Any] = []
    if teacher:
        sql += " AND t.name = ?"
        params.append(teacher)
    if date_from:
        sql += " AND n.date >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND n.date <= ?"
        params.append(date_to)
    sql += " ORDER BY n.date DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    notes = [dict(r) for r in conn.execute(sql, params).fetchall()]
    return _attach_note_attachments(conn, _strip_heavy_fields(notes))


@router.get("/teacher-notes/{note_id}")
def get_note(note_id: int, conn: sqlite3.Connection = Depends(get_db_conn)):
    row = conn.execute(
        "SELECT n.*, t.name as teacher_name FROM teacher_notes n "
        "JOIN teachers t ON n.teacher_id = t.id WHERE n.id = ?", (note_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, "Note not found")
    note = dict(row)
    return _attach_note_attachments(conn, [note])[0]


@router.post("/teacher-notes")
def create_note(body: dict, conn: sqlite3.Connection = Depends(get_db_conn)):
    payload = dict(body)
    sync_wl = bool(payload.pop("sync_watchlist_from_mentions", False))
    teacher_name = payload.pop("teacher_name", None) or payload.pop("teacher", None)
    if not teacher_name:
        raise HTTPException(422, "teacher_name required")
    mentioned = payload.get("mentioned_stocks")
    if isinstance(mentioned, list) and mentioned:
        try:
            Q.validate_mentioned_stocks_entries(mentioned)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
    try:
        write_result = create_teacher_note_idempotent(
            conn,
            teacher_name=teacher_name,
            payload=payload,
        )
    except TeacherNoteProvenanceConflict as e:
        conn.rollback()
        raise HTTPException(409, str(e)) from e
    except (TypeError, ValueError) as e:
        conn.rollback()
        raise HTTPException(422, str(e)) from e
    note_id = write_result.note_id
    wl_result: dict[str, list] | None = None
    if write_result.created and sync_wl and mentioned:
        stocks_list = mentioned
        if isinstance(stocks_list, str):
            try:
                stocks_list = json.loads(stocks_list)
            except json.JSONDecodeError as e:
                raise HTTPException(422, f"mentioned_stocks invalid JSON: {e}") from e
        if isinstance(stocks_list, list) and stocks_list:
            try:
                wl_result = Q.sync_watchlist_from_mentioned_stocks(
                    conn,
                    note_id=note_id,
                    note_date=str(payload.get("date") or ""),
                    title=str(payload.get("title") or ""),
                    teacher_name=teacher_name,
                    stocks=stocks_list,
                    input_by=payload.get("input_by"),
                )
            except ValueError as e:
                conn.rollback()
                raise HTTPException(422, str(e)) from e
        else:
            wl_result = {"added": [], "skipped": []}
    conn.commit()
    out: dict[str, Any] = {
        "id": note_id,
        "created": write_result.created,
        "deduplicated_by": write_result.matched_by,
    }
    if write_result.created and sync_wl:
        out["watchlist_sync"] = wl_result or {"added": [], "skipped": []}
    return out


@router.put("/teacher-notes/{note_id}")
def update_note(note_id: int, body: dict, conn: sqlite3.Connection = Depends(get_db_conn)):
    try:
        Q.update_teacher_note(conn, note_id, **body)
    except ValueError as e:
        raise HTTPException(422, str(e))
    conn.commit()
    return {"ok": True}


@router.delete("/teacher-notes/{note_id}")
def delete_note(note_id: int, conn: sqlite3.Connection = Depends(get_db_conn)):
    cur = conn.execute("DELETE FROM teacher_notes WHERE id = ?", (note_id,))
    if cur.rowcount == 0:
        raise HTTPException(404, "Note not found")
    conn.commit()
    return {"ok": True}


_API_INPUT_BY = "web"  # API 属人工入口,写入审计缺省(value-watch spec v8)

# ── Holdings ──────────────────────────────────────────────────

@router.get("/holdings")
def list_holdings(status: Optional[str] = "active",
                  conn: sqlite3.Connection = Depends(get_db_conn)):
    return Q.get_holdings(conn, status=status)


@router.get("/holdings/signals")
def list_holding_signals(
    date: str = Query(default_factory=lambda: _date.today().isoformat()),
    conn: sqlite3.Connection = Depends(get_db_conn),
):
    return build_holding_signals(conn, date)


@router.get("/holdings/tasks")
def list_holding_tasks(
    status: Optional[str] = "open",
    date: Optional[str] = Query(None),
    conn: sqlite3.Connection = Depends(get_db_conn),
):
    return Q.list_holding_tasks(conn, status=status, date_to=date)


@router.put("/holdings/tasks/{task_id}")
def update_holding_task(task_id: int, body: dict, conn: sqlite3.Connection = Depends(get_db_conn)):
    try:
        Q.update_holding_task(conn, task_id, **body)
    except ValueError as e:
        raise HTTPException(422, str(e))
    conn.commit()
    return {"ok": True}


@router.get("/holdings/{hid}")
def get_holding(hid: int, conn: sqlite3.Connection = Depends(get_db_conn)):
    row = conn.execute("SELECT * FROM holdings WHERE id = ?", (hid,)).fetchone()
    if not row:
        raise HTTPException(404, "Holding not found")
    return dict(row)


@router.post("/holdings")
def create_holding(body: dict, conn: sqlite3.Connection = Depends(get_db_conn)):
    # 写入审计:API 属人工入口,body 可选 input_by、服务端缺省 "web"(value-watch spec v8)。
    # 不用 setdefault:显式传 null/空串会绕过缺省,POST 落 NULL、PUT 清空既有审计值(门1 M2)。
    if not body.get("input_by"):
        body["input_by"] = _API_INPUT_BY
    hid = Q.upsert_holding(conn, **body)
    conn.commit()
    return {"id": hid}


@router.put("/holdings/{hid}")
def update_holding_item(hid: int, body: dict, conn: sqlite3.Connection = Depends(get_db_conn)):
    if not body.get("input_by"):
        body["input_by"] = _API_INPUT_BY
    try:
        Q.update_holding(conn, hid, **body)
    except ValueError as e:
        raise HTTPException(422, str(e))
    conn.commit()
    return {"ok": True}


@router.delete("/holdings/{hid}")
def delete_holding_item(hid: int, input_by: str = _API_INPUT_BY,
                        conn: sqlite3.Connection = Depends(get_db_conn)):
    # spec v8:物理删除改 soft close——对齐 CLI holdings-remove 语义(status='closed'),
    # 行保留使 input_by 审计可落;Q.delete_holding 物理删除降为内部函数不再暴露。
    row = conn.execute("SELECT id, status FROM holdings WHERE id = ?", (hid,)).fetchone()
    if not row:
        raise HTTPException(404, "Holding not found")
    if row["status"] == "closed":
        return {"ok": True}  # 幂等:不重复 UPDATE,保留首次关闭者的 input_by 审计(门1 B-2)
    Q.update_holding(conn, hid, status="closed", input_by=input_by)
    conn.commit()
    return {"ok": True}


# ── Watchlist ─────────────────────────────────────────────────

@router.get("/watchlist")
def list_watchlist(tier: Optional[str] = None, status: str = "watching",
                   conn: sqlite3.Connection = Depends(get_db_conn)):
    return Q.get_watchlist(conn, tier=tier, status=status)


@router.get("/watchlist/{wid}")
def get_watchlist_item(wid: int, conn: sqlite3.Connection = Depends(get_db_conn)):
    row = conn.execute("SELECT * FROM watchlist WHERE id = ?", (wid,)).fetchone()
    if not row:
        raise HTTPException(404, "Watchlist item not found")
    return dict(row)


@router.post("/watchlist")
def create_watchlist_item(body: dict, conn: sqlite3.Connection = Depends(get_db_conn)):
    wid = Q.insert_watchlist(conn, **body)
    conn.commit()
    return {"id": wid}


@router.put("/watchlist/{wid}")
def update_watchlist(wid: int, body: dict, conn: sqlite3.Connection = Depends(get_db_conn)):
    try:
        Q.update_watchlist_item(conn, wid, **body)
    except ValueError as e:
        raise HTTPException(422, str(e))
    conn.commit()
    return {"ok": True}


@router.delete("/watchlist/{wid}")
def delete_watchlist(wid: int, conn: sqlite3.Connection = Depends(get_db_conn)):
    if Q.delete_watchlist_item(conn, wid) == 0:
        raise HTTPException(404, "Watchlist item not found")
    conn.commit()
    return {"ok": True}


# ── Blacklist ─────────────────────────────────────────────────

@router.get("/blacklist")
def list_blacklist(conn: sqlite3.Connection = Depends(get_db_conn)):
    return Q.get_blacklist(conn)


@router.post("/blacklist")
def create_blacklist_item(body: dict, conn: sqlite3.Connection = Depends(get_db_conn)):
    bid = Q.insert_blacklist(conn, **body)
    conn.commit()
    return {"id": bid}


@router.delete("/blacklist/{bid}")
def delete_blacklist_item(bid: int, conn: sqlite3.Connection = Depends(get_db_conn)):
    if Q.delete_blacklist(conn, bid) == 0:
        raise HTTPException(404, "Blacklist item not found")
    conn.commit()
    return {"ok": True}


# ── Industry / Macro ──────────────────────────────────────────

@router.get("/industry")
def list_industry(
    keyword: Optional[str] = None,
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    sector: Optional[str] = None,
    limit: int = Query(100, le=500),
    conn: sqlite3.Connection = Depends(get_db_conn),
):
    if keyword:
        return Q.search_industry_info(
            conn, keyword, date_from=date_from, date_to=date_to, limit=limit,
        )
    rows = Q.get_recent_industry_info(conn, date_from=date_from, date_to=date_to, limit=limit)
    if sector:
        rows = [r for r in rows if sector in (r.get("sector_name") or "")]
    return rows


@router.post("/industry")
def create_industry(body: dict, conn: sqlite3.Connection = Depends(get_db_conn)):
    iid = Q.insert_industry_info(conn, **body)
    conn.commit()
    return {"id": iid}


@router.put("/industry/{iid}")
def update_industry(iid: int, body: dict, conn: sqlite3.Connection = Depends(get_db_conn)):
    try:
        Q.update_industry_info(conn, iid, **body)
    except ValueError as e:
        raise HTTPException(422, str(e))
    conn.commit()
    return {"ok": True}


@router.delete("/industry/{iid}")
def delete_industry(iid: int, conn: sqlite3.Connection = Depends(get_db_conn)):
    cur = conn.execute("DELETE FROM industry_info WHERE id = ?", (iid,))
    if cur.rowcount == 0:
        raise HTTPException(404, "Industry info not found")
    conn.commit()
    return {"ok": True}


@router.get("/macro")
def list_macro(keyword: Optional[str] = None,
               conn: sqlite3.Connection = Depends(get_db_conn)):
    if keyword:
        return Q.search_macro_info(conn, keyword)
    return [dict(r) for r in conn.execute(
        "SELECT * FROM macro_info ORDER BY date DESC LIMIT 100"
    ).fetchall()]


@router.post("/macro")
def create_macro(body: dict, conn: sqlite3.Connection = Depends(get_db_conn)):
    mid = Q.insert_macro_info(conn, **body)
    conn.commit()
    return {"id": mid}


@router.put("/macro/{mid}")
def update_macro(mid: int, body: dict, conn: sqlite3.Connection = Depends(get_db_conn)):
    try:
        Q.update_macro_info(conn, mid, **body)
    except ValueError as e:
        raise HTTPException(422, str(e))
    conn.commit()
    return {"ok": True}


@router.delete("/macro/{mid}")
def delete_macro(mid: int, conn: sqlite3.Connection = Depends(get_db_conn)):
    cur = conn.execute("DELETE FROM macro_info WHERE id = ?", (mid,))
    if cur.rowcount == 0:
        raise HTTPException(404, "Macro info not found")
    conn.commit()
    return {"ok": True}


# ── Calendar ─────────────────────────────────────────────────

@router.get("/calendar")
def list_calendar(conn: sqlite3.Connection = Depends(get_db_conn)):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM calendar_events ORDER BY date DESC LIMIT 100"
    ).fetchall()]


@router.get("/calendar/range")
def calendar_range(
    date_from: str = Query(..., alias="from"),
    date_to: str = Query(..., alias="to"),
    impact: Optional[str] = None,
    category: Optional[str] = None,
    conn: sqlite3.Connection = Depends(get_db_conn),
):
    return Q.get_calendar_range(conn, date_from, date_to, impact=impact, category=category)


@router.post("/calendar")
def create_calendar(body: dict, conn: sqlite3.Connection = Depends(get_db_conn)):
    cid = Q.insert_calendar_event(conn, **body)
    conn.commit()
    return {"id": cid}


@router.put("/calendar/{cid}")
def update_calendar(cid: int, body: dict, conn: sqlite3.Connection = Depends(get_db_conn)):
    try:
        Q.update_calendar_event(conn, cid, **body)
    except ValueError as e:
        raise HTTPException(422, str(e))
    conn.commit()
    return {"ok": True}


@router.delete("/calendar/{cid}")
def delete_calendar(cid: int, conn: sqlite3.Connection = Depends(get_db_conn)):
    cur = conn.execute("DELETE FROM calendar_events WHERE id = ?", (cid,))
    if cur.rowcount == 0:
        raise HTTPException(404, "Calendar event not found")
    conn.commit()
    return {"ok": True}


# ── Trades ────────────────────────────────────────────────────

@router.get("/trades")
def list_trades(
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    stock_code: Optional[str] = None,
    conn: sqlite3.Connection = Depends(get_db_conn),
):
    return Q.get_trades(conn, date_from=date_from, date_to=date_to, stock_code=stock_code)


@router.get("/trades/{tid}")
def get_trade(tid: int, conn: sqlite3.Connection = Depends(get_db_conn)):
    row = conn.execute("SELECT * FROM trades WHERE id = ?", (tid,)).fetchone()
    if not row:
        raise HTTPException(404, "Trade not found")
    return dict(row)


@router.post("/trades")
def create_trade(body: dict, conn: sqlite3.Connection = Depends(get_db_conn)):
    tid = Q.insert_trade(conn, **body)
    conn.commit()
    return {"id": tid}


@router.put("/trades/{tid}")
def update_trade(tid: int, body: dict, conn: sqlite3.Connection = Depends(get_db_conn)):
    try:
        Q.update_trade(conn, tid, **body)
    except ValueError as e:
        raise HTTPException(422, str(e))
    conn.commit()
    return {"ok": True}


@router.delete("/trades/{tid}")
def delete_trade(tid: int, conn: sqlite3.Connection = Depends(get_db_conn)):
    cur = conn.execute("DELETE FROM trades WHERE id = ?", (tid,))
    if cur.rowcount == 0:
        raise HTTPException(404, "Trade not found")
    conn.commit()
    return {"ok": True}


# ── Market ────────────────────────────────────────────────────

@router.get("/market/research-coverage")
def get_research_coverage(
    days: int = 5,
    limit: int = 20,
    conn: sqlite3.Connection = Depends(get_db_conn),
):
    """区间研报覆盖排行：聚合最近 N 个交易日的研报覆盖 top，返回合并排行 + 按申万一级行业汇总。"""
    from collections import Counter
    from db.dual_write import parse_post_market_envelope
    from services.research_digest.collector import aggregate_by_industry, UNCLASSIFIED

    days = max(1, min(days, 60))
    limit = max(1, min(limit, 50))

    rows = conn.execute(
        "SELECT raw_data FROM daily_market ORDER BY date DESC LIMIT ?",
        (days,),
    ).fetchall()

    stock_counts: Counter[str] = Counter()
    stock_names: dict[str, str] = {}
    stock_industry: dict[str, str] = {}   # 每股行业：取首个非未分类（老 envelope 缺字段 → 未分类），API 内零网络
    covered_days = 0

    for row in rows:
        env = parse_post_market_envelope(row["raw_data"] if isinstance(row, sqlite3.Row) else row[0])
        if not env:
            continue
        inner = env.get("raw_data", env)
        rct = inner.get("research_coverage_top")
        if not isinstance(rct, list) or not rct:
            continue
        covered_days += 1
        for item in rct:
            code = item.get("stock_code", "")
            if not code:
                continue
            stock_counts[code] += item.get("report_count", 0)
            if code not in stock_names:
                stock_names[code] = item.get("stock_name", "")
            ind = item.get("industry")
            # rows 按 date DESC，取每股「首个非未分类」= 最近日分类；空串/None 被 `if ind` 拒（视作未分类，
            # 与 aggregate_by_industry 的 `or UNCLASSIFIED` 同语义）。
            if ind and ind != UNCLASSIFIED and stock_industry.get(code, UNCLASSIFIED) == UNCLASSIFIED:
                stock_industry[code] = ind

    top = stock_counts.most_common(limit)
    result = [
        {"stock_code": code, "stock_name": stock_names.get(code, ""), "report_count": count}
        for code, count in top
    ]
    # 行业汇总：仅对 Top-limit 的股票聚合（与 items 口径一致），缺行业落未分类。
    # 已知小差异（仅影响本特性上线前的历史天）：range 此处重算 → 老数据全落「未分类」并展示；
    # 而「当日」出口读 envelope 存储的 research_coverage_industry，老 envelope 无此字段 → 前端隐藏行业条。
    # 新采集天两条出口一致；老数据差异随时间消化，不回填，接受为已知。
    industry = aggregate_by_industry([
        {"industry": stock_industry.get(code, UNCLASSIFIED), "report_count": count}
        for code, count in top
    ])
    return {"days": days, "covered_days": covered_days, "items": result, "industry": industry}


@router.get("/market/history")
def get_market_history(days: int = 20,
                       conn: sqlite3.Connection = Depends(get_db_conn)):
    return Q.get_daily_market_history(conn, days=min(days, 120))


@router.get("/market/concentration/history")
def get_concentration_history(days: int = 30,
                              conn: sqlite3.Connection = Depends(get_db_conn)):
    """成交额 Top20 板块集中度趋势(供盘面概览图表):库内最新 N 日的 CR3 / 头部成交额 /
    占两市 / 板块占比序列 + 最新日连续在榜/异动快照。无数据返空壳。"""
    payload = vc_service.build_trend_payload(conn, days=max(1, min(days, 120)))  # 钳非负:防 days<=0 致 LIMIT 负数返全表
    return _sanitize_non_finite(payload)  # 脏数据 change_pct=NaN/Inf 透传会致 JSON 序列化 500(与其它 market 端点一致)


@router.get("/market/sector-gain-ranking/{date}")
def get_sector_gain_ranking(date: str,
                            conn: sqlite3.Connection = Depends(get_db_conn)):
    """成交额前50 区间涨幅排名(某交易日,5/10/20 日三档,供复盘使用):
    `rankings`=申万二级板块榜 + `concept_rankings`=同花顺概念题材榜(多标签)。

    组按组内涨幅最大个股降序、平手比次大(均派生自 daily_volume_concentration.gain_universe)。
    全客观区间涨幅,守红线不出价位目标/不给买卖建议。无记录/旧记录返三档空列表。"""
    payload = vc_service.build_sector_gain_ranking_payload(conn, date)
    return _sanitize_non_finite(payload)  # 脏数据 gain=NaN/Inf 透传会致 JSON 序列化 500(与其它 market 端点一致)


@router.get("/market/margin-index-correlation/{date}")
def get_margin_index_correlation(date: str,
                                 conn: sqlite3.Connection = Depends(get_db_conn)):
    """两融余额与指数联动性(某交易日,供复盘「1.大盘」):背离预警 / 余额水位+趋势 /
    领先滞后 / 同步相关四维。两融余额转日变化率后与指数 pct_chg 同口径统计,全 [判断]
    守红线(不出价位/不给买卖建议)。无记录返 available=False。"""
    from services.margin_index_correlation import web_payload
    return _sanitize_non_finite(web_payload.build_daily_payload(conn, date))  # 相关/分位 NaN 透传会致 500


@router.get("/market/timing/history")
def get_market_timing_history(days: int = 30, to_date: Optional[str] = None,
                              conn: sqlite3.Connection = Depends(get_db_conn)):
    """大盘择时市场级序列(供盘面概览趋势图):共振指数数 / 成交额近20日地量分位 随时间。
    路由声明在 /market/timing/{date} 之前,避免 'history' 被当作 {date} 路径参数吞掉。
    to_date 给定时只取该日及之前——复盘历史日期不带出未来数据(前瞻偏差)。"""
    from services.market_timing import web_payload
    return _sanitize_non_finite(web_payload.build_history_payload(conn, days=days, end_date=to_date))


@router.get("/market/timing/{date}")
def get_market_timing(date: str,
                      conn: sqlite3.Connection = Depends(get_db_conn)):
    """大盘择时观察(某交易日 6 指数斐波那契变盘点 + 底分型 + 市场上下文)。
    全 [判断] 派生信号,前端渲染须守红线(不出方向/价位/买卖建议)。无数据返 available=False。"""
    from services.market_timing import web_payload
    return _sanitize_non_finite(web_payload.build_daily_payload(conn, date))


@router.get("/post-market/{date}")
def get_post_market_envelope(date: str, conn: sqlite3.Connection = Depends(get_db_conn)):
    """返回与 post-market.yaml 一致的整包信封（优先 DB raw_data，否则读 daily 文件）。"""
    row = Q.get_daily_market(conn, date)
    envelope: dict[str, Any] | None = None
    raw = row.get("raw_data") if row else None
    if raw:
        if isinstance(raw, str):
            try:
                envelope = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                envelope = None
        elif isinstance(raw, dict):
            envelope = dict(raw)
    if envelope is None:
        ypath = _REPO_ROOT / "daily" / date / "post-market.yaml"
        if ypath.is_file():
            try:
                with open(ypath, encoding="utf-8") as f:
                    loaded = yaml.safe_load(f)
                if isinstance(loaded, dict):
                    envelope = loaded
            except (OSError, yaml.YAMLError):
                pass
    if not envelope:
        return {"date": date, "available": False}
    out = dict(envelope)
    # 北向净额下线（口径存疑）：剔除历史信封里的 northbound 块，避免旧库存假净额/活跃股
    # 经信封面板（整包 JSON 展示+复制）外泄。仅服务时 scrub，不改库内归档。
    out.pop("northbound", None)
    inner = out.get("raw_data")
    if isinstance(inner, dict) and "northbound" in inner:
        inner = dict(inner)
        inner.pop("northbound", None)
        out["raw_data"] = inner
    out["available"] = True
    out.setdefault("date", date)
    return _sanitize_non_finite(out)


@router.get("/main-themes")
def list_main_themes(conn: sqlite3.Connection = Depends(get_db_conn)):
    return Q.get_active_themes(conn)


@router.get("/market/{date}")
def get_market(date: str, conn: sqlite3.Connection = Depends(get_db_conn)):
    row = Q.get_daily_market(conn, date)
    if not row:
        return {"date": date, "available": False}
    _apply_market_ma5w_fallback(conn, row)
    enrich_daily_market_row(row)
    row["available"] = True
    return _sanitize_non_finite(row)
