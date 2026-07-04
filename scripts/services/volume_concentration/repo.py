"""daily_volume_concentration 表的读写 — JSON 列序列化/反序列化在此封装。

stocks / sector_summary / source 以 python 对象进出,JSON 编解码是存储细节,
不外泄给调用方(collector / trend / formatter 直接拿 list/dict)。
"""
from __future__ import annotations

import json
import sqlite3

from services.volume_concentration.aggregator import UNCLASSIFIED


def save_concentration(conn: sqlite3.Connection, record: dict) -> None:
    """写入/覆盖某交易日的集中度快照(UPSERT,幂等)。

    重跑同 date:刷新所有字段 + updated_at,但**保留首次 created_at**——用
    ON CONFLICT DO UPDATE 而非 INSERT OR REPLACE(后者=删+插,会把 created_at 重置为
    当前时间,破坏 dec-3「created_at 首次 / updated_at 末次」审计语义)。
    record 键:date / total_amount_billion(必填)/ top_n / market_total_billion(可空)
    / stocks(list) / sector_summary(list) / source(dict,可空)。
    """
    if not record.get("date"):
        raise ValueError("save_concentration: 缺少必填字段 date")
    if record.get("total_amount_billion") is None:
        raise ValueError("save_concentration: 缺少必填字段 total_amount_billion")
    conn.execute(
        """
        INSERT INTO daily_volume_concentration (
            date, top_n, total_amount_billion, market_total_billion,
            stocks_json, sector_summary_json, source_json, gain_universe_json, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(date) DO UPDATE SET
            top_n = excluded.top_n,
            total_amount_billion = excluded.total_amount_billion,
            market_total_billion = excluded.market_total_billion,
            stocks_json = excluded.stocks_json,
            sector_summary_json = excluded.sector_summary_json,
            source_json = excluded.source_json,
            gain_universe_json = excluded.gain_universe_json,
            updated_at = excluded.updated_at
        """,
        (
            record["date"],
            int(record.get("top_n", 20)),
            record["total_amount_billion"],
            record.get("market_total_billion"),
            json.dumps(record.get("stocks", []), ensure_ascii=False),
            json.dumps(record.get("sector_summary", []), ensure_ascii=False),
            json.dumps(record["source"], ensure_ascii=False)
            if record.get("source") is not None
            else None,
            json.dumps(record["gain_universe"], ensure_ascii=False)
            if record.get("gain_universe") is not None
            else None,
        ),
    )
    conn.commit()


def _row_to_record(row: sqlite3.Row) -> dict:
    # gain_universe_json 为 v34 增列;老库经迁移 ALTER 补齐,缺列时(异常半迁移态)安全退 []。
    has_gain = "gain_universe_json" in row.keys()
    gain_raw = row["gain_universe_json"] if has_gain else None
    return {
        "date": row["date"],
        "top_n": row["top_n"],
        "total_amount_billion": row["total_amount_billion"],
        "market_total_billion": row["market_total_billion"],
        "stocks": json.loads(row["stocks_json"]) if row["stocks_json"] else [],
        "sector_summary": json.loads(row["sector_summary_json"]) if row["sector_summary_json"] else [],
        "source": json.loads(row["source_json"]) if row["source_json"] else None,
        "gain_universe": json.loads(gain_raw) if gain_raw else [],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get_concentration(conn: sqlite3.Connection, date: str) -> dict | None:
    """读某交易日的集中度快照;无则 None。"""
    row = conn.execute(
        "SELECT * FROM daily_volume_concentration WHERE date = ?", (date,)
    ).fetchone()
    return _row_to_record(row) if row else None


def get_recent_concentration(
    conn: sqlite3.Connection, end_date: str, days: int
) -> list[dict]:
    """取 <= end_date 的最近 days 天快照,**按日期正序**返回(供 trend dense series)。

    SQL 用 ORDER BY date DESC LIMIT 取最近 N 条,再 reverse 成时间正序。
    """
    rows = conn.execute(
        "SELECT * FROM daily_volume_concentration WHERE date <= ? ORDER BY date DESC LIMIT ?",
        (end_date, days),
    ).fetchall()
    return [_row_to_record(r) for r in reversed(rows)]


def get_latest_concentration(conn: sqlite3.Connection, days: int) -> list[dict]:
    """取库内**最新 N 天**快照,**按日期正序**返回(供趋势图,不依赖服务端 today/非交易日)。

    镜像 queries.get_daily_market_history:ORDER BY date DESC LIMIT 取最近 N 条,再 reverse。
    """
    rows = conn.execute(
        "SELECT * FROM daily_volume_concentration ORDER BY date DESC LIMIT ?",
        (days,),
    ).fetchall()
    return [_row_to_record(r) for r in reversed(rows)]


def get_main_sectors(conn: sqlite3.Connection, date: str, top_k: int) -> tuple[set, bool]:
    """主线板块 = 当日成交额集中度 Top-K 申万二级；当日缺失回退最近一日。

    原为 board_break / trend_leader 各自内联的 `_main_sectors`（口径完全一致）；
    此处下沉为公共 helper。trend_leader._main_sectors 暂保留独立实现（禁区，
    留作 tech-debt，不在本次改动范围内回收）。
    """
    rec = get_concentration(conn, date)
    degraded = False
    if rec is None:
        degraded = True
        recent = get_recent_concentration(conn, date, 1)
        rec = recent[-1] if recent else None
    auto = []
    if rec and rec.get("sector_summary"):
        auto = [s["industry"] for s in rec["sector_summary"] if s.get("industry") != UNCLASSIFIED][:top_k]
    return set(auto), degraded
