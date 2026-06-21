"""trade_thesis 表的 CRUD 与状态转换 — service 层强制业务校验真源."""
from __future__ import annotations

import sqlite3

from .models import Thesis
from .validators import validate_market_region, validate_trade_mode


def create(conn: sqlite3.Connection, thesis: Thesis) -> int:
    """新建一个 thesis,返回 lastrowid;status 强制 'open'(初始建仓周期).

    并发安全:同账户同票同时 open 由 DDL partial unique 索引
    `idx_thesis_account_stock_status (account_id, stock_code) WHERE status='open'`
    保证;两进程并发 INSERT 时第二条会 raise sqlite3.IntegrityError —— 由调用方
    捕获并提示用户("该票该账户已有 open thesis")。Plan R8 后续以 fcntl.flock
    advisory lock 在 import 入口层兜底防双进程同步 import。

    校验范围:当前只校验 trade_mode / market_region 枚举(memory:
    feedback_real_db_vs_in_memory 提到的"service 层强制业务校验真源")。
    其它字段(planned_position_pct 0-1、opened_at 日期格式、必填非空)目前依赖
    CLI argparse(choices / required)+ DDL CHECK / NOT NULL 约束。
    TODO(codex review 中等 1):Web/API 接入 thesis 写入时,把这些下层校验上提到
    本函数,确保跨入口语义一致(plan R11 follow-up)。
    """
    validate_trade_mode(thesis.trade_mode)
    validate_market_region(thesis.market_region)

    cur = conn.execute(
        """
        INSERT INTO trade_thesis (
            stock_code, stock_name, account_id, opened_at, status,
            entry_reason, failure_condition,
            target_price, stop_loss,
            trade_mode, mode_note, market_region,
            sector, planned_position_pct,
            plan_id, notes, input_by
        ) VALUES (?, ?, ?, ?, 'open',
                  ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            thesis.stock_code, thesis.stock_name, thesis.account_id, thesis.opened_at,
            thesis.entry_reason, thesis.failure_condition,
            thesis.target_price, thesis.stop_loss,
            thesis.trade_mode, thesis.mode_note, thesis.market_region,
            thesis.sector, thesis.planned_position_pct,
            thesis.plan_id, thesis.notes, thesis.input_by,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def _row_to_thesis(row: sqlite3.Row) -> Thesis:
    return Thesis(
        id=row["id"],
        stock_code=row["stock_code"],
        stock_name=row["stock_name"],
        account_id=row["account_id"],
        opened_at=row["opened_at"],
        closed_at=row["closed_at"],
        status=row["status"],
        entry_reason=row["entry_reason"],
        failure_condition=row["failure_condition"],
        target_price=row["target_price"],
        stop_loss=row["stop_loss"],
        trade_mode=row["trade_mode"],
        mode_note=row["mode_note"],
        market_region=row["market_region"],
        sector=row["sector"],
        planned_position_pct=row["planned_position_pct"],
        plan_id=row["plan_id"],
        notes=row["notes"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        input_by=row["input_by"],
        reopen_count=row["reopen_count"],
        last_reopened_at=row["last_reopened_at"],
    )


def find_open_thesis(
    conn: sqlite3.Connection, *, account_id: str, stock_code: str
) -> Thesis | None:
    """按 (account_id, stock_code) 查当前 open thesis;partial unique 索引保证至多一行."""
    row = conn.execute(
        """
        SELECT * FROM trade_thesis
        WHERE account_id = ? AND stock_code = ? AND status = 'open'
        """,
        (account_id, stock_code),
    ).fetchone()
    return _row_to_thesis(row) if row else None


def get_by_id(conn: sqlite3.Connection, thesis_id: int) -> Thesis | None:
    row = conn.execute(
        "SELECT * FROM trade_thesis WHERE id = ?", (thesis_id,)
    ).fetchone()
    return _row_to_thesis(row) if row else None


def close(
    conn: sqlite3.Connection, *, thesis_id: int, closed_at: str, input_by: str
) -> None:
    """关闭一个 open thesis,设 status='closed' + closed_at."""
    cur = conn.execute(
        """
        UPDATE trade_thesis
           SET status = 'closed',
               closed_at = ?,
               updated_at = datetime('now')
         WHERE id = ?
        """,
        (closed_at, thesis_id),
    )
    if cur.rowcount == 0:
        raise LookupError(f"thesis #{thesis_id} 不存在,无法关闭")
    conn.commit()


# 关闭后冻结的"主字段"集合(plan 行为契约 A · Q2 决议)
# 改这些必须先 reopen
_MAIN_FIELDS_FROZEN_AFTER_CLOSE = frozenset({
    "entry_reason", "failure_condition",
    "target_price", "stop_loss",
    "trade_mode", "mode_note",
    "planned_position_pct",
    "sector", "market_region",
})

# fill 允许写入的全部字段(避免误传任意 SQL 字段)
_FILL_ALLOWED_FIELDS = frozenset({
    "entry_reason", "failure_condition",
    "target_price", "stop_loss",
    "trade_mode", "mode_note",
    "planned_position_pct",
    "sector", "market_region",
    "notes", "plan_id",
})


def fill(conn: sqlite3.Connection, *, thesis_id: int, **fields) -> None:
    """补/改 thesis 字段;closed 状态下主字段冻结,只允许 notes/plan_id(plan A)."""
    if not fields:
        return

    unknown = set(fields.keys()) - _FILL_ALLOWED_FIELDS
    if unknown:
        raise ValueError(f"fill 不支持字段 {unknown}")

    row = conn.execute(
        "SELECT status FROM trade_thesis WHERE id = ?", (thesis_id,)
    ).fetchone()
    if row is None:
        raise LookupError(f"thesis #{thesis_id} 不存在")
    if row["status"] == "closed":
        attempted_main = set(fields.keys()) & _MAIN_FIELDS_FROZEN_AFTER_CLOSE
        if attempted_main:
            raise ValueError(
                f"thesis #{thesis_id} is closed; main fields {sorted(attempted_main)} are frozen. "
                "Use db thesis-reopen to modify."
            )

    if "trade_mode" in fields:
        validate_trade_mode(fields["trade_mode"])
    if "market_region" in fields:
        validate_market_region(fields["market_region"])

    set_clauses = ", ".join(f"{col} = ?" for col in fields)
    values = list(fields.values())
    values.append(thesis_id)
    conn.execute(
        f"UPDATE trade_thesis SET {set_clauses}, updated_at = datetime('now') WHERE id = ?",
        values,
    )
    conn.commit()


def reopen(
    conn: sqlite3.Connection, *,
    thesis_id: int, reason: str, input_by: str, reopened_at: str,
) -> None:
    """重开 closed thesis;reopen_count++,notes 追加 [reopen DATE] reason 留痕(plan C)."""
    row = conn.execute(
        "SELECT status, notes, reopen_count FROM trade_thesis WHERE id = ?",
        (thesis_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"thesis #{thesis_id} 不存在")
    if row["status"] != "closed":
        raise ValueError(
            f"thesis #{thesis_id} 状态为 {row['status']!r},只能 reopen status='closed' 的 thesis"
        )

    appended = f"[reopen {reopened_at}] {reason}"
    new_notes = (row["notes"] + "\n" + appended) if row["notes"] else appended

    conn.execute(
        """
        UPDATE trade_thesis
           SET status = 'open',
               closed_at = NULL,
               reopen_count = reopen_count + 1,
               last_reopened_at = ?,
               notes = ?,
               updated_at = datetime('now')
         WHERE id = ?
        """,
        (reopened_at, new_notes, thesis_id),
    )
    conn.commit()


def backfill_pnl(conn: sqlite3.Connection, *, thesis_id: int) -> dict:
    """从 broker_executions 派生该 thesis 的 **gross PnL**(忽略手续费;首版从简).

    返回字段:realized_pnl_amount / realized_pnl_pct / holding_days

    TODO(codex review 轻微):当前只用 broker_executions.amount 累加,未减去
    commission / stamp_duty / total_fees;频繁交易场景 net PnL 与 gross 偏差较大.
    follow-up 改用 net_amount(已含费用)或显式扣 total_fees 得到 net PnL.
    """
    rows = conn.execute(
        """
        SELECT direction, shares, amount, biz_date
         FROM broker_executions
         WHERE thesis_id = ?
           AND COALESCE(is_void, 0) = 0
         ORDER BY biz_date ASC, id ASC
        """,
        (thesis_id,),
    ).fetchall()

    if not rows:
        return {
            "realized_pnl_amount": 0.0,
            "realized_pnl_pct": 0.0,
            "holding_days": 0,
        }

    buy_total = sum(r["amount"] for r in rows if r["direction"] == "buy")
    sell_total = sum(r["amount"] for r in rows if r["direction"] == "sell")
    realized_pnl_amount = sell_total - buy_total
    realized_pnl_pct = realized_pnl_amount / buy_total if buy_total > 0 else 0.0

    # holding_days = first buy → last sell(若无 sell 则取最后一笔)
    buy_dates = [r["biz_date"] for r in rows if r["direction"] == "buy"]
    sell_dates = [r["biz_date"] for r in rows if r["direction"] == "sell"]
    first_buy = min(buy_dates) if buy_dates else rows[0]["biz_date"]
    last_sell = max(sell_dates) if sell_dates else rows[-1]["biz_date"]
    from datetime import date as _date
    fb = _date.fromisoformat(first_buy)
    ls = _date.fromisoformat(last_sell)
    holding_days = max((ls - fb).days, 0)

    return {
        "realized_pnl_amount": realized_pnl_amount,
        "realized_pnl_pct": realized_pnl_pct,
        "holding_days": holding_days,
    }


def review_upsert(
    conn: sqlite3.Connection, *, thesis_id: int, input_by: str, **fields
) -> None:
    """upsert thesis_review;多次调用增量更新,未传字段保留原值(plan B)."""
    # 主字段约束:首次写入必须有 executed_as_planned(NOT NULL)
    row = conn.execute(
        "SELECT * FROM thesis_review WHERE thesis_id = ?", (thesis_id,)
    ).fetchone()

    allowed = {"executed_as_planned", "exit_trigger", "lessons", "discipline_score",
               "realized_pnl_pct", "realized_pnl_amount", "holding_days"}
    unknown = set(fields.keys()) - allowed
    if unknown:
        raise ValueError(f"review_upsert 不支持字段 {unknown}")

    if row is None:
        if "executed_as_planned" not in fields:
            raise ValueError("首次 review_upsert 必须传 executed_as_planned")
        conn.execute(
            """
            INSERT INTO thesis_review
                (thesis_id, executed_as_planned, exit_trigger, lessons,
                 discipline_score, realized_pnl_pct, realized_pnl_amount,
                 holding_days, input_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                thesis_id,
                fields["executed_as_planned"],
                fields.get("exit_trigger"),
                fields.get("lessons"),
                fields.get("discipline_score"),
                fields.get("realized_pnl_pct"),
                fields.get("realized_pnl_amount"),
                fields.get("holding_days"),
                input_by,
            ),
        )
    else:
        # 增量更新:仅显式传入的字段会被改写,其余保留原值
        if not fields:
            return
        set_clauses = ", ".join(f"{col} = ?" for col in fields)
        values = list(fields.values())
        values.append(thesis_id)
        conn.execute(
            f"UPDATE thesis_review SET {set_clauses} WHERE thesis_id = ?",
            values,
        )
    conn.commit()
