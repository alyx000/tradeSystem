"""月线指标日频监控的本地快照、差异事件与推送账本。

本模块只消费 :mod:`indicator_watch_service` 已经计算好的只读 summary：

- 不连接、迁移或写入 SQLite；
- 只有同一 ``seed_month_end`` 的 ``complete -> complete`` 才比较股票状态；
- ``partial`` / ``blocked`` 只产生运行健康事件，不推进股票基线；
- 首个完整快照和完成月翻页只建基线，不制造批量进出事件；
- 推送前先原子落本地 pending 账本，发送成功后才记 sent，失败下次重试。

本地文件属于运行审计产物，不是 ``monthly_pattern_pool``、关注池或计划层。
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
import uuid
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping
from zoneinfo import ZoneInfo

from pushers.dingtalk_pusher import DingTalkPusher


STATE_SCHEMA_VERSION = 1
LOGIC_VERSION = 1
MAX_LEDGER_IDS = 20_000
PUSH_CHUNK_SIZE = 30
PUSH_MAX_BYTES = 18_000

_CODE_RE = re.compile(r"^\d{6}$")
_RUN_STATUSES = {"complete", "partial", "blocked"}
_BUCKETS = {
    "candidate",
    "waiting_reclaim",
    "indeterminate",
    "data_blocked",
    "excluded_st",
}
_STAGES = {
    "resonance_observed",
    "daily_reactivated",
    "monthly_seeded",
    "insufficient_history",
    "blocked",
    "excluded_st",
}
_BUCKET_SOURCES = (
    ("candidate", "candidates"),
    ("waiting_reclaim", "waiting_monthly_reclaim"),
    ("indeterminate", "indeterminate_current_month_ma5"),
    ("data_blocked", "data_issues"),
    ("excluded_st", "st_excluded_items"),
)

_EVENT_LABELS = {
    "run_blocked": "运行被关键事实阻断",
    "run_block_reason_changed": "运行阻断原因变化",
    "run_degraded": "运行覆盖降级为 partial",
    "run_degradation_changed": "partial 覆盖缺口变化",
    "run_recovered": "运行覆盖恢复完整",
    "seed_month_rolled": "完成月种子翻页并重建基线",
    "baseline_initialized": "初始化完整快照基线",
    "historical_snapshot_ignored": "历史快照仅作审计，未倒退状态水位",
    "same_day_state_revised": "同日状态事实发生修订",
    "same_day_evidence_revised": "同日展示证据修订，状态未变化",
    "seed_entered_scope": "进入本月完整种子观察范围",
    "seed_exited_scope": "离开本月完整种子观察范围",
    "st_excluded": "目标日 ST 身份成立，暂停技术观察",
    "st_restored": "目标日 ST 身份解除，恢复技术观察",
    "monthly_ma5_lost": "目标日动态 5 月线支撑由成立转为不成立",
    "monthly_ma5_reclaimed": "目标日重新站回动态 5 月线",
    "state_became_indeterminate": "动态 5 月线状态转为不可判",
    "state_determination_recovered": "动态 5 月线状态恢复可判",
    "daily_reactivated_observed": "日线零轴重回状态已观察",
    "daily_reactivation_lost": "日线零轴重回状态不再成立",
    "resonance_observed": "日周零上运行共振已观察",
    "resonance_lost": "日周零上运行共振不再成立",
    "daily_macd_above_zero_entered": "日线 MACD 双线进入零轴上方",
    "daily_macd_above_zero_exited": "日线 MACD 双线离开零轴上方",
    "daily_macd_bullish_entered": "日线 MACD 进入零上向上运行",
    "daily_macd_bullish_exited": "日线 MACD 离开零上向上运行",
    "weekly_macd_above_zero_entered": "周线 MACD 双线进入零轴上方",
    "weekly_macd_above_zero_exited": "周线 MACD 双线离开零轴上方",
    "weekly_macd_bullish_entered": "周线 MACD 进入零上向上运行",
    "weekly_macd_bullish_exited": "周线 MACD 离开零上向上运行",
}


class MonitorDailyError(RuntimeError):
    """日频状态层校验或持久化失败。"""


def _canonical_json(payload: Any) -> str:
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise MonitorDailyError(f"监控快照不是严格 JSON: {exc}") from exc


def _hash_payload(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _bare_code(raw: Any) -> str:
    text = str(raw or "").strip().upper().split(".")[0]
    if _CODE_RE.fullmatch(text) is None:
        raise MonitorDailyError(f"股票代码非法: {raw!r}")
    return text


def _optional_bool(value: Any, *, label: str) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    raise MonitorDailyError(f"{label} 必须为 bool 或 null")


def _daily_evidence(item: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = item.get("daily_evidence")
    return payload if isinstance(payload, Mapping) else {}


def _macd_payload(
    daily: Mapping[str, Any],
    primary: str,
    fallback: str,
) -> Mapping[str, Any]:
    payload = daily.get(primary) or daily.get(fallback) or {}
    return payload if isinstance(payload, Mapping) else {}


def _reason_code(
    item: Mapping[str, Any],
    *,
    bucket: str,
    daily: Mapping[str, Any],
) -> str | None:
    if bucket == "data_blocked":
        source = item.get("source")
        if isinstance(source, Mapping):
            return str(source.get("status") or source.get("error") or "blocked")
        return str(item.get("error") or item.get("stage") or "blocked")
    if bucket == "excluded_st":
        return "target_date_st"
    value = daily.get("reason") or item.get("reason")
    return str(value) if value else None


def _reason_detail(
    item: Mapping[str, Any],
    *,
    bucket: str,
    daily: Mapping[str, Any],
) -> str | None:
    if bucket == "data_blocked":
        source = item.get("source")
        source_error = (
            source.get("error")
            if isinstance(source, Mapping)
            else None
        )
        value = item.get("error") or source_error
    else:
        value = daily.get("reason") or item.get("reason")
    return str(value) if value else None


def _normalize_stock(
    item: Mapping[str, Any],
    *,
    bucket: str,
    target_date: str,
) -> dict[str, Any]:
    code = _bare_code(item.get("stock_code"))
    daily = _daily_evidence(item)
    dynamic = daily.get("dynamic_monthly_ma5")
    dynamic = dynamic if isinstance(dynamic, Mapping) else {}
    daily_macd = _macd_payload(daily, "daily_macd", "current_daily")
    weekly_macd = _macd_payload(daily, "weekly_macd", "current_weekly")

    evidence_date = daily.get("target_date")
    if evidence_date is not None and str(evidence_date) != target_date:
        raise MonitorDailyError(
            f"{code} 日线证据日期错位: {evidence_date} != {target_date}"
        )

    if bucket == "excluded_st":
        stage = "excluded_st"
        support_state = None
    elif bucket == "data_blocked":
        stage = "blocked"
        support_state = None
    else:
        stage = str(item.get("stage") or "")
        if stage not in _STAGES - {"blocked", "excluded_st"}:
            raise MonitorDailyError(f"{code} 未知观察阶段: {stage!r}")
        support_state = _optional_bool(
            dynamic.get("support_held"),
            label=f"{code}.support_held",
        )
        expected_support = {
            "candidate": True,
            "waiting_reclaim": False,
            "indeterminate": None,
        }[bucket]
        if support_state is not expected_support:
            raise MonitorDailyError(
                f"{code} bucket={bucket} 与 support_held={support_state!r} 不一致"
            )

    return {
        "stock_code": code,
        "stock_name": str(item.get("stock_name") or ""),
        "bucket": bucket,
        "stage": stage,
        "support_state": support_state,
        "reason_code": _reason_code(item, bucket=bucket, daily=daily),
        "reason_detail": _reason_detail(
            item,
            bucket=bucket,
            daily=daily,
        ),
        "reentry_date": (
            str(daily.get("reentry_date") or daily.get("first_reentry_date"))
            if daily.get("reentry_date") or daily.get("first_reentry_date")
            else None
        ),
        "daily_above_zero": _optional_bool(
            daily_macd.get("above_zero"),
            label=f"{code}.daily_above_zero",
        ),
        "daily_bullish_on_zero": _optional_bool(
            daily_macd.get("bullish_on_zero"),
            label=f"{code}.daily_bullish_on_zero",
        ),
        "weekly_above_zero": _optional_bool(
            weekly_macd.get("above_zero"),
            label=f"{code}.weekly_above_zero",
        ),
        "weekly_bullish_on_zero": _optional_bool(
            weekly_macd.get("bullish_on_zero"),
            label=f"{code}.weekly_bullish_on_zero",
        ),
    }


def _state_stock(stock: Mapping[str, Any]) -> dict[str, Any]:
    """返回会触发状态事件的稳定字段；展示名称和数值证据不进入状态哈希。"""
    keys = (
        "stock_code",
        "bucket",
        "stage",
        "support_state",
        "reason_code",
        "reentry_date",
        "daily_above_zero",
        "daily_bullish_on_zero",
        "weekly_above_zero",
        "weekly_bullish_on_zero",
    )
    return {key: stock.get(key) for key in keys}


def normalize_summary(summary: Mapping[str, Any]) -> dict[str, Any]:
    """把 monitor summary 归一为可确定性 diff 的 canonical snapshot。"""
    status = str(summary.get("status") or "")
    if status not in _RUN_STATUSES:
        raise MonitorDailyError(f"未知运行状态: {status!r}")
    target_date = str(summary.get("target_date") or "")
    try:
        date.fromisoformat(target_date)
    except ValueError as exc:
        raise MonitorDailyError(f"target_date 非法: {target_date!r}") from exc

    seed_month_end = summary.get("seed_month_end")
    if status != "blocked" and not seed_month_end:
        raise MonitorDailyError("非 blocked 快照缺少 seed_month_end")
    seed_month_end = str(seed_month_end) if seed_month_end else None
    if seed_month_end is not None:
        try:
            date.fromisoformat(seed_month_end)
        except ValueError as exc:
            raise MonitorDailyError(
                f"seed_month_end 非法: {seed_month_end!r}"
            ) from exc
        if seed_month_end > target_date:
            raise MonitorDailyError(
                "seed_month_end 不得晚于 target_date: "
                f"{seed_month_end} > {target_date}"
            )

    stocks: dict[str, dict[str, Any]] = {}
    for bucket, source_key in _BUCKET_SOURCES:
        raw_items = summary.get(source_key) or []
        if not isinstance(raw_items, list):
            raise MonitorDailyError(f"{source_key} 必须为 list")
        for raw in raw_items:
            if not isinstance(raw, Mapping):
                raise MonitorDailyError(f"{source_key} 存在非法行")
            stock = _normalize_stock(
                raw,
                bucket=bucket,
                target_date=target_date,
            )
            code = stock["stock_code"]
            if code in stocks:
                raise MonitorDailyError(
                    f"{code} 同时出现在 {stocks[code]['bucket']} 与 {bucket}"
                )
            stocks[code] = stock

    counts = summary.get("counts") or {}
    if not isinstance(counts, Mapping):
        raise MonitorDailyError("counts 必须为 mapping")
    truncated = counts.get("monthly_seed_truncated", 0)
    if type(truncated) is not int or truncated != 0:
        raise MonitorDailyError("生产日频事件禁止截断 monthly seeds")
    seed_total = counts.get("monthly_seed_total", 0)
    if type(seed_total) is not int or seed_total < 0:
        raise MonitorDailyError("monthly_seed_total 非法")
    if status != "blocked" and len(stocks) != seed_total:
        raise MonitorDailyError(
            "月线种子身份不守恒: "
            f"normalized={len(stocks)} monthly_seed_total={seed_total}"
        )

    ordered_stocks = {
        code: stocks[code]
        for code in sorted(stocks)
    }
    state_payload = {
        "schema_version": STATE_SCHEMA_VERSION,
        "logic_version": LOGIC_VERSION,
        "seed_month_end": seed_month_end,
        "stocks": [
            _state_stock(ordered_stocks[code])
            for code in ordered_stocks
        ],
    }
    health_payload = {
        "status": status,
        "error": summary.get("error"),
        "source_status": summary.get("source_status") or {},
        "coverage": {
            key: counts.get(key, 0)
            for key in (
                "blocked",
                "daily_blocked",
                "daily_insufficient",
                "indeterminate_current_month_ma5",
                "monthly_seed_truncated",
            )
        },
        "blocking_counts": {
            str(key): value
            for key, value in sorted(counts.items())
            if str(key).startswith("blocked")
        },
        "stock_issues": [
            {
                "stock_code": stock["stock_code"],
                "bucket": stock["bucket"],
                "stage": stock["stage"],
                "reason_code": stock["reason_code"],
                "reason_detail": stock["reason_detail"],
            }
            for stock in ordered_stocks.values()
            if (
                stock["bucket"] in {"data_blocked", "indeterminate"}
                or stock["stage"] == "insufficient_history"
            )
        ],
    }
    evidence_hash = _hash_payload(summary)
    state_hash = _hash_payload(state_payload) if status == "complete" else None
    health_fingerprint = _hash_payload(health_payload)
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "logic_version": LOGIC_VERSION,
        "target_date": target_date,
        "seed_month_end": seed_month_end,
        "run_status": status,
        "health_fingerprint": health_fingerprint,
        "state_hash": state_hash,
        "evidence_hash": evidence_hash,
        "stocks": ordered_stocks,
        "counts": dict(counts),
        "error": summary.get("error"),
    }


def _new_state() -> dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "logic_version": LOGIC_VERSION,
        "last_complete": None,
        "last_health": None,
        "pending_events": [],
        "sent_event_ids": [],
        "suppressed_event_ids": [],
        "updated_at": None,
    }


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _new_state()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MonitorDailyError(f"状态账本无法读取: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise MonitorDailyError("状态账本根节点必须为 object")
    if payload.get("schema_version") != STATE_SCHEMA_VERSION:
        raise MonitorDailyError(
            "状态账本 schema_version 不兼容: "
            f"{payload.get('schema_version')!r}"
        )
    if payload.get("logic_version") != LOGIC_VERSION:
        raise MonitorDailyError(
            "状态账本 logic_version 不兼容: "
            f"{payload.get('logic_version')!r}"
        )
    for key in ("pending_events", "sent_event_ids", "suppressed_event_ids"):
        if not isinstance(payload.get(key), list):
            raise MonitorDailyError(f"状态账本 {key} 必须为 list")
    return payload


def _event(
    event_type: str,
    current: Mapping[str, Any],
    *,
    stock: Mapping[str, Any] | None = None,
    before: Any = None,
    after: Any = None,
    notify: bool = True,
) -> dict[str, Any]:
    code = str((stock or {}).get("stock_code") or "") or None
    identity = {
        "schema_version": STATE_SCHEMA_VERSION,
        "logic_version": LOGIC_VERSION,
        "event_type": event_type,
        "target_date": current.get("target_date"),
        "seed_month_end": current.get("seed_month_end"),
        "stock_code": code,
        "before": before,
        "after": after,
        "current_state_hash": current.get("state_hash"),
    }
    return {
        "event_id": _hash_payload(identity),
        "event_type": event_type,
        "target_date": current.get("target_date"),
        "seed_month_end": current.get("seed_month_end"),
        "stock_code": code,
        "stock_name": str((stock or {}).get("stock_name") or ""),
        "before": before,
        "after": after,
        "notify": notify,
    }


def _health_events(
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any],
) -> list[dict[str, Any]]:
    status = current["run_status"]
    if previous is None:
        if status == "blocked":
            return [
                _event(
                    "run_blocked",
                    current,
                    after=current.get("health_fingerprint"),
                )
            ]
        if status == "partial":
            return [
                _event(
                    "run_degraded",
                    current,
                    after=current.get("health_fingerprint"),
                )
            ]
        return []

    previous_status = previous.get("run_status")
    if status == previous_status:
        if current["health_fingerprint"] == previous.get("health_fingerprint"):
            return []
        if status == "blocked":
            event_type = "run_block_reason_changed"
        elif status == "partial":
            event_type = "run_degradation_changed"
        else:
            return []
        return [
            _event(
                event_type,
                current,
                before=previous.get("health_fingerprint"),
                after=current.get("health_fingerprint"),
            )
        ]
    if status == "blocked":
        event_type = "run_blocked"
    elif status == "partial":
        event_type = "run_degraded"
    else:
        event_type = "run_recovered"
    return [
        _event(
            event_type,
            current,
            before=previous_status,
            after=status,
        )
    ]


def _bool_transition_event(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
    *,
    snapshot: Mapping[str, Any],
    field: str,
    entered: str,
    exited: str,
) -> dict[str, Any] | None:
    before = previous.get(field)
    after = current.get(field)
    if before is after:
        return None
    if before is False and after is True:
        event_type = entered
    elif before is True and after is False:
        event_type = exited
    else:
        return None
    return _event(
        event_type,
        snapshot,
        stock=current,
        before=before,
        after=after,
    )


def _stock_transition_events(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
    *,
    snapshot: Mapping[str, Any],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    before_bucket = previous.get("bucket")
    after_bucket = current.get("bucket")
    if before_bucket != after_bucket and (
        before_bucket == "excluded_st" or after_bucket == "excluded_st"
    ):
        event_type = (
            "st_excluded"
            if after_bucket == "excluded_st"
            else "st_restored"
        )
        return [
            _event(
                event_type,
                snapshot,
                stock=current,
                before=before_bucket,
                after=after_bucket,
            )
        ]

    before_support = previous.get("support_state")
    after_support = current.get("support_state")
    if before_support is not after_support:
        if before_support is True and after_support is False:
            events.append(
                _event(
                    "monthly_ma5_lost",
                    snapshot,
                    stock=current,
                    before=True,
                    after=False,
                )
            )
        elif before_support is False and after_support is True:
            events.append(
                _event(
                    "monthly_ma5_reclaimed",
                    snapshot,
                    stock=current,
                    before=False,
                    after=True,
                )
            )
        elif after_support is None:
            events.append(
                _event(
                    "state_became_indeterminate",
                    snapshot,
                    stock=current,
                    before=before_support,
                    after=None,
                )
            )
        elif before_support is None:
            events.append(
                _event(
                    "state_determination_recovered",
                    snapshot,
                    stock=current,
                    before=None,
                    after=after_support,
                )
            )

    before_stage = previous.get("stage")
    after_stage = current.get("stage")
    if before_stage != after_stage:
        if after_stage == "resonance_observed":
            event_type = "resonance_observed"
        elif before_stage == "resonance_observed":
            event_type = "resonance_lost"
        elif after_stage == "daily_reactivated":
            event_type = "daily_reactivated_observed"
        elif before_stage == "daily_reactivated":
            event_type = "daily_reactivation_lost"
        else:
            event_type = None
        if event_type:
            events.append(
                _event(
                    event_type,
                    snapshot,
                    stock=current,
                    before=before_stage,
                    after=after_stage,
                )
            )

    bool_transitions = (
        (
            "daily_above_zero",
            "daily_macd_above_zero_entered",
            "daily_macd_above_zero_exited",
        ),
        (
            "daily_bullish_on_zero",
            "daily_macd_bullish_entered",
            "daily_macd_bullish_exited",
        ),
        (
            "weekly_above_zero",
            "weekly_macd_above_zero_entered",
            "weekly_macd_above_zero_exited",
        ),
        (
            "weekly_bullish_on_zero",
            "weekly_macd_bullish_entered",
            "weekly_macd_bullish_exited",
        ),
    )
    for field, entered, exited in bool_transitions:
        event = _bool_transition_event(
            previous,
            current,
            snapshot=snapshot,
            field=field,
            entered=entered,
            exited=exited,
        )
        if event:
            events.append(event)
    return events


def diff_complete_snapshots(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """比较同一完成月、不同目标日的两个完整快照。"""
    if previous.get("run_status") != "complete":
        raise MonitorDailyError("previous 必须为 complete")
    if current.get("run_status") != "complete":
        raise MonitorDailyError("current 必须为 complete")
    if previous.get("seed_month_end") != current.get("seed_month_end"):
        raise MonitorDailyError("跨完成月禁止执行股票状态 diff")
    previous_stocks = previous.get("stocks") or {}
    current_stocks = current.get("stocks") or {}
    events: list[dict[str, Any]] = []
    for code in sorted(set(previous_stocks) | set(current_stocks)):
        before = previous_stocks.get(code)
        after = current_stocks.get(code)
        if before is None:
            events.append(
                _event(
                    "seed_entered_scope",
                    current,
                    stock=after,
                    before=None,
                    after=_state_stock(after),
                )
            )
        elif after is None:
            events.append(
                _event(
                    "seed_exited_scope",
                    current,
                    stock=before,
                    before=_state_stock(before),
                    after=None,
                )
            )
        else:
            events.extend(
                _stock_transition_events(
                    before,
                    after,
                    snapshot=current,
                )
            )
    return events


def plan_transition(
    state: Mapping[str, Any],
    current: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, dict[str, Any] | None]:
    """规划事件、新 complete 基线和新 health 水位，不执行任何写入。"""
    previous_complete = state.get("last_complete")
    previous_health = state.get("last_health")
    target_date = str(current["target_date"])
    prior_dates = [
        str(payload.get("target_date") or "")
        for payload in (previous_complete, previous_health)
        if isinstance(payload, Mapping)
    ]
    if prior_dates and target_date < max(prior_dates):
        return (
            [
                _event(
                    "historical_snapshot_ignored",
                    current,
                    after=max(prior_dates),
                    notify=False,
                )
            ],
            previous_complete,
            previous_health,
        )

    events = _health_events(previous_health, current)
    next_health = {
        "target_date": target_date,
        "seed_month_end": current.get("seed_month_end"),
        "run_status": current["run_status"],
        "health_fingerprint": current["health_fingerprint"],
    }
    if current["run_status"] != "complete":
        return events, previous_complete, next_health

    if not isinstance(previous_complete, Mapping):
        events.append(
            _event(
                "baseline_initialized",
                current,
                after=current["state_hash"],
                notify=False,
            )
        )
        return events, dict(current), next_health

    previous_seed = previous_complete.get("seed_month_end")
    if previous_seed != current.get("seed_month_end"):
        events.extend(
            [
                _event(
                    "seed_month_rolled",
                    current,
                    before=previous_seed,
                    after=current.get("seed_month_end"),
                ),
                _event(
                    "baseline_initialized",
                    current,
                    after=current["state_hash"],
                    notify=False,
                ),
            ]
        )
        return events, dict(current), next_health

    previous_date = str(previous_complete.get("target_date") or "")
    if target_date == previous_date:
        if current["state_hash"] != previous_complete.get("state_hash"):
            events.append(
                _event(
                    "same_day_state_revised",
                    current,
                    before=previous_complete.get("state_hash"),
                    after=current["state_hash"],
                )
            )
        elif current["evidence_hash"] != previous_complete.get("evidence_hash"):
            events.append(
                _event(
                    "same_day_evidence_revised",
                    current,
                    before=previous_complete.get("evidence_hash"),
                    after=current["evidence_hash"],
                    notify=False,
                )
            )
        return events, dict(current), next_health

    events.extend(diff_complete_snapshots(previous_complete, current))
    return events, dict(current), next_health


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _atomic_write_json(path: Path, payload: Any) -> None:
    _atomic_write_text(path, _canonical_json(payload) + "\n")


def _atomic_create_json(path: Path, payload: Any) -> None:
    """原子创建不可覆盖的审计记录。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(_canonical_json(payload) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise MonitorDailyError(f"审计记录已存在，拒绝覆盖: {path}") from exc
        temporary.unlink()
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


@contextmanager
def _state_lock(state_dir: Path) -> Iterator[None]:
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = state_dir / ".lock"
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _trim_ids(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))[-MAX_LEDGER_IDS:]


