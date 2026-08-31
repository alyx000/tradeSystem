"""盘中实时阈值监控编排。"""
from __future__ import annotations

import hashlib
import math
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Callable, Iterable

from .formatter import render_alert, render_e2e_test_alert
from .guards import (
    confirmed_trade_day,
    is_close_finalization_window,
    is_intraday_session,
    previous_open_dates,
    quote_is_fresh,
    shanghai_now,
)
from .rules import DEFAULT_RULES, MonitorRule, should_emit
from .state import DEFAULT_STATE_PATH, load_state, locked_state, save_state
from utils.qfq import apply_qfq


MIN_FETCH_INTERVAL = timedelta(seconds=3)
SENT_IDS_LIMIT = 500
E2E_TEST_MARGIN_MIN = 1.0
E2E_TEST_MARGIN_RATIO = 0.001
CLOSING_SNAPSHOT_MIN_QUOTE_TIME = time(15, 0)


def _parse_iso_datetime(raw: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None


def _event_id(
    rule: MonitorRule,
    quote_at: str,
    value: float,
    threshold: float,
    observation_phase: str,
) -> str:
    raw = (
        f"{rule.rule_id}|{quote_at}|{value:.8f}|{threshold:.8f}|{observation_phase}"
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def _event(
    rule: MonitorRule,
    quote: dict,
    quoted_at: datetime,
    source: str,
    *,
    value: float,
    threshold: float,
    threshold_source: str | None = None,
    threshold_basis_dates: list[str] | None = None,
    threshold_anchor_pre_close: float | None = None,
    observation_phase: str = "intraday",
) -> dict:
    price = float(quote["price"])
    quote_at = quoted_at.isoformat()
    return {
        "event_id": _event_id(rule, quote_at, value, threshold, observation_phase),
        "rule_id": rule.rule_id,
        "instrument_name": rule.instrument_name,
        "code": rule.code,
        "threshold": threshold,
        "threshold_source": threshold_source,
        "threshold_basis_dates": list(threshold_basis_dates or []),
        "threshold_anchor_pre_close": threshold_anchor_pre_close,
        "threshold_label": rule.threshold_label,
        "threshold_mode": rule.threshold_mode,
        "direction": rule.direction,
        "action_text": rule.action_text,
        "value_label": rule.value_label,
        "value_unit": rule.value_unit,
        "value_mode": rule.value_mode,
        "value": value,
        "price": price,
        "quote_at": quote_at,
        "source": source,
        "observation_phase": observation_phase,
    }


def _normalize_trade_date(raw: object) -> str | None:
    text = str(raw or "").strip().replace("-", "")[:8]
    if len(text) != 8 or not text.isdigit():
        return None
    return f"{text[:4]}-{text[4:6]}-{text[6:]}"


def _exact_history_rows(
    rows: object,
    expected_dates: list[str],
    *,
    value_key: str,
    label: str,
) -> list[dict]:
    if not isinstance(rows, list):
        raise ValueError(f"{label}返回格式非法")
    expected = set(expected_dates)
    by_date: dict[str, dict] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            raise ValueError(f"{label}包含非对象行")
        trade_date = _normalize_trade_date(raw.get("trade_date"))
        if trade_date is None:
            raise ValueError(f"{label}包含非法交易日")
        if trade_date not in expected:
            raise ValueError(f"{label}返回非预期交易日 {trade_date}")
        if trade_date in by_date:
            raise ValueError(f"{label}存在重复交易日 {trade_date}")
        if raw.get(value_key) is None:
            raise ValueError(f"{label}在 {trade_date} 缺少 {value_key}")
        row = dict(raw)
        row["trade_date"] = trade_date
        by_date[trade_date] = row
    missing = [trade_date for trade_date in expected_dates if trade_date not in by_date]
    if missing:
        raise ValueError(f"{label}缺少开放日: {','.join(missing)}")
    return [by_date[trade_date] for trade_date in expected_dates]


def _initialized_provider(registry, provider_name: str):
    provider = registry.get_provider(provider_name)
    if provider is None:
        raise ValueError(f"{provider_name}: 动态阈值 provider 未注册")
    if not provider.supports("get_stock_daily_range"):
        raise ValueError(f"{provider_name}: 不支持个股区间日线")
    if not provider.supports("get_stock_adj_factor_range"):
        raise ValueError(f"{provider_name}: 不支持个股复权因子")
    if not bool(getattr(provider, "_initialized", False)):
        try:
            initialized = bool(provider.initialize())
        except Exception as exc:
            raise ValueError(
                f"{provider_name}: 动态阈值 provider 初始化异常 {type(exc).__name__}: {exc}"
            ) from exc
        if not initialized:
            raise ValueError(f"{provider_name}: 动态阈值 provider 初始化失败")
    return provider


def _resolve_rule_threshold(
    registry,
    rule: MonitorRule,
    quote: dict,
    *,
    now: datetime,
    db_path,
    previous_state: dict | None = None,
) -> tuple[float, str | None, list[str], float | None]:
    if rule.threshold_mode != "previous_close_ma":
        return rule.resolve_threshold(quote), None, [], None

    window = int(rule.threshold_window or 0)
    basis_dates = previous_open_dates(now.date().isoformat(), window, db_path=db_path)
    if basis_dates is None:
        raise ValueError(f"前 {window} 个开放日历缺失或不可读")

    try:
        quote_pre_close = float(quote.get("pre_close"))
    except (TypeError, ValueError) as exc:
        raise ValueError("实时前收盘价无法用于均线坐标锚定") from exc
    if not math.isfinite(quote_pre_close) or quote_pre_close <= 0:
        raise ValueError("实时前收盘价非法")

    previous = previous_state or {}
    cached_dates = previous.get("threshold_basis_dates")
    cached_threshold = previous.get("last_threshold")
    cached_anchor = previous.get("threshold_anchor_pre_close")
    try:
        cached_anchor_value = float(cached_anchor)
    except (TypeError, ValueError):
        cached_anchor_value = float("nan")
    if cached_dates == basis_dates and math.isclose(
        cached_anchor_value,
        quote_pre_close,
        rel_tol=0.0,
        abs_tol=1e-6,
    ):
        try:
            cached_value = float(cached_threshold)
        except (TypeError, ValueError):
            cached_value = float("nan")
        if math.isfinite(cached_value) and cached_value > 0:
            return (
                cached_value,
                str(previous.get("threshold_source") or "cached_previous_close_ma"),
                basis_dates,
                quote_pre_close,
            )

    provider_name = str(rule.threshold_provider or "").strip()
    _initialized_provider(registry, provider_name)
    start_date, end_date = basis_dates[0], basis_dates[-1]
    daily_result = registry.call_specific(
        provider_name,
        "get_stock_daily_range",
        rule.code,
        start_date,
        end_date,
    )
    if not daily_result.success:
        raise ValueError(f"历史日线失败: {daily_result.error or '未知错误'}")
    factor_result = registry.call_specific(
        provider_name,
        "get_stock_adj_factor_range",
        rule.code,
        start_date,
        end_date,
    )
    if not factor_result.success:
        raise ValueError(f"复权因子失败: {factor_result.error or '未知错误'}")
    bars = _exact_history_rows(
        daily_result.data,
        basis_dates,
        value_key="close",
        label="历史日线",
    )
    factors = _exact_history_rows(
        factor_result.data,
        basis_dates,
        value_key="adj_factor",
        label="复权因子",
    )
    adjusted = apply_qfq(bars, factors, keys=("close",))
    if adjusted is None:
        raise ValueError("历史日线与复权因子无法按开放日对齐")
    try:
        adjusted_last_close = float(adjusted[-1]["close"])
    except (TypeError, ValueError, IndexError, KeyError) as exc:
        raise ValueError("实时前收盘价无法用于均线坐标锚定") from exc
    if (
        not math.isfinite(adjusted_last_close)
        or adjusted_last_close <= 0
    ):
        raise ValueError("实时前收盘价或历史末日收盘价非法")
    # 以前一开放日为 T 的前复权序列仍可能漏掉“今天恰为除权日”的坐标变化。
    # 新浪实时 pre_close 是今天盘口采用的官方前收盘参考价，用它把整段历史再锚到
    # 当前实时价格坐标，避免除权日把正常价格跳空误报成跌破 MA5。
    coordinate_scale = quote_pre_close / adjusted_last_close
    historical_closes = [float(row["close"]) * coordinate_scale for row in adjusted]
    threshold = rule.resolve_threshold(
        quote,
        historical_closes=historical_closes,
    )
    source = (
        f"{daily_result.source}+{factor_result.source}+"
        f"{rule.provider}:pre_close_anchor"
    )
    return threshold, source, basis_dates, quote_pre_close


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
    db_path,
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
            value = rule.resolve_value(quote)
        except (TypeError, ValueError) as exc:
            errors.append(f"{rule.rule_id}: {exc}")
            continue
        previous = rule_states.get(rule.rule_id) or {}
        same_trade_date = previous.get("trade_date") == now.date().isoformat()
        try:
            (
                threshold,
                threshold_source,
                threshold_basis_dates,
                threshold_anchor_pre_close,
            ) = _resolve_rule_threshold(
                registry,
                rule,
                quote,
                now=now,
                db_path=db_path,
                previous_state=previous if same_trade_date else None,
            )
        except (TypeError, ValueError) as exc:
            errors.append(f"{rule.rule_id}: {exc}")
            continue
        if not math.isfinite(threshold) or (
            rule.value_mode == "price" and threshold <= 0
        ):
            errors.append(f"{rule.rule_id}: 比较阈值非法")
            continue
        previous_active = (
            bool(previous.get("active"))
            if same_trade_date
            else None
        )
        active = rule.is_active(value, threshold=threshold)
        local_time = now.time().replace(tzinfo=None)
        quote_time = quoted_at.time().replace(tzinfo=None)
        close_phase = local_time >= time(15, 0)
        if close_phase and quote_time < CLOSING_SNAPSHOT_MIN_QUOTE_TIME:
            errors.append(f"{rule.rule_id}: 收盘行情尚未就绪（行情时间 {quote_time.isoformat()}）")
            continue
        closing_snapshot = (
            rule.threshold_mode == "daily_up_limit"
            and close_phase
            and quote_time >= CLOSING_SNAPSHOT_MIN_QUOTE_TIME
        )
        valid_quote_codes.add(rule.code.upper())
        close_confirmed_before = bool(previous.get("close_confirmed")) if same_trade_date else False
        transition_alert = should_emit(
            previous_active=previous_active,
            current_active=active,
            emit_on_initial_match=rule.emit_on_initial_match,
        )
        close_confirmation_alert = active and closing_snapshot and not close_confirmed_before
        close_event_recorded = False
        if transition_alert or close_confirmation_alert:
            event = _event(
                rule,
                quote,
                quoted_at,
                str(quote.get("_source") or rule.provider),
                value=value,
                threshold=threshold,
                threshold_source=threshold_source,
                threshold_basis_dates=threshold_basis_dates,
                threshold_anchor_pre_close=threshold_anchor_pre_close,
                observation_phase="close" if closing_snapshot else "intraday",
            )
            if event["event_id"] not in sent_ids and event["event_id"] not in pending_ids:
                events.append(event)
                pending_ids.add(event["event_id"])
                close_event_recorded = closing_snapshot
            elif closing_snapshot:
                close_event_recorded = True
        rule_states[rule.rule_id] = {
            "trade_date": today,
            "active": active,
            "last_price": price,
            "last_value": value,
            "last_threshold": threshold,
            "threshold_source": threshold_source,
            "threshold_basis_dates": threshold_basis_dates,
            "threshold_anchor_pre_close": threshold_anchor_pre_close,
            "last_quote_at": quoted_at.isoformat(),
            "updated_at": now.isoformat(),
            "close_confirmed": close_confirmed_before or close_event_recorded,
        }

    if not valid_quote_codes:
        if dry_run:
            return {"status": "source_failed", "events": [], "errors": errors}
        # 已确认并落盘的 pending 不依赖本轮行情继续成立；数据源故障时仍应重试
        # 钉钉，否则“首轮推送失败 + 后续行情源失败”会让事件跨日静默过期。
        save_state(state_path, state)
        pending_before_send = len(state.get("pending_events") or [])
        push_ok, push_error = _send_pending(state, pusher_factory)
        save_state(state_path, state)
        status = "source_failed"
        if not push_ok:
            status = "push_failed"
            errors.append(push_error or "钉钉发送失败")
        return {
            "status": status,
            "events": [],
            "errors": errors,
            "quotes_checked": 0,
            "pending_count": len(state.get("pending_events") or []),
            "pushed": push_ok and pending_before_send > 0,
        }

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
    effective_rules = tuple(rule for rule in rules if rule.is_effective_on(local_now.date()))
    if not effective_rules:
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
            "status": "no_active_rules",
            "events": [],
            "errors": [],
            "rules_configured": len(rules),
            "rules_checked": 0,
            "quotes_checked": 0,
            "pushed": False,
            "retired_pending_count": retired_pending_count,
        }
    close_finalization = is_close_finalization_window(local_now)
    if not is_intraday_session(local_now) and not close_finalization:
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
            rules=effective_rules,
            now=local_now,
            state=state,
            state_path=path,
            db_path=db_path,
            dry_run=True,
            pusher_factory=pusher_factory,
        )

    try:
        with locked_state(path) as state:
            return _run_locked(
                registry=registry,
                rules=effective_rules,
                now=local_now,
                state=state,
                state_path=path,
                db_path=db_path,
                dry_run=False,
                pusher_factory=pusher_factory,
            )
    except (OSError, ValueError) as exc:
        return {"status": "state_error", "events": [], "errors": [str(exc)]}


