"""margin_index_correlation.aggregator 纯函数单测（无 IO）。

锁口径：两融余额→日变化率(%) 后再与指数涨跌幅(%) 做相关（漏做 pct_change = 伪相关）。
锁 lag 符号：lag>0 = 两融滞后指数。锁背离方向：涨指两融降 / 跌指两融升。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from services.margin_index_correlation import aggregator as agg


def _s(values, start="2026-01-01"):
    idx = pd.date_range(start, periods=len(values), freq="D").strftime("%Y-%m-%d")
    return pd.Series(values, index=idx, dtype=float)


# ── margin_returns：余额(水位) → 日变化率(%)，锁口径 ──
def test_margin_returns_pct_change_not_level():
    bal = _s([100.0, 102.0, 101.0])
    ret = agg.margin_returns(bal)
    assert np.isnan(ret.iloc[0])  # 首行无前值
    assert ret.iloc[1] == pytest.approx(2.0)            # (102-100)/100*100
    assert ret.iloc[2] == pytest.approx(-0.98039, abs=1e-4)  # (101-102)/102*100
    # 绝不返回绝对水位
    assert ret.iloc[1] != 102.0


# ── lagged_correlation：lag>0=两融滞后 ──
def test_lagged_margin_lags_index_positive_lag():
    """构造两融今日变化 = 指数 2 日前变化 → 两融滞后指数 2 日 → best_lag=+2、relation 含滞后。"""
    rng = np.random.RandomState(0)
    idx = _s(rng.uniform(-3, 3, 40))
    margin = idx.shift(2)  # margin[t] = idx[t-2] → 两融滞后指数 2 日
    out = agg.lagged_correlation(margin, idx, max_lag=3, min_sample=10)
    assert out["best_lag"] == 2
    assert out["relation"] == "两融滞后"
    assert out["best_corr"] == pytest.approx(1.0, abs=1e-6)


def test_lagged_margin_leads_index_negative_lag():
    rng = np.random.RandomState(1)
    idx = _s(rng.uniform(-3, 3, 40))
    margin = idx.shift(-2)  # margin[t] = idx[t+2] → 两融领先指数 2 日
    out = agg.lagged_correlation(margin, idx, max_lag=3, min_sample=10)
    assert out["best_lag"] == -2
    assert out["relation"] == "两融领先"


def test_lagged_insufficient_sample():
    idx = _s([1.0, -1.0, 2.0])
    margin = _s([1.0, -1.0, 2.0])
    out = agg.lagged_correlation(margin, idx, max_lag=3, min_sample=10)
    assert out["relation"] == "样本不足"
    assert out["best_lag"] is None


# ── detect_divergence：涨指两融降 / 跌指两融升 ──
def test_divergence_index_up_margin_down():
    # 指数近 5 日累计涨，两融余额累计降 → 涨指两融降
    index_ret = _s([1.0, 1.0, 1.0, 1.0, 1.0, 1.0])  # 每日 +1%
    balances = _s([1000.0, 995.0, 990.0, 985.0, 980.0, 975.0])  # 余额持续降
    out = agg.detect_divergence(balances, index_ret, windows=[5], min_gap=0.5)
    w = out[5]
    assert w["diverged"] is True
    assert w["type"] == "涨指两融降"
    assert w["index_cum"] > 0 and w["margin_cum"] < 0


def test_divergence_index_down_margin_up():
    index_ret = _s([-1.0, -1.0, -1.0, -1.0, -1.0, -1.0])
    balances = _s([1000.0, 1005.0, 1010.0, 1015.0, 1020.0, 1025.0])
    out = agg.detect_divergence(balances, index_ret, windows=[5], min_gap=0.5)
    assert out[5]["type"] == "跌指两融升"
    assert out[5]["diverged"] is True


def test_divergence_same_direction_no_signal():
    index_ret = _s([1.0, 1.0, 1.0, 1.0, 1.0, 1.0])
    balances = _s([1000.0, 1005.0, 1010.0, 1015.0, 1020.0, 1025.0])
    out = agg.detect_divergence(balances, index_ret, windows=[5], min_gap=0.5)
    assert out[5]["diverged"] is False
    assert out[5]["type"] == "无背离"


def test_divergence_margin_missing_trading_day_flags_gap():
    """两融在窗内缺某交易日 → 标「日期缺口」不评估，杜绝稀疏日当连续5日伪造预警。"""
    index_ret = _s([1.0, 1.0, 1.0, 1.0, 1.0, 1.0])  # 指数 6 个连续交易日
    # 两融缺中间一天（drop 第 3 个交易日），与指数脊柱不连续
    bal_full = _s([1000.0, 995.0, 990.0, 985.0, 980.0, 975.0])
    balances = bal_full.drop(bal_full.index[2])
    out = agg.detect_divergence(balances, index_ret, windows=[5], min_gap=0.5)
    assert out[5]["type"] == "日期缺口"
    assert out[5]["diverged"] is False


def test_divergence_tiny_gap_is_noise():
    """符号相反但幅度 < min_gap → 不算背离（噪声过滤）。"""
    index_ret = _s([0.0, 0.05, 0.0, 0.0, 0.0, 0.0])  # 窗口内累计 +0.05%
    balances = _s([1000.0, 999.95, 999.95, 999.95, 999.95, 999.95])  # 累计 -0.005%
    out = agg.detect_divergence(balances, index_ret, windows=[5], min_gap=0.5)
    # index_cum≈+0.05 / margin_cum≈-0.005 符号相反但 |差|≈0.055 < 0.5 → 噪声不告警
    assert out[5]["diverged"] is False


# ── balance_levels：水位 + 趋势 ──
def test_balance_levels_streaks_and_dod():
    bal = _s([100.0, 101.0, 102.0, 103.0, 104.0])  # 连增 4 日
    lv = agg.balance_levels(bal)
    assert lv["latest_yi"] == pytest.approx(104.0)
    assert lv["dod_pct"] == pytest.approx(0.97, abs=1e-2)  # 日环比保留 2 位显示精度
    assert lv["up_streak"] == 4
    assert lv["down_streak"] == 0
    assert lv["pctile_20d"] == pytest.approx(1.0)  # 最新是窗口最大 → 分位 1.0


def test_balance_levels_down_streak():
    bal = _s([104.0, 103.0, 102.0])
    lv = agg.balance_levels(bal)
    assert lv["down_streak"] == 2
    assert lv["up_streak"] == 0


def test_balance_levels_tiny_ma20_no_explosion():
    """ma20 极小（近 0）时 vs_ma20 返 None，不爆百万级百分比（_EPS 守门）。"""
    bal = _s([1e-13, 1e-13, 1e-13])  # 均值 < _EPS
    lv = agg.balance_levels(bal)
    assert lv["vs_ma20"] is None


# ── 边界修复回归 ──
def test_margin_returns_zero_crossing_to_nan():
    """余额穿 0 产生的 inf 必须清成 NaN，不外泄污染统计。"""
    ret = agg.margin_returns(_s([100.0, 0.0, 50.0]))
    assert ret.iloc[1] == pytest.approx(-100.0)  # (0-100)/100*100，有限值保留
    assert np.isnan(ret.iloc[2])                  # (50-0)/0=inf → 清成 NaN
    assert not np.isinf(ret.to_numpy()).any()


def test_divergence_zero_start_balance_not_evaluable():
    """期初余额为 0 → margin_cum 无法定义 → type='无法评估'，不冒充'无背离'。"""
    index_ret = _s([1.0, 1.0, 1.0, 1.0, 1.0, 1.0])
    balances = _s([0.0, 10.0, 20.0, 30.0, 40.0, 50.0])
    out = agg.detect_divergence(balances, index_ret, windows=[5], min_gap=0.5)
    assert out[5]["margin_cum"] is None
    assert out[5]["type"] == "无法评估"
    assert out[5]["diverged"] is False


# ── summarize_divergence_risk：把多窗口/多指数背离汇总成预警等级 ──
def test_divergence_risk_high_when_short_and_medium_windows_diverge():
    divergence = {
        "total:000001.SH": {
            "5": {"index_cum": -1.2, "margin_cum": 1.1, "diverged": True,
                  "type": "跌指两融升", "magnitude": 2.3},
            "20": {"index_cum": -3.4, "margin_cum": 2.0, "diverged": True,
                   "type": "跌指两融升", "magnitude": 5.4},
        },
        "total:399300.SZ": {
            "5": {"index_cum": -0.8, "margin_cum": 1.0, "diverged": True,
                  "type": "跌指两融升", "magnitude": 1.8},
        },
    }
    indices = [
        {"pair_key": "total:000001.SH", "index_name": "上证指数", "margin_key": "total"},
        {"pair_key": "total:399300.SZ", "index_name": "沪深300", "margin_key": "total"},
    ]
    out = agg.summarize_divergence_risk(divergence, indices=indices)
    assert out["level"] == "high"
    assert out["score"] >= 7
    assert "5日+20日" in out["headline"]
    assert any("跌指两融升" in reason for reason in out["reasons"])


def test_divergence_risk_low_for_single_mild_opposite_direction():
    divergence = {
        "total:000001.SH": {
            "5": {"index_cum": 1.1, "margin_cum": -0.6, "diverged": True,
                  "type": "涨指两融降", "magnitude": 1.7},
        },
    }
    indices = [{"pair_key": "total:000001.SH", "index_name": "上证指数", "margin_key": "total"}]
    out = agg.summarize_divergence_risk(divergence, indices=indices)
    assert out["level"] == "low"
    assert out["score"] > 0
    assert out["hit_count"] == 1


def test_divergence_risk_dedupes_same_index_window_from_cross_pair():
    divergence = {
        "total:000001.SH": {
            "5": {"index_cum": -1.2, "margin_cum": 1.1, "diverged": True,
                  "type": "跌指两融升", "magnitude": 2.3},
        },
        "sse:000001.SH": {
            "5": {"index_cum": -1.2, "margin_cum": 1.0, "diverged": True,
                  "type": "跌指两融升", "magnitude": 2.2},
        },
    }
    indices = [
        {"pair_key": "total:000001.SH", "index_name": "上证指数", "margin_key": "total"},
        {"pair_key": "sse:000001.SH", "index_name": "上证指数", "margin_key": "sse",
         "margin_label": "沪市两融"},
    ]
    out = agg.summarize_divergence_risk(divergence, indices=indices)
    assert out["level"] == "medium"
    assert out["hit_count"] == 1


def test_divergence_risk_unevaluated_when_only_gaps():
    divergence = {
        "total:000001.SH": {
            "5": {"index_cum": None, "margin_cum": None, "diverged": False,
                  "type": "日期缺口", "magnitude": None},
        },
    }
    out = agg.summarize_divergence_risk(divergence, indices=[])
    assert out["level"] == "unevaluated"
    assert out["score"] == 0
    assert out["unevaluated_count"] == 1


# ── sync_correlation：复用 sector align_panel/raw_correlation ──
def test_sync_correlation_positive():
    rng = np.random.RandomState(2)
    margin = _s(rng.uniform(-2, 2, 30))
    index_ret = margin * 1.0  # 完全正相关
    out = agg.sync_correlation(margin, index_ret, windows=[20], min_sample_by_window={20: 15})
    assert out[20]["corr"] == pytest.approx(1.0, abs=1e-6)
    assert out[20]["label"] == "强同向"


def test_sync_correlation_insufficient_returns_none():
    margin = _s([1.0, -1.0, 2.0])
    index_ret = _s([1.0, -1.0, 2.0])
    out = agg.sync_correlation(margin, index_ret, windows=[20], min_sample_by_window={20: 15})
    assert out[20]["corr"] is None
