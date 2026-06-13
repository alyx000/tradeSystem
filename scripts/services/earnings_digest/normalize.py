"""业绩预告/快报标准化层：raw rows → 统一 DTO（dict）。

处理 STEP0 实测的三个数据陷阱：
1. update_flag 0/1 双行（同公告修正前后快照）→ 同 (ts_code, end_date) 取 ann_date 最新、
   update_flag 最大的一行为当前版本；多版本或 first_ann_date != ann_date 标「修正」。
2. 单位不一致：forecast 净利为**万元**，express 营收/净利为**元** → DTO 统一万元。
3. 口径二偏离（内部链路，零外部依赖）：
   - express 行回看同公司同报告期 forecast 区间 → vs_forecast 位置标签；
   - forecast 行若同批存在同公司更早报告期预告 → 增速加速/减速（无则 None，
     随落库历史积累逐步生效）。
"""
from __future__ import annotations

from typing import Any

# forecast type 8 分类 → 公告方向（市场投票 2×2 的公告轴；亦用于分类计数排序）
POSITIVE_TYPES = ("预增", "扭亏", "续盈", "略增")
NEGATIVE_TYPES = ("预减", "首亏", "续亏", "略减")
ALL_TYPES = POSITIVE_TYPES + NEGATIVE_TYPES

_YUAN_PER_WAN = 10_000.0


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _mid(lo: float | None, hi: float | None) -> float | None:
    values = [v for v in (lo, hi) if v is not None]
    if not values:
        return None
    return sum(values) / len(values)


def _digits(date_text: Any) -> str:
    return str(date_text or "").replace("-", "")


def is_top_up_candidate(item: dict, min_profit_wan: float) -> bool:
    """预增 Top 榜候选：净利中值达阈值且预告同比为正（renderer ⑤ 与口径三候选共用）。"""
    return (
        item.get("net_profit_mid_wan") is not None
        and item["net_profit_mid_wan"] >= min_profit_wan
        and item.get("p_change_mid") is not None
        and item["p_change_mid"] > 0
    )


def _sort_key(row: dict) -> tuple[str, str]:
    return (_digits(row.get("ann_date")), str(row.get("update_flag") or "0"))


def normalize_forecast(rows: list[dict]) -> list[dict]:
    """raw forecast rows → 每 (ts_code, end_date) 一条当前版本 DTO，按公告日倒序。"""
    groups: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        ts_code = str(row.get("ts_code") or "").strip()
        if not ts_code:
            continue
        groups.setdefault((ts_code, _digits(row.get("end_date"))), []).append(row)

    items: list[dict] = []
    for (ts_code, end_date), group in groups.items():
        current = max(group, key=_sort_key)
        ann_date = _digits(current.get("ann_date"))
        first_ann = _digits(current.get("first_ann_date"))
        p_min = _to_float(current.get("p_change_min"))
        p_max = _to_float(current.get("p_change_max"))
        np_min = _to_float(current.get("net_profit_min"))
        np_max = _to_float(current.get("net_profit_max"))
        items.append({
            "ts_code": ts_code,
            "end_date": end_date,
            "ann_date": ann_date,
            "type": str(current.get("type") or "").strip(),
            "p_change_min": p_min,
            "p_change_max": p_max,
            "p_change_mid": _mid(p_min, p_max),
            "net_profit_min_wan": np_min,
            "net_profit_max_wan": np_max,
            "net_profit_mid_wan": _mid(np_min, np_max),
            "last_parent_net_wan": _to_float(current.get("last_parent_net")),
            "summary": str(current.get("summary") or "").strip(),
            "change_reason": str(current.get("change_reason") or "").strip(),
            # 修正判定：官方 first_ann_date 早于本次公告，或窗口内出现多版本
            "is_revision": bool((first_ann and first_ann != ann_date) or len(group) > 1),
        })

    # 口径二②：同批内同公司存在更早报告期预告 → 比较同比中值标加速/减速
    by_code: dict[str, list[dict]] = {}
    for item in items:
        by_code.setdefault(item["ts_code"], []).append(item)
    for code_items in by_code.values():
        code_items.sort(key=lambda x: x["end_date"])
        for prev, cur in zip(code_items, code_items[1:]):
            if prev["p_change_mid"] is None or cur["p_change_mid"] is None:
                continue
            cur["growth_trend"] = "加速" if cur["p_change_mid"] > prev["p_change_mid"] else "减速"
    for item in items:
        item.setdefault("growth_trend", None)

    items.sort(key=lambda x: x["ann_date"], reverse=True)
    return items


def normalize_express(rows: list[dict], forecast_items: list[dict] | None = None) -> list[dict]:
    """raw express rows → DTO（金额元→万元）+ 口径二① vs_forecast 区间位置标签。"""
    forecast_map = {
        (f["ts_code"], f["end_date"]): f for f in (forecast_items or [])
    }
    items: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for row in sorted(rows, key=_sort_key, reverse=True):
        ts_code = str(row.get("ts_code") or "").strip()
        end_date = _digits(row.get("end_date"))
        if not ts_code or (ts_code, end_date) in seen:
            continue  # 同报告期多版本取公告日最新
        seen.add((ts_code, end_date))
        n_income_yuan = _to_float(row.get("n_income"))
        revenue_yuan = _to_float(row.get("revenue"))
        n_income_wan = None if n_income_yuan is None else n_income_yuan / _YUAN_PER_WAN
        items.append({
            "ts_code": ts_code,
            "end_date": end_date,
            "ann_date": _digits(row.get("ann_date")),
            "revenue_wan": None if revenue_yuan is None else revenue_yuan / _YUAN_PER_WAN,
            "n_income_wan": n_income_wan,
            "yoy_dedu_np": _to_float(row.get("yoy_dedu_np")),
            "diluted_eps": _to_float(row.get("diluted_eps")),
            "diluted_roe": _to_float(row.get("diluted_roe")),
            "perf_summary": str(row.get("perf_summary") or "").strip(),
            "is_audit": row.get("is_audit"),
            "vs_forecast": _vs_forecast_label(
                n_income_wan, forecast_map.get((ts_code, end_date))
            ),
        })
    items.sort(key=lambda x: x["ann_date"], reverse=True)
    return items


def _vs_forecast_label(n_income_wan: float | None, forecast: dict | None) -> str | None:
    """口径二①：快报实绩落在此前预告区间的位置（公司自己的预期 vs 自己的实绩）。"""
    if n_income_wan is None or forecast is None:
        return None
    lo = forecast.get("net_profit_min_wan")
    hi = forecast.get("net_profit_max_wan")
    if lo is None or hi is None:
        return None
    if n_income_wan > hi:
        return "超出预告上界"
    if n_income_wan < lo:
        return "跌破预告下界"
    span = hi - lo
    if span <= 0:
        return "落在预告区间内"
    position = (n_income_wan - lo) / span
    if position >= 0.5:
        return "落在预告区间上沿"
    return "落在预告区间下沿"
