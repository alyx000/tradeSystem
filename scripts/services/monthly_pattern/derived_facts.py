"""由日线与复权因子构造可审计的完成月派生事实。

本模块只做纯计算与验签，不访问 provider、SQLite 或文件系统。调用方负责证明
目标月已经完成，以及 ``certified_no_trade`` 所需的外部宇宙和空源证据。
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from typing import Any

from . import detectors
from utils.qfq import OHLC_PRICE_KEYS, apply_qfq


FORMULA_VERSION = "daily_qfq_month_v1"

_CODE_RE = re.compile(r"^\d{6}$")
_ROW_CODE_RE = re.compile(r"^(?P<code>\d{6})(?:\.(?:SH|SZ|BJ))?$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_PRICE_QUANT = Decimal("0.00000001")
_FACTOR_QUANT = Decimal("0.0000000001")
_FLOW_QUANT = Decimal("0.000001")
_DAILY_VOLUME_TO_MONTHLY = Decimal("100")
_DAILY_AMOUNT_TO_MONTHLY = Decimal("1000")
_BAR_STATUSES = {"full_ohlcv", "close_volume_amount", "source_only_no_raw"}
_FACT_STATUSES = {"certified_bar", "certified_no_trade"}


def _stock_code(value: Any) -> str:
    code = str(value or "").strip()
    if _CODE_RE.fullmatch(code) is None:
        raise ValueError("stock_code must be exactly 6 digits")
    return code


def _iso_date(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field} must be YYYY-MM-DD") from exc
    if parsed.isoformat() != text:
        raise ValueError(f"{field} must be YYYY-MM-DD")
    return text


def _provider_date(value: Any, *, field: str) -> str:
    """接受系统 ISO 日期与 Tushare 行情接口的紧凑日期。"""
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        try:
            return date(
                int(text[:4]),
                int(text[4:6]),
                int(text[6:8]),
            ).isoformat()
        except ValueError as exc:
            raise ValueError(f"{field} must be YYYY-MM-DD or YYYYMMDD") from exc
    return _iso_date(text, field=field)


def _decimal(value: Any, *, field: str, positive: bool = True) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite number")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite number") from exc
    if not number.is_finite():
        raise ValueError(f"{field} must be a finite number")
    if positive and number <= 0:
        raise ValueError(f"{field} must be positive")
    if not positive and number < 0:
        raise ValueError(f"{field} must be non-negative")
    return number


def _quantized(
    value: Any,
    *,
    field: str,
    quantum: Decimal,
    positive: bool = True,
) -> float:
    number = _decimal(value, field=field, positive=positive)
    try:
        quantized = number.quantize(quantum, rounding=ROUND_HALF_EVEN)
    except InvalidOperation as exc:
        raise ValueError(f"{field} cannot be quantized") from exc
    if positive and quantized <= 0:
        raise ValueError(f"{field} rounds to a non-positive value")
    return float(quantized)


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("payload must be canonical JSON data") from exc


def _json_mapping(value: Any, *, field: str) -> dict[str, Any]:
    if value is None or value == "":
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field} must be a JSON object") from exc
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a mapping")
    # Round-trip both rejects NaN/inf/non-JSON objects and removes custom mapping types.
    return json.loads(_canonical_json(dict(value)))


def _payload_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _validated_payload_hash(value: Any) -> str:
    text = str(value or "").strip().lower()
    if _HASH_RE.fullmatch(text) is None:
        raise ValueError("source_payload_hash must be 64 lowercase hex characters")
    return text


def _row_code_matches(row: Mapping[str, Any], stock_code: str, *, label: str) -> None:
    raw = (
        row.get("stock_code")
        or row.get("ts_code")
        or row.get("code")
    )
    if raw is None:
        return
    match = _ROW_CODE_RE.fullmatch(str(raw).strip().upper())
    if match is None or match.group("code") != stock_code:
        raise ValueError(f"{label} stock code does not match {stock_code}")


def _normalize_daily_row(row: Mapping[str, Any], stock_code: str) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        raise ValueError("daily row must be a mapping")
    _row_code_matches(row, stock_code, label="daily")
    trade_date = _iso_date(row.get("trade_date"), field="daily.trade_date")
    raw_volume = row.get("volume", row.get("vol"))
    raw_values = {
        "open": _decimal(row.get("open"), field=f"{trade_date}.open"),
        "high": _decimal(row.get("high"), field=f"{trade_date}.high"),
        "low": _decimal(row.get("low"), field=f"{trade_date}.low"),
        "close": _decimal(row.get("close"), field=f"{trade_date}.close"),
        "volume": _decimal(
            raw_volume,
            field=f"{trade_date}.volume",
            positive=False,
        ),
        "amount": _decimal(
            row.get("amount"),
            field=f"{trade_date}.amount",
            positive=False,
        ),
    }
    normalized = {
        "trade_date": trade_date,
        "open": _quantized(
            row.get("open"), field=f"{trade_date}.open", quantum=_PRICE_QUANT
        ),
        "high": _quantized(
            row.get("high"), field=f"{trade_date}.high", quantum=_PRICE_QUANT
        ),
        "low": _quantized(
            row.get("low"), field=f"{trade_date}.low", quantum=_PRICE_QUANT
        ),
        "close": _quantized(
            row.get("close"), field=f"{trade_date}.close", quantum=_PRICE_QUANT
        ),
        "volume": _quantized(
            raw_volume,
            field=f"{trade_date}.volume",
            quantum=_FLOW_QUANT,
            positive=False,
        ),
        "amount": _quantized(
            row.get("amount"),
            field=f"{trade_date}.amount",
            quantum=_FLOW_QUANT,
            positive=False,
        ),
        "_source_fingerprint": tuple(
            (field, str(value.normalize()))
            for field, value in sorted(raw_values.items())
        ),
    }
    if normalized["high"] < max(normalized["open"], normalized["close"]):
        raise ValueError(f"{trade_date}.high must cover open and close")
    if normalized["low"] > min(normalized["open"], normalized["close"]):
        raise ValueError(f"{trade_date}.low must cover open and close")
    if normalized["high"] < normalized["low"]:
        raise ValueError(f"{trade_date}.high must be at or above low")
    return normalized


def _normalize_factor_row(row: Mapping[str, Any], stock_code: str) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        raise ValueError("factor row must be a mapping")
    _row_code_matches(row, stock_code, label="factor")
    trade_date = _iso_date(row.get("trade_date"), field="factor.trade_date")
    raw_factor = _decimal(
        row.get("adj_factor"),
        field=f"{trade_date}.adj_factor",
    )
    return {
        "trade_date": trade_date,
        "adj_factor": _quantized(
            row.get("adj_factor"),
            field=f"{trade_date}.adj_factor",
            quantum=_FACTOR_QUANT,
        ),
        "_source_fingerprint": str(raw_factor.normalize()),
    }


def _dedupe_by_date(rows: Sequence[dict[str, Any]], *, label: str) -> list[dict[str, Any]]:
    by_date: dict[str, dict[str, Any]] = {}
    for row in rows:
        trade_date = row["trade_date"]
        prior = by_date.get(trade_date)
        if prior is None:
            by_date[trade_date] = row
        elif prior.get("_source_fingerprint") != row.get("_source_fingerprint"):
            raise ValueError(f"{label} duplicate conflict: {trade_date}")
    return [
        {
            field: value
            for field, value in by_date[key].items()
            if field != "_source_fingerprint"
        }
        for key in sorted(by_date)
    ]


def _normalize_raw_monthly(
    row: Mapping[str, Any],
    stock_code: str,
    month: str,
) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        raise ValueError("raw_monthly must be a mapping")
    _row_code_matches(row, stock_code, label="raw_monthly")
    raw_date = row.get("trade_date") or row.get("month_end") or row.get("end_date")
    if raw_date is not None:
        parsed = _provider_date(raw_date, field="raw_monthly.trade_date")
        if parsed[:7] != month:
            raise ValueError("raw_monthly date is outside target month")
    normalized = {
        "open": _quantized(
            row.get("open"), field="raw_monthly.open", quantum=_PRICE_QUANT
        ),
        "high": _quantized(
            row.get("high"), field="raw_monthly.high", quantum=_PRICE_QUANT
        ),
        "low": _quantized(
            row.get("low"), field="raw_monthly.low", quantum=_PRICE_QUANT
        ),
        "close": _quantized(
            row.get("close"), field="raw_monthly.close", quantum=_PRICE_QUANT
        ),
        "volume": _quantized(
            row.get("volume", row.get("vol")),
            field="raw_monthly.volume",
            quantum=_FLOW_QUANT,
            positive=False,
        ),
        "amount": _quantized(
            row.get("amount"),
            field="raw_monthly.amount",
            quantum=_FLOW_QUANT,
            positive=False,
        ),
    }
    if row.get("adj_factor") is None:
        normalized["adj_factor"] = None
    else:
        normalized["adj_factor"] = _quantized(
            row.get("adj_factor"),
            field="raw_monthly.adj_factor",
            quantum=_FACTOR_QUANT,
        )
    return normalized


def _crosscheck_equal(
    field: str,
    derived: float,
    raw: float,
    *,
    trading_days: int,
) -> bool:
    """按 Tushare 两个接口的原生单位与舍入精度核对同一月事实。

    ``daily`` 的 ``vol``/``amount`` 分别是手/千元，而 ``monthly`` 是股/元；
    日频逐行与月频汇总各自会舍入，因此流量字段只允许一个由交易日数约束的
    极小绝对误差。价格仍按 8 位小数严格核对，不能用相对容差掩盖行情冲突。
    """
    if field in {"open", "high", "low", "close"}:
        return math.isclose(derived, raw, rel_tol=0.0, abs_tol=1e-8)
    if field == "volume":
        tolerance = max(32.0, float(trading_days) * 1.0)
    elif field == "amount":
        tolerance = max(64.0, float(trading_days) * 2.0)
    else:  # pragma: no cover - 调用字段为模块内常量
        raise ValueError(f"unsupported crosscheck field: {field}")
    return math.isclose(derived, raw, rel_tol=0.0, abs_tol=tolerance)


def _fact_hash_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    status = str(row.get("fact_status") or "")
    payload: dict[str, Any] = {
        "stock_code": _stock_code(row.get("stock_code")),
        "month_end": _iso_date(row.get("month_end"), field="month_end"),
        "fact_status": status,
        "replacement_reason": str(row.get("replacement_reason") or "").strip(),
        "formula_version": str(row.get("formula_version") or ""),
        "raw_crosscheck_status": str(row.get("raw_crosscheck_status") or ""),
        "source_meta": _json_mapping(
            row.get("source_meta_json", row.get("source_meta")),
            field="source_meta_json",
        ),
        "source_payload_hash": _validated_payload_hash(
            row.get("source_payload_hash")
        ),
    }
    for field in (
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "anchor_adj_factor",
        "trading_days",
        "first_trade_date",
        "last_trade_date",
    ):
        payload[field] = row.get(field)
    return payload


def compute_fact_hash(row: Mapping[str, Any]) -> str:
    """计算 canonical 业务事实哈希；审计请求者、run id 与时间戳不参与。"""
    if not isinstance(row, Mapping):
        raise ValueError("fact row must be a mapping")
    return _payload_hash(_fact_hash_payload(row))


def validate_fact_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """校验派生事实并返回适合 repository 持久化的规范化行。"""
    if not isinstance(row, Mapping):
        raise ValueError("fact row must be a mapping")
    stock_code = _stock_code(row.get("stock_code"))
    month_end = _iso_date(row.get("month_end"), field="month_end")
    status = str(row.get("fact_status") or "")
    if status not in _FACT_STATUSES:
        raise ValueError(f"invalid fact_status: {status}")
    formula_version = str(row.get("formula_version") or "")
    if formula_version != FORMULA_VERSION:
        raise ValueError(f"unsupported formula_version: {formula_version}")
    replacement_reason = str(row.get("replacement_reason") or "").strip()
    if not replacement_reason:
        raise ValueError("replacement_reason is required")
    source_meta = _json_mapping(
        row.get("source_meta_json", row.get("source_meta")),
        field="source_meta_json",
    )
    source_payload_hash = _validated_payload_hash(row.get("source_payload_hash"))
    raw_crosscheck_status = str(row.get("raw_crosscheck_status") or "")

    normalized: dict[str, Any] = {
        "stock_code": stock_code,
        "stock_name": (
            str(row.get("stock_name") or "").strip() or None
        ),
        "month_end": month_end,
        "fact_status": status,
        "replacement_reason": replacement_reason,
        "formula_version": formula_version,
        "raw_crosscheck_status": raw_crosscheck_status,
        "source_meta_json": _canonical_json(source_meta),
        "source_payload_hash": source_payload_hash,
    }
    if status == "certified_bar":
        if raw_crosscheck_status not in _BAR_STATUSES:
            raise ValueError(
                f"invalid raw_crosscheck_status for certified_bar: "
                f"{raw_crosscheck_status}"
            )
        for field in ("open", "high", "low", "close"):
            normalized[field] = _quantized(
                row.get(field), field=field, quantum=_PRICE_QUANT
            )
        for field in ("volume", "amount"):
            normalized[field] = _quantized(
                row.get(field),
                field=field,
                quantum=_FLOW_QUANT,
                positive=False,
            )
        normalized["anchor_adj_factor"] = _quantized(
            row.get("anchor_adj_factor"),
            field="anchor_adj_factor",
            quantum=_FACTOR_QUANT,
        )
        raw_days = row.get("trading_days")
        if isinstance(raw_days, bool):
            raise ValueError("trading_days must be a positive integer")
        try:
            trading_days = int(raw_days)
        except (TypeError, ValueError) as exc:
            raise ValueError("trading_days must be a positive integer") from exc
        if trading_days <= 0 or str(trading_days) != str(raw_days):
            raise ValueError("trading_days must be a positive integer")
        normalized["trading_days"] = trading_days
        first_trade_date = _iso_date(
            row.get("first_trade_date"), field="first_trade_date"
        )
        last_trade_date = _iso_date(
            row.get("last_trade_date"), field="last_trade_date"
        )
        if (
            first_trade_date[:7] != month_end[:7]
            or last_trade_date[:7] != month_end[:7]
            or first_trade_date > last_trade_date
            or last_trade_date > month_end
        ):
            raise ValueError("trade-date bounds must stay inside target month")
        normalized["first_trade_date"] = first_trade_date
        normalized["last_trade_date"] = last_trade_date
        if normalized["high"] < max(normalized["open"], normalized["close"]):
            raise ValueError("high must cover open and close")
        if normalized["low"] > min(normalized["open"], normalized["close"]):
            raise ValueError("low must cover open and close")
        if normalized["high"] < normalized["low"]:
            raise ValueError("high must be at or above low")
    else:
        if raw_crosscheck_status != "certified_no_trade":
            raise ValueError(
                "certified_no_trade requires matching raw_crosscheck_status"
            )
        for field in (
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "anchor_adj_factor",
            "first_trade_date",
            "last_trade_date",
        ):
            if row.get(field) is not None:
                raise ValueError(f"{field} must be null for certified_no_trade")
            normalized[field] = None
        if row.get("trading_days") != 0:
            raise ValueError("trading_days must be 0 for certified_no_trade")
        normalized["trading_days"] = 0

    expected_hash = compute_fact_hash(normalized)
    supplied_hash = str(row.get("fact_hash") or "").strip().lower()
    if _HASH_RE.fullmatch(supplied_hash) is None:
        raise ValueError("fact_hash must be 64 lowercase hex characters")
    if supplied_hash != expected_hash:
        raise ValueError("fact_hash mismatch")
    normalized["fact_hash"] = supplied_hash
    for audit_field in (
        "input_by",
        "run_id",
        "created_at",
        "updated_at",
        "fetched_at",
    ):
        if audit_field in row:
            normalized[audit_field] = row[audit_field]
    return normalized


def _finalize_fact(row: dict[str, Any]) -> dict[str, Any]:
    row["fact_hash"] = compute_fact_hash(row)
    return validate_fact_row(row)


def build_month_fact(
    stock_code: str,
    month_end: str,
    daily_rows: Sequence[Mapping[str, Any]],
    factor_rows: Sequence[Mapping[str, Any]],
    *,
    raw_monthly: Mapping[str, Any] | None = None,
    replacement_reason: str = "shape_unverifiable",
    stock_name: str | None = None,
    source_meta: Mapping[str, Any] | None = None,
    source_payload_hash: str | None = None,
) -> dict[str, Any]:
    """以目标月最后交易日因子为锚，构造一根 certified 前复权月 K。"""
    code = _stock_code(stock_code)
    canonical_month_end = _iso_date(month_end, field="month_end")
    month = canonical_month_end[:7]
    normalized_daily = [_normalize_daily_row(row, code) for row in daily_rows]
    normalized_factors = [
        _normalize_factor_row(row, code) for row in factor_rows
    ]
    daily = _dedupe_by_date(
        [row for row in normalized_daily if row["trade_date"][:7] == month],
        label="daily",
    )
    factors = _dedupe_by_date(
        [row for row in normalized_factors if row["trade_date"][:7] == month],
        label="factor",
    )
    if not daily:
        raise ValueError("target month has no daily rows")
    if daily[-1]["trade_date"] > canonical_month_end:
        raise ValueError("daily row is later than month_end")

    factor_map = {row["trade_date"]: row["adj_factor"] for row in factors}
    missing_factor_dates = [
        row["trade_date"] for row in daily if row["trade_date"] not in factor_map
    ]
    if missing_factor_dates:
        raise ValueError(
            "missing adj_factor for daily dates: "
            + ",".join(missing_factor_dates)
        )
    anchor_date = daily[-1]["trade_date"]
    anchor_factor = factor_map[anchor_date]
    aligned_factors: list[float] = []
    for row in daily:
        factor = factor_map[row["trade_date"]]
        aligned_factors.append(factor)
    adjusted_rows = apply_qfq(daily, factors, keys=OHLC_PRICE_KEYS)
    if adjusted_rows is None:
        raise ValueError("qfq failed despite validated daily/factor alignment")

    monthly = detectors.aggregate_completed_monthly_bars(
        adjusted_rows,
        last_month_complete=True,
    )
    if len(monthly) != 1 or monthly[0].month != month:
        raise ValueError("daily aggregation did not produce exactly one target month")
    bar = monthly[0]
    business = {
        "open": _quantized(bar.open, field="open", quantum=_PRICE_QUANT),
        "high": _quantized(bar.high, field="high", quantum=_PRICE_QUANT),
        "low": _quantized(bar.low, field="low", quantum=_PRICE_QUANT),
        "close": _quantized(bar.close, field="close", quantum=_PRICE_QUANT),
        "volume": _quantized(
            Decimal(str(bar.volume)) * _DAILY_VOLUME_TO_MONTHLY,
            field="volume",
            quantum=_FLOW_QUANT,
            positive=False,
        ),
        "amount": _quantized(
            Decimal(str(bar.amount)) * _DAILY_AMOUNT_TO_MONTHLY,
            field="amount",
            quantum=_FLOW_QUANT,
            positive=False,
        ),
    }
    factor_changed = len(set(aligned_factors)) > 1
    if raw_monthly is None:
        raw_crosscheck_status = "source_only_no_raw"
        crosscheck_fields: list[str] = []
        normalized_raw = None
    else:
        normalized_raw = _normalize_raw_monthly(raw_monthly, code, month)
        crosscheck_fields = (
            ["close", "volume", "amount"]
            if factor_changed
            else ["open", "high", "low", "close", "volume", "amount"]
        )
        mismatched = [
            field
            for field in crosscheck_fields
            if not _crosscheck_equal(
                field,
                business[field],
                normalized_raw[field],
                trading_days=len(daily),
            )
        ]
        if normalized_raw["adj_factor"] is not None:
            crosscheck_fields.append("adj_factor")
            if not math.isclose(
                anchor_factor,
                normalized_raw["adj_factor"],
                rel_tol=0.0,
                abs_tol=1e-10,
            ):
                mismatched.append("adj_factor")
        if mismatched:
            raise ValueError(
                "raw monthly crosscheck mismatch: " + ",".join(mismatched)
            )
        raw_crosscheck_status = (
            "close_volume_amount" if factor_changed else "full_ohlcv"
        )

    meta = _json_mapping(source_meta, field="source_meta")
    meta["derivation"] = {
        "anchor_date": anchor_date,
        "factor_changed_within_month": factor_changed,
        "first_trade_date": daily[0]["trade_date"],
        "last_trade_date": anchor_date,
        "trading_days": len(daily),
        "output_units": {
            "volume": "share",
            "amount": "yuan",
        },
    }
    meta["crosscheck"] = {
        "status": raw_crosscheck_status,
        "fields": crosscheck_fields,
        "raw_present": raw_monthly is not None,
    }
    payload = {
        "stock_code": code,
        "month_end": canonical_month_end,
        "daily": daily,
        "factors": [
            row for row in factors if row["trade_date"] in {item["trade_date"] for item in daily}
        ],
        "raw_monthly": normalized_raw,
    }
    resolved_payload_hash = (
        _validated_payload_hash(source_payload_hash)
        if source_payload_hash is not None
        else _payload_hash(payload)
    )
    return _finalize_fact(
        {
            "stock_code": code,
            "stock_name": stock_name,
            "month_end": canonical_month_end,
            "fact_status": "certified_bar",
            **business,
            "anchor_adj_factor": _quantized(
                anchor_factor,
                field="anchor_adj_factor",
                quantum=_FACTOR_QUANT,
            ),
            "trading_days": len(daily),
            "first_trade_date": daily[0]["trade_date"],
            "last_trade_date": anchor_date,
            "formula_version": FORMULA_VERSION,
            "replacement_reason": replacement_reason,
            "raw_crosscheck_status": raw_crosscheck_status,
            "source_meta_json": _canonical_json(meta),
            "source_payload_hash": resolved_payload_hash,
        }
    )


def build_certified_no_trade_fact(
    stock_code: str,
    month_end: str,
    *,
    universe_proven: bool,
    raw_monthly_empty: bool,
    daily_empty: bool,
    replacement_reason: str = "certified_no_trade",
    stock_name: str | None = None,
    source_meta: Mapping[str, Any] | None = None,
    source_payload_hash: str | None = None,
) -> dict[str, Any]:
    """构造无交易月事实；三项外部证据必须由上层明确证明为真。"""
    if (
        universe_proven is not True
        or raw_monthly_empty is not True
        or daily_empty is not True
    ):
        raise ValueError(
            "certified_no_trade requires universe_proven, "
            "raw_monthly_empty and daily_empty"
        )
    code = _stock_code(stock_code)
    canonical_month_end = _iso_date(month_end, field="month_end")
    meta = _json_mapping(source_meta, field="source_meta")
    meta["no_trade_evidence"] = {
        "universe_proven": True,
        "raw_monthly_empty": True,
        "daily_empty": True,
    }
    resolved_payload_hash = (
        _validated_payload_hash(source_payload_hash)
        if source_payload_hash is not None
        else _payload_hash(
            {
                "stock_code": code,
                "month_end": canonical_month_end,
                "no_trade_evidence": meta["no_trade_evidence"],
            }
        )
    )
    return _finalize_fact(
        {
            "stock_code": code,
            "stock_name": stock_name,
            "month_end": canonical_month_end,
            "fact_status": "certified_no_trade",
            "open": None,
            "high": None,
            "low": None,
            "close": None,
            "volume": None,
            "amount": None,
            "anchor_adj_factor": None,
            "trading_days": 0,
            "first_trade_date": None,
            "last_trade_date": None,
            "formula_version": FORMULA_VERSION,
            "replacement_reason": replacement_reason,
            "raw_crosscheck_status": "certified_no_trade",
            "source_meta_json": _canonical_json(meta),
            "source_payload_hash": resolved_payload_hash,
        }
    )