def _brief_change(value: Any) -> str:
    if isinstance(value, Mapping):
        return "/".join(
            str(value.get(key))
            for key in ("bucket", "stage", "support_state")
            if value.get(key) is not None
        ) or "状态快照"
    text = str(value)
    if len(text) > 32:
        return text[:16] + "…"
    return text


def _event_line(event: Mapping[str, Any]) -> str:
    event_type = str(event.get("event_type") or "unknown")
    label = _EVENT_LABELS.get(event_type, event_type)
    code = event.get("stock_code")
    name = str(event.get("stock_name") or "")
    identity = f"{name}（{code}）" if code else "运行级"
    before = event.get("before")
    after = event.get("after")
    change = ""
    if before is not None or after is not None:
        change = (
            f"；{_brief_change(before)} → {_brief_change(after)}"
        )
    return (
        f"- {identity}：[事实] {label}{change}"
        f"（目标日 {event.get('target_date') or '—'}）"
    )


def render_push_markdown(events: list[Mapping[str, Any]]) -> str:
    latest = max(str(event.get("target_date") or "") for event in events)
    lines = [
        f"# 月线指标日频监控变化 · {latest}",
        "",
        "> [事实] 仅列出相对上一份完整基线的新变化；"
        "partial/blocked 不会把缺席股票解释为退出。"
        "本消息是观察提醒，不构成具体买卖建议。",
        "",
    ]
    lines.extend(_event_line(event) for event in events)
    lines.extend(
        [
            "",
            "> [事实] 未写 SQLite、月线观察池、关注池、TradeDraft 或 TradePlan。",
        ]
    )
    return "\n".join(lines) + "\n"


