"""从 daily_market.raw_data 中展开扩展字段，供多个路由共用。"""
from __future__ import annotations

import json
from datetime import date

from analyzers.shanghai_ma import PERIODS, archived_view, daily_cross_signals

from utils.hk_indices import hk_indices_view
from utils.sector_fund_flow import fund_flow_rows_view

# 需要从 raw_data（顶层或内层 raw_data 子键）提取并提升到顶层的字段列表
_ENRICH_KEYS = (
    "sector_industry",
    "sector_concept",
    "sector_fund_flow",
    "sector_moneyflow_ths",
    "concept_moneyflow_ths",
    "sector_moneyflow_dc",
    "concept_moneyflow_dc",
    "market_moneyflow_dc",
    "daily_info",
    "limit_step",
    "limit_cpt_list",
    "indices",
    "moving_averages",
    "style_factors",
    "sector_rhythm_industry",
    "sector_rhythm_concept",
)


def _enrich_shanghai_daily_ma(row, conn):
    ma = row.get("moving_averages")
    sh = ma.get("shanghai") if isinstance(ma, dict) else None
    if not isinstance(sh, dict):
        return
    try:
        day = date.fromisoformat(row["date"]).isoformat()
    except (KeyError, TypeError, ValueError):
        return
    if not any(f"ma{p}" in sh or f"above_ma{p}" in sh for p in PERIODS) and "daily_ma_evidence" not in sh:
        return
    indices = row.get("indices") or {}
    sh_quote = indices.get("shanghai") if isinstance(indices, dict) else None
    verified = archived_view(sh, day, sh_quote or {}, conn)
    row["moving_averages"] = {**ma, "shanghai": verified}
    signals = row.get("node_signals")
    try:
        parsed = json.loads(signals) if isinstance(signals, str) else signals
    except ValueError:
        return
    if isinstance(parsed, list):
        labels = {op + f"MA{p}" for p in PERIODS for op in ("突破", "跌破")}
        def is_daily_cross(record):
            return isinstance(record, dict) and record.get("type") == "ma_cross" and record.get("signal") in labels
        old = [s for s in parsed if is_daily_cross(s)]
        if old and "archived_unverified" in verified:
            verified["archived_unverified"]["node_signals"] = old
        checked = daily_cross_signals(verified, day) + [s for s in parsed if not is_daily_cross(s)]
        row["node_signals"] = json.dumps(checked, ensure_ascii=False) if isinstance(signals, str) else checked


def enrich_daily_market_row(row: dict | None, *, conn=None) -> dict | None:
    """解析 daily_market 行中的 raw_data 字符串，把扩展键提升到顶层。

    - 提取 _ENRICH_KEYS 里所有存在的键（先找顶层 parsed，再找内层 parsed['raw_data']）。
    - 删除体积较大的 raw_data 字段，避免下游 payload 膨胀。
    - row 为 None 时原样返回。
    """
    if row is None:
        return None

    raw = row.pop("raw_data", None)

    try:
        parsed: dict = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
    except (json.JSONDecodeError, TypeError, ValueError):
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}

    # post-market YAML 入库时整包存在 raw_data，实际采集内容在 parsed["raw_data"] 子键下
    inner: dict = parsed.get("raw_data") or {}
    if not isinstance(inner, dict):
        inner = {}

    for key in _ENRICH_KEYS:
        if key in parsed:
            row[key] = parsed[key]
        elif key in inner:
            row[key] = inner[key]

    hk = inner.get("hk_indices", parsed.get("hk_indices", row.get("hk_indices")))
    if isinstance(hk, str):
        try:
            hk = json.loads(hk)
        except ValueError:
            hk = None
    if isinstance(hk, dict):
        # 维持扁平行情接口既有字段类型，完整信封则返回嵌套对象。
        view = hk_indices_view(hk)
        row["hk_indices"] = json.dumps(view, ensure_ascii=False) if isinstance(row.get("hk_indices"), str) else view

    if "sector_fund_flow" in row:
        row["sector_fund_flow"] = fund_flow_rows_view(row["sector_fund_flow"])
    _enrich_shanghai_daily_ma(row, conn)
    return row
