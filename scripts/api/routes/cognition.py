"""交易认知只读 API：trading_cognitions / cognition_instances / periodic_reviews。

只读路由，均委托 `CognitionService`。service 内部自己开连接（Phase 1b 已知 N+1
连接问题，属于 backlog，本次不修）；`get_db_conn` 依赖保留作占位，避免 import 漂移。
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_db_conn  # noqa: F401  (保留占位，service 自行开连接)
from services.cognition_service import CognitionService

router = APIRouter(prefix="/api/cognition", tags=["cognition"])

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_JSON_FIELDS_COGNITION = [
    "conditions_json",
    "exceptions_json",
    "invalidation_conditions_json",
    "tags",
]
_JSON_FIELDS_INSTANCE = [
    "regime_tags_json",
    "outcome_fact_refs_json",
    "parameters_json",
]
_JSON_FIELDS_REVIEW = [
    "active_cognitions_json",
    "validation_stats_json",
    "teacher_participation_json",
    "consensus_summary_json",
    "disagreement_summary_json",
    "new_cognitions_json",
    "refined_cognitions_json",
    "deprecated_cognitions_json",
    "key_lessons_json",
    "evolving_views_json",
    "action_items_json",
]


def _parse_json_fields(row: Optional[dict], fields: list[str]) -> Optional[dict]:
    if row is None:
        return row
    out = dict(row)
    for f in fields:
        v = out.get(f)
        if v is None or isinstance(v, (list, dict)):
            continue
        if isinstance(v, str):
            try:
                out[f] = json.loads(v)
            except (json.JSONDecodeError, TypeError):
                out[f] = None
    return out


def _parse_date(s: Optional[str]) -> Optional[str]:
    if s is None:
        return None
    if not _DATE_RE.match(s):
        raise HTTPException(422, f"date must be YYYY-MM-DD, got: {s}")
    return s


# ──────────────────────────────────────────────────────────────
# /cognitions
# 静态路由必须在 /cognitions/{id} 之前注册；FastAPI 按声明顺序匹配。
# ──────────────────────────────────────────────────────────────
@router.get("/cognitions")
def list_cognitions(
    category: Optional[str] = Query(None),
    sub_category: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    evidence_level: Optional[str] = Query(None),
    conflict_group: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    try:
        rows = CognitionService().list_cognitions(
            status=status,
            category=category,
            sub_category=sub_category,
            evidence_level=evidence_level,
            conflict_group=conflict_group,
            keyword=keyword,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    parsed = [_parse_json_fields(r, _JSON_FIELDS_COGNITION) for r in rows]
    return {"total": len(parsed), "cognitions": parsed}


@router.get("/cognitions/{cognition_id}")
def get_cognition(cognition_id: str):
    try:
        row = CognitionService().get_cognition(cognition_id)
    except KeyError:
        raise HTTPException(404, f"cognition not found: {cognition_id}")
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return {"cognition": _parse_json_fields(row, _JSON_FIELDS_COGNITION)}


# ──────────────────────────────────────────────────────────────
# /instances
# ──────────────────────────────────────────────────────────────
@router.get("/instances")
def list_instances(
    cognition_id: Optional[str] = Query(None),
    outcome: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    teacher_id: Optional[int] = Query(None),
    source_type: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=2000),
    offset: int = Query(0, ge=0),
):
    df = _parse_date(date_from)
    dt = _parse_date(date_to)
    try:
        rows = CognitionService().list_instances(
            cognition_id=cognition_id,
            outcome=outcome,
            date_from=df,
            date_to=dt,
            teacher_id=teacher_id,
            source_type=source_type,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    parsed = [_parse_json_fields(r, _JSON_FIELDS_INSTANCE) for r in rows]
    return {"total": len(parsed), "instances": parsed}


# ──────────────────────────────────────────────────────────────
# /reviews
# ──────────────────────────────────────────────────────────────
@router.get("/reviews")
def list_reviews(
    period_type: Optional[str] = Query(None),
    review_scope: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=2000),
    offset: int = Query(0, ge=0),
):
    df = _parse_date(date_from)
    dt = _parse_date(date_to)
    try:
        rows = CognitionService().list_reviews(
            period_type=period_type,
            review_scope=review_scope,
            status=status,
            from_date=df,
            to_date=dt,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    parsed = [_parse_json_fields(r, _JSON_FIELDS_REVIEW) for r in rows]
    return {"total": len(parsed), "reviews": parsed}


@router.get("/reviews/{review_id}")
def get_review(review_id: str):
    try:
        row = CognitionService().get_review(review_id)
    except KeyError:
        raise HTTPException(404, f"review not found: {review_id}")
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return {"review": _parse_json_fields(row, _JSON_FIELDS_REVIEW)}
