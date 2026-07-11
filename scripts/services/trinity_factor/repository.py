"""三位一体评分运行与回验记录的 SQLite repository。"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from typing import Any


_RUN_COLUMNS = (
    "score_run_id", "trade_date", "retry_of_run_id", "cache_key",
    "input_digest", "is_cacheable", "provider", "requested_model",
    "actual_model", "cli_version", "runtime_version",
    "prompt_versions_json", "prompt_sha256_json", "schema_version",
    "ruleset_version", "evidence_snapshot_json", "rule_gate_json",
    "factor_scores_json", "sector_scores_json", "system_recommendation_json",
    "valid_raw_json", "raw_output_sha256_json", "diagnostics_json", "status",
    "attempt_count", "duration_ms",
)
_RUN_JSON_COLUMNS = frozenset({
    "prompt_versions_json", "prompt_sha256_json", "evidence_snapshot_json",
    "rule_gate_json", "factor_scores_json", "sector_scores_json",
    "system_recommendation_json", "valid_raw_json", "raw_output_sha256_json",
    "diagnostics_json",
})
_OPTIONAL_RUN_COLUMNS = frozenset({
    "retry_of_run_id", "actual_model", "cli_version", "factor_scores_json",
    "sector_scores_json", "system_recommendation_json", "valid_raw_json",
    "raw_output_sha256_json",
})
_EVALUATION_COLUMNS = (
    "evaluation_id", "score_run_id", "source_review_date",
    "evaluation_trade_date", "rule_top_code", "llm_top_code",
    "system_top_code", "human_top_code", "system_outcome",
    "confirmed_outcome", "actual_evidence_json", "evaluation_note",
    "input_by",
)
_EVALUATION_JSON_COLUMNS = frozenset({"actual_evidence_json"})
_OPTIONAL_EVALUATION_COLUMNS = frozenset({
    "rule_top_code", "llm_top_code", "system_top_code", "human_top_code",
    "system_outcome", "confirmed_outcome", "evaluation_note",
})


def _canonical_json(value: Any, *, field: str) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be valid JSON data") from exc


def _decode_json(value: str | None, *, field: str) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"stored {field} is invalid JSON") from exc


def _require_record(
    record: Mapping[str, Any],
    columns: tuple[str, ...],
    optional: frozenset[str],
) -> None:
    if not isinstance(record, Mapping):
        raise ValueError("record must be a mapping")
    missing = [name for name in columns if name not in optional and record.get(name) is None]
    if missing:
        raise ValueError(f"missing required fields: {', '.join(missing)}")


def insert_score_run(conn: sqlite3.Connection, record: Mapping[str, Any]) -> str:
    """纯 INSERT 新增一次评分运行；主键/外键冲突直接交给 SQLite。"""
    _require_record(record, _RUN_COLUMNS, _OPTIONAL_RUN_COLUMNS)
    status = record["status"]
    valid_raw = record.get("valid_raw_json")
    if status == "success" and valid_raw is None:
        raise ValueError("valid_raw_json is required when status is success")
    if status not in {"success", "sector_failed"} and valid_raw is not None:
        raise ValueError(
            "valid_raw_json is only allowed for successful validated layers"
        )

    values = []
    for column in _RUN_COLUMNS:
        value = record.get(column)
        if column in _RUN_JSON_COLUMNS and value is not None:
            value = _canonical_json(value, field=column)
        elif column == "is_cacheable":
            if isinstance(value, bool):
                value = int(value)
            elif not isinstance(value, int) or value not in (0, 1):
                raise ValueError("is_cacheable must be a boolean")
        values.append(value)

    placeholders = ",".join("?" for _ in _RUN_COLUMNS)
    conn.execute(
        f"INSERT INTO daily_review_factor_score_runs ({','.join(_RUN_COLUMNS)}) "
        f"VALUES ({placeholders})",
        values,
    )
    return str(record["score_run_id"])


def _row_to_score_run(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    for column in _RUN_JSON_COLUMNS:
        result[column] = _decode_json(result[column], field=column)
    result["is_cacheable"] = bool(result["is_cacheable"])
    return result


def get_score_run(conn: sqlite3.Connection, score_run_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM daily_review_factor_score_runs WHERE score_run_id = ?",
        (score_run_id,),
    ).fetchone()
    return _row_to_score_run(row) if row else None


def find_cached_score_run(
    conn: sqlite3.Connection,
    cache_key: str,
) -> dict[str, Any] | None:
    """返回同一缓存键下最新的完整、部分降级或确定性规则结果。"""
    row = conn.execute(
        "SELECT * FROM daily_review_factor_score_runs "
        "WHERE cache_key = ? AND is_cacheable = 1 "
        "AND status IN ('success', 'sector_failed', 'rule_only') "
        "AND (status = 'rule_only' OR valid_raw_json IS NOT NULL) "
        "ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (cache_key,),
    ).fetchone()
    return _row_to_score_run(row) if row else None


def _validate_limit(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError("limit must be a positive integer")
    return limit


def list_score_runs(
    conn: sqlite3.Connection,
    *,
    trade_date: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """按固定指标字段筛选评分运行，最新记录优先。"""
    clauses: list[str] = []
    params: list[Any] = []
    if trade_date is not None:
        clauses.append("trade_date = ?")
        params.append(trade_date)
    if status is not None:
        clauses.append("status = ?")
        params.append(status)

    sql = "SELECT * FROM daily_review_factor_score_runs"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY trade_date DESC, created_at DESC, rowid DESC LIMIT ?"
    params.append(_validate_limit(limit))
    return [_row_to_score_run(row) for row in conn.execute(sql, params).fetchall()]


def _row_to_evaluation(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    for column in _EVALUATION_JSON_COLUMNS:
        result[column] = _decode_json(result[column], field=column)
    return result


def upsert_evaluation(conn: sqlite3.Connection, record: Mapping[str, Any]) -> str:
    """按回验自然键写入或更新；冲突时保留原 evaluation_id。"""
    _require_record(record, _EVALUATION_COLUMNS, _OPTIONAL_EVALUATION_COLUMNS)
    values = []
    for column in _EVALUATION_COLUMNS:
        value = record.get(column)
        if column in _EVALUATION_JSON_COLUMNS:
            value = _canonical_json(value, field=column)
        values.append(value)

    placeholders = ",".join("?" for _ in _EVALUATION_COLUMNS)
    mutable_columns = (
        "rule_top_code", "llm_top_code", "system_top_code", "human_top_code",
        "system_outcome", "confirmed_outcome", "actual_evidence_json",
        "evaluation_note", "input_by",
    )
    updates = ",".join(f"{column}=excluded.{column}" for column in mutable_columns)
    conn.execute(
        f"INSERT INTO daily_review_factor_evaluations "
        f"({','.join(_EVALUATION_COLUMNS)}) VALUES ({placeholders}) "
        f"ON CONFLICT(source_review_date, evaluation_trade_date, score_run_id) "
        f"DO UPDATE SET {updates}, updated_at=datetime('now')",
        values,
    )
    row = conn.execute(
        "SELECT evaluation_id FROM daily_review_factor_evaluations "
        "WHERE source_review_date = ? AND evaluation_trade_date = ? "
        "AND score_run_id = ?",
        (
            record["source_review_date"],
            record["evaluation_trade_date"],
            record["score_run_id"],
        ),
    ).fetchone()
    return str(row["evaluation_id"])


def get_evaluation(
    conn: sqlite3.Connection,
    evaluation_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM daily_review_factor_evaluations WHERE evaluation_id = ?",
        (evaluation_id,),
    ).fetchone()
    return _row_to_evaluation(row) if row else None


def list_evaluations(
    conn: sqlite3.Connection,
    *,
    score_run_id: str | None = None,
    source_review_date: str | None = None,
    evaluation_trade_date: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """按运行或日期筛选回验记录，回验日期最新者优先。"""
    filters = (
        ("score_run_id", score_run_id),
        ("source_review_date", source_review_date),
        ("evaluation_trade_date", evaluation_trade_date),
    )
    clauses: list[str] = []
    params: list[Any] = []
    for column, value in filters:
        if value is not None:
            clauses.append(f"{column} = ?")
            params.append(value)

    sql = "SELECT * FROM daily_review_factor_evaluations"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += (
        " ORDER BY evaluation_trade_date DESC, source_review_date DESC, "
        "created_at DESC, rowid DESC LIMIT ?"
    )
    params.append(_validate_limit(limit))
    return [_row_to_evaluation(row) for row in conn.execute(sql, params).fetchall()]
