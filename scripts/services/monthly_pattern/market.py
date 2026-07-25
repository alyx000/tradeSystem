"""月线全市场行情的日历、覆盖率与前复权口径。"""
from __future__ import annotations

import calendar
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime
from typing import Any


class SourceCoverageError(RuntimeError):
    """来源为空、截断、重复或复权覆盖不足，不能继续扫描。"""


def _date(raw: Any) -> str | None:
    text = str(raw or "").strip().replace("-", "")
    if len(text) < 8 or not text[:8].isdigit():
        return None
    text = text[:8]
    try:
        return datetime.strptime(text, "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


def _strict_iso_date(raw: Any) -> str | None:
    """只接受精确 YYYY-MM-DD，禁止截断带后缀的损坏收据日期。"""
    if not isinstance(raw, str) or len(raw) != 10:
        return None
    try:
        parsed = datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        return None
    return raw if parsed.strftime("%Y-%m-%d") == raw else None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _code(row: dict) -> str:
    return str(row.get("ts_code") or row.get("stock_code") or row.get("code") or "").split(".")[0]


def _exchange(row: dict) -> str | None:
    raw = str(
        row.get("ts_code") or row.get("stock_code") or row.get("code") or ""
    ).strip()
    parts = raw.rsplit(".", 1)
    if len(parts) != 2:
        return None
    exchange = parts[1].strip().upper()
    return exchange or None


_CODE_ALIAS_QUOTE_FIELDS = (
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "change",
    "pct_chg",
    "vol",
    "amount",
)
_CODE_ALIAS_EXCHANGES = frozenset({"SH", "SZ", "BJ"})
_CODE_ALIAS_EVIDENCE = "identical_complete_month_quote_and_adj_factor"
_CODE_ALIAS_EVIDENCE_SCHEMA_VERSION = 2
_CODE_ALIAS_RECEIPT_VERSION = 2
_CODE_ALIAS_CACHE_FIELDS = (
    "open",
    "high",
    "low",
    "close",
    "vol",
    "amount",
)
_CODE_ALIAS_RECEIPT_FIELDS = frozenset(
    {
        "normalization_type",
        "evidence_version",
        "month_end",
        "alias_code",
        "canonical_code",
        "exchange",
        "quote_fields",
        "cache_verifiable_quote_fields",
        "quote_fingerprint",
        "alias_adj_factor",
        "canonical_adj_factor",
        "evidence",
        "receipt_sha256",
    }
)


def _code_alias_receipt_sha256(receipt: dict) -> str:
    payload = {
        key: value
        for key, value in receipt.items()
        if key != "receipt_sha256"
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _complete_quote_fingerprint(row: dict) -> tuple[float, ...] | None:
    values = tuple(_number(row.get(field)) for field in _CODE_ALIAS_QUOTE_FIELDS)
    if any(value is None for value in values):
        return None
    return tuple(float(value) for value in values if value is not None)


def _validated_quote_ohlcv(
    row: dict,
) -> tuple[dict[str, float], float, float] | None:
    raw_prices = {
        field: _number(row.get(field))
        for field in ("open", "high", "low", "close")
    }
    if any(value is None or value <= 0 for value in raw_prices.values()):
        return None
    prices = {
        field: float(value)
        for field, value in raw_prices.items()
        if value is not None
    }
    if not (
        prices["high"] >= max(prices["open"], prices["close"])
        and prices["low"] <= min(prices["open"], prices["close"])
        and prices["high"] >= prices["low"]
    ):
        return None
    volume = _number(row.get("vol") if "vol" in row else row.get("volume"))
    amount = _number(row.get("amount"))
    if volume is None or amount is None or volume < 0 or amount < 0:
        return None
    return prices, volume, amount


def _certify_code_alias_normalizations(
    quote_map: dict[str, dict],
    factor_map: dict[str, float],
    factor_row_map: dict[str, dict],
    universe_codes: set[str],
    universe_rows: list[dict],
    *,
    month_end: str,
) -> tuple[set[str], list[dict]]:
    """识别供应商因证券代码迁移留下的严格重复行。

    这里只认证可证明的重复：同交易所、完整月线字段逐项一致、复权因子一致，
    且只能唯一对应到外部宇宙内的一个 canonical code。任何缺字段或歧义都
    不猜测，仍交给外部宇宙门禁 fail-closed。
    """
    joined_codes = set(quote_map) & set(factor_map)
    unexpected_codes = sorted(joined_codes - universe_codes)
    if not unexpected_codes:
        return set(), []

    universe_exchanges: dict[str, set[str]] = defaultdict(set)
    for row in universe_rows:
        code = _code(row)
        exchange = _exchange(row)
        if code in universe_codes and exchange is not None:
            universe_exchanges[code].add(exchange)

    canonical_codes = sorted(joined_codes & universe_codes)
    normalized_aliases: set[str] = set()
    receipts: list[dict] = []
    for alias_code in unexpected_codes:
        alias_fingerprint = _complete_quote_fingerprint(quote_map[alias_code])
        alias_quote_exchange = _exchange(quote_map[alias_code])
        alias_factor_exchange = _exchange(factor_row_map[alias_code])
        if (
            alias_fingerprint is None
            or _validated_quote_ohlcv(quote_map[alias_code]) is None
            or alias_quote_exchange is None
            or alias_quote_exchange != alias_factor_exchange
        ):
            continue

        matches: list[str] = []
        for canonical_code in canonical_codes:
            canonical_exchange = _exchange(quote_map[canonical_code])
            canonical_factor_exchange = _exchange(factor_row_map[canonical_code])
            if (
                canonical_exchange != alias_quote_exchange
                or canonical_factor_exchange != alias_quote_exchange
                or universe_exchanges.get(canonical_code) != {alias_quote_exchange}
                or _validated_quote_ohlcv(quote_map[canonical_code]) is None
                or _complete_quote_fingerprint(quote_map[canonical_code])
                != alias_fingerprint
                or factor_map[canonical_code] != factor_map[alias_code]
            ):
                continue
            matches.append(canonical_code)

        if len(matches) > 1:
            raise SourceCoverageError(
                f"{month_end} 代码迁移归并存在歧义: {alias_code} 同时匹配 "
                + ",".join(matches[:5])
            )
        if len(matches) != 1:
            continue
        canonical_code = matches[0]
        normalized_aliases.add(alias_code)
        quote_fingerprint = {
            field: value
            for field, value in zip(
                _CODE_ALIAS_QUOTE_FIELDS,
                alias_fingerprint,
            )
        }
        receipt = {
            "normalization_type": "vendor_shadow_duplicate",
            "evidence_version": _CODE_ALIAS_RECEIPT_VERSION,
            "month_end": month_end,
            "alias_code": alias_code,
            "canonical_code": canonical_code,
            "exchange": alias_quote_exchange,
            "quote_fields": list(_CODE_ALIAS_QUOTE_FIELDS),
            "cache_verifiable_quote_fields": list(_CODE_ALIAS_CACHE_FIELDS),
            "quote_fingerprint": quote_fingerprint,
            "alias_adj_factor": factor_map[alias_code],
            "canonical_adj_factor": factor_map[canonical_code],
            "evidence": _CODE_ALIAS_EVIDENCE,
        }
        receipt["receipt_sha256"] = _code_alias_receipt_sha256(receipt)
        receipts.append(receipt)
    return normalized_aliases, receipts


def _month_index(month: str) -> int:
    year, number = month.split("-", 1)
    return int(year) * 12 + int(number)


def validate_consecutive_month_ends(
    month_ends: list[str],
    *,
    expected_months: int,
) -> None:
    """要求完成月窗口数量精确且自然月连续，禁止用更早月份填补中间缺口。"""
    if len(month_ends) != expected_months:
        raise SourceCoverageError(
            f"完成月交易日历仅 {len(month_ends)} 个月，要求 {expected_months} 个月"
        )
    normalized = [_date(item) for item in month_ends]
    if any(item is None for item in normalized) or normalized != sorted(set(normalized)):
        raise SourceCoverageError("完成月交易日历包含非法、重复或乱序日期")
    months = [str(item)[:7] for item in normalized]
    for previous, current in zip(months, months[1:]):
        if _month_index(current) != _month_index(previous) + 1:
            raise SourceCoverageError(
                f"完成月交易日历不连续: {previous} -> {current}"
            )


def select_as_of_universe_codes(
    universe_rows: list[dict],
    month_end: str,
) -> set[str]:
    """按上市/退市日期从外部 stock_basic 清单构造目标月可审计分母。"""
    normalized_month_end = _date(month_end)
    if normalized_month_end is None:
        raise ValueError(f"非法 month_end: {month_end}")
    month_start = normalized_month_end[:7] + "-01"
    output: set[str] = set()
    for row in universe_rows:
        code = _code(row)
        if not code:
            continue
        list_date = _date(row.get("list_date"))
        delist_date = _date(row.get("delist_date"))
        if list_date is not None and list_date > normalized_month_end:
            continue
        # 月中退市股票在该月仍形成月线，只有月初前已退市才排除。
        if delist_date is not None and delist_date < month_start:
            continue
        output.add(code)
    if not output:
        raise SourceCoverageError(f"{month_end} 外部股票宇宙为空")
    return output


def select_completed_month_ends(
    calendar_rows: list[dict],
    as_of_date: str,
    *,
    months: int,
) -> list[str]:
    """从完整交易日历选择截至 as-of 已结束月份的最后开放日。"""
    if months <= 0:
        raise ValueError("months 必须为正整数")
    as_of = _date(as_of_date)
    if as_of is None:
        raise ValueError(f"非法日期: {as_of_date}")
    as_of_dt = datetime.strptime(as_of, "%Y-%m-%d")
    current_month_complete = (
        as_of_dt.day == calendar.monthrange(as_of_dt.year, as_of_dt.month)[1]
    )
    last_open_by_month: dict[str, str] = {}
    for row in calendar_rows:
        try:
            is_open = int(row.get("is_open", 0)) == 1
        except (TypeError, ValueError):
            is_open = False
        day = _date(row.get("cal_date") or row.get("date"))
        if not is_open or day is None:
            continue
        month = day[:7]
        if day > last_open_by_month.get(month, ""):
            last_open_by_month[month] = day
    completed = sorted(
        day
        for month, day in last_open_by_month.items()
        if day <= as_of
        and (month < as_of[:7] or (month == as_of[:7] and current_month_complete))
    )
    return completed[-months:]


def join_month_quotes_and_factors(
    quotes: list[dict],
    factors: list[dict],
    *,
    month_end: str,
    min_rows: int = 4000,
    min_factor_coverage: float = 0.95,
    universe_rows: list[dict] | None = None,
    min_universe_coverage: float = 0.95,
    universe_source: str = "stock_basic",
    return_manifest: bool = False,
) -> list[dict] | tuple[list[dict], dict]:
    """校验全市场月线和月末复权因子后，形成可持久化的原始月线事实。"""
    normalized_month_end = _date(month_end)
    if normalized_month_end is None:
        raise ValueError(f"非法 month_end: {month_end}")
    if universe_rows is None and len(quotes) < min_rows:
        raise SourceCoverageError(
            f"{month_end} 月线行情仅 {len(quotes)} 行，低于完整性地板 {min_rows}"
        )
    quote_map: dict[str, dict] = {}
    for row in quotes:
        if row.get("trade_date") is not None:
            returned_date = _date(row.get("trade_date"))
            if returned_date != normalized_month_end:
                raise SourceCoverageError(
                    f"{month_end} 月线行情日期错位: {returned_date or row.get('trade_date')}"
                )
        code = _code(row)
        if not code:
            continue
        if code in quote_map:
            raise SourceCoverageError(f"{month_end} 月线行情存在重复股票代码: {code}")
        quote_map[code] = row

    universe_codes: set[str] | None = None
    quote_universe_coverage: float | None = None
    if universe_rows is not None:
        universe_codes = select_as_of_universe_codes(universe_rows, normalized_month_end)
        quote_universe_coverage = len(set(quote_map) & universe_codes) / len(universe_codes)
        if quote_universe_coverage < min_universe_coverage:
            raise SourceCoverageError(
                f"{month_end} 月线行情对外部股票宇宙覆盖率 "
                f"{quote_universe_coverage:.2%}，低于 {min_universe_coverage:.2%} "
                f"({len(set(quote_map) & universe_codes)}/{len(universe_codes)})"
            )

    factor_map: dict[str, float] = {}
    factor_row_map: dict[str, dict] = {}
    for row in factors:
        if row.get("trade_date") is not None:
            returned_date = _date(row.get("trade_date"))
            if returned_date != normalized_month_end:
                raise SourceCoverageError(
                    f"{month_end} 复权因子日期错位: {returned_date or row.get('trade_date')}"
                )
        code = _code(row)
        factor = _number(row.get("adj_factor"))
        if not code or factor is None or factor <= 0:
            continue
        if code in factor_map:
            raise SourceCoverageError(f"{month_end} 复权因子存在重复股票代码: {code}")
        factor_map[code] = factor
        factor_row_map[code] = row

    joined_code_set = set(quote_map) & set(factor_map)
    code_alias_normalizations: list[dict] = []
    normalized_aliases: set[str] = set()
    if universe_codes is not None:
        normalized_aliases, code_alias_normalizations = (
            _certify_code_alias_normalizations(
                quote_map,
                factor_map,
                factor_row_map,
                universe_codes,
                universe_rows or [],
                month_end=normalized_month_end,
            )
        )
        unexpected_codes = sorted(
            joined_code_set - universe_codes - normalized_aliases
        )
        if unexpected_codes:
            preview = ",".join(unexpected_codes[:5])
            raise SourceCoverageError(
                f"{month_end} 行情/复权包含 {len(unexpected_codes)} 个"
                f"外部股票宇宙之外代码（示例: {preview}）"
            )
        joined_code_set -= normalized_aliases
        joined_code_set &= universe_codes
    joined_codes = sorted(joined_code_set)
    denominator = max(len(quote_map) - len(normalized_aliases), 1)
    factor_coverage = len(joined_codes) / denominator
    if factor_coverage < min_factor_coverage:
        raise SourceCoverageError(
            f"{month_end} 复权因子覆盖率 {factor_coverage:.2%}，"
            f"低于 {min_factor_coverage:.2%}"
        )

    output: list[dict] = []
    for code in joined_codes:
        row = quote_map[code]
        validated = _validated_quote_ohlcv(row)
        if validated is None:
            continue
        prices, volume, amount = validated
        output.append(
            {
                "month_end": normalized_month_end,
                "stock_code": code,
                "stock_name": row.get("name") or row.get("stock_name"),
                **prices,
                "volume": volume,
                "amount": amount,
                "adj_factor": factor_map[code],
                "source": row.get("_source") or "tushare:monthly+adj_factor",
            }
        )
    if universe_codes is None and len(output) < min_rows:
        raise SourceCoverageError(
            f"{month_end} 清洗后有效月线仅 {len(output)} 行，低于完整性地板 {min_rows}"
        )
    valid_universe_coverage: float | None = None
    if universe_codes is not None:
        output_codes = {str(row["stock_code"]) for row in output}
        valid_universe_coverage = len(output_codes & universe_codes) / len(universe_codes)
        if valid_universe_coverage < min_universe_coverage:
            raise SourceCoverageError(
                f"{month_end} 清洗后对外部股票宇宙覆盖率 "
                f"{valid_universe_coverage:.2%}，低于 {min_universe_coverage:.2%}"
            )
    if not return_manifest:
        return output
    manifest = {
        "month_end": normalized_month_end,
        "status": "certified",
        "universe_source": universe_source,
        "universe_count": len(universe_codes or quote_map),
        "quote_count": len(quote_map),
        "factor_count": len(factor_map),
        "joined_count": len(output),
        "quote_coverage": (
            quote_universe_coverage
            if quote_universe_coverage is not None
            else 1.0
        ),
        "factor_coverage": factor_coverage,
        "source_meta": {
            "code_alias_evidence_schema_version": (
                _CODE_ALIAS_EVIDENCE_SCHEMA_VERSION
            ),
            "valid_universe_coverage": valid_universe_coverage,
            "min_universe_coverage": min_universe_coverage,
            "min_factor_coverage": min_factor_coverage,
            "factor_coverage_denominator": denominator,
            "raw_joined_code_count": len(set(quote_map) & set(factor_map)),
            "normalized_joined_code_count": len(joined_codes),
            "code_alias_normalizations": code_alias_normalizations,
        },
    }
    return output, manifest


def _manifest_count(source_meta: dict, field: str, month_end: str) -> int:
    value = _number(source_meta.get(field))
    if value is None or value < 0 or not value.is_integer():
        raise SourceCoverageError(
            f"{month_end} manifest 代码归并收据 {field} 非法"
        )
    return int(value)


def validate_code_alias_normalization_receipts(
    manifest: dict,
) -> list[dict]:
    """严格复验 shadow duplicate 收据，供缓存复用与跨月检查共用。"""
    month_end = _strict_iso_date(manifest.get("month_end"))
    if month_end is None:
        raise SourceCoverageError("manifest 代码归并收据 month_end 非法")
    source_meta = manifest.get("source_meta")
    if not isinstance(source_meta, dict):
        raise SourceCoverageError(
            f"{month_end} manifest 代码归并收据 source_meta 非法"
        )
    schema_version = source_meta.get("code_alias_evidence_schema_version")
    if (
        isinstance(schema_version, bool)
        or schema_version != _CODE_ALIAS_EVIDENCE_SCHEMA_VERSION
    ):
        raise SourceCoverageError(
            f"{month_end} manifest 代码归并收据 schema 版本非法"
        )
    if "code_alias_normalizations" not in source_meta:
        raise SourceCoverageError(
            f"{month_end} manifest 代码归并收据字段缺失"
        )
    receipts = source_meta["code_alias_normalizations"]
    if not isinstance(receipts, list):
        raise SourceCoverageError(
            f"{month_end} manifest 代码归并收据格式非法"
        )

    count_fields = (
        "factor_coverage_denominator",
        "raw_joined_code_count",
        "normalized_joined_code_count",
    )
    if not all(field in source_meta for field in count_fields):
        raise SourceCoverageError(
            f"{month_end} manifest 代码归并收据缺少归并计数"
        )
    denominator = _manifest_count(
        source_meta,
        "factor_coverage_denominator",
        month_end,
    )
    raw_count = _manifest_count(
        source_meta,
        "raw_joined_code_count",
        month_end,
    )
    normalized_count = _manifest_count(
        source_meta,
        "normalized_joined_code_count",
        month_end,
    )
    quote_count = _manifest_count(manifest, "quote_count", month_end)
    factor_count = _manifest_count(manifest, "factor_count", month_end)
    joined_count = _manifest_count(manifest, "joined_count", month_end)
    universe_count = _manifest_count(manifest, "universe_count", month_end)
    factor_coverage = _number(manifest.get("factor_coverage"))
    expected_factor_coverage = (
        normalized_count / denominator if denominator > 0 else None
    )
    if (
        denominator <= 0
        or denominator != quote_count - len(receipts)
        or raw_count > min(quote_count, factor_count)
        or raw_count - normalized_count != len(receipts)
        or joined_count > normalized_count
        or normalized_count > universe_count
        or factor_coverage is None
        or expected_factor_coverage is None
        or not math.isclose(
            factor_coverage,
            expected_factor_coverage,
            rel_tol=1e-12,
            abs_tol=1e-12,
        )
    ):
        raise SourceCoverageError(
            f"{month_end} manifest 代码归并收据计数不一致"
        )

    aliases_in_month: set[str] = set()
    normalized_receipts: list[dict] = []
    expected_fields = list(_CODE_ALIAS_QUOTE_FIELDS)
    expected_field_set = set(_CODE_ALIAS_QUOTE_FIELDS)
    for receipt in receipts:
        if not isinstance(receipt, dict):
            raise SourceCoverageError(
                f"{month_end} manifest 代码归并收据格式非法"
            )
        alias_code = str(receipt.get("alias_code") or "").strip()
        canonical_code = str(receipt.get("canonical_code") or "").strip()
        exchange = str(receipt.get("exchange") or "").strip().upper()
        receipt_month = _strict_iso_date(receipt.get("month_end"))
        evidence_version = receipt.get("evidence_version")
        quote_fields = receipt.get("quote_fields")
        cache_fields = receipt.get("cache_verifiable_quote_fields")
        quote_fingerprint = receipt.get("quote_fingerprint")
        alias_factor = _number(receipt.get("alias_adj_factor"))
        canonical_factor = _number(receipt.get("canonical_adj_factor"))
        receipt_sha256 = receipt.get("receipt_sha256")
        if (
            set(receipt) != _CODE_ALIAS_RECEIPT_FIELDS
            or receipt.get("normalization_type") != "vendor_shadow_duplicate"
            or isinstance(evidence_version, bool)
            or evidence_version != _CODE_ALIAS_RECEIPT_VERSION
            or receipt_month != month_end
            or receipt.get("evidence") != _CODE_ALIAS_EVIDENCE
            or len(alias_code) != 6
            or not alias_code.isdigit()
            or len(canonical_code) != 6
            or not canonical_code.isdigit()
            or alias_code == canonical_code
            or exchange not in _CODE_ALIAS_EXCHANGES
            or quote_fields != expected_fields
            or cache_fields != list(_CODE_ALIAS_CACHE_FIELDS)
            or not isinstance(quote_fingerprint, dict)
            or set(quote_fingerprint) != expected_field_set
            or alias_factor is None
            or alias_factor <= 0
            or canonical_factor is None
            or canonical_factor <= 0
            or alias_factor != canonical_factor
            or not isinstance(receipt_sha256, str)
            or len(receipt_sha256) != 64
            or any(char not in "0123456789abcdef" for char in receipt_sha256)
        ):
            raise SourceCoverageError(
                f"{month_end} manifest 代码归并收据证据不一致"
            )
        fingerprint_values = [
            _number(quote_fingerprint.get(field))
            for field in _CODE_ALIAS_QUOTE_FIELDS
        ]
        if any(value is None for value in fingerprint_values):
            raise SourceCoverageError(
                f"{month_end} manifest 代码归并收据行情指纹非法"
            )
        try:
            expected_receipt_sha256 = _code_alias_receipt_sha256(receipt)
        except (TypeError, ValueError, OverflowError):
            raise SourceCoverageError(
                f"{month_end} manifest 代码归并收据摘要非法"
            ) from None
        if receipt_sha256 != expected_receipt_sha256:
            raise SourceCoverageError(
                f"{month_end} manifest 代码归并收据摘要不一致"
            )
        if alias_code in aliases_in_month:
            raise SourceCoverageError(
                f"{month_end} manifest 代码归并收据重复: {alias_code}"
            )
        aliases_in_month.add(alias_code)
        normalized_receipts.append(receipt)
    return normalized_receipts


def _validate_code_alias_normalization_sequence(
    manifests: list[dict],
) -> None:
    targets: dict[str, tuple[str, str, str]] = {}
    alias_codes: set[str] = set()
    canonical_codes: set[str] = set()
    for manifest in manifests:
        month_end = str(manifest.get("month_end") or "")
        receipts = validate_code_alias_normalization_receipts(manifest)
        for receipt in receipts:
            alias_code = str(receipt.get("alias_code") or "").strip()
            canonical_code = str(receipt.get("canonical_code") or "").strip()
            exchange = str(receipt.get("exchange") or "").strip().upper()
            if (
                alias_code in canonical_codes
                or canonical_code in alias_codes
            ):
                raise SourceCoverageError(
                    f"代码归并角色冲突: {alias_code} -> {canonical_code}"
                )
            alias_codes.add(alias_code)
            canonical_codes.add(canonical_code)
            target = (canonical_code, exchange, month_end)
            previous = targets.get(alias_code)
            if previous is not None and previous[:2] != target[:2]:
                raise SourceCoverageError(
                    f"代码归并映射漂移: {alias_code} 在 {previous[2]} 指向 "
                    f"{previous[0]}.{previous[1]}，在 {month_end} 指向 "
                    f"{canonical_code}.{exchange}"
                )
            targets[alias_code] = target


def _manifest_effective_coverage(manifest: dict) -> float:
    universe_count = int(manifest.get("universe_count") or 0)
    joined_count = int(manifest.get("joined_count") or 0)
    if universe_count <= 0:
        raise SourceCoverageError(
            f"{manifest.get('month_end')} manifest 外部股票宇宙分母非法"
        )
    quote_coverage = _number(manifest.get("quote_coverage"))
    factor_coverage = _number(manifest.get("factor_coverage"))
    joined_coverage = joined_count / universe_count
    candidates = [joined_coverage]
    if quote_coverage is not None:
        candidates.append(quote_coverage)
    if factor_coverage is not None:
        candidates.append(factor_coverage)
    source_meta = manifest.get("source_meta")
    if isinstance(source_meta, dict):
        valid_coverage = _number(source_meta.get("valid_universe_coverage"))
        if valid_coverage is not None:
            candidates.append(valid_coverage)
    return min(candidates)


def validate_month_manifest_sequence(
    manifests: list[dict],
    *,
    min_adjacent_coverage_ratio: float = 0.98,
    max_adjacent_coverage_ratio: float = 1.02,
) -> None:
    """按外部宇宙归一后的有效覆盖比较相邻月，拦截单月共同截断/异常缩水。"""
    ordered = sorted(manifests, key=lambda item: str(item.get("month_end") or ""))
    _validate_code_alias_normalization_sequence(ordered)
    for previous, current in zip(ordered, ordered[1:]):
        previous_month = str(previous.get("month_end") or "")[:7]
        current_month = str(current.get("month_end") or "")[:7]
        if (
            len(previous_month) != 7
            or len(current_month) != 7
            or _month_index(current_month) != _month_index(previous_month) + 1
        ):
            continue
        previous_coverage = _manifest_effective_coverage(previous)
        current_coverage = _manifest_effective_coverage(current)
        if previous_coverage <= 0:
            raise SourceCoverageError(
                f"{previous.get('month_end')} manifest 有效覆盖率非法"
            )
        ratio = current_coverage / previous_coverage
        if not min_adjacent_coverage_ratio <= ratio <= max_adjacent_coverage_ratio:
            raise SourceCoverageError(
                f"相邻月有效覆盖异常: {previous.get('month_end')} "
                f"{previous_coverage:.2%} -> {current.get('month_end')} "
                f"{current_coverage:.2%} (ratio={ratio:.4f}, "
                f"允许 {min_adjacent_coverage_ratio:.4f}.."
                f"{max_adjacent_coverage_ratio:.4f})"
            )


def apply_month_end_qfq(rows: list[dict]) -> list[dict]:
    """按每只股票窗口末月因子，对月 OHLC 四价做同口径前复权。"""
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        code = _code(row)
        if code:
            grouped[code].append(dict(row))

    adjusted: list[dict] = []
    for code, stock_rows in grouped.items():
        stock_rows.sort(key=lambda item: str(item.get("month_end") or ""))
        latest_factor = _number(stock_rows[-1].get("adj_factor"))
        if latest_factor is None or latest_factor <= 0:
            raise SourceCoverageError(f"{code} 窗口末月复权因子缺失")
        previous_factor: float | None = None
        for row in stock_rows:
            factor = _number(row.get("adj_factor"))
            if factor is None or factor <= 0:
                raise SourceCoverageError(f"{code} {row.get('month_end')} 复权因子缺失")
            row["price_shape_valid"] = bool(
                previous_factor is None
                or math.isclose(
                    factor,
                    previous_factor,
                    rel_tol=1e-12,
                    abs_tol=1e-12,
                )
            )
            ratio = factor / latest_factor
            for field in ("open", "high", "low", "close"):
                value = _number(row.get(field))
                if value is None:
                    raise SourceCoverageError(f"{code} {row.get('month_end')} {field} 缺失")
                row[field] = round(value * ratio, 8)
            row["stock_code"] = code
            adjusted.append(row)
            previous_factor = factor
    return sorted(adjusted, key=lambda item: (item["stock_code"], item["month_end"]))
