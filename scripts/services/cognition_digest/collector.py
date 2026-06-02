"""只读聚合：窗口内认知活跃度 + 概览统计。

只读连接（SQLite mode=ro，URI）：**不调 migrate、不 commit**，保证生产路径对真实
data/trade.db 严格只读（codex 严重 1 修订）。db_path=None → 默认库。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from db.connection import _DEFAULT_DB_PATH


@dataclass
class CognitionActivity:
    cognition_id: str
    title: str
    category: str
    sub_category: str | None
    pattern: str | None
    confidence: float
    status: str
    created_at: str
    instances: list[dict] = field(default_factory=list)  # {observed_date, teacher_id, teacher_name}


@dataclass
class WindowData:
    activities: list[CognitionActivity]
    total_instances: int       # 只数 activities（非弃用认知）的窗口实例，与"活跃认知"口径一致
    teacher_names: list[str]


def _ro_connect(db_path: str | None) -> sqlite3.Connection:
    """只读连接（mode=ro）。生产路径绝不写库、不 migrate；DB 必须已存在。"""
    path = str(db_path or _DEFAULT_DB_PATH)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def collect(db_path: str | None, start: str, end: str) -> WindowData:
    """聚合 [start, end] 闭区间内的认知活跃度（只读）。"""
    conn = _ro_connect(db_path)
    try:
        inst_rows = conn.execute(
            """
            SELECT cognition_id, observed_date, teacher_id, teacher_name_snapshot
            FROM cognition_instances
            WHERE observed_date >= ? AND observed_date <= ?
            """,
            (start, end),
        ).fetchall()
        new_rows = conn.execute(
            """
            SELECT cognition_id FROM trading_cognitions
            WHERE date(created_at) >= ? AND date(created_at) <= ?
              AND status != 'deprecated'
            """,
            (start, end),
        ).fetchall()

        by_cog: dict[str, list[dict]] = {}
        for r in inst_rows:
            by_cog.setdefault(r["cognition_id"], []).append(
                {
                    "observed_date": r["observed_date"],
                    "teacher_id": r["teacher_id"],
                    "teacher_name": r["teacher_name_snapshot"],
                }
            )

        cand_ids = set(by_cog) | {r["cognition_id"] for r in new_rows}
        if not cand_ids:
            return WindowData([], 0, [])

        placeholders = ",".join("?" * len(cand_ids))
        meta_rows = conn.execute(
            f"""
            SELECT cognition_id, title, category, sub_category, pattern,
                   confidence, status, created_at
            FROM trading_cognitions
            WHERE cognition_id IN ({placeholders})
              AND status != 'deprecated'
            """,
            tuple(cand_ids),
        ).fetchall()
    finally:
        conn.close()

    activities = [
        CognitionActivity(
            cognition_id=m["cognition_id"],
            title=m["title"],
            category=m["category"],
            sub_category=m["sub_category"],
            pattern=m["pattern"],
            confidence=float(m["confidence"]),
            status=m["status"],
            created_at=m["created_at"],
            instances=by_cog.get(m["cognition_id"], []),
        )
        for m in meta_rows
    ]
    # 概览口径：只统计非弃用（activities）认知的窗口实例 + 这些实例覆盖的老师（与 active 口径一致）
    total_instances = sum(len(a.instances) for a in activities)
    teacher_names = sorted(
        {it["teacher_name"] for a in activities for it in a.instances if it["teacher_name"]}
    )
    return WindowData(activities, total_instances, teacher_names)
