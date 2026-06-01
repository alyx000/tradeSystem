"""板块相关性 编排层（只编排，不实现）。

run_daily：采集→落库→渲染 daily markdown（返回 None 表无数据，调用方据此不推送）。
run_matrix：读当天（无则现采）→渲染完整矩阵。run_trend：只读最近 N 天→漂移摘要。
"""
from __future__ import annotations

import logging
import sqlite3

from . import collector, formatter, repo

logger = logging.getLogger(__name__)

# 默认参数（CLI 可覆盖）
WINDOWS = [5, 20, 60]  # 5=近期共振 / 20=中期 / 60=结构
TOP_INDUSTRIES = 15
TOP_CONCEPTS = 10
ACTIVITY_DAYS = 10
INDICES = ["000001.SH", "399006.SZ", "000300.SH", "000688.SH"]
BASE_INDEX = "000001.SH"
MIN_SAMPLE_BY_WINDOW = {5: 5, 20: 15, 60: 20}


def _build(provider, date, *, windows, top_industries, top_concepts,
           indices, activity_days, include_concept):
    """从 provider 取 pro / 申万二级码 / 概念映射，调 collector.build_record。"""
    return collector.build_record(
        provider.pro,
        date=date,
        windows=windows,
        top_industries=top_industries,
        top_concepts=top_concepts,
        indices=indices,
        base_index=BASE_INDEX,
        activity_days=activity_days,
        l2_codes=provider._ensure_sw_l2_codes() or set(),
        concept_map=provider._ensure_ths_concept_map() or {},
        # min_sample 不得超过窗口本身，否则自定义小窗(如 [5])会被全剔成空(review M3)
        min_sample_by_window={w: min(MIN_SAMPLE_BY_WINDOW.get(w, 15), w) for w in windows},
        include_concept=include_concept,
    )


def run_daily(
    conn: sqlite3.Connection,
    provider,
    date: str,
    *,
    windows=None,
    top_industries: int = TOP_INDUSTRIES,
    top_concepts: int = TOP_CONCEPTS,
    indices=None,
    activity_days: int = ACTIVITY_DAYS,
    include_concept: bool = True,
    persist: bool = True,
) -> str | None:
    """采集 + 落库 + 渲染。无数据返 None（调用方不推送）。"""
    record = _build(
        provider, date,
        windows=windows or WINDOWS,
        top_industries=top_industries, top_concepts=top_concepts,
        indices=indices or INDICES, activity_days=activity_days,
        include_concept=include_concept,
    )
    if record is None:
        return None
    if persist:
        repo.save_correlation(conn, record)
    return formatter.format_daily_report(record)


def run_matrix(
    conn: sqlite3.Connection,
    provider,
    date: str,
    *,
    windows=None,
    top_industries: int = TOP_INDUSTRIES,
    top_concepts: int = TOP_CONCEPTS,
    indices=None,
    activity_days: int = ACTIVITY_DAYS,
    include_concept: bool = True,
    refetch: bool = False,
) -> str:
    """读当天快照（无 / refetch 则现采，不落库）→ 渲染完整矩阵。

    matrix 是只读巡检入口，**刻意不落库**：daily 是 sector_correlation_daily 的唯一写入口，
    避免两个命令竞写同 date 行（review H4）。refetch=True 仅忽略库内缓存、强制现算。
    """
    record = None if refetch else repo.get_correlation(conn, date)
    if record is None:
        record = _build(
            provider, date,
            windows=windows or WINDOWS,
            top_industries=top_industries, top_concepts=top_concepts,
            indices=indices or INDICES, activity_days=activity_days,
            include_concept=include_concept,
        )
    if record is None:
        return f"{date} 无板块相关性数据。"
    return formatter.format_matrix(record)


def run_trend(conn: sqlite3.Connection, date: str, days: int = 30) -> str:
    """只读最近 N 天快照 → 漂移摘要（不采集不推送）。"""
    return formatter.format_trend(repo.get_recent_correlation(conn, date, days))
