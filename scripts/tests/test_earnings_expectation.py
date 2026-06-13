"""L2: 口径三·券商一致预期（H1 占比折算）单测。

expectation.py 是纯计算（无 IO），fixture 直接喂 report_rc / income 形状的行。
"""
from __future__ import annotations

import pytest

from services.earnings_digest import expectation


_RC_SEQ = [0]


def _rc(np, quarter="2026Q4", org=None, author="", report_date="20260601"):
    """默认每行不同机构（org 缺省自增），使按机构去重后仍是独立样本。"""
    if org is None:
        _RC_SEQ[0] += 1
        org = f"券商{_RC_SEQ[0]}"
    return {"quarter": quarter, "np": np, "org_name": org,
            "author_name": author, "report_date": report_date}


def _inc(end_date, n_income_attr_p, update_flag="1"):
    return {"end_date": end_date, "n_income_attr_p": n_income_attr_p,
            "report_type": "1", "update_flag": update_flag}


# ---------- annual_consensus_median ----------

def test_consensus_median_picks_target_year_quarter():
    rows = [_rc(10000.0, "2026Q4"), _rc(12000.0, "2026Q4"),
            _rc(99999.0, "2027Q4")]  # 别年份不计
    assert expectation.annual_consensus_median(rows, 2026) == 11000.0


def test_consensus_median_no_coverage_returns_none():
    assert expectation.annual_consensus_median([_rc(1.0, "2027Q4")], 2026) is None


def test_consensus_median_broker_weighted_not_row_weighted():
    """codex 回归：同一机构 120 天内多次修订只算 1 个样本（取最新报告日）。"""
    rows = [
        _rc(10000.0, org="券商A", report_date="20260301"),   # A 旧版
        _rc(99999.0, org="券商A", report_date="20260601"),   # A 修订后（取此）
        _rc(20000.0, org="券商B", report_date="20260501"),   # B
    ]
    # 按机构去重：A=99999、B=20000 → 中值 59999.5（非按行 median 的 20000）
    assert expectation.annual_consensus_median(rows, 2026) == pytest.approx(59999.5)


# ---------- h1_ratio ----------

def test_h1_ratio_average_of_valid_years():
    # 中报/全年（元）：2025 H1=5000万 全年=1亿(0.5)；2024 H1=4000万 全年=1亿(0.4)
    rows = [
        _inc("20250630", 5e7), _inc("20251231", 1e8),
        _inc("20240630", 4e7), _inc("20241231", 1e8),
    ]
    assert expectation.h1_ratio(rows, 2026) == pytest.approx(0.45)


def test_h1_ratio_skips_loss_years():
    # 2024 全年亏损 → 该年剔除；2023、2025 正常 → 仍够 2 个样本
    rows = [
        _inc("20250630", 5e7), _inc("20251231", 1e8),     # 0.5
        _inc("20240630", -1e7), _inc("20241231", -2e7),   # 亏损，剔除
        _inc("20230630", 3e7), _inc("20231231", 1e8),     # 0.3
    ]
    assert expectation.h1_ratio(rows, 2026) == pytest.approx(0.4)


def test_h1_ratio_insufficient_samples_returns_none():
    rows = [_inc("20250630", 5e7), _inc("20251231", 1e8)]  # 仅 1 年
    assert expectation.h1_ratio(rows, 2026) is None


def test_h1_ratio_anomalous_ratio_excluded():
    # H1 > 全年（占比 >1.2，如全年因 H2 亏损缩水）→ 剔除 → 样本不足
    rows = [
        _inc("20250630", 9e7), _inc("20251231", 1e8),     # 0.9 有效
        _inc("20240630", 1.5e8), _inc("20241231", 1e8),   # 1.5 > 1.2 剔除
    ]
    assert expectation.h1_ratio(rows, 2026) is None  # 仅 1 个有效样本


def test_h1_ratio_dedup_same_period_takes_latest_update_flag():
    rows = [
        _inc("20251231", 1e8, update_flag="0"),
        _inc("20251231", 2e8, update_flag="1"),  # 修正后胜出
        _inc("20250630", 5e7),
        _inc("20240630", 4e7), _inc("20241231", 1e8),
    ]
    # 2025: 5000万 / 2亿 = 0.25；2024: 0.4 → 均值 0.325
    assert expectation.h1_ratio(rows, 2026) == pytest.approx(0.325)


# ---------- assess ----------

def _income_5050():
    """稳定 H1 占比 0.5 的两年样本。"""
    return [_inc("20250630", 5e7), _inc("20251231", 1e8),
            _inc("20240630", 5e7), _inc("20241231", 1e8)]


@pytest.mark.parametrize("forecast_mid,expected", [
    (6500.0, "超预期"),    # 隐含 H1=11000×0.5=5500，6500 偏 +18% > 10%
    (5500.0, "符合预期"),  # 恰好等于隐含
    (4500.0, "低于预期"),  # 偏 -18% < -10%
])
def test_assess_verdicts(forecast_mid, expected):
    rc = [_rc(10000.0), _rc(12000.0)]  # 全年一致中值 11000 万
    result = expectation.assess(forecast_mid, rc, _income_5050(), 2026)
    assert result is not None
    assert result["label"] == expected
    assert result["implied_h1_wan"] == pytest.approx(5500.0)


def test_assess_no_consensus_returns_none():
    result = expectation.assess(6000.0, [], _income_5050(), 2026)
    assert result is None


def test_assess_no_ratio_returns_none():
    result = expectation.assess(6000.0, [_rc(10000.0)], [_inc("20250630", 5e7)], 2026)
    assert result is None  # H1 占比样本不足


def test_assess_none_forecast_returns_none():
    assert expectation.assess(None, [_rc(10000.0)], _income_5050(), 2026) is None


def test_assess_negative_forecast_mid_returns_none():
    """codex 回归：首亏/续亏（forecast 中值为负）→ 不套 H1 正盈利模型。"""
    assert expectation.assess(-3000.0, [_rc(10000.0)], _income_5050(), 2026) is None


def test_assess_negative_consensus_returns_none():
    """codex 回归：券商预测全年亏损（consensus 为负）→ 不硬折算。"""
    assert expectation.assess(5000.0, [_rc(-8000.0)], _income_5050(), 2026) is None


def test_assess_tolerance_boundary():
    rc = [_rc(10000.0)]  # 隐含 H1 = 10000×0.5 = 5000
    # +10% 恰好（5500）→ 符合（deviation == tol，不算超）
    assert expectation.assess(5500.0, rc, _income_5050(), 2026)["label"] == "符合预期"
    # 略超 10% → 超预期
    assert expectation.assess(5501.0, rc, _income_5050(), 2026)["label"] == "超预期"
