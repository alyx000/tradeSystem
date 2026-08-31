"""全市场盘中半小时扫描编排。"""
from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path
from typing import Callable

from services.intraday_monitor.guards import confirmed_trade_day, quote_is_fresh, shanghai_now

from .analytics import analyze_interval, current_breadth
from .formatter import render_report
from .schedule import ScanSlot, slot_for_time
from .state import (
    DEFAULT_REPORT_ROOT,
    DEFAULT_STATE_PATH,
    HISTORY_LIMIT,
    atomic_write_text,
    empty_state,
    load_state,
    locked_state,
    reset_for_day,
    save_state,
)


INDEX_CODES = ("000001.SH", "399001.SZ", "399006.SZ", "000688.SH")
MIN_UNIVERSE = 4_000
MIN_QUOTE_COVERAGE = 0.95
REALTIME_ATTEMPTS = 2


def _number(value, *, positive: bool = False, nonnegative: bool = False) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if positive and number <= 0:
        return None
    if nonnegative and number < 0:
        return None
    return number


def _initialize_capability(registry, capability: str) -> None:
    """Registry 不做惰性初始化；只初始化本次能力可能使用的数据源。"""
    for provider in getattr(registry, "providers", ()):
        try:
            if provider.supports(capability) and not getattr(provider, "_initialized", False):
                provider.initialize()
        except Exception:
            continue


def _load_universe(registry, state: dict, day: str) -> tuple[list[str], str | None]:
    cached = state.get("universe") or {}
    codes = cached.get("codes") if cached.get("trade_date") == day else None
    if isinstance(codes, list) and len(codes) >= MIN_UNIVERSE:
        return [str(code) for code in codes], None
    _initialize_capability(registry, "get_stock_basic_list")
    try:
        result = registry.call("get_stock_basic_list", day)
    except Exception as exc:
        return [], f"全市场证券清单异常 {type(exc).__name__}: {exc}"
    if not getattr(result, "success", False) or not isinstance(result.data, list):
        return [], f"全市场证券清单失败：{getattr(result, 'error', '返回非法')}"
    normalized = sorted({
        str(row.get("ts_code") or row.get("code") or "").strip().upper()
        for row in result.data if isinstance(row, dict)
    } - {""})
    if len(normalized) < MIN_UNIVERSE:
        return [], f"全市场证券清单覆盖不足（{len(normalized)} < {MIN_UNIVERSE}）"
    state["universe"] = {"trade_date": day, "codes": normalized}
    return normalized, None


def _load_industry_map(registry, state: dict, day: str) -> tuple[dict[str, str], str | None]:
    cached = state.get("industry_map") or {}
    mapping = cached.get("mapping") if cached.get("trade_date") == day else None
    if isinstance(mapping, dict) and mapping:
        return mapping, None
    _initialize_capability(registry, "get_stock_sw_industry_map")
    try:
        result = registry.call("get_stock_sw_industry_map")
    except Exception as exc:
        return {}, f"申万二级映射异常 {type(exc).__name__}: {exc}"
    if not getattr(result, "success", False) or not isinstance(result.data, dict):
        return {}, f"申万二级映射失败：{getattr(result, 'error', '返回非法')}"
    normalized = {}
    for code, item in result.data.items():
        name = item.get("sw_l2") if isinstance(item, dict) else item
        code_text = str(code or "").strip().upper()
        name_text = str(name or "").strip()
        if code_text and name_text and name_text not in {"未分类", "UNCLASSIFIED"}:
            normalized[code_text] = name_text
    if not normalized:
        return {}, "申万二级映射为空"
    state["industry_map"] = {"trade_date": day, "mapping": normalized}
    return normalized, None


