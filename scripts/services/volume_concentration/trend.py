"""跨日趋势分析(纯函数):板块轮动 / 头部量级环比 / 个股留存。

入参 records:按日期正序的集中度快照列表(repo.get_recent_concentration 输出)。
- 个股留存:dense series,从最新日往前数连续在榜天数(codex 中4)。
- 板块轮动:最新日 vs 前一日,「未分类」不计入(映射补全≠轮动,codex 中5)。
- 不足 2 个交易日:sufficient=False,由 formatter 出兜底文案(dec-10)。
"""
from __future__ import annotations

from .aggregator import UNCLASSIFIED


def _stock_retention(records: list[dict]) -> list[dict]:
    """最新日在榜个股,各自连续在榜天数(从最新日往前数到第一次缺席)。"""
    if not records:
        return []
    day_code_sets = [{s.get("code") for s in r["stocks"]} for r in records]
    latest = records[-1]["stocks"]
    result = []
    for s in latest:
        code = s.get("code")
        streak = 0
        for codes in reversed(day_code_sets):  # 从最新往前
            if code in codes:
                streak += 1
            else:
                break
        result.append({"code": code, "name": s.get("name", ""), "streak": streak})
    result.sort(key=lambda x: x["streak"], reverse=True)
    return result


def _sector_rotation(records: list[dict], unclassified: str) -> dict:
    """最新日 vs 前一日的行业集合差异;排除「未分类」。"""
    if len(records) < 2:
        return {"new": [], "dropped": [], "持续": []}

    def inds(rec):
        return {
            s["industry"]
            for s in rec["sector_summary"]
            if s.get("industry") and s["industry"] != unclassified
        }

    latest, prev = inds(records[-1]), inds(records[-2])
    return {
        "new": sorted(latest - prev),
        "dropped": sorted(prev - latest),
        "持续": sorted(latest & prev),
    }


def _amount_trend(records: list[dict]) -> dict:
    """头部 topN 合计成交额环比(最新 vs 前一日)。"""
    if not records:
        return {"latest": None, "previous": None, "change_pct": None}
    latest = records[-1]["total_amount_billion"]
    if len(records) < 2:
        return {"latest": latest, "previous": None, "change_pct": None}
    prev = records[-2]["total_amount_billion"]
    change_pct = round((latest - prev) / prev * 100, 2) if prev else None
    return {"latest": latest, "previous": prev, "change_pct": change_pct}


def compute_trend(records: list[dict], unclassified: str = UNCLASSIFIED) -> dict:
    """综合面板三段趋势。内部按日期正序防御(乱序传入也正确;比 assert 在 -O 下更稳)。"""
    records = sorted(records, key=lambda r: r["date"])
    return {
        "days": len(records),
        "sufficient": len(records) >= 2,
        "sector_rotation": _sector_rotation(records, unclassified),
        "amount_trend": _amount_trend(records),
        "stock_retention": _stock_retention(records),
    }
