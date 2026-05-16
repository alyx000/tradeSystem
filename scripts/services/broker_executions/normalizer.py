from __future__ import annotations

import json
import re
from datetime import datetime

from .models import ErrorRow, NormalizedRow, RawRow

_COLUMN_MAP = {
    "成交日期": "biz_date",
    "交收日期": "settlement_date",
    "成交时间": "exec_time",
    "证券代码": "stock_code_raw",
    "证券名称": "stock_name",
    "买卖标志": "direction_raw",
    "操作": "direction_raw",
    "成交数量": "shares",
    "成交均价": "price",
    "成交价格": "price",
    "成交金额": "amount",
    "余额": "balance_after",
    "股票余额": "balance_after",
    "佣金": "commission",
    "净佣金": "commission",
    "印花税": "stamp_duty",
    "过户费": "transfer_fee",
    "交易所费用": "exchange_fee",
    "经手费": "exchange_fee",
    "监管费": "regulatory_fee",
    "证管费": "regulatory_fee",
    "其他费": "other_fees",
    "其他杂费": "other_fees",
    "发生金额": "net_amount",
    "合同编号": "broker_contract_no",
    "成交编号": "broker_trade_no",
    "交易市场": "market_raw",
    "市场名称": "market_name",
    "真实操作": "real_operation",
}

_BUY_DIRECTIONS = {"买入", "买", "担保买入", "融资买入"}
_SELL_DIRECTIONS = {"卖出", "卖", "担保卖出", "融券卖出"}


def normalize_rows(
    rows: list[RawRow],
    *,
    account_id: str = "default",
    input_by: str = "broker_export",
    source_file: str = "",
    source_format: str = "",
    import_run_id: str = "",
) -> tuple[list[NormalizedRow], list[ErrorRow]]:
    normalized: list[NormalizedRow] = []
    errors: list[ErrorRow] = []

    for row in rows:
        mapped = _map_payload(row.payload)
        try:
            normalized.append(
                _normalize_row(
                    row,
                    mapped,
                    account_id=account_id,
                    input_by=input_by,
                    source_file=source_file,
                    source_format=source_format,
                    import_run_id=import_run_id,
                )
            )
        except ValueError as exc:
            errors.append(ErrorRow(row_index=row.row_index, reason=str(exc), raw=row.payload))

    return normalized, errors


def _normalize_row(
    row: RawRow,
    mapped: dict[str, str],
    *,
    account_id: str,
    input_by: str,
    source_file: str,
    source_format: str,
    import_run_id: str,
) -> NormalizedRow:
    direction_raw = _required(mapped, "direction_raw")
    direction = _normalize_direction(direction_raw)
    biz_date = _normalize_date(_required(mapped, "biz_date"))
    exec_time = _normalize_time(mapped.get("exec_time", ""))
    stock_code_raw = _required(mapped, "stock_code_raw")
    stock_code = _normalize_stock_code(stock_code_raw)
    stock_name = _required(mapped, "stock_name")
    shares = _to_int(_required(mapped, "shares"), "shares")
    price = _to_float(_required(mapped, "price"), "price")
    amount = _to_float(_required(mapped, "amount"), "amount")
    if not _amount_matches(amount, price, shares):
        raise ValueError("amount_mismatch")

    commission = _to_float(mapped.get("commission", ""), "commission")
    stamp_duty = _to_float(mapped.get("stamp_duty", ""), "stamp_duty")
    transfer_fee = _to_float(mapped.get("transfer_fee", ""), "transfer_fee")
    exchange_fee = _to_float(mapped.get("exchange_fee", ""), "exchange_fee")
    regulatory_fee = _to_float(mapped.get("regulatory_fee", ""), "regulatory_fee")
    other_fees = _to_float(mapped.get("other_fees", ""), "other_fees")
    total_fees = (
        commission
        + stamp_duty
        + transfer_fee
        + exchange_fee
        + regulatory_fee
        + other_fees
    )
    broker_contract_no = _blank_to_none(mapped.get("broker_contract_no", ""))
    broker_trade_no = _blank_to_none(mapped.get("broker_trade_no", ""))
    settlement_date = _blank_to_none(mapped.get("settlement_date", ""))
    notes = f"settlement_date={settlement_date}" if settlement_date else None

    return NormalizedRow(
        account_id=account_id,
        broker_code=None,
        biz_date=biz_date,
        exec_time=exec_time,
        stock_code_raw=stock_code_raw,
        stock_code=stock_code,
        stock_name=stock_name,
        market=_infer_market(stock_code),
        market_raw=_blank_to_none(mapped.get("market_raw", "")),
        direction=direction,
        direction_raw=direction_raw,
        shares=shares,
        price=price,
        amount=amount,
        net_amount=_to_optional_float(mapped.get("net_amount", ""), "net_amount"),
        balance_after=_to_optional_int(mapped.get("balance_after", ""), "balance_after"),
        commission=commission,
        stamp_duty=stamp_duty,
        transfer_fee=transfer_fee,
        exchange_fee=exchange_fee,
        regulatory_fee=regulatory_fee,
        other_fees=other_fees,
        total_fees=total_fees,
        broker_contract_no=broker_contract_no,
        broker_trade_no=broker_trade_no,
        currency="CNY",
        raw_payload_json=json.dumps(row.payload, ensure_ascii=False, sort_keys=True),
        source_file=source_file,
        source_format=source_format,
        source_archive_path=None,
        input_by=input_by,
        import_run_id=import_run_id,
        notes=notes,
        row_index=row.row_index,
        _dedupe_mode="degraded" if not broker_contract_no and not broker_trade_no else "strict",
    )