def _call_realtime(registry, codes: list[str]):
    _initialize_capability(registry, "get_realtime_quotes")
    last = None
    for _ in range(REALTIME_ATTEMPTS):
        try:
            last = registry.call("get_realtime_quotes", codes)
        except Exception as exc:
            last = exc
            continue
        if getattr(last, "success", False) and isinstance(last.data, list):
            return last, None
    if isinstance(last, Exception):
        return None, f"实时行情异常 {type(last).__name__}: {last}"
    return None, f"实时行情失败（含重试）：{getattr(last, 'error', '返回非法')}"


def _capture_snapshot(registry, state: dict, day: str, slot: ScanSlot, now: datetime) -> tuple[dict | None, list[str]]:
    codes, universe_error = _load_universe(registry, state, day)
    if universe_error:
        return None, [universe_error]
    requested = list(dict.fromkeys([*codes, *INDEX_CODES]))
    result, realtime_error = _call_realtime(registry, requested)
    if realtime_error or result is None:
        return None, [realtime_error or "实时行情失败"]
    quotes = {}
    freshness_errors = []
    quote_times = []
    for raw in result.data:
        if not isinstance(raw, dict):
            continue
        code = str(raw.get("code") or "").strip().upper()
        fresh, reason, quoted_at = quote_is_fresh(raw, now)
        if not fresh or quoted_at is None:
            freshness_errors.append(f"{code or 'unknown'}:{reason}")
            continue
        price = _number(raw.get("price"), positive=True)
        amount = _number(raw.get("amount"), nonnegative=True)
        if not code or price is None or amount is None:
            continue
        quotes[code] = {
            "name": str(raw.get("name") or "").strip(),
            "price": price,
            "pct_chg": _number(raw.get("pct_chg")),
            "amount": amount,
            "quote_at": quoted_at.isoformat(),
        }
        quote_times.append(quoted_at)
    stocks = {code: quotes[code] for code in codes if code in quotes}
    coverage = len(stocks) / len(codes) if codes else 0.0
    if coverage < MIN_QUOTE_COVERAGE:
        sample = "，".join(freshness_errors[:3])
        detail = f"；样例 {sample}" if sample else ""
        return None, [f"实时全市场覆盖不足（{coverage:.1%} < {MIN_QUOTE_COVERAGE:.0%}）{detail}"]
    indices = {code: quotes[code] for code in INDEX_CODES if code in quotes}
    snapshot = {
        "trade_date": day,
        "slot": slot.label,
        "slot_id": slot.slot_id(day),
        "captured_at": now.isoformat(),
        "quote_time": max(quote_times).strftime("%H:%M:%S") if quote_times else None,
        "coverage_pct": round(coverage * 100, 2),
        "stocks": stocks,
        "indices": indices,
    }
    gaps = []
    if len(indices) < len(INDEX_CODES):
        gaps.append(f"宽基指数仅取得 {len(indices)}/{len(INDEX_CODES)}")
    if freshness_errors:
        gaps.append(f"剔除陈旧或非法行情 {len(freshness_errors)} 条")
    return snapshot, gaps


def _default_pusher_factory():
    from pushers.dingtalk_pusher import DingTalkPusher

    return DingTalkPusher(config={})


def _flush_pending(state: dict, pusher_factory: Callable[[], object]) -> tuple[bool, str | None, int]:
    pending = list(state.get("pending_reports") or [])
    if not pending:
        return True, None, 0
    try:
        pusher = pusher_factory()
        if not pusher.initialize():
            return False, "钉钉凭据未配置", 0
        sent_count = 0
        for item in pending:
            if not pusher.send_markdown(title=item["title"], content=item["content"]):
                return False, "钉钉发送失败", sent_count
            sent_count += 1
            state.setdefault("sent_slot_ids", []).append(item["slot_id"])
            state["pending_reports"] = state["pending_reports"][1:]
        state["sent_slot_ids"] = state["sent_slot_ids"][-HISTORY_LIMIT:]
        return True, None, sent_count
    except Exception as exc:
        return False, f"钉钉发送异常 {type(exc).__name__}: {exc}", 0


