from __future__ import annotations

import sqlite3
from typing import Any

from db import queries


def repair_reconcile(
    conn: sqlite3.Connection,
    *,
    account_id: str = "default",
    date_from: str | None = None,
    date_to: str | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Repair historical execution links and reconcile current holding state.

    The repair is intentionally conservative:
    - it links existing execution rows to an already-created thesis when the row
      falls inside that thesis' opened/closed date window;
    - it uses the broker-provided latest ``balance_after`` as the authority for
      current holdings, avoiding double-counting rows from duplicate imports.
    """
    report: dict[str, Any] = {
        "dry_run": dry_run,
        "account_id": account_id,
        "date_from": date_from,
        "date_to": date_to,
        "linked_execution_rows": 0,
        "active_holdings_upserted": 0,
        "holdings_closed": 0,
        "thesis_closed": 0,
        "voided_execution_rows": 0,
        "missing_thesis": [],
        "operations": [],
    }

    conn.execute("SAVEPOINT repair_reconcile")
    try:
        report["voided_execution_rows"] = void_semantic_duplicates(
            conn,
            account_id=account_id,
            date_from=date_from,
            date_to=date_to,
            report=report,
        )
        report["linked_execution_rows"] = _link_existing_executions(
            conn,
            account_id=account_id,
            date_from=date_from,
            date_to=date_to,
            report=report,
        )
        _reconcile_latest_balances(
            conn,
            account_id=account_id,
            date_from=date_from,
            date_to=date_to,
            report=report,
        )
        if dry_run:
            conn.execute("ROLLBACK TO repair_reconcile")
        conn.execute("RELEASE repair_reconcile")
    except Exception:
        conn.execute("ROLLBACK TO repair_reconcile")
        conn.execute("RELEASE repair_reconcile")
        raise
    return report


def _link_existing_executions(
    conn: sqlite3.Connection,
    *,
    account_id: str,
    date_from: str | None,
    date_to: str | None,
    report: dict[str, Any],
) -> int:
    rows = conn.execute(
        """
        SELECT id, account_id, stock_code, stock_name, biz_date, direction
          FROM broker_executions
         WHERE account_id = ?
           AND thesis_id IS NULL
           AND COALESCE(is_void, 0) = 0
           AND (? IS NULL OR biz_date >= ?)
           AND (? IS NULL OR biz_date <= ?)
         ORDER BY biz_date, exec_time, id
        """,
        (account_id, date_from, date_from, date_to, date_to),
    ).fetchall()

    linked = 0
    for row in rows:
        thesis = _find_thesis_covering_date(
            conn,
            account_id=row["account_id"],
            stock_code=row["stock_code"],
            biz_date=row["biz_date"],
        )
        if thesis is None:
            report["missing_thesis"].append({
                "execution_id": row["id"],
                "stock_code": row["stock_code"],
                "stock_name": row["stock_name"],
                "biz_date": row["biz_date"],
                "direction": row["direction"],
            })
            continue
        conn.execute(
            "UPDATE broker_executions SET thesis_id = ? WHERE id = ?",
            (thesis["id"], row["id"]),
        )
        linked += 1
        report["operations"].append({
            "action": "link_execution",
            "execution_id": row["id"],
            "thesis_id": thesis["id"],
            "stock_code": row["stock_code"],
            "biz_date": row["biz_date"],
        })
    return linked


def _find_thesis_covering_date(
    conn: sqlite3.Connection,
    *,
    account_id: str,
    stock_code: str,
    biz_date: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
          FROM trade_thesis
         WHERE account_id = ?
           AND stock_code = ?
           AND opened_at <= ?
           AND (closed_at IS NULL OR closed_at >= ?)
         ORDER BY
           CASE WHEN status = 'open' THEN 0 ELSE 1 END,
           opened_at DESC,
           id DESC
         LIMIT 1
        """,
        (account_id, stock_code, biz_date, biz_date),
    ).fetchone()


def _reconcile_latest_balances(
    conn: sqlite3.Connection,
    *,
    account_id: str,
    date_from: str | None,
    date_to: str | None,
    report: dict[str, Any],
) -> None:
    rows = conn.execute(
        """
        SELECT b.*
          FROM broker_executions b
          JOIN (
                SELECT stock_code, MAX(biz_date || ' ' || COALESCE(exec_time, '') || ' ' || printf('%012d', id)) AS max_key
                  FROM broker_executions
                 WHERE account_id = ?
                   AND balance_after IS NOT NULL
                   AND COALESCE(is_void, 0) = 0
                   AND (? IS NULL OR biz_date >= ?)
                   AND (? IS NULL OR biz_date <= ?)
                 GROUP BY stock_code
          ) latest
            ON latest.stock_code = b.stock_code
           AND latest.max_key = b.biz_date || ' ' || COALESCE(b.exec_time, '') || ' ' || printf('%012d', b.id)
         WHERE b.account_id = ?
         ORDER BY b.biz_date, b.exec_time, b.id
        """,
        (account_id, date_from, date_from, date_to, date_to, account_id),
    ).fetchall()

    for row in rows:
        balance = int(row["balance_after"] or 0)
        if balance <= 0:
            _close_current_state_for_zero_balance(conn, row=row, report=report)
            continue
        _upsert_current_holding(conn, row=row, balance=balance, report=report)


