"""两融余额与指数联动性 纯数学层（无 IO，pandas/numpy）。

**核心口径（锁死，否则伪相关）**：相关性两侧必须同为 % return 才可比。
- 指数侧：直接用 pro.index_daily 的 pct_chg（已是日涨跌幅 %）。
- 两融侧：余额是**水位**（亿元），必须先 `margin_returns` 转日变化率(%)，再做 Pearson；
  漏做 pct_change 直接拿绝对余额算相关 = 伪高相关（两者都含趋势）。

四维：lagged_correlation(领先/滞后) / sync_correlation(同步滚动相关) /
detect_divergence(背离预警) / balance_levels(水位+趋势)。
sync 复用 sector_correlation.aggregator 的 align_panel/raw_correlation（不重写）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from services.sector_correlation import aggregator as _sector
from services.sector_correlation.analyzer import classify_corr

_EPS = _sector._EPS


def _round(v, ndigits: int = 4):
    if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
        return None
    return round(float(v), ndigits)


def margin_returns(balances: pd.Series) -> pd.Series:
    """两融余额(水位,亿元) → 日变化率(%)。首行无前值为 NaN。

    这是与指数涨跌幅同口径的关键一步：margin_ret_t = (bal_t-bal_{t-1})/bal_{t-1}*100。
    余额穿 0（清算/异常）会产生 ±inf，源头清为 NaN（下游 _pearson/align_panel 亦会清，
    此处防外层直接消费 inf 污染统计）。
    """
    ret = balances.astype(float).pct_change() * 100.0
    return ret.replace([np.inf, -np.inf], np.nan)


def _pearson(a: pd.Series, b: pd.Series, min_sample: int):
    """两 Series 对齐后 Pearson；有效配对 < min_sample 或任一近常量 → None。"""
    df = pd.concat([a, b], axis=1).replace([np.inf, -np.inf], np.nan).dropna()
    if len(df) < min_sample:
        return None
    x, y = df.iloc[:, 0], df.iloc[:, 1]
    if x.std() <= _EPS or y.std() <= _EPS:
        return None
    c = x.corr(y)
    return None if pd.isna(c) else float(c)


def lagged_correlation(
    margin_ret: pd.Series,
    index_ret: pd.Series,
    *,
    max_lag: int = 3,
    min_sample: int = 10,
) -> dict:
    """领先/滞后相关：对 lag∈[-max_lag,max_lag] 算 corr(margin_ret, index_ret.shift(lag))。

    符号约定：index_ret.shift(L)[t]=index_ret[t-L]，corr 在 margin_ret[t] 与指数 L 日前
    对齐时最大。故 **lag>0 = 两融滞后指数**（指数先动、两融后跟）；lag<0 = 两融领先；
    lag=0 = 同步。取 |corr| 最大的 lag 为 best_lag；每 lag 有效配对 < min_sample 记 None，
    全 None → relation="样本不足"（不强出结论）。
    """
    by_lag: dict[int, float | None] = {}
    for lag in range(-max_lag, max_lag + 1):
        by_lag[lag] = _pearson(margin_ret, index_ret.shift(lag), min_sample)

    valid = {lag: c for lag, c in by_lag.items() if c is not None}
    if not valid:
        return {"best_lag": None, "best_corr": None,
                "by_lag": {l: _round(c) for l, c in by_lag.items()},
                "relation": "样本不足"}

    best_lag = max(valid, key=lambda lag: abs(valid[lag]))
    relation = "同步" if best_lag == 0 else ("两融滞后" if best_lag > 0 else "两融领先")
    return {
        "best_lag": best_lag,
        "best_corr": _round(valid[best_lag]),
        "by_lag": {l: _round(c) for l, c in by_lag.items()},
        "relation": relation,
    }


def sync_correlation(
    margin_ret: pd.Series,
    index_ret: pd.Series,
    *,
    windows: list[int],
    min_sample_by_window: dict[int, int],
) -> dict:
    """同步滚动相关：各窗口末 w 行的 margin_ret×index_ret Pearson + 同向/逆向标签。

    复用 sector align_panel（剔 inf/常量/样本不足列）+ raw_correlation（min_periods 守门）。
    """
    panel = pd.concat([margin_ret.rename("__m__"), index_ret.rename("__i__")], axis=1)
    out: dict[int, dict] = {}
    for w in windows:
        ms = min_sample_by_window[w]
        clean, _ = _sector.align_panel(panel.tail(w), ms)
        corr = None
        if "__m__" in clean.columns and "__i__" in clean.columns:
            v = _sector.raw_correlation(clean, ms).loc["__m__", "__i__"]
            corr = None if pd.isna(v) else float(v)
        out[w] = {"corr": _round(corr), "label": classify_corr(corr, "raw")}
    return out


def detect_divergence(
    balances: pd.Series,
    index_ret: pd.Series,
    *,
    windows: list[int],
    min_gap: float,
) -> dict:
    """背离预警：近 N 日指数累计涨幅 vs 两融余额累计变化，符号相反且 |差|≥min_gap 告警。

    两侧同为 N 区间累计比率（%）口径：
    - margin_cum = (bal_t/bal_{t-N}-1)*100（余额比率）
    - index_cum  = (Π(1+r/100)-1)*100（涨跌幅复利，与 margin 比率口径一致，非线性求和）
    type：涨指两融降（资金不认可上涨 [判断]）/ 跌指两融升（杠杆逆势加仓 [判断]）/ 无背离。

    **窗口以指数交易日脊柱定义**：两融 provider 会剔除不完整日，若中间漏交易日，直接
    tail(n+1) 会把"近5日"拉成跨更多日历日的稀疏窗，伪造/抑制预警（codex 门2 #3）。故取
    指数末 n+1 个真实交易日，要求两融在这 n+1 日**全覆盖**，缺任一日标「日期缺口」不评估。
    """
    ret = index_ret.replace([np.inf, -np.inf], np.nan).dropna().sort_index()
    bal = balances.replace([np.inf, -np.inf], np.nan).dropna().sort_index()
    out: dict[int, dict] = {}
    for n in windows:
        if len(ret) < n + 1:
            out[n] = {"index_cum": None, "margin_cum": None,
                      "diverged": False, "type": "样本不足", "magnitude": None}
            continue
        win = list(ret.index[-(n + 1):])  # 末 n+1 个指数交易日（连续脊柱）
        if not all(d in bal.index for d in win):  # 两融在窗内缺交易日 → 缺口，不评估
            out[n] = {"index_cum": None, "margin_cum": None,
                      "diverged": False, "type": "日期缺口", "magnitude": None}
            continue
        bal0 = float(bal.loc[win[0]])
        bal1 = float(bal.loc[win[-1]])
        margin_cum = (bal1 / bal0 - 1) * 100 if bal0 else None
        rets = ret.loc[win[1:]].to_numpy(dtype=float)  # 末 N 个连续区间的日涨跌幅
        index_cum = (float(np.prod(1 + rets / 100.0)) - 1) * 100

        diverged = False
        magnitude = None
        if margin_cum is None:
            # 期初余额为 0 → 比率无法定义；显式标「无法评估」，不冒充「已评估无背离」。
            typ = "无法评估"
        else:
            typ = "无背离"
            opposite = (index_cum > 0 and margin_cum < 0) or (index_cum < 0 and margin_cum > 0)
            magnitude = abs(index_cum - margin_cum)
            if opposite and magnitude >= min_gap:
                diverged = True
                typ = "涨指两融降" if index_cum > 0 else "跌指两融升"
        out[n] = {
            "index_cum": _round(index_cum, 2),
            "margin_cum": _round(margin_cum, 2),
            "diverged": diverged,
            "type": typ,
            "magnitude": _round(magnitude, 2),
        }
    return out


_UNEVALUATED_TYPES = {"日期缺口", "样本不足", "无法评估"}


def summarize_divergence_risk(divergence: dict, *, indices: list[dict] | None = None) -> dict:
    """把多口径背离明细汇总为盘后风险预警等级。

    这是报告层的派生 [判断]：不新增事实，不替代交易决策。评分只用于排序提示：
    - `跌指两融升`：指数走弱但杠杆仍逆势上升，权重更高；
    - 5 日和 20 日同时命中：短中窗口共振，额外加权；
    - 多指数/多口径同时命中：说明不是单一指数噪声，额外加权。
    """
    index_map = {p.get("pair_key"): p for p in (indices or [])}
    hits: list[dict] = []
    unevaluated_count = 0
    evaluated_count = 0
    score = 0
    seen_hit_signatures: set[tuple[str, str, str]] = set()

    for pair_key, by_win in (divergence or {}).items():
        p = index_map.get(pair_key, {})
        index_name = p.get("index_name", pair_key)
        margin_key = p.get("margin_key", pair_key.split(":", 1)[0])
        margin_label = p.get("margin_label", "两融合计" if margin_key == "total" else margin_key)
        for win_raw, d in (by_win or {}).items():
            typ = d.get("type")
            if typ in _UNEVALUATED_TYPES:
                unevaluated_count += 1
                continue
            evaluated_count += 1
            if not d.get("diverged"):
                continue

            try:
                win = int(win_raw)
            except (TypeError, ValueError):
                win = 0
            signature = (str(index_name), str(win_raw), str(typ))
            if signature in seen_hit_signatures:
                continue
            seen_hit_signatures.add(signature)

            weight = 1
            if win <= 5:
                weight += 1
            elif win >= 20:
                weight += 1
            if typ == "跌指两融升":
                weight += 2
            if margin_key == "total":
                weight += 1
            score += weight
            hits.append({
                "pair_key": pair_key,
                "window": str(win_raw),
                "type": typ,
                "index_name": index_name,
                "margin_label": margin_label,
                "index_cum": d.get("index_cum"),
                "margin_cum": d.get("margin_cum"),
                "magnitude": d.get("magnitude"),
                "weight": weight,
            })

    windows_by_pair: dict[str, set[str]] = {}
    for h in hits:
        windows_by_pair.setdefault(h["pair_key"], set()).add(h["window"])
    paired_windows = any({"5", "20"}.issubset(ws) for ws in windows_by_pair.values())
    if paired_windows:
        score += 2
    if len(windows_by_pair) >= 2:
        score += 1

    if hits:
        if score >= 7:
            level = "high"
            level_text = "高风险"
        elif score >= 4:
            level = "medium"
            level_text = "中风险"
        else:
            level = "low"
            level_text = "低风险"
        headline_parts = [f"{level_text}：两融与指数反向"]
        if paired_windows:
            headline_parts.append("5日+20日共振")
        if len(windows_by_pair) >= 2:
            headline_parts.append(f"{len(windows_by_pair)}组口径命中")
        headline = "，".join(headline_parts)
    elif evaluated_count:
        level = "none"
        headline = "未触发两融×指数反向风险"
    else:
        level = "unevaluated"
        headline = "数据不足，暂不评估两融×指数反向风险"

    reasons = [
        f"{h['margin_label']}×{h['index_name']} {h['window']}日 {h['type']}"
        for h in sorted(hits, key=lambda x: (-x["weight"], x["pair_key"], x["window"]))[:6]
    ]
    return {
        "level": level,
        "score": int(score),
        "headline": headline,
        "reasons": reasons,
        "hit_count": len(hits),
        "unevaluated_count": unevaluated_count,
    }


def balance_levels(balances: pd.Series) -> dict:
    """两融余额绝对水位 + 趋势：latest/日环比/近20日分位/连增连降/MA20 偏离。"""
    s = balances.astype(float).dropna()
    if s.empty:
        return {"latest_yi": None, "dod_pct": None, "pctile_20d": None,
                "up_streak": 0, "down_streak": 0, "ma20": None, "vs_ma20": None}

    vals = s.to_numpy()
    latest = float(vals[-1])
    dod = (latest / vals[-2] - 1) * 100 if len(vals) >= 2 and vals[-2] else None

    window = vals[-20:]
    pctile = (float((window <= latest).sum()) - 1) / (len(window) - 1) if len(window) > 1 else None
    ma20 = float(window.mean())
    vs_ma20 = (latest / ma20 - 1) * 100 if abs(ma20) > _EPS else None  # _EPS 守门防极小分母爆百分比

    up = down = 0
    i = len(vals) - 1
    while i > 0 and vals[i] > vals[i - 1]:
        up += 1
        i -= 1
    i = len(vals) - 1
    while i > 0 and vals[i] < vals[i - 1]:
        down += 1
        i -= 1

    return {
        "latest_yi": _round(latest, 2),
        "dod_pct": _round(dod, 2),
        "pctile_20d": _round(pctile),
        "up_streak": up,
        "down_streak": down,
        "ma20": _round(ma20, 2),
        "vs_ma20": _round(vs_ma20, 2),
    }
