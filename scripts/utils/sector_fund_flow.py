"""板块资金流金额按来源单位归一，保留同义字段冲突造成的缺口。"""
from __future__ import annotations

import math


def consistent_amount(row, fields=("net_inflow_billion", "net_inflow")) -> float | None:
    values = set()
    for field in fields:
        raw = row.get(field)
        if raw is None or isinstance(raw, bool):
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(value):
            values.add(0.0 if value == 0 else value)
    return values.pop() if len(values) == 1 else None


def fund_flow_rows_view(section):
    """兼容扁平列表和带来源信息的 data 包装，仅在读取时统一同单位金额。"""
    rows = section.get("data") if isinstance(section, dict) else section
    if not isinstance(rows, list):
        return section
    normalized = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        amount = consistent_amount(row)
        item = {**row, "net_inflow_billion": amount}
        if "net_inflow" in row:
            item["net_inflow"] = amount
        normalized.append(item)
    return {**section, "data": normalized} if isinstance(section, dict) else normalized
