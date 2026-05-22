"""半自动建议器 — 纯函数,便于单测(plan 关键服务设计)."""
from __future__ import annotations

import sqlite3
from typing import Any, Iterable

from db import queries

from . import repository


def _build_open_command_template(*, account_id: str, stock_code: str, biz_date: str) -> str:
    """plan 严格模式:给出"模板"(占位符必须由用户自己替换),不是 paste 即用的命令.

    占位符故意保持 `<...>` / `'<...>'` 形式 —— 强迫用户思考"我开仓的真实理由 / 模式"
    而非盲目复制(codex review 中等 3).参数来源(stock_code/account_id/biz_date)
    都已被 normalizer 与 argparse 校验,不存在 shell injection 路径.
    """
    return (
        f"db thesis-open --code {stock_code} --account {account_id} "
        f"--opened-at {biz_date} "
        "--entry-reason '<开仓主线>' "
        "--trade-mode '<break|dip|trend|scalp|swing|arbitrage|gap_jump|sentiment_relay|other>' "
        "--failure-condition '<失效条件>' "
        "--planned-position-pct '<0.0-1.0>' --sector '<板块>' "
        "--market-region a-share --input-by '<user>'"
    )


def suggest_open_for_executions(
    conn: sqlite3.Connection, executions: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    """对每条 buy 给出 open / attach 建议;sell 不在本函数职责."""
    out: list[dict[str, Any]] = []
    for row in executions:
        if row.get("direction") != "buy":
            continue
        account_id = row["account_id"]
        stock_code = row["stock_code"]
        biz_date = row["biz_date"]

        existing = repository.find_open_thesis(
            conn, account_id=account_id, stock_code=stock_code,
        )
        if existing is None:
            out.append({
                "action": "open",
                "account_id": account_id,
                "stock_code": stock_code,
                "biz_date": biz_date,
                "command_template": _build_open_command_template(
                    account_id=account_id, stock_code=stock_code, biz_date=biz_date,
                ),
            })
        else:
            out.append({
                "action": "attach",
                "thesis_id": existing.id,
                "account_id": account_id,
                "stock_code": stock_code,
                "biz_date": biz_date,
            })
    return out


def suggest_close_for_holdings(
    conn: sqlite3.Connection, close_events: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    """对每条 new_balance=0 的事件给出 close / historical_orphan 建议."""
    out: list[dict[str, Any]] = []
    for evt in close_events:
        if evt.get("new_balance") != 0:
            continue
        account_id = evt["account_id"]
        stock_code = evt["stock_code"]

        existing = repository.find_open_thesis(
            conn, account_id=account_id, stock_code=stock_code,
        )
        if existing is not None:
            out.append({
                "action": "close",
                "thesis_id": existing.id,
                "account_id": account_id,
                "stock_code": stock_code,
                "biz_date": evt["biz_date"],
            })
        else:
            out.append({
                "action": "historical_orphan",
                "account_id": account_id,
                "stock_code": stock_code,
                "biz_date": evt["biz_date"],
                "reason": "holdings 归零但无 open thesis 关联(R5/U2 历史孤儿)",
            })
    return out


def suggest_review(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """列出 status='closed' 但 thesis_review 缺失的 thesis(plan E 第三类待补)."""
    rows = conn.execute(
        """
        SELECT t.id AS thesis_id, t.stock_code, t.account_id,
               t.opened_at, t.closed_at
          FROM trade_thesis t
          LEFT JOIN thesis_review r ON r.thesis_id = t.id
         WHERE t.status = 'closed' AND r.thesis_id IS NULL
         ORDER BY t.closed_at DESC
        """
    ).fetchall()
    return [dict(r) for r in rows]


# ──────────────────────────────────────────────────────────────
# importer 集成 hook(阶段 4 / plan I 系列)
# ──────────────────────────────────────────────────────────────

def detect_thesis_triggers(
    conn: sqlite3.Connection,
    rows: list,  # list[NormalizedRow] — 弱类型避免循环 import
) -> list[dict[str, Any]]:
    """import dry-run 阶段:列出每条 buy 的 thesis 触发类型(open / attach).

    纯查询,不写;返回 list 与 row 一一对应(非 buy 的跳过)."""
    out: list[dict[str, Any]] = []
    for row in rows:
        if row.direction != "buy":
            continue
        existing = repository.find_open_thesis(
            conn, account_id=row.account_id, stock_code=row.stock_code,
        )
        if existing is None:
            out.append({
                "action": "open",
                "row_index": row.row_index,
                "account_id": row.account_id,
                "stock_code": row.stock_code,
                "biz_date": row.biz_date,
                # 占位符故意保持 `<...>` / `'<...>'` 形式:这不是"直接 paste 可跑"的
                # 命令,而是带"必须自己替换"语义的模板(codex review 中等 3 反驳)。
                # 让用户思考 mode 选哪个比盲目复制更安全。stock_code / account_id /
                # biz_date 来自已校验的 NormalizedRow(stock_code 是 6 位数字字符串,
                # account_id 走 normalizer/CLI required,biz_date GLOB ????-??-??),
                # 无 shell escape 风险。
                "command_template": (
                    f"db thesis-open --code {row.stock_code} "
                    f"--account {row.account_id} --opened-at {row.biz_date} "
                    "--entry-reason '<开仓主线>' "
                    "--trade-mode '<break|dip|trend|scalp|swing|arbitrage|gap_jump|sentiment_relay|other>' "
                    "--failure-condition '<失效条件>' "
                    "--planned-position-pct '<0.0-1.0>' --sector '<板块>' "
                    "--market-region a-share --input-by '<user>'"
                ),
            })
        else:
            out.append({
                "action": "attach",
                "row_index": row.row_index,
                "thesis_id": existing.id,
                "account_id": row.account_id,
                "stock_code": row.stock_code,
                "biz_date": row.biz_date,
            })
    return out


def link_thesis_to_executions(
    conn: sqlite3.Connection, *, import_run_id: str,
) -> dict[str, int]:
    """import 实写后,把当前 run 的 broker_executions.thesis_id 填上(UPDATE).

    对每行(buy + sell)查 find_open_thesis;有则 UPDATE,无则保持 NULL(orphan).
    用元组索引访问,兼容未配置 row_factory 的 connection.

    **不自己 commit**:由调用方控制 —— importer 在 BEGIN/COMMIT 主事务内调用此函数,
    确保 broker_executions INSERT 与 thesis_id 回填要么同时成功要么同时回滚
    (codex review 严重 1).
    """
    linked = 0
    orphan = 0
    rows = conn.execute(
        """
        SELECT id, account_id, stock_code FROM broker_executions
         WHERE import_run_id = ? AND thesis_id IS NULL
        """,
        (import_run_id,),
    ).fetchall()
    for r in rows:
        be_id, acct, code = r[0], r[1], r[2]
        existing = repository.find_open_thesis(
            conn, account_id=acct, stock_code=code,
        )
        if existing is not None:
            conn.execute(
                "UPDATE broker_executions SET thesis_id = ? WHERE id = ?",
                (existing.id, be_id),
            )
            linked += 1
        else:
            orphan += 1
    return {"linked": linked, "orphan": orphan}


def sync_holdings_from_executions(
    conn: sqlite3.Connection, *, import_run_id: str,
) -> dict[str, int]:
    """按本批已关联 thesis 的成交事实维护 holdings 当前持仓.

    只处理 `broker_executions.thesis_id IS NOT NULL` 的行;历史 orphan 不派生持仓.
    不自行 commit,由 importer 主事务统一控制。
    """
    affected = conn.execute(
        """
        SELECT DISTINCT thesis_id
          FROM broker_executions
         WHERE import_run_id = ? AND thesis_id IS NOT NULL
         ORDER BY thesis_id
        """,
        (import_run_id,),
    ).fetchall()

    active = 0
    closed = 0
    skipped = 0
    for r in affected:
        thesis_id = r[0]
        thesis = conn.execute(
            """
            SELECT stock_code, stock_name, opened_at, entry_reason,
                   market_region, sector, planned_position_pct
              FROM trade_thesis
             WHERE id = ?
            """,
            (thesis_id,),
        ).fetchone()
        if thesis is None:
            skipped += 1
            continue

        stock_code = thesis[0]
        balance_row = conn.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN direction = 'buy' THEN shares ELSE -shares END), 0)
                    AS balance,
                COALESCE(SUM(CASE WHEN direction = 'buy' THEN shares ELSE 0 END), 0)
                    AS buy_shares,
                COALESCE(SUM(CASE WHEN direction = 'buy' THEN amount ELSE 0 END), 0)
                    AS buy_amount,
                MIN(CASE WHEN direction = 'buy' THEN biz_date ELSE NULL END)
                    AS entry_date
              FROM broker_executions
             WHERE thesis_id = ?
            """,
            (thesis_id,),
        ).fetchone()
        balance = int(balance_row[0] or 0)
        buy_shares = int(balance_row[1] or 0)
        buy_amount = float(balance_row[2] or 0)
        entry_price = round(buy_amount / buy_shares, 6) if buy_shares else None

        if balance > 0:
            queries.upsert_holding(
                conn,
                stock_code=stock_code,
                stock_name=thesis[1],
                market=_market_label(thesis[4]),
                sector=thesis[5],
                shares=balance,
                entry_date=balance_row[3] or thesis[2],
                entry_price=entry_price,
                position_ratio=thesis[6],
                status="active",
                entry_reason=thesis[3],
                thesis_id=thesis_id,
            )
            active += 1
            continue

        cur = conn.execute(
            """
            UPDATE holdings
               SET shares = 0, status = 'closed', updated_at = datetime('now')
             WHERE thesis_id = ? AND status = 'active'
            """,
            (thesis_id,),
        )
        if cur.rowcount == 0:
            cur = conn.execute(
                """
                UPDATE holdings
                   SET shares = 0, status = 'closed', thesis_id = ?,
                       updated_at = datetime('now')
                 WHERE stock_code = ? AND status = 'active'
                """,
                (thesis_id, stock_code),
            )
        closed += cur.rowcount

    return {"active": active, "closed": closed, "skipped": skipped}


def _market_label(market_region: str | None) -> str:
    if market_region == "a-share":
        return "A股"
    if market_region == "hk":
        return "港股"
    if market_region == "us":
        return "美股"
    return "A股"


def auto_close_zero_balance_thesis(
    conn: sqlite3.Connection, *, import_run_id: str,
) -> list[int]:
    """import 实写后,对本批 sell 涉及到的 thesis,若累计 holdings 归零 → close + 追加 notes.

    返回被 auto-close 的 thesis_id 列表.用元组索引,兼容无 row_factory 的连接.
    """
    affected = conn.execute(
        """
        SELECT DISTINCT thesis_id, biz_date FROM broker_executions
         WHERE import_run_id = ? AND direction = 'sell' AND thesis_id IS NOT NULL
         ORDER BY biz_date DESC
        """,
        (import_run_id,),
    ).fetchall()

    closed_ids: list[int] = []
    for r in affected:
        thesis_id, biz_date = r[0], r[1]

        # 累计 holdings(全量 broker_executions,不限本批 → 跨 import 批次正确累计)
        bal_row = conn.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN direction = 'buy' THEN shares ELSE 0 END), 0)
              - COALESCE(SUM(CASE WHEN direction = 'sell' THEN shares ELSE 0 END), 0)
                AS balance
              FROM broker_executions
             WHERE thesis_id = ?
            """,
            (thesis_id,),
        ).fetchone()
        if bal_row[0] != 0:
            continue

        # 当前 thesis 必须 open 才 close;若已 closed 跳过
        t_row = conn.execute(
            "SELECT status, notes FROM trade_thesis WHERE id = ?", (thesis_id,),
        ).fetchone()
        if t_row is None or t_row[0] != "open":
            continue

        existing_notes = t_row[1]
        appended = f"[auto-close {biz_date}] holdings 归零自动关闭"
        new_notes = (existing_notes + "\n" + appended) if existing_notes else appended
        conn.execute(
            """
            UPDATE trade_thesis
               SET status = 'closed', closed_at = ?, notes = ?,
                   updated_at = datetime('now')
             WHERE id = ?
            """,
            (biz_date, new_notes, thesis_id),
        )
        closed_ids.append(thesis_id)
    conn.commit()
    return closed_ids
