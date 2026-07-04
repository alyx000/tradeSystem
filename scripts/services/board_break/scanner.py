"""断板反包筛选层（[事实]）：昨日连板>=2 → 今日断板 → <=6% 未跌停 → 10cm 主板非 ST。

无状态：不建池不落库。源状态三分（source_failed / source_ok_empty / rule_filtered_empty），
任一核心源失败不得输出正常候选（spec v2 严重1）。
"""
from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timedelta

from services.board_break import constants as C
from services.volume_concentration import repo as vc_repo
from utils import is_st_stock
from utils.trade_date import get_prev_trade_date


def bare_code(code: str) -> str:
    # provider 降级可能给非字符串码（如 int）；str() 兜底防 .split 抛 AttributeError
    return str(code or "").split(".")[0].strip()


def is_main_board(code: str) -> bool:
    return bare_code(code).startswith(C.MAIN_BOARD_PREFIXES)


def _coerce_limit_times(raw) -> int | None:
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):  # 挡 NaN 与 ±inf；int(inf) 会抛 OverflowError，一律归为脏值
        return None
    return int(v)


def filter_candidates(prev_limit_up, today_limit_up_codes, today_limit_down_codes):
    cands, rejects = [], {"lt_below_min": 0, "dirty_limit_times": 0, "non_main_board": 0,
                          "st": 0, "still_limit_up": 0, "limit_down": 0}
    for row in prev_limit_up or []:
        code = bare_code(row.get("code", ""))
        lt = _coerce_limit_times(row.get("limit_times"))
        if lt is None:
            rejects["dirty_limit_times"] += 1
            continue
        if lt < C.MIN_LIMIT_TIMES:
            rejects["lt_below_min"] += 1
            continue
        if not code.startswith(C.MAIN_BOARD_PREFIXES):  # code 已裸化，直接判前缀，免二次裸化
            rejects["non_main_board"] += 1
            continue
        if is_st_stock(row.get("name", "")):
            rejects["st"] += 1
            continue
        if code in today_limit_up_codes:
            rejects["still_limit_up"] += 1
            continue
        if code in today_limit_down_codes:
            rejects["limit_down"] += 1
            continue
        cands.append({"code": code, "name": row.get("name", ""), "limit_times": lt,
                      "industry": row.get("industry", "")})
    return cands, rejects


def _compact_date(date: str) -> str:
    """"2026-07-04" -> "20260704"，用于与日线 bar 的 trade_date 比对。"""
    return date.replace("-", "")


def _lookback_start(date: str) -> str:
    """T-400 自然日窗口起点（一次取够，供 Stage 2 打分复用）。"""
    return (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=C.LOOKBACK_NATURAL_DAYS)).strftime("%Y-%m-%d")


def enrich_with_today_bar(candidates, fetch_range, date: str) -> tuple[list[dict], dict]:
    """逐票取 T 日 bar：校验末根 trade_date==T 与 close/pct_chg 非空，再按断板日涨幅<=6% 过滤。

    候选 dict 补 {close, pct_chg, ref_price, bars}；bars 挂全窗口日线供 Stage 2 打分复用。
    """
    compact_date = _compact_date(date)
    start = _lookback_start(date)
    out, rejects = [], {"bar_missing": 0, "pct_too_high": 0}
    for cand in candidates:
        bars = fetch_range(cand["code"], start, date)
        if not bars:
            rejects["bar_missing"] += 1
            continue
        last = bars[-1]
        if last.get("trade_date") != compact_date:
            rejects["bar_missing"] += 1
            continue
        close, pct = last.get("close"), last.get("pct_chg")
        if close is None or pct is None:
            rejects["bar_missing"] += 1
            continue
        if pct > C.BREAK_DAY_MAX_PCT:
            rejects["pct_too_high"] += 1
            continue
        out.append({**cand, "bars": bars, "close": close, "pct_chg": pct,
                    "ref_price": round(close * C.REBOUND_REF_RATIO, 2)})
    return out, rejects


def _prev_trade_date(registry, date: str) -> str:
    """薄封装，便于测试 monkeypatch；内部调用 utils.trade_date.get_prev_trade_date。"""
    return get_prev_trade_date(registry, date)


