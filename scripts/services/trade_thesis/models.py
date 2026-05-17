from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Thesis:
    """trade_thesis 表的内存模型;字段与 schema v24 一一对应."""

    stock_code: str
    stock_name: str
    account_id: str
    opened_at: str
    entry_reason: str
    failure_condition: str
    trade_mode: str
    market_region: str
    sector: str
    planned_position_pct: float
    input_by: str
    id: int | None = None
    closed_at: str | None = None
    status: str = "open"
    target_price: float | None = None
    stop_loss: float | None = None
    mode_note: str | None = None
    plan_id: str | None = None
    notes: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    reopen_count: int = 0
    last_reopened_at: str | None = None


@dataclass
class ThesisReview:
    """thesis_review 表的内存模型;1:1 关联 Thesis."""

    thesis_id: int
    executed_as_planned: int
    input_by: str
    exit_trigger: str | None = None
    lessons: str | None = None
    discipline_score: int | None = None
    realized_pnl_pct: float | None = None
    realized_pnl_amount: float | None = None
    holding_days: int | None = None
    created_at: str | None = None
