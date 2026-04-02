"""数据管理 CRUD 路由：老师观点、持仓、关注池、黑名单、行业/宏观、日历、交易记录、市场行情。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_db_conn
from api.market_enrich import enrich_daily_market_row
from db import queries as Q

_REPO_ROOT = Path(__file__).resolve().parents[3]

router = APIRouter(prefix="/api", tags=["crud"])


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
    conn: sqlite3.Connection = Depends(get_db_conn),
):
    if keyword:
        notes = Q.search_teacher_notes(conn, keyword, teacher_name=teacher,
                                       date_from=date_from, date_to=date_to)
        return _attach_note_attachments(conn, notes)
    sql = "SELECT n.*, t.name as teacher_name FROM teacher_notes n JOIN teachers t ON n.teacher_id = t.id WHERE 1=1"
    params = []
    if teacher:
        sql += " AND t.name = ?"
        params.append(teacher)
    if date_from:
        sql += " AND n.date >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND n.date <= ?"
        params.append(date_to)
    sql += " ORDER BY n.date DESC LIMIT 100"
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
    teacher_name = body.pop("teacher_name", None) or body.pop("teacher", None)
    if not teacher_name:
        raise HTTPException(422, "teacher_name required")
    tid = Q.get_or_create_teacher(conn, teacher_name)
    note_id = Q.insert_teacher_note(conn, teacher_id=tid, **body)
    conn.commit()
    return {"id": note_id}


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
def list_industry(keyword: Optional[str] = None,
                  conn: sqlite3.Connection = Depends(get_db_conn)):
    if keyword:
        return Q.search_industry_info(conn, keyword)
    return [dict(r) for r in conn.execute(
        "SELECT * FROM industry_info ORDER BY date DESC LIMIT 100"
    ).fetchall()]


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
    return out


@router.get("/main-themes")
def list_main_themes(conn: sqlite3.Connection = Depends(get_db_conn)):
    return Q.get_active_themes(conn)


@router.get("/market/{date}")
def get_market(date: str, conn: sqlite3.Connection = Depends(get_db_conn)):
    row = Q.get_daily_market(conn, date)
    if not row:
        return {"date": date, "available": False}
    enrich_daily_market_row(row)
    row["available"] = True
    return row