def _map_payload(payload: dict[str, str]) -> dict[str, str]:
    mapped: dict[str, str] = {}
    for column, value in payload.items():
        key = _COLUMN_MAP.get(column.strip())
        if not key:
            continue
        if key in mapped and mapped[key]:
            continue
        mapped[key] = value.strip()
    return mapped


def _required(mapped: dict[str, str], key: str) -> str:
    value = mapped.get(key, "").strip()
    if value in ("", "-"):
        raise ValueError(f"missing_{key}")
    return value


def _normalize_direction(value: str) -> str:
    compact = value.strip()
    if compact in _BUY_DIRECTIONS:
        return "buy"
    if compact in _SELL_DIRECTIONS:
        return "sell"
    raise ValueError("invalid_direction")


def _normalize_date(value: str) -> str:
    stripped = value.strip()
    if re.fullmatch(r"\d{8}", stripped):
        return f"{stripped[:4]}-{stripped[4:6]}-{stripped[6:8]}"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", stripped):
        return stripped
    raise ValueError("invalid_date")


def _normalize_time(value: str) -> str | None:
    stripped = value.strip()
    if not stripped or stripped == "-":
        return None
    for fmt in ("%H:%M:%S", "%H:%M", "%H%M%S"):
        try:
            return datetime.strptime(stripped, fmt).strftime("%H:%M:%S")
        except ValueError:
            continue
    raise ValueError("invalid_time")


def _normalize_stock_code(value: str) -> str:
    stripped = value.strip().upper()
    stripped = re.sub(r"\.(SH|SZ|SS|BJ)$", "", stripped)
    match = re.search(r"\d{6}", stripped)
    return match.group(0) if match else stripped


def _infer_market(stock_code: str) -> str:
    if stock_code.startswith("6"):
        return "SH"
    if stock_code.startswith(("0", "3")):
        return "SZ"
    if stock_code.startswith(("4", "8", "9")):
        return "BJ"
    return "UNKNOWN"


def _to_float(value: str, field_name: str) -> float:
    stripped = value.strip().replace(",", "")
    if stripped in ("", "-"):
        return 0.0
    try:
        return float(stripped)
    except ValueError as exc:
        raise ValueError(f"invalid_{field_name}") from exc


def _to_optional_float(value: str, field_name: str) -> float | None:
    stripped = value.strip()
    if stripped in ("", "-"):
        return None
    return _to_float(stripped, field_name)


def _to_int(value: str, field_name: str) -> int:
    stripped = value.strip().replace(",", "")
    if stripped in ("", "-"):
        return 0
    try:
        return int(float(stripped))
    except ValueError as exc:
        raise ValueError(f"invalid_{field_name}") from exc


def _to_optional_int(value: str, field_name: str) -> int | None:
    stripped = value.strip()
    if stripped in ("", "-"):
        return None
    return _to_int(stripped, field_name)


def _blank_to_none(value: str) -> str | None:
    stripped = value.strip()
    return None if stripped in ("", "-") else stripped


def _amount_matches(amount: float, price: float, shares: int) -> bool:
    tolerance = max(0.01, shares * 0.0005)
    return abs(amount - (price * shares)) <= tolerance
