"""口径三·券商一致预期（H1 占比折算）。

[判断] 层，非事实层——含折算假设，输出必须标注 `[判断·H1占比折算]`：

券商盈利预测（report_rc）只给**全年**归母净利，中报季要的是**中报**预期，故：
    隐含中报一致预期 = 全年一致预期中值 × 该股近年「中报归母 / 全年归母」占比均值

护栏（任一不满足 → 返回 None「暂无一致预期数据」，不硬算误导）：
- 无券商覆盖（目标年无 report_rc 行）；
- H1 占比近 3 年有效样本 < 2（亏损年/占比异常剔除后）；
- 占比落在 (0, 1.2] 之外（亏损翻盈、季节极端 → 折算无意义）。

判定：forecast 中报归母中值 vs 隐含中报预期，偏离 ±EXPECT_TOL（默认 10%）
分「超预期 / 符合预期 / 低于预期」。
"""
from __future__ import annotations

import logging
from statistics import median

from .normalize import _digits, _to_float  # 同包共用，避免 NaN/格式化口径漂移

logger = logging.getLogger(__name__)

EXPECT_TOL = 0.10            # ±10% 视为符合预期
_RATIO_MIN, _RATIO_MAX = 0.0, 1.2  # H1 占比有效区间（含上界余量）
_MIN_RATIO_SAMPLES = 2      # 至少 2 个有效年份才折算
_RATIO_LOOKBACK_YEARS = 5   # 最多回看 5 年凑有效样本
_RATIO_TARGET_SAMPLES = 3   # 凑满近 3 个有效年份即止（近期优先）

_VERDICT_LABELS = {"beat": "超预期", "inline": "符合预期", "miss": "低于预期"}


def annual_consensus_median(report_rc_rows: list[dict], target_year: int) -> float | None:
    """目标年全年归母净利一致预期中值（万元）；quarter 形如 2026Q4。

    **按机构（+分析师）取最新一份**再求中值：120 天 report_rc 流里同一券商多次修订
    若按行直接 median，会把同机构的多份当独立样本扭曲中值（codex review）。
    """
    tag = f"{target_year}Q4"
    latest_by_analyst: dict[tuple, dict] = {}
    for row in report_rc_rows:
        if str(row.get("quarter") or "").strip() != tag or _to_float(row.get("np")) is None:
            continue
        key = (str(row.get("org_name") or ""), str(row.get("author_name") or ""))
        prev = latest_by_analyst.get(key)
        if prev is None or _digits(row.get("report_date")) >= _digits(prev.get("report_date")):
            latest_by_analyst[key] = row
    nps = [_to_float(r.get("np")) for r in latest_by_analyst.values()]
    return median(nps) if nps else None


def h1_ratio(income_rows: list[dict], target_year: int) -> float | None:
    """近年「中报归母 / 全年归母」占比均值（target_year 之前的已披露年份）。

    income n_income_attr_p 单位元（比值无量纲）；同报告期多行按 update_flag 取最新。
    """
    by_period: dict[str, dict] = {}
    for row in income_rows:
        end = _digits(row.get("end_date"))
        if not (end.endswith("0630") or end.endswith("1231")):
            continue
        prev = by_period.get(end)
        if prev is None or str(row.get("update_flag") or "0") >= str(prev.get("update_flag") or "0"):
            by_period[end] = row

    ratios: list[float] = []
    for year in range(target_year - 1, target_year - 1 - _RATIO_LOOKBACK_YEARS, -1):
        h1 = _to_float((by_period.get(f"{year}0630") or {}).get("n_income_attr_p"))
        fy = _to_float((by_period.get(f"{year}1231") or {}).get("n_income_attr_p"))
        if h1 is None or fy is None or h1 <= 0 or fy <= 0:
            continue  # 亏损年/缺失 → 占比无意义，剔除
        r = h1 / fy
        if _RATIO_MIN < r <= _RATIO_MAX:
            ratios.append(r)
            if len(ratios) >= _RATIO_TARGET_SAMPLES:
                break
    if len(ratios) < _MIN_RATIO_SAMPLES:
        return None
    return sum(ratios) / len(ratios)


def assess(
    forecast_mid_wan: float | None,
    report_rc_rows: list[dict],
    income_rows: list[dict],
    target_year: int,
    *,
    tol: float = EXPECT_TOL,
) -> dict | None:
    """对单只票做一致预期判定；任一护栏不满足 → None（暂无）。"""
    # H1 占比折算只在正盈利域有意义：consensus 或 forecast 为负=亏损/扭亏场景，
    # 折算会算出"负预期 vs 负实绩"的荒唐 ±%（codex review）——直接判暂无，不硬套
    if forecast_mid_wan is None or forecast_mid_wan <= 0:
        return None
    consensus_annual = annual_consensus_median(report_rc_rows, target_year)
    ratio = h1_ratio(income_rows, target_year)
    if consensus_annual is None or consensus_annual <= 0 or ratio is None:
        return None
    implied_h1 = consensus_annual * ratio
    deviation = (forecast_mid_wan - implied_h1) / abs(implied_h1)
    if deviation > tol:
        verdict = "beat"
    elif deviation < -tol:
        verdict = "miss"
    else:
        verdict = "inline"
    return {
        "verdict": verdict,
        "label": _VERDICT_LABELS[verdict],
        "implied_h1_wan": implied_h1,
        "consensus_annual_wan": consensus_annual,
        "h1_ratio": ratio,
        "deviation": deviation,
    }