def render_daily_report(
    result: Mapping[str, Any],
    monitor_markdown: str,
) -> str:
    events = result.get("events") or []
    lines = [
        f"# 月线指标日频自动监控收据 · {result.get('target_date') or '—'}",
        "",
        "> [事实] 本地每日快照；只有状态变化才进入通知候选。"
        "不构成具体买卖建议。",
        "",
        "## 自动化收据",
        "",
        f"- 运行状态：[事实] {result.get('run_status') or 'unknown'}",
        f"- 处理模式：[事实] {result.get('mode') or 'unknown'}",
        f"- 完成月基线：[事实] {result.get('seed_month_end') or '—'}",
        f"- 本轮事件：[事实] {len(events)}",
        f"- 新通知候选：[事实] {result.get('new_notification_count', 0)}",
        f"- 待重试事件：[事实] {result.get('pending_count', 0)}",
        f"- 本轮成功发送：[事实] {result.get('sent_count', 0)}",
        f"- 推送状态：[事实] {result.get('push_status') or 'not_requested'}",
        f"- JSON 快照：[事实] {result.get('snapshot_path') or 'dry-run 不落文件'}",
        f"- 审计记录：[事实] {result.get('attempt_path') or 'dry-run 不落文件'}",
        "",
        "## 本轮变化",
        "",
    ]
    if events:
        lines.extend(_event_line(event) for event in events)
    else:
        lines.append("- [事实] 无状态变化。")
    lines.extend(
        [
            "",
            "## 写入边界",
            "",
            "- [事实] 只写本地 JSON/Markdown 运行产物与通知去重账本。",
            "- [事实] 不写 SQLite、monthly_pattern_pool、关注池、"
            "TradeDraft 或 TradePlan。",
            "",
            "---",
            "",
            monitor_markdown.rstrip(),
            "",
        ]
    )
    return "\n".join(lines)


