"""从只读盘后事实层重建连板启动与情绪核心候选。"""
from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from services.emotion_leader import constants as C
from utils import is_st_stock
from utils.price_limit import limit_pct_for


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _positive_int(value: Any) -> int | None:
    number = _finite_float(value)
    if number is None or number <= 0 or not number.is_integer():
        return None
    return int(number)


def normalize_code(value: Any) -> str:
    """归一为带交易所后缀的 A 股代码；非法值返回空串。"""
    text = str(value or "").strip().upper()
    if re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", text):
        return text
    if not re.fullmatch(r"\d{6}", text):
        return ""
    if text.startswith(("43", "82", "83", "87", "88", "89", "92")):
        return f"{text}.BJ"
    if text.startswith(("60", "68", "90")):
        return f"{text}.SH"
    return f"{text}.SZ"


def board_type(code: str) -> str:
    pct = limit_pct_for(code, is_st=False)
    return f"{int(pct)}cm" if pct in {10.0, 20.0, 30.0} else "未知"


def _extract_raw(payload_text: str) -> dict | None:
    try:
        payload = json.loads(payload_text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    raw = payload.get("raw_data", payload)
    return raw if isinstance(raw, dict) else None


def _clean_limit_rows(section: Any, trade_date: str) -> tuple[list[dict], list[str]]:
    if not isinstance(section, dict):
        return [], [f"{trade_date}:缺少涨停事实"]
    if section.get("error"):
        return [], [f"{trade_date}:涨停来源失败:{section.get('error')}"]
    rows = section.get("stocks")
    if not isinstance(rows, list):
        return [], [f"{trade_date}:涨停事实缺少stocks"]

    output: list[dict] = []
    dirty = 0
    for raw in rows:
        if not isinstance(raw, dict):
            dirty += 1
            continue
        code = normalize_code(raw.get("code") or raw.get("ts_code"))
        name = str(raw.get("name") or "").strip()
        height = _positive_int(raw.get("limit_times", raw.get("nums")))
        if not code or not name or height is None:
            dirty += 1
            continue
        output.append({
            "date": trade_date,
            "code": code,
            "name": name,
            "height": height,
            "industry": str(raw.get("industry") or "").strip(),
            "amount_billion": _finite_float(raw.get("amount_billion")) or 0.0,
            "pct_chg": _finite_float(raw.get("pct_chg", raw.get("change_pct"))),
            "is_st": is_st_stock(name),
        })
    errors = [f"{trade_date}:涨停事实有{dirty}条脏记录"] if dirty else []
    return output, errors


def _clean_down_codes(section: Any, trade_date: str) -> tuple[set[str], list[str]]:
    if not isinstance(section, dict):
        return set(), [f"{trade_date}:缺少跌停事实"]
    if section.get("error"):
        return set(), [f"{trade_date}:跌停来源失败:{section.get('error')}"]
    rows = section.get("stocks")
    if not isinstance(rows, list):
        return set(), [f"{trade_date}:跌停事实缺少stocks"]
    codes = {
        normalize_code(row.get("code") or row.get("ts_code"))
        for row in rows
        if isinstance(row, dict)
    }
    codes.discard("")
    return codes, []


def load_history(
    conn: sqlite3.Connection,
    target_date: str,
    *,
    lookback_days: int = C.DEFAULT_LOOKBACK_DAYS,
) -> dict:
    """从 ``daily_market.raw_data`` 只读加载逐日涨跌停事实与覆盖收据。"""
    start = (date.fromisoformat(target_date) - timedelta(days=lookback_days)).isoformat()
    db_rows = conn.execute(
        "SELECT date, raw_data FROM daily_market WHERE date BETWEEN ? AND ? ORDER BY date",
        (start, target_date),
    ).fetchall()

    snapshots: list[dict] = []
    errors: list[str] = []
    for row in db_rows:
        trade_date = str(row["date"])
        raw = _extract_raw(row["raw_data"])
        if raw is None:
            errors.append(f"{trade_date}:daily_market.raw_data不可读")
            continue
        limit_rows, limit_errors = _clean_limit_rows(raw.get("limit_up"), trade_date)
        down_codes, down_errors = _clean_down_codes(raw.get("limit_down"), trade_date)
        errors.extend(limit_errors)
        errors.extend(down_errors)
        snapshots.append({
            "date": trade_date,
            "limit_rows": limit_rows,
            "down_codes": down_codes,
            "limit_valid": not limit_errors,
            "down_valid": not down_errors,
        })

    expected_rows = conn.execute(
        "SELECT date FROM trade_calendar WHERE date BETWEEN ? AND ? AND is_open = 1 ORDER BY date",
        (start, target_date),
    ).fetchall()
    expected = [str(row["date"]) for row in expected_rows]
    loaded_dates = {item["date"] for item in snapshots if item["limit_valid"]}
    missing_dates = [d for d in expected if d not in loaded_dates]
    if not expected:
        errors.append("交易日历覆盖不可得，无法证明历史日期完整")
    elif missing_dates:
        errors.append(f"历史涨停事实缺{len(missing_dates)}个开放日")

    target = next((item for item in snapshots if item["date"] == target_date), None)
    target_ok = bool(target and target["limit_valid"] and target["down_valid"])
    return {
        "start_date": start,
        "target_date": target_date,
        "snapshots": snapshots,
        "expected_dates": expected,
        "missing_dates": missing_dates,
        "errors": errors,
        "target_ok": target_ok,
        "coverage": {
            "expected_open_days": len(expected),
            "loaded_limit_days": len(loaded_dates),
        },
    }


def _front_codes(rows: list[dict]) -> set[str]:
    eligible = [row for row in rows if not row["is_st"] and row["height"] >= 2]
    eligible.sort(key=lambda row: (-row["height"], -row["amount_billion"], row["code"]))
    return {row["code"] for row in eligible[:2]}


def _height_breakthrough(
    history: dict,
    lifecycles: dict[str, dict],
) -> dict:
    """识别目标日是否打开近期非 ST 连板高度。

    只比较目标日与此前固定开放日窗口的客观最高板数；
    窗口内任一开放日缺失都 fail-closed，不用稀疏样本伪造突破。
    """

    lookback = C.HEIGHT_BREAKTHROUGH_LOOKBACK_OPEN_DAYS
    target_date = str(history.get("target_date") or "")
    expected_dates = [str(value) for value in history.get("expected_dates", [])]
    base = {
        "status": "missing_data",
        "source_status": "partial",
        "as_of": target_date,
        "lookback_open_days": lookback,
        "previous_window_start": "",
        "previous_window_end": "",
        "current_max_height": None,
        "previous_max_height": None,
        "leaders": [],
    }
    if target_date not in expected_dates:
        return {**base, "reason": "目标日不在已证明的开放日日历中"}
    target_index = expected_dates.index(target_date)
    prior_dates = expected_dates[max(0, target_index - lookback):target_index]
    if len(prior_dates) != lookback:
        return {**base, "reason": f"此前开放日不足{lookback}个"}

    snapshots = {
        str(item.get("date")): item
        for item in history.get("snapshots", [])
        if item.get("limit_valid")
    }
    required_dates = [*prior_dates, target_date]
    missing = [value for value in required_dates if value not in snapshots]
    window = {
        "previous_window_start": prior_dates[0],
        "previous_window_end": prior_dates[-1],
    }
    if missing:
        return {
            **base,
            **window,
            "reason": f"高度对比窗口缺{len(missing)}个开放日事实",
        }

    def eligible_rows(day: str) -> list[dict]:
        return [
            row
            for row in snapshots[day]["limit_rows"]
            if not row["is_st"] and row["height"] >= 2
        ]

    current_rows = eligible_rows(target_date)
    current_max = max((row["height"] for row in current_rows), default=0)
    previous_max = max(
        (
            row["height"]
            for trade_date in prior_dates
            for row in eligible_rows(trade_date)
        ),
        default=0,
    )
    facts = {
        **base,
        **window,
        "source_status": "complete",
        "current_max_height": current_max,
        "previous_max_height": previous_max,
    }
    if current_max < 2 or current_max <= previous_max:
        return {**facts, "status": "none", "reason": ""}

    leaders: list[dict] = []
    for row in current_rows:
        if row["height"] != current_max:
            continue
        lifecycle = lifecycles.get(row["code"])
        if lifecycle is None:
            continue
        launch_date = str(lifecycle.get("launch_date") or "")
        launch_method = str(lifecycle.get("launch_method") or "")
        try:
            valid_launch_date = date.fromisoformat(launch_date) <= date.fromisoformat(target_date)
        except ValueError:
            valid_launch_date = False
        if (
            not valid_launch_date
            or launch_method not in {"limit_chain", "calendar_inferred"}
        ):
            continue
        leaders.append(
            {
                "code": row["code"],
                "name": row["name"],
                "launch_date": launch_date,
                "launch_method": launch_method,
                "current_height": current_max,
            }
        )
    leaders.sort(key=lambda item: item["code"])
    if not leaders:
        return {
            **facts,
            "status": "missing_data",
            "source_status": "partial",
            "reason": "打开高度的股票无法对齐生命周期启动日",
        }
    return {**facts, "status": "triggered", "leaders": leaders, "reason": ""}


def _runs(occurrences: list[dict]) -> list[list[dict]]:
    """按连板高度递增重建连板段；允许停牌造成的日期间隔。"""
    output: list[list[dict]] = []
    current: list[dict] = []
    for row in occurrences:
        if current and row["height"] == current[-1]["height"] + 1:
            current.append(row)
            continue
        if current:
            output.append(current)
        current = [row]
    if current:
        output.append(current)
    return output


def _manual_leaders(conn: sqlite3.Connection, since: str, target_date: str) -> list[dict]:
    try:
        rows = conn.execute(
            """
            SELECT stock_code, stock_name, sector, attribute_type,
                   first_seen_date, last_seen_date, current_phase
            FROM leader_tracking
            WHERE is_active = 1
              AND first_seen_date <= ?
              AND last_seen_date >= ?
            ORDER BY last_seen_date DESC, id DESC
            """,
            (target_date, since),
        ).fetchall()
    except sqlite3.Error:
        return []
    return [dict(row) for row in rows if row["attribute_type"] in C.MANUAL_CORE_ATTRIBUTES]


def discover_lifecycles(conn: sqlite3.Connection, history: dict) -> dict:
    """生成自动候选/核心，并合并复盘第5步人工确认的活跃核心。"""
    snapshots = [item for item in history.get("snapshots", []) if item.get("limit_valid")]
    dates = [item["date"] for item in snapshots]
    date_index = {d: i for i, d in enumerate(dates)}
    occurrences: dict[str, list[dict]] = defaultdict(list)
    front_by_date: dict[str, set[str]] = {}
    for snapshot in snapshots:
        rows = snapshot["limit_rows"]
        front_by_date[snapshot["date"]] = _front_codes(rows)
        for row in rows:
            if not row["is_st"]:
                occurrences[row["code"]].append(row)

    lifecycles: dict[str, dict] = {}
    for code, stock_rows in occurrences.items():
        qualifying_runs = [run for run in _runs(stock_rows) if max(r["height"] for r in run) >= 2]
        if not qualifying_runs:
            continue
        first_run = qualifying_runs[0]
        first_observed = first_run[0]
        launch_method = "limit_chain"
        if first_observed["height"] == 1:
            launch_date = first_observed["date"]
        else:
            idx = date_index.get(first_observed["date"], 0)
            inferred_idx = idx - first_observed["height"] + 1
            launch_date = dates[inferred_idx] if inferred_idx >= 0 else first_observed["date"]
            launch_method = "calendar_inferred"

        all_rows = [row for run in qualifying_runs for row in run]
        candidate_rows = [row for row in all_rows if row["height"] >= 2]
        promotion_rows = [
            row for row in candidate_rows
            if row["height"] >= 3 or row["code"] in front_by_date.get(row["date"], set())
        ]
        latest = all_rows[-1]
        industries = [row["industry"] for row in reversed(all_rows) if row["industry"]]
        lifecycles[code] = {
            "code": code,
            "name": latest["name"],
            "board_type": board_type(code),
            "launch_date": launch_date,
            "launch_method": launch_method,
            "candidate_date": candidate_rows[0]["date"],
            "promoted_date": promotion_rows[0]["date"] if promotion_rows else None,
            "last_limit_up_date": latest["date"],
            "max_height": max(row["height"] for row in all_rows),
            "run_count": len(qualifying_runs),
            "limit_industry": industries[0] if industries else "",
            "manual_confirmed": False,
            "manual_sector": "",
            "manual_phase": "",
            "manual_last_seen_date": "",
        }

    for manual in _manual_leaders(conn, history["start_date"], history["target_date"]):
        code = normalize_code(manual.get("stock_code"))
        if not code:
            continue
        item = lifecycles.get(code)
        if item is None:
            item = {
                "code": code,
                "name": str(manual.get("stock_name") or code),
                "board_type": board_type(code),
                "launch_date": str(manual.get("first_seen_date") or history["target_date"]),
                "launch_method": "manual_confirmed",
                "candidate_date": None,
                "promoted_date": str(manual.get("first_seen_date") or history["target_date"]),
                "last_limit_up_date": None,
                "max_height": 0,
                "run_count": 0,
                "limit_industry": "",
                "manual_confirmed": True,
                "manual_sector": str(manual.get("sector") or ""),
                "manual_phase": str(manual.get("current_phase") or ""),
                "manual_last_seen_date": str(manual.get("last_seen_date") or ""),
            }
            lifecycles[code] = item
        else:
            item.update({
                "manual_confirmed": True,
                "manual_sector": str(manual.get("sector") or ""),
                "manual_phase": str(manual.get("current_phase") or ""),
                "manual_last_seen_date": str(manual.get("last_seen_date") or ""),
            })

    promoted = [item for item in lifecycles.values() if item.get("promoted_date")]
    candidates = [item for item in lifecycles.values() if not item.get("promoted_date")]
    promoted.sort(key=lambda item: (item["launch_date"], item["code"]))
    candidates.sort(key=lambda item: (item["candidate_date"] or "", item["code"]))
    return {
        "promoted": promoted,
        "candidates": candidates,
        "trade_dates": dates,
        "current_limit_up_codes": {
            row["code"]
            for item in snapshots if item["date"] == history["target_date"]
            for row in item["limit_rows"]
        },
        "current_down_codes": next(
            (item["down_codes"] for item in snapshots if item["date"] == history["target_date"]),
            set(),
        ),
        "height_breakthrough": _height_breakthrough(history, lifecycles),
    }
