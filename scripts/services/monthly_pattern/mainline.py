"""题材月线模式使用的客观主线代理。

成交额集中度只能提供“稳定前排申万二级”证据，输出必须标为 ``[判断]``，不能
冒充市场主线事实。
"""
from __future__ import annotations


UNCLASSIFIED = "UNCLASSIFIED"


def _ranked(record: dict, top_k: int) -> list[str]:
    result: list[str] = []
    for row in (record or {}).get("sector_summary") or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("industry") or "").strip()
        if not name or name == UNCLASSIFIED or name in result:
            continue
        result.append(name)
        if len(result) >= top_k:
            break
    return result


def stable_main_sectors(
    records: list[dict],
    *,
    top_k: int,
) -> tuple[list[str], dict]:
    """最近最多三条有效快照中，2+条历史要求至少命中两次。"""
    if top_k <= 0:
        raise ValueError("top_k 必须为正整数")
    valid = [record for record in records[-3:] if _ranked(record, top_k)]
    if not valid:
        return [], {
            "status": "missing",
            "snapshot_count": 0,
            "required_hits": 0,
            "source_dates": [],
        }
    required_hits = 2 if len(valid) >= 2 else 1
    latest_ranked = _ranked(valid[-1], top_k)
    hit_counts = {
        name: sum(name in _ranked(record, top_k) for record in valid)
        for name in latest_ranked
    }
    sectors = [name for name in latest_ranked if hit_counts[name] >= required_hits]
    return sectors, {
        "status": "ok" if len(valid) >= 2 else "limited_history",
        "snapshot_count": len(valid),
        "required_hits": required_hits,
        "source_dates": [record.get("date") for record in valid],
        "hit_counts": hit_counts,
    }
