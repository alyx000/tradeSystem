"""异动监管监控只读 API：stock_regulatory_monitor。"""
from __future__ import annotations

import re
import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_db_conn
from db import queries as Q

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

router = APIRouter(prefix="/api/regulatory-monitor", tags=["regulatory-monitor"])


def _parse_date(date: str) -> str:
    if not _DATE_RE.match(date):
        raise HTTPException(422, "date must be YYYY-MM-DD")
    return date


@router.get("")
def list_regulatory_monitor(
    date: str = Query(..., description="业务日期 YYYY-MM-DD"),
    type: Literal["all", "1", "2", "3"] = Query(
        "all",
        alias="type",
        description="all | 1=已监管 | 2=潜在 | 3=重点监控(stk_alert)",
    ),
    conn: sqlite3.Connection = Depends(get_db_conn),
):
    _parse_date(date)
    return Q.list_regulatory_monitor_api(conn, date, type)
