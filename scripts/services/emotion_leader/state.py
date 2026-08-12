"""情绪核心日报的本地增量种子读取与刷新规划。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from services.emotion_leader import constants as C


def _report_dir(root: Path | None = None) -> Path:
    repo_root = root or Path(__file__).resolve().parents[3]
    return repo_root / C.REPORT_DIR


def load_previous_report(
    target_date: str,
    *,
    lookback_days: int,
    root: Path | None = None,
) -> dict | None:
    """读取目标日前最近一份可复用日报；损坏或口径不一致时安全回退全量。"""
    out_dir = _report_dir(root)
    if not out_dir.exists():
        return None
    for path in sorted(out_dir.glob("????-??-??.json"), reverse=True):
        if path.stem >= target_date:
            continue
        try:
            payload: Any = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("date") != path.stem or payload.get("status") not in {"ok", "partial"}:
            continue
        if payload.get("lookback_days") != lookback_days:
            continue
        if not isinstance(payload.get("active"), list) or not isinstance(payload.get("archived"), list):
            continue
        return payload
    return None


def plan_incremental_refresh(
    promoted: list[dict],
    previous: dict | None,
    current_limit_up_codes: set[str],
    target_date: str,
    *,
    full_refresh: bool = False,
) -> tuple[list[dict], list[dict], dict]:
    """刷新上期活跃/本期新增，复用上期已归档行，避免重复拉取沉寂标的。"""
    if full_refresh or previous is None:
        mode = "full_refresh" if full_refresh else "full_initial"
        return list(promoted), [], {
            "mode": mode,
            "previous_report_date": None,
            "discovered_count": len(promoted),
            "metric_refresh_count": len(promoted),
            "cached_archived_count": 0,
        }

    previous_active = {
        str(row.get("code")): row
        for row in previous.get("active", [])
        if isinstance(row, dict) and row.get("code")
    }
    previous_archived = {
        str(row.get("code")): row
        for row in previous.get("archived", [])
        if isinstance(row, dict) and row.get("code")
    }
    known_codes = set(previous_active) | set(previous_archived)
    refresh: list[dict] = []
    cached_archived: list[dict] = []

    for item in promoted:
        code = str(item.get("code") or "")
        should_refresh = bool(
            code in previous_active
            or code not in known_codes
            or code in current_limit_up_codes
            or item.get("manual_confirmed")
            or item.get("promoted_date") == target_date
        )
        if should_refresh:
            refresh.append(item)
            continue

        prior = previous_archived.get(code)
        if prior is None:
            refresh.append(item)
            continue
        carried = {**prior}
        for key in (
            "name", "board_type", "launch_date", "launch_method", "candidate_date",
            "promoted_date", "last_limit_up_date", "max_height", "run_count",
            "limit_industry", "manual_confirmed", "manual_sector", "manual_phase",
            "manual_last_seen_date",
        ):
            carried[key] = item.get(key)
        carried.update({
            "archived": True,
            "metric_status": "cached_archived",
            "metric_as_of": previous.get("date"),
            "cache_note": "已归档标的复用上期指标；再次涨停或人工确认时恢复刷新",
        })
        cached_archived.append(carried)

    return refresh, cached_archived, {
        "mode": "incremental",
        "previous_report_date": previous.get("date"),
        "discovered_count": len(promoted),
        "metric_refresh_count": len(refresh),
        "cached_archived_count": len(cached_archived),
    }
