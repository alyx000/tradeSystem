"""盘中实时阈值监控编排。"""
from __future__ import annotations

import hashlib
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable

from .formatter import render_alert, render_e2e_test_alert
from .guards import confirmed_trade_day, is_intraday_session, quote_is_fresh, shanghai_now
from .rules import DEFAULT_RULES, MonitorRule, should_emit
from .state import DEFAULT_STATE_PATH, load_state, locked_state, save_state


MIN_FETCH_INTERVAL = timedelta(seconds=3)
SENT_IDS_LIMIT = 500
E2E_TEST_MARGIN_MIN = 1.0
E2E_TEST_MARGIN_RATIO = 0.001


def _parse_iso_datetime(raw: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None


def _event_id(rule: MonitorRule, quote_at: str, price: float) -> str:
    raw = f"{rule.rule_id}|{quote_at}|{price:.8f}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def _event(rule: MonitorRule, quote: dict, quoted_at: datetime, source: str) -> dict:
    price = float(quote["price"])
    quote_at = quoted_at.isoformat()
    return {
        "event_id": _event_id(rule, quote_at, price),
        "rule_id": rule.rule_id,
        "instrument_name": rule.instrument_name,
        "code": rule.code,
        "threshold": rule.threshold,
        "direction": rule.direction,
        "action_text": rule.action_text,
        "price": price,
        "quote_at": quote_at,
        "source": source,
    }


def _fetch_quotes(registry, rules: Iterable[MonitorRule]) -> tuple[dict[str, dict], list[str]]:
    quotes: dict[str, dict] = {}
    errors: list[str] = []
    grouped: dict[str, list[MonitorRule]] = {}
    for rule in rules:
        grouped.setdefault(rule.provider, []).append(rule)
    for provider_name, provider_rules in grouped.items():
        provider = registry.get_provider(provider_name)
        if provider is None:
            errors.append(f"{provider_name}: provider 未注册")
            continue
        try:
            if not provider.initialize():
                errors.append(f"{provider_name}: provider 初始化失败")
                continue
            result = registry.call_specific(
                provider_name,
                "get_realtime_quotes",
                list(dict.fromkeys(rule.code for rule in provider_rules)),
            )
        except Exception as exc:
            errors.append(f"{provider_name}: 实时行情异常 {type(exc).__name__}: {exc}")
            continue
        if not result.success or not isinstance(result.data, list):
            errors.append(f"{provider_name}: {result.error or '实时行情返回非法'}")
            continue
        for quote in result.data:
            code = str(quote.get("code") or "").upper()
            if code:
                item = dict(quote)
                item["_source"] = result.source
                quotes[code] = item
    return quotes, errors


def _send_pending(state: dict, pusher_factory: Callable[[], object]) -> tuple[bool, str | None]:
    pending = list(state.get("pending_events") or [])
    if not pending:
        return True, None
    try:
        pusher = pusher_factory()
        if not pusher.initialize():
            return False, "钉钉凭据未配置"
        content = render_alert(pending)
        if not pusher.send_markdown(title="盘中阈值监控告警", content=content):
            return False, "钉钉发送失败"
    except Exception as exc:
        return False, f"钉钉发送异常 {type(exc).__name__}: {exc}"
    sent = list(state.get("sent_event_ids") or [])
    sent.extend(event["event_id"] for event in pending)
    state["sent_event_ids"] = sent[-SENT_IDS_LIMIT:]
    state["pending_events"] = []
    return True, None


def _retire_pending_while_disabled(state_path: Path) -> int:
    """停用期只处理既有 pending；不存在状态文件时不创建任何文件。"""
    if not state_path.exists():
        return 0
    with locked_state(state_path) as state:
        pending = list(state.get("pending_events") or [])
        if not pending:
            return 0
        expired = [event.get("event_id") for event in pending if event.get("event_id")]
        state.setdefault("expired_event_ids", []).extend(expired)
        state["expired_event_ids"] = state["expired_event_ids"][-SENT_IDS_LIMIT:]
        state["pending_events"] = []
        save_state(state_path, state)
        return len(pending)


def _run_locked(
    *,
    registry,
    rules: tuple[MonitorRule, ...],
    now: datetime,
    state: dict,
    state_path: Path,
    dry_run: bool,
    pusher_factory: Callable[[], object],
) -> dict:
    last_fetch = _parse_iso_datetime(state.get("last_fetch_at"))
    if last_fetch is not None and last_fetch.tzinfo is not None:
        if now - last_fetch < MIN_FETCH_INTERVAL:
            return {"status": "throttled", "events": [], "errors": []}

    if not dry_run:
        state["last_fetch_at"] = now.isoformat()
        save_state(state_path, state)

    quotes, errors = _fetch_quotes(registry, rules)
    events: list[dict] = []
    valid_quote_codes: set[str] = set()
    rule_states = state.setdefault("rules", {})
    sent_ids = set(state.get("sent_event_ids") or [])
    today = now.date().isoformat()
    active_rule_ids = {rule.rule_id for rule in rules}
    pending = list(state.get("pending_events") or [])
    current_pending = [
        event
        for event in pending
        if str(event.get("quote_at") or "")[:10] == today
        and event.get("rule_id") in active_rule_ids
    ]
    expired = [event.get("event_id") for event in pending if event not in current_pending]
    if expired:
        state.setdefault("expired_event_ids", []).extend(event_id for event_id in expired if event_id)
        state["expired_event_ids"] = state["expired_event_ids"][-SENT_IDS_LIMIT:]
    state["pending_events"] = current_pending
    pending_ids = {event.get("event_id") for event in current_pending}

    for rule in rules:
        quote = quotes.get(rule.code.upper())
        if quote is None:
            errors.append(f"{rule.rule_id}: 实时行情缺失")
            continue
        fresh, reason, quoted_at = quote_is_fresh(quote, now)
        if not fresh or quoted_at is None:
            errors.append(f"{rule.rule_id}: {reason}")
            continue
        try:
            price = float(quote.get("price"))
        except (TypeError, ValueError):
            errors.append(f"{rule.rule_id}: 最新价非法")
            continue
        if not math.isfinite(price) or price <= 0:
            errors.append(f"{rule.rule_id}: 最新价非有限或非正数")
            continue
        valid_quote_codes.add(rule.code.upper())
        previous = rule_states.get(rule.rule_id) or {}
        previous_active = (
            bool(previous.get("active"))
            if previous.get("trade_date") == now.date().isoformat()
            else None
        )
        active = rule.is_active(price)
        if should_emit(
            previous_active=previous_active,
            current_active=active,
            emit_on_initial_match=rule.emit_on_initial_match,
        ):
            event = _event(rule, quote, quoted_at, str(quote.get("_source") or rule.provider))
            if event["event_id"] not in sent_ids and event["event_id"] not in pending_ids:
                events.append(event)
                pending_ids.add(event["event_id"])
        rule_states[rule.rule_id] = {
            "trade_date": today,
            "active": active,
            "last_price": price,
            "last_quote_at": quoted_at.isoformat(),
            "updated_at": now.isoformat(),
        }

    if not valid_quote_codes:
        return {"status": "source_failed", "events": [], "errors": errors}

    if dry_run:
        return {
            "status": "dry_run",
            "events": events,
            "errors": errors,
            "quotes_checked": len(valid_quote_codes),
        }

    if events:
        state.setdefault("pending_events", []).extend(events)
    save_state(state_path, state)
    pending_before_send = len(state.get("pending_events") or [])
    push_ok, push_error = _send_pending(state, pusher_factory)
    save_state(state_path, state)
    status = "partial" if errors else "complete"
    if not push_ok:
        status = "push_failed"
        errors.append(push_error or "钉钉发送失败")
    return {
        "status": status,
        "events": events,
        "errors": errors,
        "quotes_checked": len(valid_quote_codes),
        "pending_count": len(state.get("pending_events") or []),
        "pushed": push_ok and pending_before_send > 0,
    }


def run_check(
    registry,
    *,
    rules: tuple[MonitorRule, ...] = DEFAULT_RULES,
    now: datetime | None = None,
    state_path: str | Path = DEFAULT_STATE_PATH,
    db_path=None,
    dry_run: bool = False,
    pusher_factory: Callable[[], object] | None = None,
) -> dict:
    """执行一次盘中监控；正式模式写本地状态并按 pending 账本推送。"""
    local_now = shanghai_now(now)
    if not rules:
        retired_pending_count = 0
        if not dry_run:
            try:
                retired_pending_count = _retire_pending_while_disabled(Path(state_path))
            except (OSError, ValueError) as exc:
                return {
                    "status": "state_error",
                    "events": [],
                    "errors": [str(exc)],
                    "rules_checked": 0,
                    "quotes_checked": 0,
                    "pushed": False,
                }
        return {
            "status": "no_rules",
            "events": [],
            "errors": [],
            "rules_checked": 0,
            "quotes_checked": 0,
            "pushed": False,
            "retired_pending_count": retired_pending_count,
        }
    if not is_intraday_session(local_now):
        return {"status": "outside_session", "events": [], "errors": []}
    trade_day = confirmed_trade_day(local_now.date().isoformat(), db_path=db_path)
    if trade_day is None:
        return {"status": "blocked_calendar", "events": [], "errors": ["交易日历缺失或不可读"]}
    if not trade_day:
        return {"status": "non_trade_day", "events": [], "errors": []}
    if pusher_factory is None:
        from pushers.dingtalk_pusher import DingTalkPusher

        pusher_factory = lambda: DingTalkPusher(config={})

    path = Path(state_path)
    if dry_run:
        try:
            state = load_state(path)
        except (OSError, ValueError):
            state = {
                "version": 1,
                "last_fetch_at": None,
                "rules": {},
                "pending_events": [],
                "sent_event_ids": [],
                "expired_event_ids": [],
            }
        return _run_locked(
            registry=registry,
            rules=rules,
            now=local_now,
            state=state,
            state_path=path,
            dry_run=True,
            pusher_factory=pusher_factory,
        )

    try:
        with locked_state(path) as state:
            return _run_locked(
                registry=registry,
                rules=rules,
                now=local_now,
                state=state,
                state_path=path,
                dry_run=False,
                pusher_factory=pusher_factory,
            )
    except (OSError, ValueError) as exc:
        return {"status": "state_error", "events": [], "errors": [str(exc)]}


def run_e2e_test(
    registry,
    *,
    input_by: str,
    rule: MonitorRule | None = None,
    now: datetime | None = None,
    db_path=None,
    pusher_factory: Callable[[], object] | None = None,
) -> dict:
    """用真实新鲜行情发送一条明确标注测试的消息，不读写正式监控状态。"""
    normalized_input_by = str(input_by or "").strip()
    if not normalized_input_by:
        return {"status": "invalid_input", "events": [], "errors": ["--input-by 不能为空"]}
    if rule is None and DEFAULT_RULES:
        rule = DEFAULT_RULES[0]
    if rule is None:
        return {
            "status": "no_rules",
            "events": [],
            "errors": ["当前没有启用的生产监控规则"],
            "pushed": False,
        }
    local_now = shanghai_now(now)
    if not is_intraday_session(local_now):
        return {"status": "outside_session", "events": [], "errors": []}
    trade_day = confirmed_trade_day(local_now.date().isoformat(), db_path=db_path)
    if trade_day is None:
        return {"status": "blocked_calendar", "events": [], "errors": ["交易日历缺失或不可读"]}
    if not trade_day:
        return {"status": "non_trade_day", "events": [], "errors": []}

    quotes, errors = _fetch_quotes(registry, (rule,))
    quote = quotes.get(rule.code.upper())
    if quote is None:
        errors.append(f"{rule.rule_id}: 实时行情缺失")
        return {"status": "source_failed", "events": [], "errors": errors}
    fresh, reason, quoted_at = quote_is_fresh(quote, local_now)
    if not fresh or quoted_at is None:
        errors.append(f"{rule.rule_id}: {reason}")
        return {"status": "source_failed", "events": [], "errors": errors}
    try:
        price = float(quote.get("price"))
    except (TypeError, ValueError):
        price = float("nan")
    if not math.isfinite(price) or price <= 0:
        errors.append(f"{rule.rule_id}: 最新价非有限或非正数")
        return {"status": "source_failed", "events": [], "errors": errors}

    margin = max(E2E_TEST_MARGIN_MIN, abs(price) * E2E_TEST_MARGIN_RATIO)
    test_rule = MonitorRule(
        rule_id=f"{rule.rule_id}-e2e-test",
        instrument_name=rule.instrument_name,
        code=rule.code,
        threshold=price + margin,
        direction="below",
        provider=rule.provider,
    )
    if not test_rule.is_active(price):
        return {"status": "test_condition_failed", "events": [], "errors": ["临时测试条件未命中"]}
    event = _event(test_rule, quote, quoted_at, str(quote.get("_source") or rule.provider))

    if pusher_factory is None:
        from pushers.dingtalk_pusher import DingTalkPusher

        pusher_factory = lambda: DingTalkPusher(config={})
    try:
        pusher = pusher_factory()
        if not pusher.initialize():
            return {"status": "push_failed", "events": [event], "errors": ["钉钉凭据未配置"], "pushed": False}
        content = render_e2e_test_alert(
            event,
            production_threshold=rule.threshold,
            input_by=normalized_input_by,
        )
        pushed = bool(
            pusher.send_markdown(
                title=f"【测试】{rule.instrument_name}盘中监控链路验证",
                content=content,
            )
        )
    except Exception as exc:
        return {
            "status": "push_failed",
            "events": [event],
            "errors": [f"钉钉发送异常 {type(exc).__name__}: {exc}"],
            "pushed": False,
        }
    if not pushed:
        return {"status": "push_failed", "events": [event], "errors": ["钉钉发送失败"], "pushed": False}
    return {
        "status": "complete",
        "events": [event],
        "errors": errors,
        "quotes_checked": 1,
        "pushed": True,
        "production_threshold": rule.threshold,
        "input_by": normalized_input_by,
    }
