"""value-watch 周线层：完成周聚合 + 周均线 + 周 MACD（纯函数，无 pandas 依赖）。

spec v8 口径：ISO 自然周分组；目标周有剩余 open 日则剔除；MACD ewm(adjust=False)
等价递推（EMA seed=首值）；MA 窗口不足返回 None。
"""
from __future__ import annotations

import math

from services.value_watch import weekly


def _d(date, close, vol=100.0):
    return {"date": date, "close": close, "volume": vol}


def test_target_week_incomplete_dropped():
    daily = [_d("2026-07-13", 10), _d("2026-07-14", 11), _d("2026-07-17", 12),
             _d("2026-07-20", 13)]  # 7-20(周一)属目标周 W30
    weeks = weekly.aggregate_completed_weeks(
        daily, "2026-07-20", target_week_has_remaining_open_days=True)
    assert [w["week_end"] for w in weeks] == ["2026-07-17"]
    assert weeks[0]["close"] == 12 and weeks[0]["volume"] == 300.0


def test_target_week_complete_when_no_remaining_open_days():
    daily = [_d("2026-07-13", 10), _d("2026-07-17", 12)]
    weeks = weekly.aggregate_completed_weeks(
        daily, "2026-07-17", target_week_has_remaining_open_days=False)
    assert [w["week_end"] for w in weeks] == ["2026-07-17"]  # 周五收盘后当周即完成


def test_holiday_week_end_not_friday():
    # 2026-10 假设国庆:10-08(周四)/10-09(周五)开市,前一完成周最后 bar 是 09-28(周一)
    daily = [_d("2026-09-28", 10), _d("2026-10-08", 11), _d("2026-10-09", 12)]
    weeks = weekly.aggregate_completed_weeks(
        daily, "2026-10-12", target_week_has_remaining_open_days=True)
    assert weeks[0]["week_end"] == "2026-09-28"   # 该周只有周一有 bar → week_end 非周五
    assert weeks[1]["week_end"] == "2026-10-09"


def test_volume_none_propagates():
    daily = [_d("2026-07-13", 10), {"date": "2026-07-14", "close": 11, "volume": None},
             _d("2026-07-17", 12)]
    weeks = weekly.aggregate_completed_weeks(
        daily, "2026-07-20", target_week_has_remaining_open_days=True)
    assert weeks[0]["volume"] is None   # 任一缺失 → 周量不可信置 None


def test_ma_none_before_window():
    ma = weekly.weekly_ma([1.0] * 6, 5)
    assert ma[:4] == [None] * 4
    assert ma[4] == 1.0 and ma[5] == 1.0


def test_ma_arithmetic():
    ma = weekly.weekly_ma([1.0, 2.0, 3.0], 2)
    assert ma == [None, 1.5, 2.5]


def test_macd_length_and_uptrend_sign():
    closes = [float(i) for i in range(1, 60)]
    dif, dea = weekly.weekly_macd(closes)
    assert len(dif) == len(dea) == len(closes)
    assert dif[-1] > 0 and dea[-1] > 0     # 单调上行 warm-up 后必上零轴


def test_macd_matches_pandas_ewm_reference():
    """递推实现与 pandas ewm(adjust=False) 数值一致（抽查末值）。"""
    import pandas as pd

    closes = [10.0, 10.5, 10.2, 11.0, 11.4, 11.1, 12.0, 12.5, 12.2, 13.0,
              12.8, 13.5, 14.0, 13.6, 14.2, 15.0, 14.8, 15.5, 16.0, 15.7]
    dif, dea = weekly.weekly_macd(closes)
    s = pd.Series(closes)
    ema12 = s.ewm(span=12, adjust=False).mean()
    ema26 = s.ewm(span=26, adjust=False).mean()
    ref_dif = ema12 - ema26
    ref_dea = ref_dif.ewm(span=9, adjust=False).mean()
    assert math.isclose(dif[-1], float(ref_dif.iloc[-1]), rel_tol=1e-9)
    assert math.isclose(dea[-1], float(ref_dea.iloc[-1]), rel_tol=1e-9)
