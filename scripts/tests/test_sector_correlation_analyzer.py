"""analyzer 单测（阶段2）：阈值分类 / 上三角对列表 / 最负伙伴 / 空矩阵兜底。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from services.sector_correlation import analyzer as ana


def test_classify_corr_raw_boundaries():
    assert ana.classify_corr(0.85, "raw") == "强同向"
    assert ana.classify_corr(0.5, "raw") == "弱同向"
    assert ana.classify_corr(0.0, "raw") == "独立"
    assert ana.classify_corr(-0.5, "raw") == "弱逆向"
    assert ana.classify_corr(-0.8, "raw") == "强逆向"
    assert ana.classify_corr(float("nan"), "raw") == "无数据"


def test_classify_corr_boundary_values():
    """阈值边界（含/不含）：恰好等于阈值归强类，略低归下一档（review T2）。"""
    assert ana.classify_corr(0.7, "raw") == "强同向"      # >= strong
    assert ana.classify_corr(0.6999, "raw") == "弱同向"   # < strong
    assert ana.classify_corr(0.4, "raw") == "弱同向"      # >= weak
    assert ana.classify_corr(0.3999, "raw") == "独立"     # < weak
    assert ana.classify_corr(-0.4, "raw") == "弱逆向"     # <= weak_inv
    assert ana.classify_corr(-0.7, "raw") == "强逆向"     # <= strong_inv


def test_classify_corr_excess_thresholds_lower():
    # 超额阈值更低：0.5 在原始是弱同向，在超额已是强同向
    assert ana.classify_corr(0.5, "excess") == "强同向"
    assert ana.classify_corr(-0.3, "excess") == "弱逆向"
    assert ana.classify_corr(-0.45, "excess") == "强逆向"


def test_pairs_from_matrix_upper_triangle_sorted_skip_nan():
    cols = ["A", "B", "C"]
    m = pd.DataFrame(
        [[1.0, 0.8, np.nan], [0.8, 1.0, -0.6], [np.nan, -0.6, 1.0]],
        index=cols, columns=cols,
    )
    pairs = ana.pairs_from_matrix(m, cols, "raw", sort="desc")
    # 跳过 A-C(NaN)，只剩 A-B / B-C；降序
    keys = [(p["a"], p["b"]) for p in pairs]
    assert ("A", "C") not in keys and ("C", "A") not in keys
    assert pairs[0]["corr"] == 0.8 and pairs[0]["label"] == "强同向"
    assert pairs[-1]["corr"] == -0.6


def test_top_inverse_partners():
    excess_pairs = [
        {"a": "AI", "b": "黄金", "corr": -0.45},
        {"a": "AI", "b": "电力", "corr": -0.30},
        {"a": "AI", "b": "半导体", "corr": 0.50},
    ]
    out = ana.top_inverse_partners(excess_pairs, k=2)
    assert [p["partner"] for p in out["AI"]] == ["黄金", "电力"]  # 最负在前


def test_classify_sector_vs_index():
    # 黄金 vs 上证 = -0.50 → 弱逆向(raw 弱逆向阈值 -0.4);-0.31 会判"独立",故用 -0.5
    raw = pd.DataFrame(
        [[1.0, 0.79, -0.50], [0.79, 1.0, -0.2], [-0.50, -0.2, 1.0]],
        index=["000001.SH", "半导体", "黄金"], columns=["000001.SH", "半导体", "黄金"],
    )
    wr = {
        "raw": raw,
        "betas": {"半导体": {"000001.SH": 1.38}, "黄金": {"000001.SH": -0.27}},
        "sector_cols": ["半导体", "黄金"],
        "index_cols": ["000001.SH"],
    }
    si = ana.classify_sector_vs_index(wr)
    assert si["半导体"]["000001.SH"]["label"] == "强同向"
    assert si["半导体"]["000001.SH"]["beta"] == 1.38
    assert si["黄金"]["000001.SH"]["label"] == "弱逆向"


def test_today_comovement_groups_by_sign_sorted():
    sectors = [
        {"name": "半导体", "type": "industry", "latest_change_pct": 2.1},
        {"name": "电力", "type": "industry", "latest_change_pct": -1.2},
        {"name": "存储芯片", "type": "concept", "latest_change_pct": 3.4},
        {"name": "白酒", "type": "industry", "latest_change_pct": -0.4},
        {"name": "持平板块", "type": "industry", "latest_change_pct": 0.0},
        {"name": "缺数据", "type": "industry", "latest_change_pct": None},
    ]
    co = ana.today_comovement(sectors)
    assert [x["name"] for x in co["up"]] == ["存储芯片", "半导体"]    # 齐涨降序(最涨在前)
    assert [x["name"] for x in co["down"]] == ["电力", "白酒"]        # 齐跌升序(最跌在前: -1.2<-0.4)
    # 0 与 None 不入任何组
    names = {x["name"] for x in co["up"] + co["down"]}
    assert "持平板块" not in names and "缺数据" not in names


def test_build_window_payload_empty_excess_safe():
    raw = pd.DataFrame([[1.0]], index=["000001.SH"], columns=["000001.SH"])
    wr = {
        "sample_days": 20, "raw": raw, "betas": {}, "excess": pd.DataFrame(),
        "sector_cols": [], "index_cols": ["000001.SH"],
    }
    payload = ana.build_window_payload(wr)
    assert payload["sample_days"] == 20
    assert payload["pair_excess"] == []
    assert payload["sector_index"] == {}