def run_all_checks(registry, *, dry_run: bool = False) -> dict:
    """统一执行单标的阈值与全市场横截面监控，并保留兼容的顶层回执。"""
    from .market_scan import run_market_scan

    threshold_result = run_check(registry, dry_run=dry_run)
    market_result = run_market_scan(registry, dry_run=dry_run)
    failing = {"blocked_calendar", "source_failed", "partial", "push_failed", "state_error"}
    failures = [
        result["status"]
        for result in (threshold_result, market_result)
        if result.get("status") in failing
    ]
    if failures:
        unique_failures = set(failures)
        status = failures[0] if len(unique_failures) == 1 else "partial"
    elif dry_run and any(
        result.get("status") == "dry_run" for result in (threshold_result, market_result)
    ):
        status = "dry_run"
    else:
        # 窗口外/非交易日属于预期无动作；优先保留阈值监控原有状态语义。
        status = str(threshold_result.get("status") or market_result.get("status") or "complete")
    events = list(threshold_result.get("events") or []) + list(market_result.get("events") or [])
    errors = [
        f"threshold: {error}" for error in threshold_result.get("errors") or []
    ] + [f"market_scan: {error}" for error in market_result.get("errors") or []]
    return {
        "status": status,
        "events": events,
        "errors": errors,
        "quotes_checked": int(threshold_result.get("quotes_checked") or 0)
        + int(market_result.get("quotes_checked") or 0),
        "pending_count": int(threshold_result.get("pending_count") or 0)
        + int(market_result.get("pending_count") or 0),
        "pushed": bool(threshold_result.get("pushed") or market_result.get("pushed")),
        "threshold_monitor": threshold_result,
        "market_scan": market_result,
    }


