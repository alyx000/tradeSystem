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


# ── Teachers / Notes ──────────────────────────────────────────

@router.get("/teachers")
def list_teachers(conn: sqlite3.Connection = Depends(get_db_conn)):
    return Q.list_teachers(conn)


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
        return _attach_note_attachments(conn, notes)
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
    return _attach_note_attachments(conn, notes)


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
    tid = Q.get_or_create_teacher(conn, teacher_name)
    note_id = Q.insert_teacher_note(conn, teacher_id=tid, **payload)
    wl_result: dict[str, list] | None = None
    if sync_wl and mentioned:
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
                )
            except ValueError as e:
                conn.rollback()
                raise HTTPException(422, str(e)) from e
        else:
            wl_result = {"added": [], "skipped": []}
    conn.commit()
    out: dict[str, Any] = {"id": note_id}
    if sync_wl:
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
    hid = Q.upsert_holding(conn, **body)
    conn.commit()
    return {"id": hid}


@router.put("/holdings/{hid}")
def update_holding_item(hid: int, body: dict, conn: sqlite3.Connection = Depends(get_db_conn)):
    try:
        Q.update_holding(conn, hid, **body)
    except ValueError as e:
        raise HTTPException(422, str(e))
    conn.commit()
    return {"ok": True}


@router.delete("/holdings/{hid}")
def delete_holding_item(hid: int, conn: sqlite3.Connection = Depends(get_db_conn)):
    if Q.delete_holding(conn, hid) == 0:
        raise HTTPException(404, "Holding not found")
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
    """区间研报覆盖排行：聚合最近 N 个交易日的研报覆盖 top，返回合并排行。"""
    from collections import Counter
    from db.dual_write import parse_post_market_envelope

    days = max(1, min(days, 60))
    limit = max(1, min(limit, 50))

    rows = conn.execute(
        "SELECT raw_data FROM daily_market ORDER BY date DESC LIMIT ?",
        (days,),
    ).fetchall()

    stock_counts: Counter[str] = Counter()
    stock_names: dict[str, str] = {}
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

    result = [
        {"stock_code": code, "stock_name": stock_names.get(code, ""), "report_count": count}
        for code, count in stock_counts.most_common(limit)
    ]
    return {"days": days, "covered_days": covered_days, "items": result}


@router.get("/market/history")
def get_market_history(days: int = 20,
                       conn: sqlite3.Connection = Depends(get_db_conn)):
    return Q.get_daily_market_history(conn, days=min(days, 120))


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
