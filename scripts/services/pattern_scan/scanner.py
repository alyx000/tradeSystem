"""形态篇选股形态扫描编排。

漏斗：复用同日串阳首阴主线（申万二级 ∪ 同花顺概念分支）→ 板块内全量个股（剔 ST）
→ 逐票拉 300 自然日 OHLCV + 复权因子 → 前复权 → 四条件共振 → 盘后只读观察清单。

出处 `teacher_notes#444`，认知 `cog_3b32e660`。输出全部属于 [判断]，不写计划层、
不入关注池、不出价位、不给买卖建议。

设计取舍：
- 沿用同日串阳首阴归档的裁决。显式指定范围上限时按归档顺序截取。
- **有界双工取数而非批量粗筛**：每只股票仍保持「行情成功后才取复权因子」的原有
  调用与失败语义，但用两个 worker 并行处理两只股票；`executor.map` 保持输入顺序，
  因而结果、日志序号和确定性排序不变。它只重叠网络等待，不改变筛选口径，也避免
  全市场并发给上游造成突发压力。
- **全量前复权**：算的是 MA55 与 120 根 MACD，跨除权必错（`utils.qfq` 有实证），
  因子取不到即整票标缺失，绝不退回未复权硬算。
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from services.pattern_scan import constants as C
from services.pattern_scan import detectors
from services.concept_tags import build_stock_concept_map
from services.string_yang import renderer as string_yang_reports
from services.volume_concentration.aggregator import UNCLASSIFIED
from utils import is_st_stock
from utils.qfq import OHLC_PRICE_KEYS, apply_qfq

logger = logging.getLogger(__name__)
_FETCH_WORKERS = 2


def bare_code(code) -> str:
    return str(code or "").split(".")[0].strip()


def _lookback_start(date: str) -> str:
    return (datetime.strptime(date, "%Y-%m-%d")
            - timedelta(days=C.RANGE_LOOKBACK_DAYS)).strftime("%Y-%m-%d")


def _candidate_universe(
    sw_map: dict,
    main_sectors: set[str],
    main_concepts: set[str],
    concept_map: dict[str, set[str]] | None,
) -> tuple[list[dict], int]:
    """主线申万二级 ∪ 主线概念分支内的全量个股，剔 ST/退市风险。

    语义与 `string_yang.scanner._candidate_universe` 一致（同一主线口径下的同一批票），
    此处独立一份是因为返回字段不同；改主线成员判定时两处需同步。
    """
    out: list[dict] = []
    st_or_delist = 0
    concept_map = concept_map or {}
    for raw_code, info in sorted(sw_map.items()):
        if not isinstance(info, dict):
            continue
        sw_l2 = info.get("sw_l2") or UNCLASSIFIED
        code = bare_code(raw_code)
        if not code:
            continue
        branch_concepts = sorted((concept_map.get(code) or set()) & main_concepts)
        if sw_l2 not in main_sectors and not branch_concepts:
            continue
        if is_st_stock(info.get("name", "")):
            st_or_delist += 1
            continue
        out.append({
            "code": code,
            "name": info.get("name", ""),
            "sw_l2": sw_l2,
            "branch_concepts": branch_concepts,
        })
    return out, st_or_delist


def _fetch_bars(registry, code: str, start: str, end: str) -> list[dict]:
    r = registry.call("get_stock_daily_range", code, start, end)
    return r.data if getattr(r, "success", False) and isinstance(r.data, list) else []


def _fetch_factors(registry, code: str, start: str, end: str) -> list[dict] | None:
    r = registry.call("get_stock_adj_factor_range", code, start, end)
    return r.data if getattr(r, "success", False) and isinstance(r.data, list) else None


def _fetch_stock_inputs(
    registry,
    item: dict,
    start: str,
    end: str,
) -> tuple[dict, list[dict], list[dict] | None]:
    """单票保持既有调用顺序；行情失败时不额外请求复权因子。"""
    code = item["code"]
    bars = _fetch_bars(registry, code, start, end)
    factors = _fetch_factors(registry, code, start, end) if bars else None
    return item, bars, factors


def _sort_key(candidate: dict) -> tuple[float, float, str]:
    """今日成交额降序（资金关注度 [事实]），组数破平，code 兜底保证确定性。"""
    return (
        float(candidate.get("today_amount") or 0.0),
        float(candidate.get("rhythm_groups") or 0),
        candidate["code"],
    )


def _flatten(item: dict, bars: list[dict], detail: dict) -> dict:
    today = bars[-1]
    alignment = detail.get("alignment") or {}
    macd = detail.get("macd") or {}
    rhythm = detail.get("rhythm") or {}
    return {
        **item,
        "pct_chg": today.get("pct_chg"),
        "today_amount": today.get("amount"),
        "ma_values": alignment.get("values") or {},
        "macd_dif": macd.get("dif"),
        "macd_dea": macd.get("dea"),
        "macd_golden_cross": macd.get("golden_cross"),
        "rhythm_groups": rhythm.get("groups"),
        "yang_above_count": rhythm.get("yang_above_count"),
        "yang_total": rhythm.get("yang_total"),
        "yang_above_ratio": rhythm.get("yang_above_ratio"),
        "yin_shrink_count": rhythm.get("yin_shrink_count"),
        "yin_total": rhythm.get("yin_total"),
        "bar_count": detail.get("bar_count"),
    }


def run_daily(
    registry,
    date: str,
    *,
    top_k: int | None = None,
    top_concepts: int | None = None,
) -> dict:
    try:
        judgment = string_yang_reports.read_report_mainline(date)
    except (OSError, ValueError) as exc:
        logger.warning("[pattern-scan] %s 串阳首阴主线归档不可用：%s", date, exc)
        return {"date": date, "status": "source_failed", "candidates": [],
                "source_errors": ["串阳首阴同日主线归档不可用"]}

    sectors = judgment.main_sectors[:top_k]
    concepts = judgment.main_concepts[:top_concepts]
    main_sectors = set(sectors)
    main_concepts = set(concepts)
    concept_map: dict[str, set[str]] = {}
    missing_concepts = []
    source_errors = list(judgment.source_errors)
    if concepts:
        concept_map, member_count, _ = build_stock_concept_map(
            registry, date, concept_names=concepts)
        missing_concepts = sorted(main_concepts - member_count.keys())
        if missing_concepts:
            source_errors.append(f"同花顺概念成分缺失（{'、'.join(missing_concepts)}）")

    payload = judgment.public_payload()
    payload.update(main_sectors=sectors, main_concepts=concepts)
    base = {
        "date": date,
        "main_sectors": sectors,
        "mainline": payload,
        "mainline_limited": sectors != judgment.main_sectors or concepts != judgment.main_concepts,
        "main_sector_degraded": judgment.degraded,
    }
    if not main_sectors and not main_concepts:
        return {**base, "status": "source_failed", "candidates": [],
                "source_errors": source_errors + ["串阳首阴裁决及兜底均无可扫描板块"]}

    sw = registry.call("get_stock_sw_industry_map")
    if not (getattr(sw, "success", False) and isinstance(sw.data, dict)) or not sw.data:
        # 空 dict 也是故障：provider/缓存异常与「板块内确实没票」不可区分，必须 fail-closed，
        # 否则一次映射表故障会渲染成正常的「今日无命中」并照常推送（门2 medium）。
        return {**base, "status": "source_failed", "candidates": [], "rejects": {},
                "data_errors": [], "universe_count": 0,
                "source_errors": source_errors + ["sw_map"]}

    universe, st_or_delist = _candidate_universe(
        sw.data, main_sectors, main_concepts, concept_map)
    if not universe:
        # 主线非空却在全市场映射里一只票都匹配不到 → 板块名口径错位，不是「今日无候选」。
        return {**base, "status": "source_failed", "candidates": [], "rejects": {},
                "data_errors": [], "universe_count": 0,
                "source_errors": source_errors + ["mainline_coverage_empty"]}
    rejects = {
        "not_main_sector": max(0, len(sw.data) - len(universe) - st_or_delist),
        "st_or_delist": st_or_delist,
        "bar_missing": 0,
        "qfq_failed": 0,
        "stale_last_bar": 0,
        "insufficient_history": 0,
        "ma_not_aligned": 0,
        "macd_not_zero_axis": 0,
        "yang_volume_weak": 0,
        "rhythm_groups_below_min": 0,
        "already_accelerated": 0,
    }

    start = _lookback_start(date)
    candidates: list[dict] = []
    data_errors: list[str] = []
    alignment_breaks: dict[str, int] = {}
    total = len(universe)
    logger.info(
        "[pattern-scan] 候选宇宙 %s 只，以 %s 个 worker 拉取区间行情与复权因子",
        total,
        _FETCH_WORKERS,
    )

    def _load(item: dict) -> tuple[dict, list[dict], list[dict] | None]:
        return _fetch_stock_inputs(registry, item, start, date)

    with ThreadPoolExecutor(
        max_workers=_FETCH_WORKERS,
        thread_name_prefix="pattern-scan-fetch",
    ) as executor:
        fetched = executor.map(_load, universe)
        for idx, (item, bars, factors) in enumerate(fetched, start=1):
            code = item["code"]
            logger.info(
                "[pattern-scan] 扫描个股 %s/%s %s %s",
                idx,
                total,
                code,
                item.get("name", ""),
            )
            if not bars:
                rejects["bar_missing"] += 1
                data_errors.append(code)
                continue
            # 判阴阳要 close vs open 同坐标系，故必须复权 open（OHLC_PRICE_KEYS）。
            adjusted = apply_qfq(bars, factors or [], keys=OHLC_PRICE_KEYS)
            if adjusted is None:
                rejects["qfq_failed"] += 1
                data_errors.append(code)
                continue
            matched, detail = detectors.match_pattern(adjusted, code, target_date=date, is_st=False)
            if matched:
                candidates.append(_flatten(item, adjusted, detail))
                continue
            reason = detail.get("reason") or "insufficient_history"
            rejects[reason] = rejects.get(reason, 0) + 1
            if reason == "ma_not_aligned":
                # 口径调参用的诊断数据：五线严格递减是本扫描最强的门（2026-07-24 实测
                # 883 只里 842 只栽在这里）。记录断在哪一对，才能用真实分布决定
                # 「基本上要多头排列」该怎么放宽，而不是拍脑袋改阈值。
                # 纯观测，不参与筛选。
                broken = (detail.get("alignment") or {}).get("broken_at")
                if broken:
                    alignment_breaks[broken] = alignment_breaks.get(broken, 0) + 1

    candidates.sort(key=_sort_key, reverse=True)

    # 全宇宙取数失败 → 是链路问题而非「今日无候选」，必须区分（否则空清单会被当成
    # 「形态无票」正常推送，掩盖数据源故障）。
    # `stale_last_bar` 必须计入：Tushare 滞后时每只票都会返回「成功但末根是上一交易日」
    # 的区间，只查 bar_missing/qfq_failed 会让整批陈旧数据以 ok + 今日无命中 推出去（门2 high）。
    invalid = rejects["bar_missing"] + rejects["qfq_failed"] + rejects["stale_last_bar"]
    unevaluated = invalid + rejects["insufficient_history"]
    status = "partial" if unevaluated or missing_concepts else "ok"
    if invalid == total:
        status = "source_failed"
        source_errors.append("stock_daily_range_or_adj_factor_or_stale")
    return {**base, "status": status, "candidates": candidates, "rejects": rejects,
            "data_errors": data_errors, "universe_count": total,
            "evaluated_count": total - unevaluated, "unevaluated_count": unevaluated,
            "missing_concepts": missing_concepts,
            "alignment_breaks": alignment_breaks, "source_errors": source_errors}
