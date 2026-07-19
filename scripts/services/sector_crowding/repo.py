"""sector_crowding_daily 读写 — JSON 编解码封装在此，UPSERT 幂等、保留首次 created_at。"""
from __future__ import annotations

import json
import sqlite3


def _dump_opt(value) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False)


def save_snapshot(conn: sqlite3.Connection, record: dict) -> None:
    if record.get("date") is None or record.get("sectors") is None:
        raise ValueError("save_snapshot: 缺少必填字段 date/sectors")
    sectors = record["sectors"]
    # 结构校验(codex 门2 高):空 sectors=数据源全失败,落库会伪装成"正常无双高";
    # malformed 元素会在读取侧 _dedup_sectors 的 .get 上炸。身份字段只强制 code/level
    # (回填行 close/share_pct 合法可缺,不做字段级全量校验)。
    if not isinstance(sectors, list) or not sectors:
        raise ValueError("save_snapshot: sectors 必须为非空 list(数据源失败请勿落库)")
    for s in sectors:
        if not isinstance(s, dict) or not s.get("code") or not s.get("level"):
            raise ValueError(f"save_snapshot: sectors 元素缺 code/level: {s!r:.80}")
    conn.execute(
        """
        INSERT INTO sector_crowding_daily (
            date, market_total_billion, sectors_json, proxy_json, meta_json, updated_at
        ) VALUES (?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(date) DO UPDATE SET
            market_total_billion = excluded.market_total_billion,
            sectors_json = excluded.sectors_json,
            proxy_json = excluded.proxy_json,
            meta_json = excluded.meta_json,
            updated_at = excluded.updated_at
        """,
        (
            record["date"],
            record.get("market_total_billion"),
            json.dumps(record["sectors"], ensure_ascii=False),  # 必填列,入口已挡 None
            _dump_opt(record.get("proxy")),
            _dump_opt(record.get("meta")),
        ),
    )
    conn.commit()


def _row_to_record(row: sqlite3.Row) -> dict:
    def _j(col):
        return json.loads(row[col]) if col in row.keys() and row[col] else None

    return {
        "date": row["date"],
        "market_total_billion": row["market_total_billion"],
        "sectors": _j("sectors_json") or [],  # 恒 list 契约:防手工/遗留空串行击穿迭代方
        "proxy": _j("proxy_json"),
        "meta": _j("meta_json"),
        "created_at": row["created_at"] if "created_at" in row.keys() else None,
        "updated_at": row["updated_at"] if "updated_at" in row.keys() else None,
    }


def get_snapshot(conn: sqlite3.Connection, date: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM sector_crowding_daily WHERE date = ?", (date,)
    ).fetchone()
    return _row_to_record(row) if row else None


def get_recent(conn: sqlite3.Connection, end_date: str, days: int) -> list[dict]:
    """取 <= end_date 的最近 days 行快照，按日期升序（供分位现算/trend）。

    精简列读取：历史行的 proxy_json/meta_json 不参与分位计算，跳过解析
    （JSON 解码是该路径主导成本）；当日全量数据用 get_snapshot 单行取。"""
    if not isinstance(days, int) or days <= 0:
        # SQLite LIMIT 负数=不限行数:负窗口会静默退化成全表 JSON 解码(codex 门2 中)
        raise ValueError(f"get_recent: days 必须为正整数,得到 {days!r}")
    rows = conn.execute(
        """SELECT date, market_total_billion, sectors_json
           FROM sector_crowding_daily WHERE date <= ? ORDER BY date DESC LIMIT ?""",
        (end_date, days),
    ).fetchall()
    return [_row_to_record(r) for r in reversed(rows)]


def get_latest_market_total_before(conn: sqlite3.Connection, date: str) -> float | None:
    """date 之前最近一个非 NULL 两市总额（骤降告警基准，阶段B fetch_market_total 消费）。"""
    row = conn.execute(
        """SELECT market_total_billion FROM sector_crowding_daily
           WHERE date < ? AND market_total_billion IS NOT NULL
           ORDER BY date DESC LIMIT 1""",
        (date,),
    ).fetchone()
    return row["market_total_billion"] if row else None