def _close_current_state_for_zero_balance(
    conn: sqlite3.Connection,
    *,
    row: sqlite3.Row,
    report: dict[str, Any],
) -> None:
    cur = conn.execute(
        """
        UPDATE holdings
           SET shares = 0, status = 'closed', updated_at = datetime('now')
         WHERE stock_code = ? AND status = 'active'
        """,
        (row["stock_code"],),
    )
    if cur.rowcount:
        report["holdings_closed"] += cur.rowcount
        report["operations"].append({
            "action": "close_holding",
            "stock_code": row["stock_code"],
            "stock_name": row["stock_name"],
            "closed_at": row["biz_date"],
            "rows": cur.rowcount,
        })

    thesis = conn.execute(
        """
        SELECT id
          FROM trade_thesis
         WHERE account_id = ?
           AND stock_code = ?
           AND status = 'open'
           AND opened_at <= ?
         ORDER BY opened_at DESC, id DESC
         LIMIT 1
        """,
        (row["account_id"], row["stock_code"], row["biz_date"]),
    ).fetchone()
    if thesis is None:
        return
    conn.execute(
        """
        UPDATE trade_thesis
           SET status = 'closed', closed_at = ?, updated_at = datetime('now')
         WHERE id = ?
        """,
        (row["biz_date"], thesis["id"]),
    )
    report["thesis_closed"] += 1
    report["operations"].append({
        "action": "close_thesis",
        "thesis_id": thesis["id"],
        "stock_code": row["stock_code"],
        "closed_at": row["biz_date"],
    })


def _upsert_current_holding(
    conn: sqlite3.Connection,
    *,
    row: sqlite3.Row,
    balance: int,
    report: dict[str, Any],
) -> None:
    thesis = _find_thesis_covering_date(
        conn,
        account_id=row["account_id"],
        stock_code=row["stock_code"],
        biz_date=row["biz_date"],
    )
    if thesis is None:
        report["missing_thesis"].append({
            "execution_id": row["id"],
            "stock_code": row["stock_code"],
            "stock_name": row["stock_name"],
            "biz_date": row["biz_date"],
            "direction": row["direction"],
            "reason": "positive_balance_without_thesis",
        })
        return

    cost = _weighted_entry_price(conn, thesis_id=thesis["id"])
    entry_price = cost or row["price"]
    if _active_holding_matches(
        conn,
        stock_code=row["stock_code"],
        shares=balance,
        entry_price=entry_price,
        thesis_id=thesis["id"],
    ):
        return

    queries.upsert_holding(
        conn,
        stock_code=row["stock_code"],
        stock_name=row["stock_name"],
        market=_market_label(thesis["market_region"]),
        sector=thesis["sector"],
        shares=balance,
        entry_date=thesis["opened_at"],
        entry_price=entry_price,
        position_ratio=thesis["planned_position_pct"],
        status="active",
        entry_reason=thesis["entry_reason"],
        thesis_id=thesis["id"],
    )
    report["active_holdings_upserted"] += 1
    report["operations"].append({
        "action": "upsert_holding",
        "thesis_id": thesis["id"],
        "stock_code": row["stock_code"],
        "shares": balance,
    })


def _active_holding_matches(
    conn: sqlite3.Connection,
    *,
    stock_code: str,
    shares: int,
    entry_price: float,
    thesis_id: int,
) -> bool:
    row = conn.execute(
        """
        SELECT shares, entry_price, thesis_id
          FROM holdings
         WHERE stock_code = ? AND status = 'active'
         ORDER BY updated_at DESC, id DESC
         LIMIT 1
        """,
        (stock_code,),
    ).fetchone()
    if row is None:
        return False
    if int(row["shares"] or 0) != shares:
        return False
    if int(row["thesis_id"] or 0) != int(thesis_id):
        return False
    try:
        return abs(float(row["entry_price"] or 0) - float(entry_price)) < 0.000001
    except (TypeError, ValueError):
        return False