def _main_sectors(conn: sqlite3.Connection, date: str, top_k: int) -> tuple[set, bool]:
    """主线板块 = 当日成交额集中度 Top-K 申万二级；当日缺失回退最近一日（下沉至 vc_repo.get_main_sectors）。"""
    return vc_repo.get_main_sectors(conn, date, top_k)


def _classify_empty_kind(has_candidates: bool, entrance_count: int) -> str | None:
    """空语义三分：有候选→None；入口候选数为 0（源本身没有候选）→ source_ok_empty；
    否则（入口候选存在但被后续规则剔除）→ rule_filtered_empty。

    收敛决策（Stage 1 审查固化，勿再拆分）：bar_missing（单票日线缺失）归入
    rule_filtered_empty 展示——它与 ST/主板/仍涨停/跌停一样属于「入口候选存在但
    被后续规则剔除」；其计数保留在 rejects["bar_missing"]，由渲染层数据完整性
    脚注单独列出，不在此 empty_kind 三分基础上再细分出第四种语义。
    """
    if has_candidates:
        return None
    if entrance_count == 0:
        return "source_ok_empty"
    return "rule_filtered_empty"


def run_daily(conn: sqlite3.Connection, registry, date: str) -> dict:
    """编排：三源状态检查 → 筛选层 → 打分数据窗口读取（main_sectors）。

    实现顺序硬约束：三源检查失败必须先 return source_failed，conn 相关读取只在源 ok 后执行。
    """
    prev_date = _prev_trade_date(registry, date)

    prev_lu = registry.call("get_limit_up_list", prev_date)
    today_lu = registry.call("get_limit_up_list", date)
    today_ld = registry.call("get_limit_down_list", date)

    sources = {
        name: {"ok": not r.error, "source": getattr(r, "source", "")}
        for name, r in (
            ("prev_limit_up", prev_lu),
            ("today_limit_up", today_lu),
            ("today_limit_down", today_ld),
        )
    }
    failed_sources = [name for name, s in sources.items() if not s["ok"]]
    if failed_sources:
        return {
            "status": "source_failed",
            "date": date,
            "prev_trade_date": prev_date,
            "candidates": [],
            "rejects": {},
            "sources": sources,
            "failed_sources": failed_sources,
            "empty_kind": None,  # 与 status=="ok" 分支形状对称，供消费方无需分支判断即可安全取键
            "main_sectors": set(),
            "main_sector_degraded": False,
        }

    prev_stocks = (prev_lu.data or {}).get("stocks", []) or []
    today_up_codes = {bare_code(s.get("code", "")) for s in (today_lu.data or {}).get("stocks", []) or []}
    today_down_codes = {bare_code(s.get("code", "")) for s in (today_ld.data or {}).get("stocks", []) or []}

    cands, rejects = filter_candidates(prev_stocks, today_up_codes, today_down_codes)
    # 入口候选数 = 满足 D1 连板>=2 门槛的票数（剔除脏值/未达门槛后剩余），
    # 用于区分「源本身没有候选」与「候选被后续规则剔除」两类空语义。
    entrance_count = len(prev_stocks) - rejects["dirty_limit_times"] - rejects["lt_below_min"]

    def fetch_range(code, start, end):
        # 口径对齐 trend_leader._bars / volume_concentration.collector：success 且 data 为 list 才算有效
        r = registry.call("get_stock_daily_range", code, start, end)
        return r.data if getattr(r, "success", False) and isinstance(r.data, list) else []

    enriched, enrich_rejects = enrich_with_today_bar(cands, fetch_range, date)
    rejects.update(enrich_rejects)

    empty_kind = _classify_empty_kind(bool(enriched), entrance_count)

    main_sectors, main_sector_degraded = _main_sectors(conn, date, C.MAIN_SECTOR_TOP_K)

    return {
        "status": "ok",
        "date": date,
        "prev_trade_date": prev_date,
        "candidates": enriched,
        "rejects": rejects,
        "sources": sources,
        "empty_kind": empty_kind,
        "main_sectors": main_sectors,
        "main_sector_degraded": main_sector_degraded,
    }
