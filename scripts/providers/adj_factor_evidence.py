"""仅在单股区间复权因子冲突时，用同一事实日的独立查询口径核验。"""
from __future__ import annotations

from contextlib import closing
from datetime import date, datetime
import math
import sqlite3
from threading import Lock
from time import monotonic
from zoneinfo import ZoneInfo

from db.connection import get_readonly_connection
from services.monthly_pattern import repository


def positive_factor(value):
    if isinstance(value, bool) or value is None:
        raise ValueError("复权因子缺失或非法")
    factor = float(value)
    if not math.isfinite(factor) or factor <= 0:
        raise ValueError("复权因子缺失或非法")
    return factor


def archived_factors(day, as_of, db_path=None):
    """仅用原生目标日因子，月线和收据都必须在请求末日收盘后当晚已可见。"""
    try:
        with closing(get_readonly_connection(db_path)) as conn:
            conn.execute("BEGIN")
            if day not in repository.existing_month_ends(conn, [day]):
                return {}
            # 两张表的 fetched_at 均为 SQLite UTC；按上海自然日检查可见性。
            rows = conn.execute("""
                SELECT b.stock_code, b.adj_factor
                FROM monthly_pattern_bars b
                JOIN monthly_pattern_bar_manifests m ON m.month_end = b.month_end
                WHERE b.month_end = ? AND b.source = 'tushare:monthly+adj_factor'
                  AND json_extract(m.source_meta_json, '$.factor_source') = 'tushare:adj_factor'
                  AND date(m.fetched_at, '+8 hours') BETWEEN b.month_end AND ?
                  AND b.fetched_at = m.fetched_at
                  AND NOT EXISTS (
                      SELECT 1 FROM json_each(m.source_meta_json, '$.code_alias_normalizations') a
                      WHERE b.stock_code IN (json_extract(a.value, '$.alias_code'),
                                             json_extract(a.value, '$.canonical_code'))
                  )
            """, (day, as_of))
            return {row["stock_code"]: positive_factor(row["adj_factor"]) for row in rows}
    except (sqlite3.Error, ValueError, TypeError, OverflowError):
        return {}


class FactorDayEvidence:
    """同一 provider 的并发扫描共享核验结果；坏源与健康快照均有时限。"""

    def __init__(self):
        self.lock = Lock()
        self.days = {}

    def for_stock(self, provider, day, as_of, code):
        as_of = date.fromisoformat(as_of).isoformat()
        if provider._normalize_stock_concept_code(code) != code:
            raise ValueError("冲突证券身份无法核验")
        with self.lock:
            now = monotonic()
            self.days = {k: v for k, v in self.days.items() if v["until"] > now}
            key = (day, as_of)
            if key not in self.days:
                if len(self.days) >= 8:
                    raise ValueError("冲突日期已达批量核验上限")
                self.days[key] = dict(until=now + 1800, local=archived_factors(day, as_of))
            evidence = self.days[key]
            archived = evidence["local"].get(code[:6])
            if archived is not None:
                return archived, "monthly_pattern_bars/certified"
            if as_of != datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat():
                raise ValueError("历史扫描日当时没有可见的同日因子证据")
            if "batch" not in evidence:
                result = provider.get_adj_factor(day)
                # get_adj_factor 已逐页核对日期、唯一性与正因子，直到空页。
                evidence["batch"] = {
                    row["ts_code"]: positive_factor(row["adj_factor"]) for row in result.data
                } if result.success and result.source == "tushare:adj_factor" else {}
            if code not in evidence["batch"]:
                raise ValueError("同事实日无可核验的唯一因子")
            return evidence["batch"][code], "tushare:adj_factor/trade_date"
