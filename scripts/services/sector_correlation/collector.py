"""板块相关性 采集层（唯一 IO，Tushare 主源）。

走 TushareProvider 的 pro：指数 index_daily、申万二级 sw_daily、同花顺概念 ths_daily，
均支持区间拉取。列名差异（指数 pct_chg / 申万·同花顺 pct_change）与单位差异
（指数 amount 千元、申万 amount 万元）在此层归一。
- 行业按多日平均成交额选 top；概念按多日平均换手率选 top（ths_daily 无成交额列）
- 单标的拉取失败不阻断，记 fetch_failures
- 入矩阵板块 < MIN_SECTORS 或无指数 → build_record 返 None（上层不落库不推送）
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import pandas as pd

from . import aggregator, analyzer

logger = logging.getLogger(__name__)

MIN_SECTORS = 5  # 入矩阵板块下限，低于此不足以做相关分析


def _yyyymmdd(date_str: str) -> str:
    return date_str.replace("-", "")


def _date_range(date: str, windows: list[int], activity_days: int) -> tuple[str, str]:
    """据最长窗口 + 活跃度窗口估计起始日历日（宽放，区间拉取只返真实交易日）。"""
    # span 个交易日 ≈ span*1.5 日历日；*2+60 留足周末+春节长假(~2周)缓冲，
    # 保证区间内交易日数 >> max(windows)+activity_days（review H3）。
    span = max(windows) + activity_days
    start = datetime.strptime(date, "%Y-%m-%d") - timedelta(days=span * 2 + 60)
    return start.strftime("%Y%m%d"), _yyyymmdd(date)


def _returns_series(df, pct_col: str) -> "pd.Series | None":
    """Tushare df → 以 trade_date(str) 为索引的涨跌幅 Series；缺列/空 → None。"""
    if df is None or len(df) == 0 or pct_col not in df.columns or "trade_date" not in df.columns:
        return None
    s = df.set_index("trade_date")[pct_col].astype(float)
    s.index = s.index.astype(str)
    return s.sort_index()


def fetch_index_series(pro, index_codes: list[str], start: str, end: str) -> tuple[dict, list]:
    """{code: 涨跌幅 Series}, fails。指数涨跌幅列 = pct_chg。"""
    out, fails = {}, []
    for code in index_codes:
        try:
            s = _returns_series(pro.index_daily(ts_code=code, start_date=start, end_date=end), "pct_chg")
            if s is None or s.empty:
                fails.append(code)
            else:
                out[code] = s
        except Exception as e:  # noqa: BLE001
            logger.warning("index_daily 失败 %s: %s", code, e, exc_info=True)
            fails.append(code)
    return out, fails


def fetch_sector_series(pro, items: list[dict], start: str, end: str, kind: str) -> tuple[dict, list]:
    """items: [{ts_code, name}]。返回 {name: 涨跌幅 Series}, fail_names。

    kind='sw'→sw_daily / 'ths'→ths_daily；两者涨跌幅列均为 pct_change。按 name 建列（报告可读）。
    """
    fn = pro.sw_daily if kind == "sw" else pro.ths_daily
    out, fails = {}, []
    for it in items:
        code, name = it["ts_code"], it["name"]
        try:
            s = _returns_series(fn(ts_code=code, start_date=start, end_date=end), "pct_change")
            if s is None or s.empty:
                fails.append(name)
            else:
                out[name] = s
        except Exception as e:  # noqa: BLE001
            logger.warning("%s_daily 失败 %s(%s): %s", kind, name, code, e, exc_info=True)
            fails.append(name)
    return out, fails


def rank_industries(pro, dates: list[str], l2_codes: set, top_n: int) -> list[dict]:
    """逐日 sw_daily(trade_date) 快照 → 申万二级多日平均成交额(万元/1e4=亿)降序取 top_n。"""
    sums: dict[str, dict] = {}  # ts_code -> {name, total, cnt}
    for d in dates:
        try:
            df = pro.sw_daily(trade_date=_yyyymmdd(d))
        except Exception as e:  # noqa: BLE001
            logger.warning("sw_daily 快照失败 %s: %s", d, e)
            continue
        if df is None or len(df) == 0:
            continue
        for _, r in df.iterrows():
            code = str(r.get("ts_code", ""))
            if code not in l2_codes:
                continue
            rec = sums.setdefault(code, {"name": str(r.get("name", code)), "total": 0.0, "cnt": 0})
            rec["total"] += float(r.get("amount", 0) or 0)
            rec["cnt"] += 1
    # /1e4：万元→亿，与 provider _sector_rankings_sw:770 同口径。排名对单位单调
    # 不敏感（即便绝对值口径有偏，相对名次不变）；该值仅用于报告展示（review H2）。
    ranked = [
        {"ts_code": c, "name": v["name"], "avg_amount_billion": round(v["total"] / v["cnt"] / 1e4, 2)}
        for c, v in sums.items() if v["cnt"] > 0
    ]
    ranked.sort(key=lambda d: d["avg_amount_billion"], reverse=True)
    return ranked[:top_n]


def rank_concepts(pro, dates: list[str], concept_map: dict, top_m: int) -> list[dict]:
    """逐日 ths_daily(trade_date) 快照 → 概念多日平均换手率降序取 top_m（无成交额列）。"""
    sums: dict[str, dict] = {}
    for d in dates:
        try:
            df = pro.ths_daily(trade_date=_yyyymmdd(d))
        except Exception as e:  # noqa: BLE001
            logger.warning("ths_daily 快照失败 %s: %s", d, e)
            continue
        if df is None or len(df) == 0:
            continue
        for _, r in df.iterrows():
            code = str(r.get("ts_code", ""))
            if code not in concept_map:
                continue
            rec = sums.setdefault(code, {"name": concept_map[code], "total": 0.0, "cnt": 0})
            rec["total"] += float(r.get("turnover_rate", 0) or 0)
            rec["cnt"] += 1
    ranked = [
        {"ts_code": c, "name": v["name"], "avg_turnover_rate": round(v["total"] / v["cnt"], 2)}
        for c, v in sums.items() if v["cnt"] > 0
    ]
    ranked.sort(key=lambda d: d["avg_turnover_rate"], reverse=True)
    return ranked[:top_m]


def build_return_panel(index_series: dict, sector_series: dict) -> pd.DataFrame:
    """index_series(code→Series) + sector_series(name→Series) → date×标的 涨跌幅宽表。"""
    merged = {**index_series, **sector_series}
    if not merged:
        return pd.DataFrame()
    return pd.DataFrame(merged).sort_index()


def build_record(
    pro,
    *,
    date: str,
    windows: list[int],
    top_industries: int,
    top_concepts: int,
    indices: list[str],
    base_index: str,
    activity_days: int,
    l2_codes: set,
    concept_map: dict,
    min_sample_by_window: dict,
    include_concept: bool = True,
) -> dict | None:
    """采集 → 多日选板块 → 拉序列 → 聚合分类 → 组装 record（不落库）。无足够数据返 None。"""
    start, end = _date_range(date, windows, activity_days)

    index_series, index_fails = fetch_index_series(pro, indices, start, end)
    if not index_series:
        logger.info("[sector-correlation] %s 无任何指数数据，跳过", date)
        return None

    # 交易日脊柱取自 base 指数（缺则退任一可用指数；A股各指数共用交易日历，脊柱日期一致）。
    spine = index_series.get(base_index)
    if spine is None:  # 不能用 `or`：Series 真值判断会 ValueError
        logger.warning("[sector-correlation] base 指数 %s 缺失：超额相关将为空，脊柱退用其它指数", base_index)
        spine = next(iter(index_series.values()))
    trade_dates = list(spine.index)
    rank_dates = trade_dates[-activity_days:]

    industries = rank_industries(pro, rank_dates, l2_codes, top_industries)
    concepts = rank_concepts(pro, rank_dates, concept_map, top_concepts) if include_concept else []

    # 概念名与行业/已选名冲突时加后缀：panel 以 name 作列，重名会被 pd.DataFrame(dict)
    # 静默覆盖丢数据（review M1）。申万内/概念内各自不重名，仅跨类型可能撞名。
    _used = {it["name"] for it in industries}
    for it in concepts:
        if it["name"] in _used:
            it["name"] = f"{it['name']}(概念)"
        _used.add(it["name"])

    sec_series, sec_fails = fetch_sector_series(pro, industries, start, end, "sw")
    if concepts:
        con_series, con_fails = fetch_sector_series(pro, concepts, start, end, "ths")
        sec_series.update(con_series)
        sec_fails += con_fails

    if len(sec_series) < MIN_SECTORS:
        logger.info("[sector-correlation] %s 入矩阵板块 %d < %d，跳过", date, len(sec_series), MIN_SECTORS)
        return None

    sector_cols = list(sec_series.keys())
    index_cols = list(index_series.keys())
    panel = build_return_panel(index_series, sec_series)

    compute = aggregator.compute(
        panel, windows=windows, base_index=base_index,
        index_cols=index_cols, sector_cols=sector_cols,
        min_sample_by_window=min_sample_by_window,
    )
    # compute 后复校验：若各窗口对齐(剔常量/样本不足)后有效板块均 < MIN_SECTORS，
    # 矩阵无意义，返 None（review M2：compute 前的原始计数可能虚高）。
    if max((len(compute[w]["sector_cols"]) for w in windows), default=0) < MIN_SECTORS:
        logger.info("[sector-correlation] %s 各窗口有效板块均 < %d，跳过", date, MIN_SECTORS)
        return None
    payloads = {w: analyzer.build_window_payload(compute[w]) for w in windows}

    # sectors 明细（含活跃度证据 + 最新涨跌幅）
    type_by_name = {it["name"]: ("industry", it.get("avg_amount_billion")) for it in industries}
    type_by_name.update({it["name"]: ("concept", it.get("avg_turnover_rate")) for it in concepts})
    sectors = []
    for name in sector_cols:
        typ, metric = type_by_name.get(name, ("industry", None))
        entry = {"name": name, "type": typ,
                 "latest_change_pct": round(float(sec_series[name].iloc[-1]), 2)}
        entry["avg_amount_billion" if typ == "industry" else "avg_turnover_rate"] = metric
        sectors.append(entry)

    return {
        "date": date,
        "windows": windows,
        "top_n": len(sector_cols),
        "activity_days": activity_days,
        "sample_days": {str(w): payloads[w]["sample_days"] for w in windows},
        "base_index": base_index,
        "indices": index_cols,
        "sectors": sectors,
        "sector_index": {str(w): payloads[w]["sector_index"] for w in windows},
        "pair_raw": {str(w): payloads[w]["pair_raw"] for w in windows},
        "pair_excess": {str(w): payloads[w]["pair_excess"] for w in windows},
        "meta": {
            "min_sample": {str(w): min_sample_by_window[w] for w in windows},
            "excluded": {str(w): compute[w]["excluded"] for w in windows},
            "fetch_failures": {"indices": index_fails, "sectors": sec_fails},
            "base_present": base_index in index_series,
            "amount_unit_note": "申万 amount 万元/1e4=亿(同 provider)；指数仅取 pct_chg；概念按 turnover_rate",
            "source": "tushare:sw_daily+ths_daily+index_daily (镜像 tushare.xyz)",
        },
    }
