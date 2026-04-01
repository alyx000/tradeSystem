"""八步复盘路由。"""
from __future__ import annotations

import re
import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_db_conn
from db import queries as Q

router = APIRouter(prefix="/api/review", tags=["review"])

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _validate_date(date: str) -> str:
    if not _DATE_RE.match(date):
        raise HTTPException(422, f"Invalid date format: {date}")
    return date


@router.get("/{date}")
def get_review(date: str, conn: sqlite3.Connection = Depends(get_db_conn)):
    date = _validate_date(date)
    review = Q.get_daily_review(conn, date)
    if not review:
        return {"date": date, "exists": False}
    return {**review, "exists": True}


@router.get("/{date}/prefill")
def get_prefill(date: str, conn: sqlite3.Connection = Depends(get_db_conn)):
    date = _validate_date(date)
    market = Q.get_daily_market(conn, date)
    prev_market = Q.get_prev_daily_market(conn, date)
    avg_5d = Q.get_avg_amount(conn, date, 5)
    avg_20d = Q.get_avg_amount(conn, date, 20)
    emotion = Q.get_latest_emotion(conn)
    themes = Q.get_active_themes(conn)
    holdings = Q.get_holdings(conn)
    calendar = Q.get_calendar_range(conn, date, date)

    notes = conn.execute(
        "SELECT n.*, t.name as teacher_name FROM teacher_notes n "
        "JOIN teachers t ON n.teacher_id = t.id WHERE n.date = ? ORDER BY n.created_at DESC",
        (date,),
    ).fetchall()
    prev_review = Q.get_prev_daily_review(conn, date)

    return {
        "date": date,
        "market": market,
        "prev_market": prev_market,
        "avg_5d_amount": avg_5d,
        "avg_20d_amount": avg_20d,
        "teacher_notes": [dict(n) for n in notes],
        "emotion_cycle": emotion,
        "main_themes": themes,
        "holdings": holdings,
        "calendar_events": calendar,
        "prev_review": prev_review,
    }


@router.put("/{date}")
def save_review(date: str, body: dict, conn: sqlite3.Connection = Depends(get_db_conn)):
    date = _validate_date(date)
    Q.upsert_daily_review(conn, date, body)
    conn.commit()
    return {"ok": True, "date": date}
