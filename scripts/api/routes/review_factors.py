"""三位一体因子评分、严格 T+1 回验与影子指标 API。"""
from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Annotated, Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    StrictBool,
    StrictStr,
    field_validator,
)

from api.deps import get_db_conn
from api.routes.review import build_review_prefill
from db import queries as Q
from services.trinity_factor.cycle import (
    build_factor_metrics,
    confirm_t1_evaluation,
    suggest_t1_evaluation,
)
from services.trinity_factor.service import TrinityFactorService
from services.trinity_factor.review_input import (
    normalize_review_steps,
    validate_trade_date,
)

router = APIRouter(prefix="/api/review-factors", tags=["review-factors"])


def _normalize_non_empty_string(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("must be a non-empty string")
    return normalized


_NonEmptyStrictStr = Annotated[
    StrictStr,
    AfterValidator(_normalize_non_empty_string),
]


class ReviewFactorScoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_by: StrictStr
    no_llm: StrictBool = False
    steps: Optional[dict[str, Any]] = None
    retry_of_run_id: Optional[_NonEmptyStrictStr] = None

    @field_validator("input_by")
    @classmethod
    def normalize_input_by(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("input_by must be a non-empty string")
        return normalized


class ReviewFactorEvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_date: Optional[_NonEmptyStrictStr] = None
    score_run_id: Optional[_NonEmptyStrictStr] = None
    confirmed_outcome: Literal[
        "hit",
        "partial",
        "miss",
        "missing_data",
        "not_applicable",
    ]
    evaluation_note: Optional[StrictStr] = None
    input_by: StrictStr

    @field_validator("input_by")
    @classmethod
    def normalize_input_by(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("input_by must be a non-empty string")
        return normalized


def _date(value: str) -> str:
    try:
        return validate_trade_date(value)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def _review_steps(
    conn: sqlite3.Connection,
    trade_date: str,
    supplied: Any,
) -> dict[str, Any]:
    source = supplied if isinstance(supplied, Mapping) else Q.get_daily_review(conn, trade_date) or {}
    return normalize_review_steps(source)


@router.post("/{date}/score")
def score_review_factors(
    date: str,
    body: ReviewFactorScoreRequest,
    conn: sqlite3.Connection = Depends(get_db_conn),
):
    trade_date = _date(date)
    try:
        prefill = build_review_prefill(conn, trade_date)
        return TrinityFactorService().score(
            conn,
            trade_date=trade_date,
            prefill=prefill,
            review_steps=_review_steps(conn, trade_date, body.steps),
            no_llm=body.no_llm,
            retry_of_run_id=body.retry_of_run_id,
            input_by=body.input_by,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/{date}/evaluation")
def get_factor_evaluation(
    date: str,
    source_date: Optional[_NonEmptyStrictStr] = Query(default=None),
    score_run_id: Optional[_NonEmptyStrictStr] = Query(default=None),
    conn: sqlite3.Connection = Depends(get_db_conn),
):
    evaluation_date = _date(date)
    if source_date is not None:
        source_date = _date(source_date)
    try:
        return suggest_t1_evaluation(
            conn,
            evaluation_trade_date=evaluation_date,
            source_review_date=source_date,
            score_run_id=score_run_id,
            prefill=build_review_prefill(conn, evaluation_date),
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.put("/{date}/evaluation")
def put_factor_evaluation(
    date: str,
    body: ReviewFactorEvaluationRequest,
    conn: sqlite3.Connection = Depends(get_db_conn),
):
    evaluation_date = _date(date)
    source_date = body.source_date
    if source_date is not None:
        source_date = _date(source_date)
    try:
        suggestion = suggest_t1_evaluation(
            conn,
            evaluation_trade_date=evaluation_date,
            source_review_date=source_date,
            score_run_id=body.score_run_id,
            prefill=build_review_prefill(conn, evaluation_date),
        )
        return confirm_t1_evaluation(
            conn,
            suggestion=suggestion,
            confirmed_outcome=body.confirmed_outcome,
            evaluation_note=body.evaluation_note or None,
            input_by=body.input_by,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/metrics")
def get_factor_metrics(
    days: int = Query(default=20, ge=1, le=250),
    conn: sqlite3.Connection = Depends(get_db_conn),
):
    return build_factor_metrics(conn, days=days)
