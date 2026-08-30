"""可配置的盘中全市场横截面监控。"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Callable

from services.tail_scan.scanner import (
    _load_no_limit_codes,
    _possible_no_limit_codes,
    _stock_universe,
)
from utils.price_limit import compute_limit_prices

from .guards import confirmed_trade_day, quote_is_fresh, shanghai_now
from .state import load_state, locked_state, save_state


DEFAULT_MARKET_SCAN_STATE_PATH = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "runs"
    / "intraday-monitor"
    / "market-scan-state.json"
)
SENT_IDS_LIMIT = 1000


@dataclass(frozen=True)
class MarketScanRule:
    """全市场快照规则；后续同类条件只需新增配置与匹配器。"""

    rule_id: str
    display_name: str
    start_time: time
    end_time: time
    min_amount_yi: float
    provider: str = "sina"

    def is_in_window(self, now: datetime) -> bool:
        current = now.time().replace(tzinfo=None)
        return self.start_time <= current < self.end_time


LIMIT_UP_AMOUNT_100B_BEFORE_1000 = MarketScanRule(
    rule_id="limit-up-amount-100b-before-1000",
    display_name="10点前百亿成交额涨停板",
    start_time=time(9, 30),
    end_time=time(10, 0),
    min_amount_yi=100.0,
)
DEFAULT_MARKET_SCAN_RULES = (LIMIT_UP_AMOUNT_100B_BEFORE_1000,)


def _number(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def match_limit_up_amount_quote(quote: dict, rule: MarketScanRule) -> dict | None:
    """纯函数匹配：最新价达到正式涨停价且累计成交额不少于阈值。"""
    code = str(quote.get("code") or "").strip().upper()
    name = str(quote.get("name") or "").strip()
    price = _number(quote.get("price"))
    pre_close = _number(quote.get("pre_close"))
    amount = _number(quote.get("amount"))
    if not code or price is None or price <= 0 or pre_close is None or pre_close <= 0:
        return None
    if amount is None or amount < rule.min_amount_yi * 1e8:
        return None
    up_limit = compute_limit_prices(pre_close, code, name).get("up_limit")
    if up_limit is None or not math.isclose(price, float(up_limit), abs_tol=0.0001):
        return None
    return {
        "code": code,
        "instrument_name": name or code,
        "price": price,
        "pre_close": pre_close,
        "up_limit": float(up_limit),
        "amount_yi": round(amount / 1e8, 2),
    }


def _event(rule: MarketScanRule, match: dict, quote_at: datetime, source: str) -> dict:
    raw = f"{rule.rule_id}|{match['code']}|{quote_at.date().isoformat()}".encode("utf-8")
    return {
        "event_id": hashlib.sha256(raw).hexdigest()[:24],
        "rule_id": rule.rule_id,
        "rule_name": rule.display_name,
        "min_amount_yi": rule.min_amount_yi,
        **match,
        "quote_at": quote_at.isoformat(),
        "source": source,
    }


def render_market_scan_alert(events: list[dict]) -> str:
    lines = ["### 🚨 盘中全市场条件监控", ""]
    for event in events:
        lines.extend(
            [
                f"- 规则：{event['rule_name']}",
                f"- [事实] **{event['instrument_name']}**（{event['code']}）当前价 "
                f"**{event['price']:.2f}** 元，已达到正式涨停价 "
                f"**{event['up_limit']:.2f}** 元",
                f"  - 当日累计成交额：**{event['amount_yi']:.2f} 亿元**"
                f"（监控线：≥{event['min_amount_yi']:g}亿元）",
                f"  - 行情时间：{event['quote_at']}",
                f"  - 数据来源：{event['source']}",
            ]
        )
    lines.extend(["", "> 仅为条件触发提醒，不构成买卖建议。"])
    return "\n".join(lines)


def _push_pending(state: dict, pusher_factory: Callable[[], object]) -> tuple[bool, str | None]:
    pending = list(state.get("pending_events") or [])
    if not pending:
        return True, None
    try:
        pusher = pusher_factory()
        if not pusher.initialize():
            return False, "钉钉凭据未配置"
        if not pusher.send_markdown(
            title="盘中全市场条件监控",
            content=render_market_scan_alert(pending),
        ):
            return False, "钉钉发送失败"
    except Exception as exc:
        return False, f"钉钉发送异常 {type(exc).__name__}: {exc}"
    sent = list(state.get("sent_event_ids") or [])
    sent.extend(str(event["event_id"]) for event in pending)
    state["sent_event_ids"] = sent[-SENT_IDS_LIMIT:]
    state["pending_events"] = []
    return True, None


def _expire_old_pending(state: dict, today: str) -> int:
    pending = list(state.get("pending_events") or [])
    current = [event for event in pending if str(event.get("quote_at") or "")[:10] == today]
    expired = [event for event in pending if event not in current]
    state["pending_events"] = current
    state.setdefault("expired_event_ids", []).extend(
        str(event["event_id"]) for event in expired if event.get("event_id")
    )
    state["expired_event_ids"] = state["expired_event_ids"][-SENT_IDS_LIMIT:]
    return len(expired)


def _fetch_universe_quotes(registry, rule: MarketScanRule, day: str):
    providers = list(getattr(registry, "providers", []) or [])
    quote_provider_ready = False
    universe_provider_ready = False
    for provider in providers:
        supports_universe = bool(provider.supports("get_stock_basic_list"))
        is_quote_provider = provider.name == rule.provider
        if not supports_universe and not is_quote_provider:
            continue
        initialized = bool(getattr(provider, "_initialized", False))
        if not initialized:
            try:
                initialized = bool(provider.initialize())
            except Exception:
                initialized = False
        if supports_universe and initialized:
            universe_provider_ready = True
        if is_quote_provider and initialized:
            quote_provider_ready = True
    # 测试 registry/兼容调用方可不暴露 providers；真实 ProviderRegistry 必须证明初始化。
    if providers and not universe_provider_ready:
        return None, None, None, "全市场代码清单数据源初始化失败"
    if providers and not quote_provider_ready:
        return None, None, None, f"实时行情数据源初始化失败（{rule.provider}）"
    codes, listing_dates = _stock_universe(registry, day)
    if not codes:
        return None, None, None, "全市场代码清单获取失败（get_stock_basic_list）"
    result = registry.call_specific(rule.provider, "get_realtime_quotes", codes)
    if not getattr(result, "success", False) or not isinstance(result.data, list):
        result = registry.call_specific(rule.provider, "get_realtime_quotes", codes)
    if not getattr(result, "success", False) or not isinstance(result.data, list):
        return None, None, None, f"实时行情获取失败（含重试）：{getattr(result, 'error', '未知')}"
    return result, listing_dates, codes, None


def _uncertain_missing_codes(requested_codes: list[str], result) -> set[str]:
    """区分确定不可能触发的停牌/无数据与可能被新浪静默漏掉的响应。"""
    returned = {
        str(quote.get("code") or "").strip().upper()
        for quote in result.data
        if isinstance(quote, dict)
    }
    benign_missing: set[str] = set()
    for item in str(getattr(result, "note", "") or "").split(";"):
        text = item.strip()
        if "(" not in text:
            continue
        code = text.split("(", 1)[0].strip().upper()
        if "停牌或无最新价" in text:
            benign_missing.add(code)
    return set(requested_codes) - returned - benign_missing


def _scan(
    registry,
    rule: MarketScanRule,
    now: datetime,
) -> dict:
    day = now.date().isoformat()
    result, listing_dates, requested_codes, fetch_error = _fetch_universe_quotes(
        registry, rule, day
    )
    if fetch_error:
        return {"status": "source_failed", "events": [], "errors": [fetch_error]}

    uncertain_codes = _uncertain_missing_codes(requested_codes, result)
    valid_quotes: list[tuple[dict, datetime]] = []
    invalid_time_codes: set[str] = set()
    for quote in result.data:
        fresh, _reason, quoted_at = quote_is_fresh(quote, now)
        if not fresh or quoted_at is None:
            invalid_time_codes.add(str(quote.get("code") or "").strip().upper())
            continue
        quote_clock = quoted_at.time().replace(tzinfo=None)
        if not rule.start_time <= quote_clock < rule.end_time:
            invalid_time_codes.add(str(quote.get("code") or "").strip().upper())
            continue
        valid_quotes.append((quote, quoted_at))
    if not valid_quotes:
        return {
            "status": "source_failed",
            "events": [],
            "errors": ["全市场实时行情日期、时间或新鲜度不满足监控窗口"],
        }

    potential: list[tuple[dict, datetime, dict]] = []
    for quote, quoted_at in valid_quotes:
        matched = match_limit_up_amount_quote(quote, rule)
        if matched is not None:
            potential.append((quote, quoted_at, matched))

    possible_no_limit = _possible_no_limit_codes(listing_dates, day)
    missing_listing = {match["code"] for _, _, match in potential} - set(listing_dates)
    if missing_listing:
        sample = ",".join(sorted(missing_listing)[:3])
        return {
            "status": "source_failed",
            "events": [],
            "errors": [f"新股无涨跌幅限制日判定失败：上市日期缺失（{sample}）"],
        }
    recent_candidates = {match["code"] for _, _, match in potential} & possible_no_limit
    no_limit_codes: set[str] = set()
    if recent_candidates:
        open_days_by_code = {
            code: 1 if code.endswith(".BJ") else 5 for code in recent_candidates
        }
        no_limit_codes, calendar_error = _load_no_limit_codes(
            registry,
            day,
            listing_dates,
            recent_candidates,
            open_days_by_code=open_days_by_code,
        )
        if calendar_error:
            return {
                "status": "source_failed",
                "events": [],
                "errors": [f"新股无涨跌幅限制日判定失败：{calendar_error}"],
            }

    events_by_id: dict[str, dict] = {}
    for _quote, quoted_at, matched in potential:
        if matched["code"] in no_limit_codes:
            continue
        event = _event(rule, matched, quoted_at, str(result.source or rule.provider))
        events_by_id[event["event_id"]] = event
    events = list(events_by_id.values())
    uncertain_codes.update(code for code in invalid_time_codes if code)
    errors: list[str] = []
    status = "complete"
    if uncertain_codes:
        sample = ",".join(sorted(uncertain_codes)[:5])
        errors.append(
            f"全市场实时行情存在不可判代码（count={len(uncertain_codes)},sample={sample}）"
        )
        status = "partial"
    return {
        "status": status,
        "events": events,
        "errors": errors,
        "quotes_checked": len(valid_quotes),
        "matched": len(events),
        "source": str(result.source or rule.provider),
    }


def run_market_scan(
    registry,
    *,
    rules: tuple[MarketScanRule, ...] = DEFAULT_MARKET_SCAN_RULES,
    now: datetime | None = None,
    state_path: str | Path = DEFAULT_MARKET_SCAN_STATE_PATH,
    db_path=None,
    dry_run: bool = False,
    pusher_factory: Callable[[], object] | None = None,
) -> dict:
    """执行横截面监控；新事件同股每日一次，失败 pending 可在窗口后继续重试。"""
    local_now = shanghai_now(now)
    path = Path(state_path)
    today = local_now.date().isoformat()
    active_rules = tuple(rule for rule in rules if rule.is_in_window(local_now))

    if pusher_factory is None:
        from pushers.dingtalk_pusher import DingTalkPusher

        pusher_factory = lambda: DingTalkPusher(config={})

    if not active_rules:
        if dry_run or not path.exists():
            return {"status": "outside_window", "events": [], "errors": [], "pushed": False}
        try:
            with locked_state(path) as state:
                expired = _expire_old_pending(state, today)
                pending_before = len(state.get("pending_events") or [])
                if not pending_before:
                    if expired:
                        save_state(path, state)
                    return {
                        "status": "outside_window",
                        "events": [],
                        "errors": [],
                        "pending_count": 0,
                        "pushed": False,
                    }
                push_ok, push_error = _push_pending(state, pusher_factory)
                save_state(path, state)
                return {
                    "status": "complete" if push_ok else "push_failed",
                    "events": [],
                    "errors": [] if push_ok else [push_error or "钉钉发送失败"],
                    "pending_count": len(state.get("pending_events") or []),
                    "pushed": push_ok and pending_before > 0,
                    "retry_only": True,
                }
        except (OSError, ValueError) as exc:
            return {"status": "state_error", "events": [], "errors": [str(exc)], "pushed": False}

    trade_day = confirmed_trade_day(today, db_path=db_path)
    if trade_day is None:
        return {"status": "blocked_calendar", "events": [], "errors": ["交易日历缺失或不可读"]}
    if not trade_day:
        return {"status": "non_trade_day", "events": [], "errors": []}

    scan_results = [_scan(registry, rule, local_now) for rule in active_rules]
    hard_failures = [result for result in scan_results if result["status"] == "source_failed"]
    successful = [result for result in scan_results if result["status"] in {"complete", "partial"}]
    if not successful:
        return {
            "status": "source_failed",
            "events": [],
            "errors": [error for result in hard_failures for error in result.get("errors") or []],
            "pushed": False,
        }
    scanned = {
        "status": "partial"
        if hard_failures or any(result["status"] == "partial" for result in successful)
        else "complete",
        "events": [event for result in successful for event in result.get("events") or []],
        "errors": [error for result in scan_results for error in result.get("errors") or []],
        "quotes_checked": sum(int(result.get("quotes_checked") or 0) for result in successful),
        "matched": sum(int(result.get("matched") or 0) for result in successful),
    }
    if dry_run:
        scanned["status"] = "dry_run" if scanned["status"] == "complete" else scanned["status"]
        scanned["pushed"] = False
        return scanned

    try:
        with locked_state(path) as state:
            _expire_old_pending(state, today)
            sent_ids = set(state.get("sent_event_ids") or [])
            pending_ids = {event.get("event_id") for event in state.get("pending_events") or []}
            new_events = [
                event
                for event in scanned["events"]
                if event["event_id"] not in sent_ids and event["event_id"] not in pending_ids
            ]
            if new_events:
                state.setdefault("pending_events", []).extend(new_events)
            state["last_fetch_at"] = local_now.isoformat()
            save_state(path, state)
            pending_before = len(state.get("pending_events") or [])
            push_ok, push_error = _push_pending(state, pusher_factory)
            save_state(path, state)
            errors = list(scanned.get("errors") or [])
            if not push_ok:
                errors.append(push_error or "钉钉发送失败")
            return {
                **scanned,
                "status": scanned["status"] if push_ok else "push_failed",
                "events": new_events,
                "errors": errors,
                "pending_count": len(state.get("pending_events") or []),
                "pushed": push_ok and pending_before > 0,
            }
    except (OSError, ValueError) as exc:
        return {"status": "state_error", "events": [], "errors": [str(exc)], "pushed": False}