def _result_for_existing(state: dict, slot_id: str, pusher_factory, no_push: bool) -> dict | None:
    if slot_id in set(state.get("captured_slot_ids") or []):
        return {"status": "already_captured", "slot_id": slot_id, "pushed": False, "errors": []}
    if slot_id in set(state.get("sent_slot_ids") or []):
        return {"status": "already_sent", "slot_id": slot_id, "pushed": False, "errors": []}
    if slot_id in set(state.get("suppressed_slot_ids") or []):
        return {"status": "suppressed", "slot_id": slot_id, "pushed": False, "errors": []}
    pending_item = next(
        (item for item in state.get("pending_reports") or [] if item.get("slot_id") == slot_id),
        None,
    )
    if pending_item is not None:
        if no_push:
            return {"status": "pending", "slot_id": slot_id, "pushed": False, "errors": []}
        ok, error, sent = _flush_pending(state, pusher_factory)
        data_status = str(pending_item.get("data_status") or "complete")
        return {
            "status": data_status if ok else "push_failed",
            "data_status": data_status,
            "slot_id": slot_id,
            "pushed": bool(sent),
            "pending_count": len(state.get("pending_reports") or []),
            "errors": [error] if error else [],
            "retry_only": True,
        }
    return None


def _expire_stale_pending(state: dict, current_slot_id: str) -> None:
    """错过槽位补发窗口的旧摘要不得在后续槽位继续发送。"""
    pending = list(state.get("pending_reports") or [])
    current = [item for item in pending if item.get("slot_id") == current_slot_id]
    stale = [item for item in pending if item.get("slot_id") != current_slot_id]
    if stale:
        expired = list(state.get("expired_pending_ids") or [])
        expired.extend(str(item.get("item_id") or "") for item in stale)
        state["expired_pending_ids"] = [item for item in expired if item][-HISTORY_LIMIT:]
    state["pending_reports"] = current


def _run_locked(
    registry,
    *,
    state: dict,
    state_path: Path,
    report_root: Path,
    slot: ScanSlot,
    now: datetime,
    dry_run: bool,
    no_push: bool,
    pusher_factory: Callable[[], object],
) -> dict:
    day = now.date().isoformat()
    if not dry_run:
        reset_for_day(state, day)
        _expire_stale_pending(state, slot.slot_id(day))
        existing = _result_for_existing(state, slot.slot_id(day), pusher_factory, no_push)
        if existing is not None:
            save_state(state_path, state)
            return existing

    snapshot, source_gaps = _capture_snapshot(registry, state, day, slot, now)
    if snapshot is None:
        if not dry_run:
            save_state(state_path, state)
        return {"status": "source_failed", "slot_id": slot.slot_id(day), "errors": source_gaps}

    if slot.kind == "baseline":
        if not dry_run:
            state["last_snapshot"] = snapshot
            state.setdefault("captured_slot_ids", []).append(slot.slot_id(day))
            state["captured_slot_ids"] = state["captured_slot_ids"][-HISTORY_LIMIT:]
            save_state(state_path, state)
        return {
            "status": "dry_run" if dry_run else "baseline_saved",
            "slot_id": slot.slot_id(day),
            "scanned": len(snapshot["stocks"]),
            "pushed": False,
            "errors": source_gaps,
        }

    previous = state.get("last_snapshot") or {}
    interval = None
    gap = None
    expected_previous_id = f"{day}T{slot.previous_label}"
    if previous.get("slot_id") != expected_previous_id:
        gap = f"缺少 {slot.previous_label} 基线快照"
    else:
        industry_map, industry_error = _load_industry_map(registry, state, day)
        if industry_error:
            source_gaps.append(industry_error)
        interval = analyze_interval(previous, snapshot, industry_map)
        if interval.get("status") == "coverage_failed":
            gap = interval.get("error")

    data_status = "complete"
    if gap or source_gaps or (interval and interval.get("status") != "complete"):
        data_status = "partial"
    payload = {
        "status": data_status,
        "trade_date": day,
        "slot": slot.label,
        "previous_slot": slot.previous_label,
        "slot_id": slot.slot_id(day),
        "quote_time": snapshot.get("quote_time"),
        "scanned": len(snapshot["stocks"]),
        "current_breadth": current_breadth(snapshot),
        "interval": interval,
        "gap": gap,
        "source_gaps": source_gaps,
    }
    markdown = render_report(payload)
    report_path = report_root / day / f"{slot.label.replace(':', '')}.md"
    if dry_run:
        return {**payload, "status": "dry_run", "data_status": data_status, "markdown": markdown, "report_path": None, "pushed": False, "errors": source_gaps}

    atomic_write_text(report_path, markdown + "\n")
    state["last_snapshot"] = snapshot
    title = f"A股盘中半小时扫描 {day} {slot.label}"
    if no_push:
        state.setdefault("suppressed_slot_ids", []).append(slot.slot_id(day))
        state["suppressed_slot_ids"] = state["suppressed_slot_ids"][-HISTORY_LIMIT:]
        save_state(state_path, state)
        return {**payload, "report_path": str(report_path), "pushed": False, "suppressed": True, "errors": source_gaps}

    state.setdefault("pending_reports", []).append({
        "item_id": f"summary:{slot.slot_id(day)}",
        "slot_id": slot.slot_id(day),
        "title": title,
        "content": markdown,
        "report_path": str(report_path),
        "created_at": now.isoformat(),
        "data_status": data_status,
    })
    save_state(state_path, state)
    push_ok, push_error, sent = _flush_pending(state, pusher_factory)
    save_state(state_path, state)
    status = data_status if push_ok else "push_failed"
    errors = list(source_gaps)
    if push_error:
        errors.append(push_error)
    return {
        **payload,
        "status": status,
        "data_status": data_status,
        "report_path": str(report_path),
        "pushed": bool(sent),
        "pending_count": len(state.get("pending_reports") or []),
        "errors": errors,
    }


