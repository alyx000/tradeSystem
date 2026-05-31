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


def _stock_rotation(records: list[dict]) -> dict:
    """最新日 vs 前一日的 Top20 个股成员差异(新进 / 退出),带 name。

    new/dropped 各保留来源日的成交额降序(stocks 已按额降序)。不足 2 天返空。
    """
    if len(records) < 2:
        return {"new": [], "dropped": []}

    def by_code(rec):  # {code: name},保持 stocks 原顺序(成交额降序)
        return {s.get("code"): s.get("name", "") for s in rec["stocks"] if s.get("code")}

    latest, prev = by_code(records[-1]), by_code(records[-2])
    return {
        "new": [{"code": c, "name": latest[c]} for c in latest if c not in prev],
        "dropped": [{"code": c, "name": prev[c]} for c in prev if c not in latest],
    }


def _cr3(rec: dict, unclassified: str) -> float:
    """前 3 行业集中度(%);排除「未分类」后取前 3(与 formatter 同口径)。
    sector_summary 已按成交额降序(aggregator),故 [:3] 即前三。"""
    sectors = [s for s in rec["sector_summary"] if s.get("industry") != unclassified]
    return round(sum(s.get("share_in_top_n", 0) for s in sectors[:3]) * 100, 1)


def _streak(series: list[float]) -> tuple[str, int]:
    """从最新日往前数连续同向变动:返回 (方向 up/down/flat, 天数)。"""
    if len(series) < 2:
        return ("flat", 0)
    diffs = [series[i] - series[i - 1] for i in range(1, len(series))]

    def _sgn(d):
        return 1 if d > 0 else (-1 if d < 0 else 0)

    last = _sgn(diffs[-1])
    if last == 0:
        return ("flat", 0)
    days = 0
    for d in reversed(diffs):
        if _sgn(d) == last:
            days += 1
        else:
            break
    return (("up" if last > 0 else "down"), days)


def _cr3_trend(records: list[dict], unclassified: str, series_len: int = 5) -> dict:
    """CR3 环比(pp)+ 窗口分位(从高到低第几,1=最集中)+ 近 series_len 日序列与连升/连降。"""
    if not records:
        return {"current": None, "previous": None, "delta_pp": None, "rank": None,
                "window": 0, "series": [], "streak_dir": "flat", "streak_days": 0}
    full = [_cr3(r, unclassified) for r in records]
    current = full[-1]
    previous = full[-2] if len(full) >= 2 else None
    delta_pp = round(current - previous, 1) if previous is not None else None
    rank = sum(1 for v in full if v > current) + 1  # 比今天高的天数 + 1
    streak_dir, streak_days = _streak(full)
    return {"current": current, "previous": previous, "delta_pp": delta_pp,
            "rank": rank, "window": len(full), "series": full[-series_len:],
            "streak_dir": streak_dir, "streak_days": streak_days}


def _amount_trend(records: list[dict], lookback: int = 5) -> dict:
    """头部 topN 合计成交额环比(最新 vs 前一日)+ 近 lookback 日均值(含今日)与今日 vs 均值。

    avg 含今日是刻意的:表达「今日在近期均值中的位置」(放/缩量),非「今日 vs 之前均值」。
    """
    if not records:
        return {"latest": None, "previous": None, "change_pct": None, "avg": None, "vs_avg_pct": None}
    latest = records[-1]["total_amount_billion"]
    window = [r["total_amount_billion"] for r in records[-lookback:]]
    avg = round(sum(window) / len(window), 1) if window else None
    vs_avg_pct = round((latest - avg) / avg * 100, 1) if avg else None
    if len(records) < 2:
        return {"latest": latest, "previous": None, "change_pct": None, "avg": avg, "vs_avg_pct": vs_avg_pct}
    prev = records[-2]["total_amount_billion"]
    change_pct = round((latest - prev) / prev * 100, 2) if prev else None
    return {"latest": latest, "previous": prev, "change_pct": change_pct, "avg": avg, "vs_avg_pct": vs_avg_pct}


def _sector_heat(records: list[dict], lookback: int, unclassified: str) -> list[dict]:
    """各行业占 Top20 比重:最新日 vs lookback 交易日前。按 delta 降序(升温在前),排除未分类。"""
    if len(records) < 2:
        return []

    def _shares(rec):
        return {s["industry"]: round(s.get("share_in_top_n", 0) * 100, 1)
                for s in rec["sector_summary"]
                if s.get("industry") and s["industry"] != unclassified}

    latest = _shares(records[-1])
    base = _shares(records[max(0, len(records) - 1 - lookback)])
    out = [{"industry": i, "current": latest.get(i, 0.0), "base": base.get(i, 0.0),
            "delta_pp": round(latest.get(i, 0.0) - base.get(i, 0.0), 1)}
           for i in set(latest) | set(base)]
    out.sort(key=lambda x: (-x["delta_pp"], x["industry"]))
    return out


def _metabolism(records: list[dict], core_threshold: int) -> dict:
    """Top20 新陈代谢:核心(streak≥core_threshold)/ 新晋(streak==1)计数 + 今日新进资金流向行业。"""
    retention = _stock_retention(records)
    core = sum(1 for r in retention if r["streak"] >= core_threshold)
    fresh = sum(1 for r in retention if r["streak"] == 1)
    industry_by_code = ({s.get("code"): s.get("industry", "") for s in records[-1]["stocks"]}
                        if records else {})
    flow: dict = {}
    for x in _stock_rotation(records)["new"]:
        ind = industry_by_code.get(x["code"], "")
        flow[ind] = flow.get(ind, 0) + 1
    new_by_sector = sorted(flow.items(), key=lambda kv: (-kv[1], kv[0]))
    return {"core": core, "fresh": fresh, "new_by_sector": new_by_sector}


def compute_trend(records: list[dict], unclassified: str = UNCLASSIFIED,
                  heat_lookback: int = 5, core_threshold: int = 10) -> dict:
    """综合趋势面板。内部按日期正序防御(乱序传入也正确;比 assert 在 -O 下更稳)。
    heat_lookback:板块热度/头部均值的回看交易日数;core_threshold:新陈代谢「核心」在榜阈值。"""
    records = sorted(records, key=lambda r: r["date"])
    heat_lookback = max(1, heat_lookback)        # 防御参数误用:≤0 会致 base 索引越界/窗口退化
    core_threshold = max(1, core_threshold)
    return {
        "days": len(records),
        "sufficient": len(records) >= 2,
        "sector_rotation": _sector_rotation(records, unclassified),
        "stock_rotation": _stock_rotation(records),
        "sector_heat": _sector_heat(records, heat_lookback, unclassified),
        "cr3_trend": _cr3_trend(records, unclassified),
        "amount_trend": _amount_trend(records, heat_lookback),
        "metabolism": _metabolism(records, core_threshold),
        "stock_retention": _stock_retention(records),
    }
