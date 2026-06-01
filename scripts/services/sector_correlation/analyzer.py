"""板块相关性 分类/排序层（纯函数）。

把 aggregator 的数值矩阵转成 JSON-ready 结构：同向/逆向标签、联动榜（原始相关降序）、
反向榜（超额相关升序=最负在前）、每板块最负相关伙伴。阈值原始与超额不同（超额量级更小）。
"""
from __future__ import annotations

import math
from itertools import combinations

import pandas as pd

# 原始与超额阈值不同：超额已剥离大盘系统成分，量级天然更小。
# 默认硬编码、可经各函数 thresholds 参数覆盖（review M4：v1 不迁 config/DB，
# 阈值是经验值，调参走参数即可，避免过度工程）。
DEFAULT_THRESHOLDS = {
    "raw":    {"strong": 0.7, "weak": 0.4, "weak_inv": -0.4, "strong_inv": -0.7},
    "excess": {"strong": 0.4, "weak": 0.2, "weak_inv": -0.2, "strong_inv": -0.4},
}


def _is_nan(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def _round(v, ndigits: int = 3):
    if _is_nan(v):
        return None
    return round(float(v), ndigits)


def classify_corr(value, kind: str, thresholds: dict | None = None) -> str:
    """按阈值给相关值打标签。kind ∈ {'raw','excess'}。"""
    t = (thresholds or DEFAULT_THRESHOLDS)[kind]
    if _is_nan(value):
        return "无数据"
    v = float(value)
    if v >= t["strong"]:
        return "强同向"
    if v >= t["weak"]:
        return "弱同向"
    if v <= t["strong_inv"]:
        return "强逆向"
    if v <= t["weak_inv"]:
        return "弱逆向"
    return "独立"


def pairs_from_matrix(corr: pd.DataFrame, cols: list[str], kind: str,
                      sort: str = "desc", thresholds: dict | None = None) -> list[dict]:
    """上三角对列表（跳过 NaN），按 corr 排序。sort='desc' 联动榜 / 'asc' 反向榜。"""
    out: list[dict] = []
    for a, b in combinations(cols, 2):
        # 同时校验行与列存在（review H2：corr 为对称方阵，两者要么都在要么都不在；
        # 双重判定对"列因对齐被剔"的非方阵也安全）
        if a in corr.index and b in corr.columns:
            v = corr.loc[a, b]
            if not _is_nan(v):
                out.append({"a": a, "b": b, "corr": _round(v),
                            "label": classify_corr(v, kind, thresholds)})
    out.sort(key=lambda d: d["corr"], reverse=(sort == "desc"))
    return out


def top_inverse_partners(excess_pairs: list[dict], k: int = 3) -> dict:
    """每个板块的最负相关 Top-k 伙伴（当它走强，谁最可能被抽血）。"""
    by_sector: dict[str, list[dict]] = {}
    for p in excess_pairs:
        for x, y in ((p["a"], p["b"]), (p["b"], p["a"])):
            by_sector.setdefault(x, []).append({"partner": y, "corr": p["corr"]})
    return {s: sorted(lst, key=lambda d: d["corr"])[:k] for s, lst in by_sector.items()}


def classify_sector_vs_index(window_result: dict, thresholds: dict | None = None) -> dict:
    """{板块: {指数: {raw_corr, beta, label}}}（标签按原始相关阈值）。"""
    raw = window_result["raw"]
    betas = window_result["betas"]
    out: dict = {}
    for s in window_result["sector_cols"]:
        out[s] = {}
        for i in window_result["index_cols"]:
            rc = raw.loc[s, i] if (s in raw.index and i in raw.columns) else float("nan")
            out[s][i] = {
                "raw_corr": _round(rc),
                "beta": _round(betas.get(s, {}).get(i, float("nan"))),
                "label": classify_corr(rc, "raw", thresholds),
            }
    return out


def today_comovement(sectors: list[dict], top_k: int = 12) -> dict:
    """单日联动快照：按当日涨跌幅(latest_change_pct)分齐涨/齐跌，各自排序取 top_k。

    回答"今天哪些板块联袂涨/跌"（事实层，对照窗口结构联动）。0 涨幅记平、不入两组。
    """
    up, down = [], []
    for s in sectors:
        pct = s.get("latest_change_pct")
        if pct is None:
            continue
        item = {"name": s.get("name"), "type": s.get("type"), "change_pct": pct}
        if pct > 0:
            up.append(item)
        elif pct < 0:
            down.append(item)
    up.sort(key=lambda d: d["change_pct"], reverse=True)
    down.sort(key=lambda d: d["change_pct"])
    return {"up": up[:top_k], "down": down[:top_k]}


def build_window_payload(window_result: dict, thresholds: dict | None = None) -> dict:
    """单窗口 JSON-ready 结构（进 DB 的按窗口键内容）。"""
    sector_cols = window_result["sector_cols"]
    pair_raw = pairs_from_matrix(window_result["raw"], sector_cols, "raw", "desc", thresholds)
    pair_excess: list[dict] = []
    excess = window_result["excess"]
    if isinstance(excess, pd.DataFrame) and not excess.empty:
        pair_excess = pairs_from_matrix(excess, sector_cols, "excess", "asc", thresholds)
    return {
        "sample_days": window_result["sample_days"],
        "sector_index": classify_sector_vs_index(window_result, thresholds),
        "pair_raw": pair_raw,
        "pair_excess": pair_excess,
    }
