"""常用查询封装：提供面向业务的 Python API，隔离 SQL 细节。"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from typing import Any

from .dual_write import _normalize_stock_code_for_match
from .schema import holding_code_norm_sql


# ──────────────────────────────────────────────────────────────
# 通用辅助
# ──────────────────────────────────────────────────────────────

def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return dict(row)


def _rows_to_list(rows: list[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in rows]


def _fts_match_expr(keyword: str) -> str:
    """将关键词转为 FTS5 MATCH 表达式。对中文用短语匹配。"""
    safe = keyword.replace('"', '""')
    return f'"{safe}"'


# ──────────────────────────────────────────────────────────────
# Teachers / Teacher Notes
# ──────────────────────────────────────────────────────────────

def get_or_create_teacher(conn: sqlite3.Connection, name: str,
                          platform: str | None = None,
                          schedule: str | None = None) -> int:
    """获取或创建老师，返回 teacher_id。"""
    row = conn.execute("SELECT id FROM teachers WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO teachers (name, platform, schedule) VALUES (?, ?, ?)",
        (name, platform, schedule),
    )
    return cur.lastrowid  # type: ignore[return-value]


def insert_teacher_note(conn: sqlite3.Connection, *, teacher_id: int, date: str,
                        title: str, **kwargs: Any) -> int:
    """插入一条老师笔记，返回 note_id。"""
    cols = ["teacher_id", "date", "title"]
    vals: list[Any] = [teacher_id, date, title]
    for k in ("source_type", "input_by", "core_view", "position_advice",
              "obsidian_path", "tags", "key_points", "sectors", "avoid", "raw_content"):
        if k in kwargs and kwargs[k] is not None:
            cols.append(k)
            v = kwargs[k]
            vals.append(json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v)
    placeholders = ", ".join("?" * len(cols))
    sql = f"INSERT INTO teacher_notes ({', '.join(cols)}) VALUES ({placeholders})"
    cur = conn.execute(sql, vals)
    return cur.lastrowid  # type: ignore[return-value]


def search_teacher_notes(conn: sqlite3.Connection, keyword: str,
                         teacher_name: str | None = None,
                         date_from: str | None = None,
                         date_to: str | None = None,
                         limit: int = 50) -> list[dict]:
    """搜索老师笔记（LIKE 模式，对中文可靠；数据量 <5000 行/年，性能无忧）。"""
    like_pat = f"%{keyword}%"
    sql = """
        SELECT n.*, t.name as teacher_name
        FROM teacher_notes n
        JOIN teachers t ON n.teacher_id = t.id
        WHERE (n.title LIKE ? OR n.core_view LIKE ? OR n.key_points LIKE ?
               OR n.sectors LIKE ? OR n.avoid LIKE ? OR n.raw_content LIKE ?)
    """
    params: list[Any] = [like_pat] * 6
    if teacher_name:
        sql += " AND t.name = ?"
        params.append(teacher_name)
    if date_from:
        sql += " AND n.date >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND n.date <= ?"
        params.append(date_to)
    sql += " ORDER BY n.date DESC LIMIT ?"
    params.append(limit)
    return _rows_to_list(conn.execute(sql, params).fetchall())


def get_teacher_timeline(conn: sqlite3.Connection, teacher_id: int,
                         date_from: str | None = None,
                         date_to: str | None = None) -> list[dict]:
    """获取指定老师的笔记时间线。"""
    sql = "SELECT * FROM teacher_notes WHERE teacher_id = ?"
    params: list[Any] = [teacher_id]
    if date_from:
        sql += " AND date >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND date <= ?"
        params.append(date_to)
    sql += " ORDER BY date DESC"
    return _rows_to_list(conn.execute(sql, params).fetchall())


def list_teachers(conn: sqlite3.Connection) -> list[dict]:
    return _rows_to_list(conn.execute("SELECT * FROM teachers ORDER BY name").fetchall())


# ──────────────────────────────────────────────────────────────
# Note Attachments
# ──────────────────────────────────────────────────────────────

def insert_attachment(conn: sqlite3.Connection, note_id: int,
                      file_path: str, file_type: str | None = None,
                      description: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO note_attachments (note_id, file_path, file_type, description) VALUES (?, ?, ?, ?)",
        (note_id, file_path, file_type, description),
    )
    return cur.lastrowid  # type: ignore[return-value]


# ──────────────────────────────────────────────────────────────
# Calendar Events
# ──────────────────────────────────────────────────────────────

def insert_calendar_event(conn: sqlite3.Connection, **kwargs: Any) -> int:
    cols, vals = [], []
    for k in ("date", "time", "event", "impact", "category", "source",
              "country", "prior", "expected", "actual", "note"):
        if k in kwargs and kwargs[k] is not None:
            cols.append(k)
            vals.append(kwargs[k])
    placeholders = ", ".join("?" * len(cols))
    cur = conn.execute(
        f"INSERT INTO calendar_events ({', '.join(cols)}) VALUES ({placeholders})", vals,
    )
    return cur.lastrowid  # type: ignore[return-value]


def get_calendar_range(conn: sqlite3.Connection, date_from: str, date_to: str,
                       impact: str | None = None,
                       category: str | None = None) -> list[dict]:
    sql = "SELECT * FROM calendar_events WHERE date >= ? AND date <= ?"
    params: list[Any] = [date_from, date_to]
    if impact:
        sql += " AND impact = ?"
        params.append(impact)
    if category:
        sql += " AND category = ?"
        params.append(category)
    sql += " ORDER BY date, time"
    return _rows_to_list(conn.execute(sql, params).fetchall())


# ──────────────────────────────────────────────────────────────
# Holdings
# ──────────────────────────────────────────────────────────────

_HOLDING_INSERTABLE = (
    "stock_code", "stock_name", "market", "sector", "shares",
    "entry_date", "entry_price", "current_price", "stop_loss",
    "target_price", "position_ratio", "status", "note",
)

_HOLDINGS_UPDATABLE = frozenset({
    "stock_code", "stock_name", "market", "sector", "shares",
    "entry_date", "entry_price", "current_price", "stop_loss",
    "target_price", "position_ratio", "status", "note",
})


def _active_holdings_by_code(conn: sqlite3.Connection, stock_code: str) -> list[dict]:
    norm = _normalize_stock_code_for_match(stock_code)
    if not norm:
        return []
    rows = conn.execute(
        f"""
        SELECT *
        FROM holdings
        WHERE status = 'active' AND {holding_code_norm_sql('stock_code')} = ?
        ORDER BY (updated_at IS NOT NULL) DESC, updated_at DESC, id DESC
        """,
        (norm,),
    ).fetchall()
    return _rows_to_list(rows)


def upsert_holding(conn: sqlite3.Connection, **kwargs: Any) -> int:
    code = kwargs.get("stock_code")
    target_status = kwargs.get("status")
    if code and target_status in (None, "active"):
        active_rows = _active_holdings_by_code(conn, str(code))
        if active_rows:
            holding_id = int(active_rows[0]["id"])
            payload = {k: v for k, v in kwargs.items() if k in _HOLDINGS_UPDATABLE and v is not None}
            if payload:
                update_holding(conn, holding_id, **payload)
            return holding_id

    cols, vals = [], []
    for k in _HOLDING_INSERTABLE:
        if k in kwargs and kwargs[k] is not None:
            cols.append(k)
            vals.append(kwargs[k])
    placeholders = ", ".join("?" * len(cols))
    cur = conn.execute(
        f"INSERT INTO holdings ({', '.join(cols)}) VALUES ({placeholders})", vals,
    )
    return cur.lastrowid  # type: ignore[return-value]


def get_holdings(conn: sqlite3.Connection, status: str | None = "active") -> list[dict]:
    if status:
        return _rows_to_list(
            conn.execute("SELECT * FROM holdings WHERE status = ?", (status,)).fetchall()
        )
    return _rows_to_list(conn.execute("SELECT * FROM holdings").fetchall())


def close_active_holdings_by_code(conn: sqlite3.Connection, stock_code: str) -> int:
    """按归一化代码关闭全部 active 持仓。"""
    rows = _active_holdings_by_code(conn, stock_code)
    for row in rows:
        update_holding(conn, int(row["id"]), status="closed")
    return len(rows)


def update_holding(conn: sqlite3.Connection, holding_id: int, **kwargs: Any) -> None:
    sets, vals = [], []
    for k, v in kwargs.items():
        if k not in _HOLDINGS_UPDATABLE:
            raise ValueError(f"Invalid column for holdings: {k}")
        sets.append(f"{k} = ?")
        vals.append(v)
    if not sets:
        return
    vals.append(holding_id)
    conn.execute(f"UPDATE holdings SET {', '.join(sets)} WHERE id = ?", vals)


def delete_holding(conn: sqlite3.Connection, holding_id: int) -> int:
    """删除持仓，返回受影响行数。"""
    cur = conn.execute("DELETE FROM holdings WHERE id = ?", (holding_id,))
    return cur.rowcount


# ──────────────────────────────────────────────────────────────
# Watchlist
# ──────────────────────────────────────────────────────────────

def insert_watchlist(conn: sqlite3.Connection, **kwargs: Any) -> int:
    cols, vals = [], []
    for k in ("stock_code", "stock_name", "tier", "sector", "add_date",
              "add_reason", "trigger_condition", "entry_condition",
              "entry_mode", "position_plan", "volume_status", "current_status",
              "leader_type", "successor", "role", "status", "note"):
        if k in kwargs and kwargs[k] is not None:
            cols.append(k)
            vals.append(kwargs[k])
    placeholders = ", ".join("?" * len(cols))
    cur = conn.execute(
        f"INSERT INTO watchlist ({', '.join(cols)}) VALUES ({placeholders})", vals,
    )
    return cur.lastrowid  # type: ignore[return-value]


def get_watchlist(conn: sqlite3.Connection, tier: str | None = None,
                  status: str = "watching") -> list[dict]:
    sql = "SELECT * FROM watchlist WHERE status = ?"
    params: list[Any] = [status]
    if tier:
        sql += " AND tier = ?"
        params.append(tier)
    sql += " ORDER BY add_date DESC"
    return _rows_to_list(conn.execute(sql, params).fetchall())


_WATCHLIST_UPDATABLE = frozenset({
    "stock_code", "stock_name", "tier", "sector", "add_date",
    "add_reason", "trigger_condition", "entry_condition", "entry_mode",
    "position_plan", "volume_status", "current_status", "leader_type",
    "successor", "role", "status", "note",
})


def update_watchlist_item(conn: sqlite3.Connection, item_id: int, **kwargs: Any) -> None:
    sets, vals = [], []
    for k, v in kwargs.items():
        if k not in _WATCHLIST_UPDATABLE:
            raise ValueError(f"Invalid column for watchlist: {k}")
        sets.append(f"{k} = ?")
        vals.append(v)
    if not sets:
        return
    vals.append(item_id)
    conn.execute(f"UPDATE watchlist SET {', '.join(sets)} WHERE id = ?", vals)


def delete_watchlist_item(conn: sqlite3.Connection, item_id: int) -> int:
    """删除关注池条目，返回受影响行数。"""
    cur = conn.execute("DELETE FROM watchlist WHERE id = ?", (item_id,))
    return cur.rowcount


# ──────────────────────────────────────────────────────────────
# Blacklist
# ──────────────────────────────────────────────────────────────

def insert_blacklist(conn: sqlite3.Connection, stock_code: str, stock_name: str,
                     reason: str | None = None, until: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO blacklist (stock_code, stock_name, reason, until) VALUES (?, ?, ?, ?)",
        (stock_code, stock_name, reason, until),
    )
    return cur.lastrowid  # type: ignore[return-value]


def get_blacklist(conn: sqlite3.Connection) -> list[dict]:
    return _rows_to_list(conn.execute("SELECT * FROM blacklist ORDER BY created_at DESC").fetchall())


def delete_blacklist(conn: sqlite3.Connection, item_id: int) -> int:
    """删除黑名单条目，返回受影响行数。"""
    cur = conn.execute("DELETE FROM blacklist WHERE id = ?", (item_id,))
    return cur.rowcount


# ──────────────────────────────────────────────────────────────
# Industry / Macro Info
# ──────────────────────────────────────────────────────────────

def insert_industry_info(conn: sqlite3.Connection, **kwargs: Any) -> int:
    cols, vals = [], []
    for k in ("date", "sector_name", "info_type", "content", "source",
              "confidence", "timeliness", "tags"):
        if k in kwargs and kwargs[k] is not None:
            cols.append(k)
            v = kwargs[k]
            vals.append(json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v)
    placeholders = ", ".join("?" * len(cols))
    cur = conn.execute(
        f"INSERT INTO industry_info ({', '.join(cols)}) VALUES ({placeholders})", vals,
    )
    return cur.lastrowid  # type: ignore[return-value]


def insert_macro_info(conn: sqlite3.Connection, **kwargs: Any) -> int:
    cols, vals = [], []
    for k in ("date", "category", "title", "content", "source",
              "impact_assessment", "confidence", "tags"):
        if k in kwargs and kwargs[k] is not None:
            cols.append(k)
            v = kwargs[k]
            vals.append(json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v)
    placeholders = ", ".join("?" * len(cols))
    cur = conn.execute(
        f"INSERT INTO macro_info ({', '.join(cols)}) VALUES ({placeholders})", vals,
    )
    return cur.lastrowid  # type: ignore[return-value]


def get_recent_industry_info(conn: sqlite3.Connection,
                             date_from: str | None = None,
                             date_to: str | None = None,
                             limit: int = 50) -> list[dict]:
    """按日期范围获取行业信息，供复盘预填使用（不需要关键词筛选）。"""
    sql = "SELECT * FROM industry_info WHERE 1=1"
    params: list[Any] = []
    if date_from:
        sql += " AND date >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND date <= ?"
        params.append(date_to)
    sql += " ORDER BY date DESC LIMIT ?"
    params.append(limit)
    return _rows_to_list(conn.execute(sql, params).fetchall())


def search_industry_info(conn: sqlite3.Connection, keyword: str,
                         date_from: str | None = None,
                         date_to: str | None = None,
                         limit: int | None = None) -> list[dict]:
    like_pat = f"%{keyword}%"
    sql = """
        SELECT i.* FROM industry_info i
        WHERE (i.sector_name LIKE ? OR i.content LIKE ? OR i.tags LIKE ?)
    """
    params: list[Any] = [like_pat] * 3
    if date_from:
        sql += " AND i.date >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND i.date <= ?"
        params.append(date_to)
    sql += " ORDER BY i.date DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return _rows_to_list(conn.execute(sql, params).fetchall())


def search_macro_info(conn: sqlite3.Connection, keyword: str,
                      date_from: str | None = None,
                      date_to: str | None = None) -> list[dict]:
    like_pat = f"%{keyword}%"
    sql = """
        SELECT m.* FROM macro_info m
        WHERE (m.title LIKE ? OR m.content LIKE ? OR m.tags LIKE ?)
    """
    params: list[Any] = [like_pat] * 3
    if date_from:
        sql += " AND m.date >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND m.date <= ?"
        params.append(date_to)
    sql += " ORDER BY m.date DESC"
    return _rows_to_list(conn.execute(sql, params).fetchall())


# ──────────────────────────────────────────────────────────────
# Daily Market
# ──────────────────────────────────────────────────────────────

def upsert_daily_market(conn: sqlite3.Connection, data: dict) -> None:
    """插入或替换每日行情数据。"""
    cols = [
        "date", "sh_index_close", "sh_index_change_pct",
        "sz_index_close", "sz_index_change_pct", "total_amount",
        "advance_count", "decline_count",
        "sh_above_ma5w", "sz_above_ma5w", "chinext_above_ma5w",
        "star50_above_ma5w", "avg_price_above_ma5w",
        "limit_up_count", "limit_down_count", "seal_rate", "broken_rate",
        "highest_board", "continuous_board_counts",
        "premium_10cm", "premium_20cm", "premium_30cm", "premium_second_board",
        "northbound_net", "margin_balance",
        "market_breadth", "raw_data",
        "node_signals", "top_volume_stocks", "etf_flow", "hk_indices",
    ]
    vals = []
    for c in cols:
        v = data.get(c)
        if isinstance(v, (list, dict)):
            v = json.dumps(v, ensure_ascii=False)
        vals.append(v)
    placeholders = ", ".join("?" * len(cols))
    conn.execute(
        f"INSERT OR REPLACE INTO daily_market ({', '.join(cols)}) VALUES ({placeholders})",
        vals,
    )


def get_daily_market(conn: sqlite3.Connection, target_date: str) -> dict | None:
    return _row_to_dict(
        conn.execute("SELECT * FROM daily_market WHERE date = ?", (target_date,)).fetchone()
    )


def get_daily_market_range(conn: sqlite3.Connection, date_from: str,
                           date_to: str) -> list[dict]:
    return _rows_to_list(
        conn.execute(
            "SELECT * FROM daily_market WHERE date >= ? AND date <= ? ORDER BY date",
            (date_from, date_to),
        ).fetchall()
    )


def update_premium(conn: sqlite3.Connection, target_date: str,
                   premium_10cm: float | None = None,
                   premium_20cm: float | None = None,
                   premium_30cm: float | None = None,
                   premium_second_board: float | None = None) -> None:
    """T+1 回填溢价率。"""
    sets, vals = [], []
    if premium_10cm is not None:
        sets.append("premium_10cm = ?")
        vals.append(premium_10cm)
    if premium_20cm is not None:
        sets.append("premium_20cm = ?")
        vals.append(premium_20cm)
    if premium_30cm is not None:
        sets.append("premium_30cm = ?")
        vals.append(premium_30cm)
    if premium_second_board is not None:
        sets.append("premium_second_board = ?")
        vals.append(premium_second_board)
    if not sets:
        return
    vals.append(target_date)
    conn.execute(f"UPDATE daily_market SET {', '.join(sets)} WHERE date = ?", vals)


def get_prev_daily_market(conn: sqlite3.Connection, target_date: str) -> dict | None:
    """获取前一交易日的行情（DB 中 date < target_date 的最近一条）。"""
    return _row_to_dict(
        conn.execute(
            "SELECT * FROM daily_market WHERE date < ? ORDER BY date DESC LIMIT 1",
            (target_date,),
        ).fetchone()
    )


def get_avg_amount(conn: sqlite3.Connection, target_date: str, days: int = 5) -> float | None:
    """获取 target_date 之前 N 个交易日的平均成交额。"""
    row = conn.execute(
        "SELECT AVG(total_amount) FROM "
        "(SELECT total_amount FROM daily_market WHERE date < ? "
        "ORDER BY date DESC LIMIT ?)",
        (target_date, days),
    ).fetchone()
    return row[0] if row else None


def get_daily_market_history(conn: sqlite3.Connection, days: int = 20) -> list[dict]:
    """获取近 N 日 daily_market（不含 raw_data），供趋势图使用。"""
    rows = conn.execute(
        "SELECT date, sh_index_close, sh_index_change_pct, "
        "sz_index_close, sz_index_change_pct, total_amount, "
        "advance_count, decline_count, limit_up_count, limit_down_count, "
        "seal_rate, broken_rate, highest_board, "
        "premium_10cm, premium_20cm, premium_30cm, premium_second_board, "
        "northbound_net "
        "FROM daily_market ORDER BY date DESC LIMIT ?",
        (days,),
    ).fetchall()
    return _rows_to_list(rows)


def get_style_factors_series(conn: sqlite3.Connection, metrics: list[str],
                             date_from: str, date_to: str) -> list[dict]:
    """获取风格化因子时间序列。"""
    allowed = {"premium_10cm", "premium_20cm", "premium_30cm", "premium_second_board",
               "seal_rate", "broken_rate", "limit_up_count", "limit_down_count",
               "highest_board", "total_amount", "northbound_net"}
    safe_cols = [m for m in metrics if m in allowed]
    if not safe_cols:
        return []
    cols_str = ", ".join(safe_cols)
    sql = f"SELECT date, {cols_str} FROM daily_market WHERE date >= ? AND date <= ? ORDER BY date"
    return _rows_to_list(conn.execute(sql, (date_from, date_to)).fetchall())


# ──────────────────────────────────────────────────────────────
# Daily Reviews
# ──────────────────────────────────────────────────────────────

def upsert_daily_review(conn: sqlite3.Connection, target_date: str,
                        data: dict) -> None:
    cols = ["date"]
    vals: list[Any] = [target_date]
    for k in ("market", "step1_market", "step2_sectors", "step3_emotion",
              "step4_style", "step5_leaders", "step6_nodes",
              "step7_positions", "step8_plan", "summary", "completion_status"):
        if k in data:
            cols.append(k)
            v = data[k]
            vals.append(json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v)
    placeholders = ", ".join("?" * len(cols))
    update_parts = ", ".join(f"{c} = excluded.{c}" for c in cols if c != "date")
    sql = (
        f"INSERT INTO daily_reviews ({', '.join(cols)}) VALUES ({placeholders})"
        f" ON CONFLICT(date) DO UPDATE SET {update_parts}"
    )
    conn.execute(sql, vals)


def get_daily_review(conn: sqlite3.Connection, target_date: str) -> dict | None:
    return _row_to_dict(
        conn.execute("SELECT * FROM daily_reviews WHERE date = ?", (target_date,)).fetchone()
    )


def get_prev_daily_review(conn: sqlite3.Connection, target_date: str) -> dict | None:
    """获取前一交易日的复盘（DB 中 date < target_date 的最近一条）。"""
    return _row_to_dict(
        conn.execute(
            "SELECT * FROM daily_reviews WHERE date < ? ORDER BY date DESC LIMIT 1",
            (target_date,),
        ).fetchone()
    )


def extract_review_conclusion_lines(review_row: dict | None, max_lines: int = 2) -> list[str]:
    """从 daily_reviews 行提取 1～2 行结论文案，供盘前简报「昨日复盘要点」。"""
    if not review_row:
        return []
    out: list[str] = []

    def _from_summary_dict(summ: Any) -> None:
        if not isinstance(summ, dict) or len(out) >= max_lines:
            return
        one = str(summ.get("one_sentence") or "").strip()
        tri = str(summ.get("trinity") or "").strip()
        if one:
            out.append(one[:240])
        if tri and len(out) < max_lines:
            out.append(tri[:240])

    def _from_obj(obj: Any) -> None:
        if not isinstance(obj, dict) or len(out) >= max_lines:
            return
        summ = obj.get("summary")
        if isinstance(summ, dict):
            _from_summary_dict(summ)
        elif isinstance(summ, str) and summ.strip() and len(out) < max_lines:
            out.append(summ.strip()[:240])

    for key in ("step8_plan", "summary"):
        if len(out) >= max_lines:
            break
        raw = review_row.get(key)
        if raw is None:
            continue
        if isinstance(raw, str):
            s = raw.strip()
            if not s:
                continue
            try:
                obj = json.loads(s)
            except json.JSONDecodeError:
                out.append(s[:240])
                continue
            _from_obj(obj)
        elif isinstance(raw, dict):
            _from_obj(raw)

    return out[:max_lines]


# ──────────────────────────────────────────────────────────────
# Emotion Cycle / Main Themes
# ──────────────────────────────────────────────────────────────

def upsert_emotion_cycle(conn: sqlite3.Connection, data: dict) -> None:
    cols, vals = [], []
    for k in ("date", "phase", "sub_cycle", "started_date", "days_in_phase",
              "strength_trend", "confidence", "sentiment_leaders",
              "profit_loss_effect", "indicators_snapshot", "note"):
        if k in data and data[k] is not None:
            cols.append(k)
            v = data[k]
            vals.append(json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v)
    placeholders = ", ".join("?" * len(cols))
    update_parts = ", ".join(f"{c} = excluded.{c}" for c in cols if c != "date")
    sql = (
        f"INSERT INTO emotion_cycle ({', '.join(cols)}) VALUES ({placeholders})"
        f" ON CONFLICT(date) DO UPDATE SET {update_parts}"
    )
    conn.execute(sql, vals)


def get_latest_emotion(conn: sqlite3.Connection) -> dict | None:
    return _row_to_dict(
        conn.execute("SELECT * FROM emotion_cycle ORDER BY date DESC LIMIT 1").fetchone()
    )


def upsert_main_theme(conn: sqlite3.Connection, data: dict) -> None:
    cols, vals = [], []
    for k in ("date", "theme_name", "status", "phase", "started_date",
              "duration_days", "vs_index", "incremental_or_stock",
              "key_stocks", "continuation_signals", "risk_signals", "note"):
        if k in data and data[k] is not None:
            cols.append(k)
            v = data[k]
            vals.append(json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v)
    placeholders = ", ".join("?" * len(cols))
    update_parts = ", ".join(f"{c} = excluded.{c}" for c in cols if c not in ("date", "theme_name"))
    sql = (
        f"INSERT INTO main_themes ({', '.join(cols)}) VALUES ({placeholders})"
        f" ON CONFLICT(date, theme_name) DO UPDATE SET {update_parts}"
    )
    conn.execute(sql, vals)


def get_active_themes(conn: sqlite3.Connection) -> list[dict]:
    return _rows_to_list(
        conn.execute(
            "SELECT * FROM main_themes WHERE status = 'active' ORDER BY date DESC"
        ).fetchall()
    )


# ──────────────────────────────────────────────────────────────
# Trades
# ──────────────────────────────────────────────────────────────

def insert_trade(conn: sqlite3.Connection, **kwargs: Any) -> int:
    cols, vals = [], []
    for k in ("date", "stock_code", "stock_name", "market", "direction",
              "time", "price", "shares", "amount", "market_context",
              "sector", "sector_node", "stock_role", "stock_attribute",
              "leader_type", "entry_mode", "entry_reason",
              "exit_reason", "holding_days", "pnl_pct", "pnl_amount",
              "was_correct", "lesson", "trinity_alignment"):
        if k in kwargs and kwargs[k] is not None:
            cols.append(k)
            vals.append(kwargs[k])
    placeholders = ", ".join("?" * len(cols))
    cur = conn.execute(
        f"INSERT INTO trades ({', '.join(cols)}) VALUES ({placeholders})", vals,
    )
    return cur.lastrowid  # type: ignore[return-value]


def get_trades(conn: sqlite3.Connection, date_from: str | None = None,
               date_to: str | None = None,
               stock_code: str | None = None) -> list[dict]:
    sql = "SELECT * FROM trades WHERE 1=1"
    params: list[Any] = []
    if date_from:
        sql += " AND date >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND date <= ?"
        params.append(date_to)
    if stock_code:
        sql += " AND stock_code = ?"
        params.append(stock_code)
    sql += " ORDER BY date DESC, time DESC"
    return _rows_to_list(conn.execute(sql, params).fetchall())


# ──────────────────────────────────────────────────────────────
# Unified Search (跨实体聚合)
# ──────────────────────────────────────────────────────────────

def unified_search(conn: sqlite3.Connection, keyword: str,
                   types: list[str] | None = None,
                   date_from: str | None = None,
                   date_to: str | None = None) -> dict[str, list[dict]]:
    """跨实体搜索，返回按来源分组的结果。"""
    all_types = {"teacher_notes", "industry_info", "macro_info"}
    search_types = set(types) & all_types if types else all_types
    results: dict[str, list[dict]] = {}

    if "teacher_notes" in search_types:
        results["teacher_notes"] = search_teacher_notes(
            conn, keyword, date_from=date_from, date_to=date_to
        )
    if "industry_info" in search_types:
        results["industry_info"] = search_industry_info(
            conn, keyword, date_from=date_from, date_to=date_to
        )
    if "macro_info" in search_types:
        results["macro_info"] = search_macro_info(
            conn, keyword, date_from=date_from, date_to=date_to
        )

    return results


def stock_mentions(conn: sqlite3.Connection, stock_code: str) -> dict:
    """按股票代码聚合：关注池 + 持仓 + 笔记中出现的记录。"""
    holdings = _rows_to_list(
        conn.execute("SELECT * FROM holdings WHERE stock_code = ?", (stock_code,)).fetchall()
    )
    watchlist_items = _rows_to_list(
        conn.execute("SELECT * FROM watchlist WHERE stock_code = ?", (stock_code,)).fetchall()
    )
    notes = _rows_to_list(
        conn.execute(
            "SELECT n.*, t.name as teacher_name FROM teacher_notes n JOIN teachers t ON n.teacher_id = t.id "
            "WHERE n.raw_content LIKE ? OR n.sectors LIKE ?",
            (f"%{stock_code}%", f"%{stock_code}%"),
        ).fetchall()
    )
    return {"holdings": holdings, "watchlist": watchlist_items, "notes": notes}


# ──────────────────────────────────────────────────────────────
# 通用白名单 UPDATE 工具
# ──────────────────────────────────────────────────────────────

def _safe_update(conn: sqlite3.Connection, table: str, pk_col: str,
                 pk_val: Any, allowed: frozenset[str], **kwargs: Any) -> None:
    sets, vals = [], []
    for k, v in kwargs.items():
        if k not in allowed:
            raise ValueError(f"Invalid column for {table}: {k}")
        sets.append(f"{k} = ?")
        vals.append(v)
    if not sets:
        return
    vals.append(pk_val)
    conn.execute(f"UPDATE {table} SET {', '.join(sets)} WHERE {pk_col} = ?", vals)


_TEACHER_NOTES_UPDATABLE = frozenset({
    "date", "title", "source_type", "input_by", "core_view",
    "position_advice", "obsidian_path", "tags", "key_points",
    "sectors", "avoid", "raw_content",
})

_INDUSTRY_INFO_UPDATABLE = frozenset({
    "date", "sector_name", "info_type", "content", "source",
    "confidence", "timeliness", "tags",
})

_MACRO_INFO_UPDATABLE = frozenset({
    "date", "category", "title", "content", "source",
    "impact_assessment", "confidence", "tags",
})

_CALENDAR_UPDATABLE = frozenset({
    "date", "time", "event", "impact", "category", "source",
    "country", "prior", "expected", "actual", "note",
})

_TRADES_UPDATABLE = frozenset({
    "date", "stock_code", "stock_name", "market", "direction", "time",
    "price", "shares", "amount", "market_context", "sector",
    "sector_node", "stock_role", "stock_attribute", "leader_type",
    "entry_mode", "entry_reason", "exit_reason", "holding_days",
    "pnl_pct", "pnl_amount", "was_correct", "lesson", "trinity_alignment",
})


def update_teacher_note(conn: sqlite3.Connection, note_id: int, **kw: Any) -> None:
    _safe_update(conn, "teacher_notes", "id", note_id, _TEACHER_NOTES_UPDATABLE, **kw)


def update_industry_info(conn: sqlite3.Connection, info_id: int, **kw: Any) -> None:
    _safe_update(conn, "industry_info", "id", info_id, _INDUSTRY_INFO_UPDATABLE, **kw)


def update_macro_info(conn: sqlite3.Connection, info_id: int, **kw: Any) -> None:
    _safe_update(conn, "macro_info", "id", info_id, _MACRO_INFO_UPDATABLE, **kw)


def update_calendar_event(conn: sqlite3.Connection, event_id: int, **kw: Any) -> None:
    _safe_update(conn, "calendar_events", "id", event_id, _CALENDAR_UPDATABLE, **kw)


def update_trade(conn: sqlite3.Connection, trade_id: int, **kw: Any) -> None:
    _safe_update(conn, "trades", "id", trade_id, _TRADES_UPDATABLE, **kw)
