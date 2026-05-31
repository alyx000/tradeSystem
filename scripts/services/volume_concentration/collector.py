"""成交额 top20 采集 + 申万行业打标 + 组装 record(read-through 读库优先,缺则重拉)。"""
from __future__ import annotations

import json

from db import queries

from .aggregator import UNCLASSIFIED, aggregate_sectors


def _coerce_stock_list(raw) -> list:
    """daily_market.top_volume_stocks 三态归一:list 直用 / JSON str 解析 / 其它→[]。

    get_daily_market 返回原始 TEXT 不自动 json.loads(queries.py:797),故必须在此解析。
    """
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else []
        except (ValueError, TypeError):
            return []
    return []


def load_top20(conn, registry, date: str, top_n: int = 20, refetch: bool = False) -> list[dict]:
    """read-through 取当日 top20:先读 daily_market.top_volume_stocks(命中即用,零重拉),
    缺失/空则 registry.call('get_top_volume_stocks') 自愈重拉(dec-1 C 方案)。

    refetch=True:跳过读库,强制重拉 provider —— 用于回填历史(库里 top_volume_stocks
    可能是某次换算 fix 之前采集的陈旧值,read-through 命中即用会灌坏数据)。
    """
    if not refetch:
        row = queries.get_daily_market(conn, date)
        if row is not None:
            stocks = _coerce_stock_list(row.get("top_volume_stocks"))
            if stocks:
                return stocks
    result = registry.call("get_top_volume_stocks", date, top_n)
    return result.data if (result.success and result.data) else []


def _fetch_market_total(registry, date: str):
    """单拉两市成交额(get_market_volume,自带降级);失败返 (None, None) 不阻断。"""
    result = registry.call("get_market_volume", date)
    if result.success and result.data:
        return result.data.get("total_billion"), result.source
    return None, None


def build_record(conn, registry, date: str, top_n: int = 20, refetch: bool = False) -> dict | None:
    """编排当日集中度 record:read-through 取 top20 → 申万打标 → market_total 单拉 →
    聚合 → 组装。无 top20 数据(非交易日/源全挂)返 None,由上层不写库不推送。

    refetch=True 透传给 load_top20,强制重拉绕过陈旧缓存(回填历史用)。
    """
    stocks = load_top20(conn, registry, date, top_n, refetch=refetch)
    if not stocks:
        return None

    labeled = label_industries(stocks, registry)
    market_total, market_total_source = _fetch_market_total(registry, date)
    agg = aggregate_sectors(labeled["stocks"])

    return {
        "date": date,
        "top_n": agg["top_n"],
        "total_amount_billion": agg["total_amount_billion"],
        "market_total_billion": market_total,
        "stocks": labeled["stocks"],
        "sector_summary": agg["sector_summary"],
        "source": {
            "industry_source": labeled["industry_source"],
            "industry_coverage": agg["industry_coverage"],
            "market_total_source": market_total_source,
        },
    }


def label_industries(stocks: list[dict], registry) -> dict:
    """给 top20 打申万二级行业标签 + 回填 name,三级降级:

    ① 申万成分命中 → industry=sw_l2、name 取申万(回填空 name)
    ② 缺成分(多为次新)→ 批量 get_stock_basic_batch 兜 name、industry=「未分类」
    ③ stock_basic 也无 → name 留原值(常为空)、industry=「未分类」

    返回 {stocks: 已打标列表, industry_source: 溯源字符串(供 source_json)}。
    """
    sw_result = registry.call("get_stock_sw_industry_map")
    sw_map = sw_result.data if (sw_result.success and sw_result.data) else {}
    industry_source = (
        sw_result.source if sw_result.success else f"sw_failed:{sw_result.error}"
    )

    labeled: list[dict] = []
    miss_codes: list[str] = []
    for s in stocks:
        code = s.get("code")
        entry = sw_map.get(code)
        if entry and entry.get("sw_l2"):
            labeled.append({
                **s,
                "industry": entry["sw_l2"],
                "name": entry.get("name") or s.get("name", ""),
            })
        else:
            miss_codes.append(code)
            labeled.append({**s, "industry": UNCLASSIFIED, "name": s.get("name", "")})

    if miss_codes:
        basic_result = registry.call("get_stock_basic_batch", miss_codes)
        basic_name: dict[str, str] = {}
        if basic_result.success and basic_result.data:
            for r in basic_result.data:
                c = r.get("ts_code")
                if c:
                    basic_name[c] = r.get("name") or ""
        if basic_name:
            industry_source = f"{industry_source}+stock_basic兜底"
        miss_set = set(miss_codes)
        for s in labeled:
            if s["code"] in miss_set:
                s["name"] = basic_name.get(s["code"]) or s.get("name", "")

    return {"stocks": labeled, "industry_source": industry_source}
