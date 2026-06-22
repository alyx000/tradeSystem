"""两融余额与指数联动性 采集层（唯一 IO）。

两融区间序列走 registry.call('get_margin_series')（自动 tushare→akshare 降级）；指数涨跌幅
序列复用 sector_correlation.collector.fetch_index_series（pro.index_daily 区间，列 pct_chg）。
两融余额转日变化率后与指数对齐做四维分析，组装 record（不落库）。无足够数据返 None。

pairing 设计（满足「上证为主 + 多宽基 + 沪深各自对照」）：
- broad：三市合计两融(total) vs 各宽基指数；
- cross：沪市两融(sse) vs 上证、深市两融(szse) vs 深成。
pair_key = f"{margin_key}:{index_code}"，formatter 按 margin_key 分组展示。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import pandas as pd

from services.sector_correlation.collector import fetch_index_series

from . import aggregator

logger = logging.getLogger(__name__)


def _date_range(date: str, windows: list[int], max_lag: int) -> tuple[str, str]:
    """据最长窗口 + lag 余量估起始日历日（宽放，区间拉取只返真实交易日）。"""
    span = max(windows) + max_lag + 5
    start = datetime.strptime(date, "%Y-%m-%d") - timedelta(days=span * 2 + 60)
    return start.strftime("%Y%m%d"), date.replace("-", "")


def _balance_series(margin_rows: list[dict], key: str) -> pd.Series:
    """从两融序列抽某口径(total/sse/szse rzrqye)的余额 Series（index=trade_date）。

    **关键**：index 归一为 YYYYMMDD，与 fetch_index_series（pro.index_daily 原生 trade_date
    为 YYYYMMDD）对齐——两融原始 trade_date 是 'YYYY-MM-DD'，不归一则 concat 零交集、
    所有相关恒为「样本不足」（见 test_margin_index_collector 的对齐回归用例）。
    """
    col = f"{key}_rzrqye_yi"
    s = pd.Series(
        {row["trade_date"].replace("-", ""): float(row[col]) for row in margin_rows},
        dtype=float,
    )
    return s.sort_index()


def build_record(
    registry,
    pro,
    *,
    date: str,
    windows: list[int],
    broad_indices: list[tuple[str, str]],
    cross_pairs: list[tuple[str, str, str, str]],
    base_index: str,
    divergence_windows: list[int],
    min_gap: float,
    max_lag: int,
    min_sample_by_window: dict[int, int],
    lag_min_sample: int,
) -> dict | None:
    """采集两融序列 + 指数序列 → 四维联动分析 → 组装 record（不落库）。无足够数据返 None。"""
    start, end = _date_range(date, windows, max_lag)

    margin_res = registry.call("get_margin_series", start, end)
    if not getattr(margin_res, "success", False) or not margin_res.data:
        logger.info("[margin-index] %s 无两融区间序列，跳过", date)
        return None
    margin_rows = margin_res.data
    data_trade_date = margin_rows[-1]["trade_date"]
    market_scope = margin_rows[-1].get("market_scope", "")

    # pairing 表：broad(total) + cross(sse/szse)
    pairings: list[dict] = []
    for code, name in broad_indices:
        pairings.append({"pair_key": f"total:{code}", "margin_key": "total",
                         "index_code": code, "index_name": name, "group": "broad"})
    for mkey, mlabel, code, name in cross_pairs:
        pairings.append({"pair_key": f"{mkey}:{code}", "margin_key": mkey,
                         "index_code": code, "index_name": name, "group": "cross",
                         "margin_label": mlabel})

    index_codes = sorted({p["index_code"] for p in pairings})
    index_series, index_fails = fetch_index_series(pro, index_codes, start, end)
    if not index_series:
        logger.info("[margin-index] %s 无任何指数数据，跳过", date)
        return None

    # 截断指数序列到两融真实末日(data_trade_date)：两融盘后发布滞后时，指数会含请求日 T 的
    # bar 而两融止于 T-1，不截断则背离/相关窗口以无两融配对的 T 日结尾 → 误报「日期缺口」、
    # 混入无配对指数日（codex 门2 round2 #1）。截断后四维统一以两融真实日为脊柱对齐。
    cutoff = data_trade_date.replace("-", "")
    index_series = {c: s[s.index <= cutoff] for c, s in index_series.items()}
    index_series = {c: s for c, s in index_series.items() if not s.empty}
    if not index_series:
        logger.info("[margin-index] %s 指数截断到两融末日 %s 后无数据，跳过", date, data_trade_date)
        return None

    balances = {k: _balance_series(margin_rows, k) for k in ("total", "sse", "szse")}
    margin_rets = {k: aggregator.margin_returns(v) for k, v in balances.items()}

    lag_out: dict = {}
    sync_out: dict = {}
    div_out: dict = {}
    covered: list[dict] = []
    for p in pairings:
        code = p["index_code"]
        if code not in index_series:
            continue  # 指数缺失的 pairing 跳过（记 fetch_failures）
        m_ret = margin_rets[p["margin_key"]]
        idx_ret = index_series[code]
        bal = balances[p["margin_key"]]
        key = p["pair_key"]
        lag_out[key] = aggregator.lagged_correlation(
            m_ret, idx_ret, max_lag=max_lag, min_sample=lag_min_sample)
        # window 键统一 stringify：与 repo JSON 往返后的形态一致（fresh==persisted，
        # 防下游按 record["sync_corr"][pk]["20"] 索引时 fresh 用 int 键 KeyError）。
        sync_out[key] = {str(w): v for w, v in aggregator.sync_correlation(
            m_ret, idx_ret, windows=windows, min_sample_by_window=min_sample_by_window).items()}
        div_out[key] = {str(w): v for w, v in aggregator.detect_divergence(
            bal, idx_ret, windows=divergence_windows, min_gap=min_gap).items()}
        covered.append(p)

    if not covered:
        logger.info("[margin-index] %s 无任何可对照 pairing，跳过", date)
        return None

    balance_levels = {k: aggregator.balance_levels(v) for k, v in balances.items()}

    return {
        "date": date,
        "data_trade_date": data_trade_date,
        "windows": windows,
        "indices": covered,
        "base_index": base_index,
        "lag": lag_out,
        "sync_corr": sync_out,
        "divergence": div_out,
        "balance": balance_levels,
        "sample_days": {str(w): int(len(balances["total"].tail(w))) for w in windows},
        "meta": {
            "source": margin_res.source or "tushare:margin",
            "market_scope": market_scope,
            "analysis_trade_date": data_trade_date,  # 四维分析实际对齐的脊柱末日
            "stale": data_trade_date != date,
            "divergence_windows": divergence_windows,
            "min_gap": min_gap,
            "max_lag": max_lag,
            "lag_min_sample": lag_min_sample,
            "fetch_failures": {"indices": index_fails},
            "caliber_note": "两融余额转日变化率(%)后与指数 pct_chg 做 Pearson；背离用复利累计比率",
        },
    }