def run(
    registry,
    *,
    now: datetime | None = None,
    state_path: str | Path = DEFAULT_STATE_PATH,
    report_root: str | Path = DEFAULT_REPORT_ROOT,
    db_path=None,
    dry_run: bool = False,
    no_push: bool = False,
    pusher_factory: Callable[[], object] | None = None,
) -> dict:
    """运行一次预设槽位扫描；正式模式写滚动快照、报告与发送 outbox。"""
    local_now = shanghai_now(now)
    slot = slot_for_time(local_now)
    if slot is None:
        return {"status": "outside_slot", "pushed": False, "errors": []}
    trade_day = confirmed_trade_day(local_now.date().isoformat(), db_path=db_path)
    if trade_day is None:
        return {"status": "blocked_calendar", "pushed": False, "errors": ["交易日历缺失或不可读"]}
    if not trade_day:
        return {"status": "non_trade_day", "pushed": False, "errors": []}
    factory = pusher_factory or _default_pusher_factory
    path = Path(state_path)
    reports = Path(report_root)
    if dry_run:
        try:
            state = load_state(path)
        except (OSError, ValueError):
            state = empty_state()
        if state.get("trade_date") not in {None, local_now.date().isoformat()}:
            state = empty_state()
        state["trade_date"] = local_now.date().isoformat()
        return _run_locked(
            registry,
            state=state,
            state_path=path,
            report_root=reports,
            slot=slot,
            now=local_now,
            dry_run=True,
            no_push=True,
            pusher_factory=factory,
        )
    try:
        with locked_state(path) as state:
            return _run_locked(
                registry,
                state=state,
                state_path=path,
                report_root=reports,
                slot=slot,
                now=local_now,
                dry_run=False,
                no_push=no_push,
                pusher_factory=factory,
            )
    except (OSError, ValueError) as exc:
        return {"status": "state_error", "pushed": False, "errors": [str(exc)]}
