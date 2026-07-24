"""月线模式股票池 episode 状态机与读写仓库。

池内身份是「A 股裸代码 + strategy_type」。同一身份只允许一个未退出 episode；
退出后再次命中会以新的 entered_date 建行，保留历史。调用方的 ``--dry-run``
应直接跳过本模块的写函数，本模块不提供会误写数据库的 dry-run 分支。
所有写函数均不提交事务，由 orchestrator 统一决定 commit / rollback。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date as date_type


OPEN_STATUSES = frozenset(
    {"technical_candidate", "fundamental_verified", "active", "risk"}
)
VALID_STATUSES = OPEN_STATUSES | {"exited"}

_ALLOWED_TRANSITIONS = {
    "technical_candidate": OPEN_STATUSES,
    "fundamental_verified": frozenset(
        {"fundamental_verified", "active", "risk"}
    ),
    "active": frozenset({"active", "risk"}),
    # 未曾 active 的基本面 episode 可在资格恢复后回到两阶段确认层；
    # service 通过 source_meta.risk_from_status 严格限制这条边。
    "risk": frozenset({"risk", "fundamental_verified", "active"}),
}


def _norm_stock_code(stock_code: str) -> str:
    """统一为 A 股裸代码，避免 ``600000`` 与 ``600000.SH`` 重复入池。"""
    normalized = (stock_code or "").strip().upper().split(".", 1)[0]
    if not normalized:
        raise ValueError("stock_code must not be empty")
    return normalized


def _required_text(value: str, field: str) -> str:
    normalized = (value or "").strip()
    if not normalized:
        raise ValueError(f"{field} must not be empty")
    return normalized


def _validate_date(value: str | None, field: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    normalized = _required_text(value or "", field)
    try:
        date_type.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{field} must be YYYY-MM-DD") from exc
    return normalized


def _validate_month(value: str) -> str:
    normalized = _required_text(value, "signal_month")
    try:
        date_type.fromisoformat(f"{normalized}-01")
    except ValueError as exc:
        raise ValueError("signal_month must be YYYY-MM") from exc
    if len(normalized) != 7:
        raise ValueError("signal_month must be YYYY-MM")
    return normalized


def _dump(payload) -> str | None:
    if payload is None:
        return None
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _decoded_rows(cursor: sqlite3.Cursor) -> list[dict]:
    columns = [column[0] for column in cursor.description]
    decoded: list[dict] = []
    for raw_row in cursor.fetchall():
        row = dict(zip(columns, raw_row))
        for column, public_name in (
            ("technical_evidence_json", "technical_evidence"),
            ("financial_evidence_json", "financial_evidence"),
            ("source_meta_json", "source_meta"),
        ):
            raw = row.pop(column, None)
            row[public_name] = json.loads(raw) if raw else {}
        decoded.append(row)
    return decoded


def get_open(
    conn: sqlite3.Connection,
    stock_code: str,
    strategy_type: str,
) -> dict | None:
    """读取同股同策略当前唯一未退出 episode。"""
    rows = _decoded_rows(
        conn.execute(
            """
            SELECT *
            FROM monthly_pattern_pool
            WHERE stock_code = ? AND strategy_type = ? AND status <> 'exited'
            ORDER BY entered_date DESC
            """,
            (
                _norm_stock_code(stock_code),
                _required_text(strategy_type, "strategy_type"),
            ),
        )
    )
    return rows[0] if rows else None


def list_pool(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    stock_code: str | None = None,
    strategy_type: str | None = None,
) -> list[dict]:
    """按可选状态、股票、策略过滤，返回已解析 JSON 的全部 episode。"""
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(f"invalid monthly pattern pool status: {status}")

    predicates: list[str] = []
    params: list[str] = []
    if status is not None:
        predicates.append("status = ?")
        params.append(status)
    if stock_code is not None:
        predicates.append("stock_code = ?")
        params.append(_norm_stock_code(stock_code))
    if strategy_type is not None:
        predicates.append("strategy_type = ?")
        params.append(_required_text(strategy_type, "strategy_type"))

    where = f" WHERE {' AND '.join(predicates)}" if predicates else ""
    return _decoded_rows(
        conn.execute(
            "SELECT * FROM monthly_pattern_pool"
            f"{where} ORDER BY entered_date, stock_code, strategy_type",
            params,
        )
    )


def record(
    conn: sqlite3.Connection,
    *,
    stock_code: str,
    stock_name: str,
    strategy_type: str,
    status: str,
    signal_month: str,
    date: str,
    report_period: str | None = None,
    financial_ann_date: str | None = None,
    technical_evidence=None,
    financial_evidence=None,
    source_meta=None,
) -> str:
    """新建、刷新或推进一个未退出 episode。

    返回 ``entered`` / ``refreshed`` / ``transitioned`` / ``stale``。
    ``stale`` 表示扫描日期早于现有 ``last_seen_date``，此时严格不修改状态或证据。
    退出必须使用 :func:`mark_exited`，避免普通刷新意外清空 episode。
    """
    if status not in OPEN_STATUSES:
        raise ValueError(f"invalid monthly pattern pool open status: {status}")

    normalized_code = _norm_stock_code(stock_code)
    normalized_name = _required_text(stock_name, "stock_name")
    normalized_strategy = _required_text(strategy_type, "strategy_type")
    normalized_month = _validate_month(signal_month)
    normalized_date = _validate_date(date, "date")
    normalized_period = _validate_date(
        report_period, "report_period", optional=True
    )
    normalized_ann_date = _validate_date(
        financial_ann_date, "financial_ann_date", optional=True
    )
    technical_json = _dump(technical_evidence)
    financial_json = _dump(financial_evidence)
    source_json = _dump(source_meta)

    open_row = conn.execute(
        """
        SELECT entered_date, last_seen_date, status
        FROM monthly_pattern_pool
        WHERE stock_code = ? AND strategy_type = ? AND status <> 'exited'
        """,
        (normalized_code, normalized_strategy),
    ).fetchone()

    if open_row is None:
        latest_seen = conn.execute(
            """
            SELECT MAX(last_seen_date)
            FROM monthly_pattern_pool
            WHERE stock_code = ? AND strategy_type = ?
            """,
            (normalized_code, normalized_strategy),
        ).fetchone()[0]
        if latest_seen is not None and normalized_date < latest_seen:
            return "stale"

        # 同日退出又重入时复用相同 PK 行并清除退出标记；跨日重入自然新建一行，
        # 因而常规 episode 历史完整保留。
        conn.execute(
            """
            INSERT INTO monthly_pattern_pool (
                stock_code, stock_name, strategy_type, status, signal_month,
                entered_date, last_seen_date, report_period, financial_ann_date,
                technical_evidence_json, financial_evidence_json, source_meta_json,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(stock_code, strategy_type, entered_date) DO UPDATE SET
                stock_name = excluded.stock_name,
                status = excluded.status,
                signal_month = excluded.signal_month,
                last_seen_date = excluded.last_seen_date,
                exited_date = NULL,
                exit_reason = NULL,
                report_period = excluded.report_period,
                financial_ann_date = excluded.financial_ann_date,
                technical_evidence_json = excluded.technical_evidence_json,
                financial_evidence_json = excluded.financial_evidence_json,
                source_meta_json = excluded.source_meta_json,
                updated_at = datetime('now')
            """,
            (
                normalized_code,
                normalized_name,
                normalized_strategy,
                status,
                normalized_month,
                normalized_date,
                normalized_date,
                normalized_period,
                normalized_ann_date,
                technical_json or "{}",
                financial_json or "{}",
                source_json or "{}",
            ),
        )
        return "entered"

    entered_date, last_seen_date, current_status = open_row
    if normalized_date < last_seen_date:
        return "stale"
    if status not in _ALLOWED_TRANSITIONS[current_status]:
        raise ValueError(
            "invalid monthly pattern pool transition: "
            f"{current_status} -> {status}"
        )

    conn.execute(
        """
        UPDATE monthly_pattern_pool
        SET stock_name = ?,
            status = ?,
            last_seen_date = ?,
            report_period = COALESCE(?, report_period),
            financial_ann_date = COALESCE(?, financial_ann_date),
            technical_evidence_json = COALESCE(?, technical_evidence_json),
            financial_evidence_json = COALESCE(?, financial_evidence_json),
            source_meta_json = COALESCE(?, source_meta_json),
            updated_at = datetime('now')
        WHERE stock_code = ? AND strategy_type = ? AND entered_date = ?
        """,
        (
            normalized_name,
            status,
            normalized_date,
            normalized_period,
            normalized_ann_date,
            technical_json,
            financial_json,
            source_json,
            normalized_code,
            normalized_strategy,
            entered_date,
        ),
    )
    return "transitioned" if status != current_status else "refreshed"


def mark_exited(
    conn: sqlite3.Connection,
    stock_code: str,
    strategy_type: str,
    *,
    date: str,
    reason: str,
) -> bool:
    """将当前未退出 episode 标记为 exited；旧日期请求严格 no-op。"""
    normalized_date = _validate_date(date, "date")
    normalized_reason = _required_text(reason, "reason")
    cursor = conn.execute(
        """
        UPDATE monthly_pattern_pool
        SET status = 'exited',
            last_seen_date = ?,
            exited_date = ?,
            exit_reason = ?,
            updated_at = datetime('now')
        WHERE stock_code = ?
          AND strategy_type = ?
          AND status <> 'exited'
          AND last_seen_date <= ?
        """,
        (
            normalized_date,
            normalized_date,
            normalized_reason,
            _norm_stock_code(stock_code),
            _required_text(strategy_type, "strategy_type"),
            normalized_date,
        ),
    )
    return cursor.rowcount > 0