def _default_pusher_factory() -> DingTalkPusher:
    return DingTalkPusher(config={})


def _pending_chunks(events: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    ordered = sorted(
        events,
        key=lambda item: (
            str(item.get("target_date") or ""),
            str(item.get("event_id") or ""),
        ),
    )
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for event in ordered:
        candidate = current + [event]
        if (
            current
            and (
                len(candidate) > PUSH_CHUNK_SIZE
                or len(render_push_markdown(candidate).encode("utf-8"))
                > PUSH_MAX_BYTES
            )
        ):
            chunks.append(current)
            current = [event]
        else:
            current = candidate
        if len(render_push_markdown(current).encode("utf-8")) > PUSH_MAX_BYTES:
            raise MonitorDailyError(
                f"单条推送事件超过 {PUSH_MAX_BYTES} bytes: "
                f"{event.get('event_id')}"
            )
    if current:
        chunks.append(current)
    return chunks


def _persist_state(path: Path, state: dict[str, Any], now: datetime) -> None:
    state["updated_at"] = now.isoformat()
    state["sent_event_ids"] = _trim_ids(state["sent_event_ids"])
    state["suppressed_event_ids"] = _trim_ids(state["suppressed_event_ids"])
    _atomic_write_json(path, state)


def process_summary(
    summary: Mapping[str, Any],
    *,
    state_dir: Path,
    report_dir: Path,
    monitor_markdown: str,
    dry_run: bool,
    push: bool,
    push_allowed: bool,
    persist_gate_blocked: bool = False,
    now: datetime | None = None,
    pusher_factory: Callable[[], Any] = _default_pusher_factory,
) -> dict[str, Any]:
    """规划、持久化并按账本发送状态变化。

    ``push=False`` 是操作员明确抑制：本轮新事件记入 suppressed，不会在下一次
    正式运行补发；此前因真实发送失败留下的 pending 仍保留。
    """
    shanghai = ZoneInfo("Asia/Shanghai")
    if now is None:
        now = datetime.now(shanghai)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=shanghai)
    else:
        now = now.astimezone(shanghai)
    current = normalize_summary(summary)
    state_path = state_dir / "state.json"

    def _plan(state: dict[str, Any]) -> tuple[
        list[dict[str, Any]],
        dict[str, Any] | None,
        dict[str, Any] | None,
    ]:
        return plan_transition(state, current)

    preview_mode = (
        "dry-run"
        if dry_run
        else "push-gated"
        if push and not push_allowed and not persist_gate_blocked
        else None
    )
    if preview_mode is not None:
        state = load_state(state_path)
        events, _, _ = _plan(state)
        notification_events = [event for event in events if event["notify"]]
        return {
            "target_date": current["target_date"],
            "seed_month_end": current["seed_month_end"],
            "run_status": current["run_status"],
            "mode": preview_mode,
            "events": events,
            "new_notification_count": len(notification_events),
            "pending_count": len(state["pending_events"]),
            "sent_count": 0,
            "push_status": (
                "dry_run"
                if preview_mode == "dry-run"
                else "gate_blocked"
            ),
            "snapshot_path": None,
            "report_path": None,
            "attempt_path": None,
            "snapshot": current,
            "write_boundary": {
                "local_files": False,
                "database": False,
                "monthly_pattern_pool": False,
                "watchlist": False,
                "trade_draft": False,
                "trade_plan": False,
            },
        }

    with _state_lock(state_dir):
        state = load_state(state_path)
        events, next_complete, next_health = _plan(state)
        state["last_complete"] = next_complete
        state["last_health"] = next_health

        sent_ids = set(state["sent_event_ids"])
        suppressed_ids = set(state["suppressed_event_ids"])
        pending_by_id = {
            event["event_id"]: event
            for event in state["pending_events"]
            if event.get("event_id") not in sent_ids
            and event.get("event_id") not in suppressed_ids
        }
        new_notifications = [
            event
            for event in events
            if event["notify"]
            and event["event_id"] not in sent_ids
            and event["event_id"] not in suppressed_ids
            and event["event_id"] not in pending_by_id
        ]
        can_push = push and push_allowed
        if can_push or (push and persist_gate_blocked):
            for event in new_notifications:
                pending_by_id[event["event_id"]] = event
        else:
            state["suppressed_event_ids"].extend(
                event["event_id"] for event in new_notifications
            )
        state["pending_events"] = list(pending_by_id.values())

        sent_count = 0
        push_status = (
            "not_requested"
            if not push
            else "gate_blocked"
            if not push_allowed
            else "no_changes"
            if not state["pending_events"]
            else "pending"
        )

        snapshot_path = (
            state_dir
            / "snapshots"
            / f"{current['target_date']}.json"
        )
        report_path = report_dir / f"{current['target_date']}.md"
        attempt_id = (
            now.strftime("%Y%m%dT%H%M%S.%f%z")
            + f"-{os.getpid()}-{uuid.uuid4().hex[:12]}"
        )
        attempt_dir = (
            state_dir / "attempts" / current["target_date"]
        )
        planned_attempt_path = (
            attempt_dir / f"{attempt_id}-planned.json"
        )
        final_attempt_path = attempt_dir / f"{attempt_id}-final.json"
        result = {
            "attempt_id": attempt_id,
            "target_date": current["target_date"],
            "seed_month_end": current["seed_month_end"],
            "run_status": current["run_status"],
            "mode": (
                "daily"
                if can_push
                else "push-gated-persisted"
                if push and persist_gate_blocked
                else "no-push"
                if not push
                else "push-gated"
            ),
            "events": events,
            "new_notification_count": len(new_notifications),
            "pending_count": len(state["pending_events"]),
            "sent_count": sent_count,
            "push_status": push_status,
            "push_error": None,
            "snapshot_path": str(snapshot_path),
            "report_path": str(report_path),
            "planned_attempt_path": str(planned_attempt_path),
            "attempt_path": str(final_attempt_path),
            "delivery_record_paths": [],
            "snapshot": current,
            "generated_at": now.isoformat(),
            "write_boundary": {
                "local_files": True,
                "database": False,
                "monthly_pattern_pool": False,
                "watchlist": False,
                "trade_draft": False,
                "trade_plan": False,
            },
        }

        _atomic_create_json(
            planned_attempt_path,
            {
                "schema_version": STATE_SCHEMA_VERSION,
                "logic_version": LOGIC_VERSION,
                "phase": "planned",
                "attempt_id": attempt_id,
                "recorded_at": now.isoformat(),
                "result": result,
                "summary": summary,
            },
        )

        # 先写内容产物，最后以 state.json 作为本轮本地状态提交点；若 state
        # 写失败，下一轮仍从旧基线重算，不会静默吞掉变化。
        _atomic_write_json(
            snapshot_path,
            {
                **result,
                "summary": summary,
            },
        )
        _atomic_write_text(
            report_path,
            render_daily_report(result, monitor_markdown),
        )
        _persist_state(state_path, state, now)

        if can_push and state["pending_events"]:
            pusher = pusher_factory()
            if not pusher.initialize():
                push_status = "credentials_missing"
                result["push_error"] = "DingTalk credentials missing"
            else:
                push_status = "success"
                for chunk_index, chunk in enumerate(
                    _pending_chunks(state["pending_events"]),
                    start=1,
                ):
                    latest = max(
                        str(event.get("target_date") or "")
                        for event in chunk
                    )
                    push_error = None
                    try:
                        ok = pusher.send_markdown(
                            title=f"月线指标日频监控变化 {latest}",
                            content=render_push_markdown(chunk),
                        )
                    except Exception as exc:
                        ok = False
                        push_error = f"{type(exc).__name__}: {exc}"
                    if not ok and push_error is None:
                        push_error = "send_markdown returned false"
                    delivery_path = (
                        attempt_dir
                        / (
                            f"{attempt_id}-delivery-"
                            f"{chunk_index:03d}.json"
                        )
                    )
                    _atomic_create_json(
                        delivery_path,
                        {
                            "schema_version": STATE_SCHEMA_VERSION,
                            "logic_version": LOGIC_VERSION,
                            "phase": "delivery",
                            "attempt_id": attempt_id,
                            "chunk_index": chunk_index,
                            "recorded_at": datetime.now(
                                shanghai
                            ).isoformat(),
                            "status": "sent" if ok else "failed",
                            "error": push_error,
                            "events": chunk,
                        },
                    )
                    result["delivery_record_paths"].append(
                        str(delivery_path)
                    )
                    if not ok:
                        push_status = "failed"
                        result["push_error"] = push_error
                        break
                    chunk_ids = {event["event_id"] for event in chunk}
                    sent_count += len(chunk_ids)
                    state["sent_event_ids"].extend(sorted(chunk_ids))
                    state["pending_events"] = [
                        event
                        for event in state["pending_events"]
                        if event["event_id"] not in chunk_ids
                    ]
                    _persist_state(state_path, state, now)
                if state["pending_events"] and push_status == "success":
                    push_status = "partial"

        result["pending_count"] = len(state["pending_events"])
        result["sent_count"] = sent_count
        result["push_status"] = push_status
        _atomic_write_json(
            snapshot_path,
            {
                **result,
                "summary": summary,
            },
        )
        _atomic_write_text(
            report_path,
            render_daily_report(result, monitor_markdown),
        )
        _atomic_create_json(
            final_attempt_path,
            {
                "schema_version": STATE_SCHEMA_VERSION,
                "logic_version": LOGIC_VERSION,
                "phase": "final",
                "attempt_id": attempt_id,
                "recorded_at": datetime.now(shanghai).isoformat(),
                "result": result,
                "summary": summary,
            },
        )
        return result


__all__ = [
    "LOGIC_VERSION",
    "MonitorDailyError",
    "STATE_SCHEMA_VERSION",
    "diff_complete_snapshots",
    "load_state",
    "normalize_summary",
    "plan_transition",
    "process_summary",
    "render_daily_report",
    "render_push_markdown",
]
