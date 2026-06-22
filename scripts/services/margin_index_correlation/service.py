"""两融余额与指数联动性 编排层。

run_daily：采集 → 落库（persist 时）→ 渲染，返回 markdown（无数据返 None）。
run_signals：只读最近 N 日快照渲染（不采集不推送，供周日复盘）。
默认参数集中在此层；CLI 可经 overrides 覆盖。
"""
from __future__ import annotations

import sqlite3

from . import collector, formatter, repo

# 默认对照配置（满足「上证为主 + 多宽基 + 沪深各自对照」）
WINDOWS = [5, 20, 60]
BASE_INDEX = "000001.SH"
# broad：三市合计两融 vs 各宽基指数
BROAD_INDICES = [
    ("000001.SH", "上证指数"),
    ("399006.SZ", "创业板指"),
    ("000300.SH", "沪深300"),
    ("000688.SH", "科创50"),
]
# cross：沪市两融 vs 上证、深市两融 vs 深成
CROSS_PAIRS = [
    ("sse", "沪市两融", "000001.SH", "上证指数"),
    ("szse", "深市两融", "399001.SZ", "深证成指"),
]
DIVERGENCE_WINDOWS = [5, 20]
MIN_GAP = 0.5
MAX_LAG = 3
MIN_SAMPLE_BY_WINDOW = {5: 5, 20: 15, 60: 20}
LAG_MIN_SAMPLE = 10


def run_daily(
    conn: sqlite3.Connection,
    registry,
    provider,
    date: str,
    *,
    persist: bool = True,
    windows: list[int] | None = None,
    divergence_windows: list[int] | None = None,
    min_gap: float | None = None,
    max_lag: int | None = None,
) -> str | None:
    """采集→（落库）→渲染。返回 markdown；无足够数据返 None（上层不推送）。

    collector 的指数序列复用 sector `fetch_index_series`，它需要**原始 tushare pro_api**
    对象（有 `.index_daily`），故此处解包 `provider.pro`（同 sector_correlation）——
    传 TushareProvider 包装类会 AttributeError: no attribute 'index_daily'（真机暴露）。
    """
    windows = windows or WINDOWS
    record = collector.build_record(
        registry, provider.pro, date=date,
        windows=windows,
        broad_indices=BROAD_INDICES,
        cross_pairs=CROSS_PAIRS,
        base_index=BASE_INDEX,
        divergence_windows=divergence_windows or DIVERGENCE_WINDOWS,
        min_gap=MIN_GAP if min_gap is None else min_gap,
        max_lag=MAX_LAG if max_lag is None else max_lag,
        min_sample_by_window={w: MIN_SAMPLE_BY_WINDOW.get(w, max(5, w // 3)) for w in windows},
        lag_min_sample=LAG_MIN_SAMPLE,
    )
    if record is None:
        return None
    if persist:
        repo.save(conn, record)
    return formatter.format_daily_report(record)


def run_signals(conn: sqlite3.Connection, *, end_date: str, days: int) -> str:
    """只读最近 N 日联动快照摘要（不采集不推送）。"""
    records = repo.get_recent(conn, end_date, days)
    return formatter.format_signals(records)
