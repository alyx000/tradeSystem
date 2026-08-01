"""形态篇选股形态扫描编排。

漏斗：主线板块（成交额集中度 Top-K ∪ 同花顺概念分支）→ 板块内全量个股（剔 ST）
→ 逐票拉 300 自然日 OHLCV + 复权因子 → 前复权 → 四条件共振 → 盘后只读观察清单。

出处 `teacher_notes#444`，认知 `cog_3b32e660`。输出全部属于 [判断]，不写计划层、
不入关注池、不出价位、不给买卖建议。

设计取舍（都有实测依据，不是猜的）：
- **不接 LLM**：四条件全是机械判定，接 LLM 只会引入幻觉面。申万二级主线复用
  `string_yang.mainline.judge_mainline(use_llm=False)`——主线口径必须与 string-yang
  一致，各写一份必然给出两个「什么是主线」的答案。**但概念分支必须自建**
  （`_mechanical_concept_branch`）：judge_mainline 的概念图只在 `use_llm` 分支内构建，
  不接 LLM 时恒返回空，直接依赖会让 `--top-concepts` 变成死参数。
- **有界双工取数而非批量粗筛**：每只股票仍保持「行情成功后才取复权因子」的原有
  调用与失败语义，但用两个 worker 并行处理两只股票；`executor.map` 保持输入顺序，
  因而结果、日志序号和确定性排序不变。它只重叠网络等待，不改变筛选口径，也避免
  全市场并发给上游造成突发压力。
- **全量前复权**：算的是 MA55 与 120 根 MACD，跨除权必错（`utils.qfq` 有实证），
  因子取不到即整票标缺失，绝不退回未复权硬算。
"""
from __future__ import annotations

import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from services.pattern_scan import constants as C
from services.pattern_scan import detectors
from services.concept_tags import build_stock_concept_map
from services.string_yang import mainline
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


def _mechanical_concept_branch(
    registry, date: str, top_concepts: int,
) -> tuple[set[str], dict[str, set[str]], list[str]]:
    """不接 LLM 时的同花顺概念分支（资金流 Top-M + 容器概念过滤）。

    `judge_mainline(use_llm=False)` **只出申万二级**：概念图仅在 `use_llm` 分支内构建，
    fallback 恒返回 `main_concepts=[]`（门2 high）。若直接依赖它，`--top-concepts`
    会是死参数，而报告仍宣称「∪ 同花顺概念分支」——文档与实现不符。

    这里复用 mainline 的三个现成函数（不复制口径：容器过滤阈值、预取条数、资金流排序
    都必须与 string-yang 一致），只补「不接 LLM 时如何选概念」这段编排：
    LLM 版是让模型从热概念里裁决，机械版直接取资金流前 M 个非容器概念。
    """
    from services.string_yang.mainline import (
        _concept_prefetch_limit, _filter_hot_concept_rows, _ranked_concept_rows,
    )

    errors: list[str] = []
    ranked, ok = _ranked_concept_rows(registry, date)
    if not ok:
        return set(), {}, ["concept_flow"]
    prefetch = [row["name"] for row in ranked[:_concept_prefetch_limit(top_concepts)]]
    concept_map, member_count, member_ok = build_stock_concept_map(
        registry, date, concept_names=prefetch)
    if not member_ok:
        errors.append("ths_member")
    hot, coverage_ok = _filter_hot_concept_rows(ranked, top_concepts, member_count)
    if member_ok and not coverage_ok:
        errors.append("concept_coverage")
    return {row["name"] for row in hot}, concept_map, errors


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
    conn: sqlite3.Connection,
    registry,
    date: str,
    *,
    top_k: int = C.DEFAULT_TOP_K_SECTORS,
    top_concepts: int = C.DEFAULT_TOP_CONCEPTS,
) -> dict:
    judgment = mainline.judge_mainline(
        conn, registry, date,
        top_k=top_k, top_concepts=top_concepts, use_llm=False,
    )
    main_sectors = set(judgment.main_sectors)
    main_concepts, concept_map, concept_errors = _mechanical_concept_branch(
        registry, date, top_concepts)
    source_errors = judgment.source_errors + concept_errors

    payload = judgment.public_payload()
    payload["main_concepts"] = sorted(main_concepts)  # 机械分支补齐，非 judgment 的空列表
    base = {
        "date": date,
        "main_sectors": sorted(main_sectors),
        "mainline": payload,
        "main_sector_degraded": judgment.degraded,
    }
    if not main_sectors and not main_concepts:
        return {**base, "status": "ok", "candidates": [],
                "rejects": {"no_main_sector": 1}, "data_errors": [],
                "source_errors": source_errors, "universe_count": 0}

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
    if invalid == len(universe):
        return {**base, "status": "source_failed", "candidates": [], "rejects": rejects,
                "data_errors": data_errors, "universe_count": total,
                "alignment_breaks": alignment_breaks,
                "source_errors": source_errors + ["stock_daily_range_or_adj_factor_or_stale"]}

    return {**base, "status": "ok", "candidates": candidates, "rejects": rejects,
            "data_errors": data_errors, "universe_count": total,
            "alignment_breaks": alignment_breaks, "source_errors": source_errors}