def _weighted_entry_price(conn: sqlite3.Connection, *, thesis_id: int) -> float | None:
    row = conn.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN direction = 'buy' THEN shares ELSE 0 END), 0) AS buy_shares,
            COALESCE(SUM(CASE WHEN direction = 'buy' THEN amount ELSE 0 END), 0) AS buy_amount
         FROM broker_executions
         WHERE thesis_id = ?
           AND COALESCE(is_void, 0) = 0
        """,
        (thesis_id,),
    ).fetchone()
    buy_shares = int(row["buy_shares"] or 0)
    if buy_shares <= 0:
        return None
    return round(float(row["buy_amount"] or 0) / buy_shares, 6)


def void_semantic_duplicates(
    conn: sqlite3.Connection,
    *,
    account_id: str,
    date_from: str | None,
    date_to: str | None,
    report: dict[str, Any] | None = None,
) -> int:
    if report is None:
        report = {"operations": []}
    cur = conn.execute(
        """
        SELECT id, account_id, biz_date, exec_time, stock_code, stock_name,
               direction, shares, price, amount, balance_after, source_file,
               broker_contract_no, broker_trade_no
          FROM broker_executions
         WHERE account_id = ?
           AND COALESCE(is_void, 0) = 0
           AND (? IS NULL OR biz_date >= ?)
           AND (? IS NULL OR biz_date <= ?)
         ORDER BY biz_date, exec_time, stock_code, direction, id
        """,
        (account_id, date_from, date_from, date_to, date_to),
    )
    rows = [_execution_row_to_dict(cur, row) for row in cur.fetchall()]

    by_exact: dict[tuple[Any, ...], list[sqlite3.Row]] = {}
    for row in rows:
        key = (
            row["account_id"],
            row["biz_date"],
            row["exec_time"],
            row["stock_code"],
            row["direction"],
            row["broker_contract_no"] or "",
            row["broker_trade_no"] or "",
            int(row["shares"] or 0),
            _money_key(row["price"]),
            _money_key(row["amount"]),
        )
        by_exact.setdefault(key, []).append(row)

    void_ids: dict[int, str] = {}
    for group in by_exact.values():
        if len(group) <= 1:
            continue
        keep = _preferred_duplicate_row(group)
        for row in group:
            if row["id"] != keep["id"]:
                void_ids[int(row["id"])] = "semantic_duplicate_exact"

    by_semantic_exact: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in rows:
        key = (
            row["account_id"],
            row["biz_date"],
            row["exec_time"],
            row["stock_code"],
            row["direction"],
            int(row["shares"] or 0),
            _money_key(row["price"]),
            _money_key(row["amount"]),
        )
        by_semantic_exact.setdefault(key, []).append(row)

    for group in by_semantic_exact.values():
        if len(group) <= 1:
            continue
        balance_rows = [row for row in group if row["balance_after"] is not None]
        no_balance_rows = [row for row in group if row["balance_after"] is None]
        if not balance_rows or not no_balance_rows:
            continue
        for row in no_balance_rows:
            void_ids[int(row["id"])] = "semantic_duplicate_exact"

    active_rows = [row for row in rows if int(row["id"]) not in void_ids]
    by_trade_slot: dict[tuple[Any, ...], list[sqlite3.Row]] = {}
    for row in active_rows:
        key = (
            row["account_id"],
            row["biz_date"],
            row["exec_time"],
            row["stock_code"],
            row["direction"],
            _money_key(row["price"]),
        )
        by_trade_slot.setdefault(key, []).append(row)

    for group in by_trade_slot.values():
        if len(group) <= 1:
            continue
        aggregate = _find_aggregate_duplicate(group)
        if aggregate is None:
            continue
        for row in group:
            if row["id"] != aggregate["id"]:
                void_ids[int(row["id"])] = "semantic_duplicate_component"

    for be_id, reason in sorted(void_ids.items()):
        conn.execute(
            """
            UPDATE broker_executions
               SET is_void = 1, void_reason = ?, voided_at = datetime('now')
             WHERE id = ? AND COALESCE(is_void, 0) = 0
            """,
            (reason, be_id),
        )
        report["operations"].append({
            "action": "void_execution",
            "execution_id": be_id,
            "reason": reason,
        })
    return len(void_ids)


def _preferred_duplicate_row(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return sorted(
        rows,
        key=lambda row: (
            0 if row["balance_after"] is not None else 1,
            -int(row["id"]),
        ),
    )[0]


def _find_aggregate_duplicate(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = sorted(
        rows,
        key=lambda row: (
            0 if row["balance_after"] is not None else 1,
            -int(row["shares"] or 0),
            -int(row["id"]),
        ),
    )
    for candidate in candidates:
        others = [row for row in rows if row["id"] != candidate["id"]]
        if not others:
            continue
        candidate_shares = int(candidate["shares"] or 0)
        if candidate_shares <= max(int(row["shares"] or 0) for row in others):
            continue
        shares_sum = sum(int(row["shares"] or 0) for row in others)
        amount_sum = sum(float(row["amount"] or 0) for row in others)
        if candidate_shares != shares_sum:
            continue
        if abs(float(candidate["amount"] or 0) - amount_sum) > 0.01:
            continue
        return candidate
    return None


def _money_key(value: Any) -> int:
    try:
        return int(round(float(value or 0) * 10000))
    except (TypeError, ValueError):
        return 0


def _execution_row_to_dict(
    cur: sqlite3.Cursor,
    row: sqlite3.Row | tuple[Any, ...],
) -> dict[str, Any]:
    if isinstance(row, sqlite3.Row):
        return dict(row)
    return {desc[0]: row[index] for index, desc in enumerate(cur.description or [])}


def _market_label(market_region: str | None) -> str:
    if market_region == "hk":
        return "港股"
    if market_region == "us":
        return "美股"
    return "A股"