def run_e2e_test(
    registry,
    *,
    input_by: str,
    confirm_real_push: bool = False,
    rule: MonitorRule | None = None,
    now: datetime | None = None,
    db_path=None,
    pusher_factory: Callable[[], object] | None = None,
) -> dict:
    """用真实新鲜行情发送一条明确标注测试的消息，不读写正式监控状态。"""
    normalized_input_by = str(input_by or "").strip()
    if not normalized_input_by:
        return {"status": "invalid_input", "events": [], "errors": ["--input-by 不能为空"]}
    if confirm_real_push is not True:
        return {
            "status": "authorization_required",
            "events": [],
            "errors": ["真实钉钉链路测试必须显式传 --confirm-real-push"],
            "pushed": False,
        }
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
    if not rule.is_effective_on(local_now.date()):
        return {
            "status": "inactive_rule",
            "events": [],
            "errors": [f"规则 {rule.rule_id} 在 {local_now.date().isoformat()} 不生效"],
            "pushed": False,
        }
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

    try:
        value = rule.resolve_value(quote)
    except (TypeError, ValueError) as exc:
        errors.append(f"{rule.rule_id}: {exc}")
        return {"status": "source_failed", "events": [], "errors": errors}
    margin = max(E2E_TEST_MARGIN_MIN, abs(value) * E2E_TEST_MARGIN_RATIO)
    try:
        (
            production_threshold,
            production_threshold_source,
            production_basis_dates,
            production_anchor_pre_close,
        ) = _resolve_rule_threshold(
            registry,
            rule,
            quote,
            now=local_now,
            db_path=db_path,
        )
    except (TypeError, ValueError) as exc:
        errors.append(f"{rule.rule_id}: {exc}")
        return {"status": "source_failed", "events": [], "errors": errors}

    test_rule = MonitorRule(
        rule_id=f"{rule.rule_id}-e2e-test",
        instrument_name=rule.instrument_name,
        code=rule.code,
        threshold=value + margin,
        direction="below",
        provider=rule.provider,
        value_label=rule.value_label,
        value_unit=rule.value_unit,
        value_mode=rule.value_mode,
    )
    if not test_rule.is_active(value):
        return {"status": "test_condition_failed", "events": [], "errors": ["临时测试条件未命中"]}
    event = _event(
        test_rule,
        quote,
        quoted_at,
        str(quote.get("_source") or rule.provider),
        value=value,
        threshold=float(test_rule.threshold),
    )

    if pusher_factory is None:
        from pushers.dingtalk_pusher import DingTalkPusher

        pusher_factory = lambda: DingTalkPusher(config={})
    try:
        pusher = pusher_factory()
        if not pusher.initialize():
            return {"status": "push_failed", "events": [event], "errors": ["钉钉凭据未配置"], "pushed": False}
        content = render_e2e_test_alert(
            event,
            production_threshold=production_threshold,
            production_threshold_mode=rule.threshold_mode,
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
        "production_threshold": production_threshold,
        "production_threshold_source": production_threshold_source,
        "production_threshold_basis_dates": production_basis_dates,
        "production_threshold_anchor_pre_close": production_anchor_pre_close,
        "input_by": normalized_input_by,
    }
