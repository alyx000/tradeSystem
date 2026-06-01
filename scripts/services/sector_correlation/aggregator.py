"""板块相关性 纯数学层（无 IO，pandas/numpy）。

输入 return panel：DataFrame(index=日期, columns=板块/指数, values=日涨跌幅%)。
- raw_correlation：原始日涨跌幅 Pearson（涨跌幅本身即收益率，不再差分）
- excess_returns：对 base 指数回归取残差（剔除大盘 β 成分），残差再算相关 → 真跷跷板
- beta：板块对指数的弹性
日期对齐由 panel 构建时 outer-join 完成（缺失=NaN）；本层按窗口取末 N 行切片，
剔除常量列 / 样本不足列，避免 corr=NaN 污染。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# 常量列判定容差：浮点累计误差/四舍五入可能让"几乎不动"的列 std≈1e-16 而非严格 0，
# 严格 ==0 会漏剔 → 其 corr 数值不稳定污染矩阵。用 <=_EPS 容差判常量（review H1）。
_EPS = 1e-12


def align_panel(df: pd.DataFrame, min_sample: int) -> tuple[pd.DataFrame, dict]:
    """剔除常量列（std≈0，corr 分母为 0）与非 NaN 样本 < min_sample 的列。

    只剔列不剔行；返回 (clean_df, {"insufficient": [...], "constant": [...]})。
    """
    # 先把 inf/-inf 当缺失：否则含 inf 的列 std()→NaN 会绕过下方 _EPS 守门被误留，
    # 且 inf 流入 polyfit 会 LinAlgError/NaN 传播（codex 中等项，单点修覆盖下游）。
    df = df.replace([np.inf, -np.inf], np.nan)
    excluded: dict[str, list[str]] = {"insufficient": [], "constant": []}
    keep: list[str] = []
    for col in df.columns:
        s = df[col]
        if int(s.notna().sum()) < min_sample:
            excluded["insufficient"].append(col)
            continue
        std = s.std(skipna=True)
        if pd.isna(std) or std <= _EPS:
            excluded["constant"].append(col)
            continue
        keep.append(col)
    return df[keep], excluded


def raw_correlation(df: pd.DataFrame, min_sample: int) -> pd.DataFrame:
    """Pearson 相关矩阵。

    min_periods=min_sample 是 pandas「两列间非 NaN 配对数下限」：任意一对 overlap
    < min_sample 时该 pair 返 NaN（符合「样本不足 pair 不可信」约定，由上层跳过）。
    """
    return df.corr(method="pearson", min_periods=min_sample)


def beta(y: pd.Series, x: pd.Series) -> float:
    """β = OLS(y ~ x) 斜率（= Cov(y,x)/Var(x)）；样本不足(含 0)/x 近常量 → NaN。"""
    mask = np.isfinite(y) & np.isfinite(x)  # isfinite 同时滤 NaN 与 inf(codex 中等)
    if int(mask.sum()) < 2 or x[mask].std() <= _EPS:  # <2 已覆盖 0 样本(review L4)
        return float("nan")
    slope, _ = np.polyfit(x[mask].to_numpy(), y[mask].to_numpy(), 1)
    return float(slope)


def _ols_residual(y: pd.Series, x: pd.Series) -> pd.Series:
    """y ~ a + b·x 的残差序列（对齐 y.index，缺失处 NaN）。"""
    mask = np.isfinite(y) & np.isfinite(x)  # 同 beta：滤 NaN+inf
    resid = pd.Series(np.nan, index=y.index)
    if int(mask.sum()) < 2 or x[mask].std() <= _EPS:
        return resid
    b, a = np.polyfit(x[mask].to_numpy(), y[mask].to_numpy(), 1)
    resid[mask] = y[mask] - (a + b * x[mask])
    return resid


def excess_returns(df: pd.DataFrame, base_col: str) -> pd.DataFrame:
    """对 base_col 回归取残差；base 列本身不进残差矩阵。

    base_col 存在性由唯一调用方 compute_window 守门（`base_index in clean.columns`），
    故此处不重复校验（review H3：非公开入口，避免冗余防御）。
    """
    return pd.DataFrame(
        {col: _ols_residual(df[col], df[base_col]) for col in df.columns if col != base_col}
    )


def compute_betas(df: pd.DataFrame, sector_cols: list[str], index_cols: list[str]) -> dict:
    return {s: {i: beta(df[s], df[i]) for i in index_cols} for s in sector_cols}


def compute_window(
    panel: pd.DataFrame,
    *,
    window: int,
    base_index: str,
    index_cols: list[str],
    sector_cols: list[str],
    min_sample: int,
) -> dict:
    """单窗口：取 panel 末 window 行 → 剔列 → 原始相关 + β + 超额相关。"""
    sliced = panel.tail(window)
    clean, excluded = align_panel(sliced, min_sample)
    s_cols = [c for c in sector_cols if c in clean.columns]
    i_cols = [c for c in index_cols if c in clean.columns]

    raw = raw_correlation(clean, min_sample)
    betas = compute_betas(clean, s_cols, i_cols)

    excess = pd.DataFrame()
    if base_index in clean.columns and s_cols:
        resid = excess_returns(clean[[base_index] + s_cols], base_index)
        excess = raw_correlation(resid, min_sample)

    return {
        # sample_days = 窗口内交易日数（panel 行数）。A 股各板块/指数交易日一致，
        # 故 ≈ 每对实际 overlap；稀疏场景下个别 pair 真实 overlap 可能更小（review L1）。
        "sample_days": int(len(clean)),
        "raw": raw,
        "betas": betas,
        "excess": excess,
        "sector_cols": s_cols,
        "index_cols": i_cols,
        "excluded": excluded,
    }


def compute(
    panel: pd.DataFrame,
    *,
    windows: list[int],
    base_index: str,
    index_cols: list[str],
    sector_cols: list[str],
    min_sample_by_window: dict[int, int],
) -> dict:
    """双窗聚合：{window: compute_window(...)}。各窗取 panel 末 window 行切片。"""
    return {
        w: compute_window(
            panel,
            window=w,
            base_index=base_index,
            index_cols=index_cols,
            sector_cols=sector_cols,
            min_sample=min_sample_by_window[w],
        )
        for w in windows
    }
