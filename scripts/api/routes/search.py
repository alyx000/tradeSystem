"""搜索与查询中心路由。"""
from __future__ import annotations

import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, Query

from api.deps import get_db_conn
from db import queries as Q

router = APIRouter(prefix="/api", tags=["search"])


@router.get("/search/unified")
def unified_search(
    q: str = Query(..., min_length=1),
    types: Optional[str] = None,
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    conn: sqlite3.Connection = Depends(get_db_conn),
):
    type_list = types.split(",") if types else None
    return Q.unified_search(conn, q, types=type_list, date_from=date_from, date_to=date_to)


@router.get("/teachers/{teacher_id}/timeline")
def teacher_timeline(
    teacher_id: int,
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    conn: sqlite3.Connection = Depends(get_db_conn),
):
    return Q.get_teacher_timeline(conn, teacher_id, date_from=date_from, date_to=date_to)


@router.get("/stock/{code}/mentions")
def stock_mentions(code: str, conn: sqlite3.Connection = Depends(get_db_conn)):
    return Q.stock_mentions(conn, code)


@router.get("/style-factors/series")
def style_factors_series(
    metrics: str = Query(...),
    date_from: str = Query(..., alias="from"),
    date_to: str = Query(..., alias="to"),
    conn: sqlite3.Connection = Depends(get_db_conn),
):
    metric_list = [m.strip() for m in metrics.split(",")]
    return Q.get_style_factors_series(conn, metric_list, date_from, date_to)


@router.get("/search/export")
def export_search(
    q: str = Query(..., min_length=1),
    types: Optional[str] = None,
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    conn: sqlite3.Connection = Depends(get_db_conn),
):
    type_list = types.split(",") if types else None
    results = Q.unified_search(conn, q, types=type_list, date_from=date_from, date_to=date_to)

    lines = [f"# 搜索结果：{q}\n"]
    for entity, items in results.items():
        if not items:
            continue
        lines.append(f"\n## {entity} ({len(items)} 条)\n")
        for item in items:
            date = item.get("date", "")
            title = item.get("title", item.get("sector_name", ""))
            lines.append(f"- [{date}] {title}")
            if item.get("core_view"):
                lines.append(f"  > {item['core_view'][:100]}")
            elif item.get("content"):
                lines.append(f"  > {item['content'][:100]}")

    from fastapi.responses import PlainTextResponse
    return PlainTextResponse("\n".join(lines), media_type="text/markdown")
