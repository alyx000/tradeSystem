"""aggregator 纯数学单测（阶段2 核心）：相关 / 残差 / β / 双窗。

合成 return panel，覆盖：正相关≈1、反相关≈−1、常量列剔除、样本不足剔除、
残差法 β 还原、超额相关量级<原始、双窗聚合。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from services.sector_correlation import aggregator as agg


def _panel(cols: dict) -> pd.DataFrame:
    n = len(next(iter(cols.values())))
    idx = [f"d{i:03d}" for i in range(n)]
    return pd.DataFrame(cols, index=idx)


def test_align_panel_drops_constant_and_insufficient():
    df = _panel({
        "A":     [1.0, 2, 3, 1, 2, 3, 1, 2, 3, 1, 2, 3, 1, 2, 3, 1, 2, 3, 1, 2],
        "CONST": [5.0] * 20,
        "SHORT": [1.0, 2, 3] + [np.nan] * 17,
    })
    clean, ex = agg.align_panel(df, min_sample=15)
    assert "A" in clean.columns
    assert "CONST" in ex["constant"]
    assert "SHORT" in ex["insufficient"]


def test_raw_correlation_perfect_pos_and_neg():
    base = np.array([0.1, -0.2, 0.3, -0.1, 0.05, 0.2, -0.15, 0.1, -0.05, 0.25,
                     -0.1, 0.15, -0.2, 0.1, 0.3, -0.25, 0.2, -0.1, 0.05, 0.15])
    df = _panel({"X": base.tolist(), "POS": (base * 2).tolist(), "NEG": (-base).tolist()})
    c = agg.raw_correlation(df, min_sample=15)
    assert c.loc["X", "POS"] == pytest.approx(1.0, abs=1e-9)
    assert c.loc["X", "NEG"] == pytest.approx(-1.0, abs=1e-9)


def test_raw_correlation_insufficient_overlap_returns_nan():
    a = [0.1, -0.2, 0.3, -0.1, 0.2] + [np.nan] * 15
    b = [np.nan] * 15 + [0.1, -0.2, 0.3, -0.1, 0.2]
    df = _panel({"A": a, "B": b})  # 零重叠
    c = agg.raw_correlation(df, min_sample=15)
    assert pd.isna(c.loc["A", "B"])


def test_beta_recovers_slope():
    rng = np.random.RandomState(0)
    base = rng.normal(0, 1, 60)
    y = 1.5 * base + rng.normal(0, 0.01, 60)
    df = _panel({"BASE": base.tolist(), "Y": y.tolist()})
    assert agg.beta(df["Y"], df["BASE"]) == pytest.approx(1.5, abs=0.05)


def test_excess_returns_orthogonal_to_base_and_shrinks_corr():
    rng = np.random.RandomState(1)
    base = rng.normal(0, 1, 60)
    s1 = 1.2 * base + rng.normal(0, 0.5, 60)
    s2 = 0.8 * base + rng.normal(0, 0.5, 60)
    df = _panel({"BASE": base.tolist(), "S1": s1.tolist(), "S2": s2.tolist()})

    raw = agg.raw_correlation(df, 15)
    resid = agg.excess_returns(df[["BASE", "S1", "S2"]], "BASE")
    exc = agg.raw_correlation(resid, 15)

    # 残差与 base 近乎正交
    r1 = agg._ols_residual(df["S1"], df["BASE"])
    assert df["BASE"].corr(r1) == pytest.approx(0.0, abs=1e-9)
    # 剔大盘后 S1-S2 相关量级显著下降（共同市场成分被剥离）
    assert raw.loc["S1", "S2"] > 0.3
    assert abs(exc.loc["S1", "S2"]) < raw.loc["S1", "S2"]


def test_inf_values_treated_as_missing_no_crash():
    """注入 inf：align_panel 当缺失处理(不让 std→NaN 漏剔)，beta 仍从有限点估出有限值。"""
    rng = np.random.RandomState(7)
    base = rng.normal(0, 1, 30)
    y = base * 1.4 + rng.normal(0, 0.05, 30)
    y[5] = np.inf
    df = _panel({"BASE": base.tolist(), "Y": y.tolist()})

    clean, ex = agg.align_panel(df, min_sample=15)
    assert "Y" in clean.columns        # 去掉 1 个 inf 点后仍 >=15 有效，保留
    assert "Y" not in ex["constant"]   # 没被 inf 导致的 NaN std 误判为常量
    b = agg.beta(df["Y"], df["BASE"])
    assert np.isfinite(b) and b == pytest.approx(1.4, abs=0.1)


def test_compute_window_structure():
    rng = np.random.RandomState(2)
    base = rng.normal(0, 1, 60)
    panel = _panel({
        "000001.SH": base.tolist(),
        "半导体": (1.3 * base + rng.normal(0, 0.4, 60)).tolist(),
        "黄金": (-0.5 * base + rng.normal(0, 0.4, 60)).tolist(),
    })
    res = agg.compute_window(panel, window=60, base_index="000001.SH",
                             index_cols=["000001.SH"], sector_cols=["半导体", "黄金"],
                             min_sample=20)
    assert res["sample_days"] == 60
    assert res["betas"]["半导体"]["000001.SH"] == pytest.approx(1.3, abs=0.1)
    assert res["betas"]["黄金"]["000001.SH"] < 0  # 逆向 β<0
    assert "半导体" in res["raw"].columns
    assert "半导体" in res["excess"].columns  # 残差矩阵含板块列
    assert "000001.SH" not in res["excess"].columns  # base 不进残差矩阵


def test_compute_dual_window_keyed():
    rng = np.random.RandomState(3)
    base = rng.normal(0, 1, 80)
    panel = _panel({
        "000001.SH": base.tolist(),
        "半导体": (1.2 * base + rng.normal(0, 0.3, 80)).tolist(),
    })
    out = agg.compute(panel, windows=[20, 60], base_index="000001.SH",
                      index_cols=["000001.SH"], sector_cols=["半导体"],
                      min_sample_by_window={20: 15, 60: 20})
    assert set(out.keys()) == {20, 60}
    assert out[20]["sample_days"] == 20
    assert out[60]["sample_days"] == 60
