"""从 daily_market.raw_data 中展开扩展字段，供多个路由共用。"""
from __future__ import annotations

import json

# 需要从 raw_data（顶层或内层 raw_data 子键）提取并提升到顶层的字段列表
_ENRICH_KEYS = (
    "sector_industry",
    "sector_concept",
    "sector_fund_flow",
    "sector_moneyflow_ths",
    "sector_moneyflow_dc",
    "market_moneyflow_dc",
    "daily_info",
    "limit_step",
    "limit_cpt_list",
    "indices",
    "style_factors",
    "sector_rhythm_industry",
    "sector_rhythm_concept",
)


def enrich_daily_market_row(row: dict | None) -> dict | None:
    """解析 daily_market 行中的 raw_data 字符串，把扩展键提升到顶层。

    - 提取 _ENRICH_KEYS 里所有存在的键（先找顶层 parsed，再找内层 parsed['raw_data']）。
    - 删除体积较大的 raw_data 字段，避免下游 payload 膨胀。
    - row 为 None 时原样返回。
    """
    if row is None:
        return None

    raw = row.pop("raw_data", None)
    if not raw:
        return row

    try:
        parsed: dict = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return row

    # post-market YAML 入库时整包存在 raw_data，实际采集内容在 parsed["raw_data"] 子键下
    inner: dict = parsed.get("raw_data") or {}
    if not isinstance(inner, dict):
        inner = {}

    for key in _ENRICH_KEYS:
        if key in parsed:
            row[key] = parsed[key]
        elif key in inner:
            row[key] = inner[key]

    return row
