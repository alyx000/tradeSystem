import pytest
from services.tail_scan import indicators as ind


def test_gain_nd():
    closes = [10.0, 10.5, 11.0, 11.5, 12.0]  # T-5..T-1
    # 近5日涨幅 = live(13.2)/closes[-5](10.0) -1 = 32%
    assert ind.gain_nd(closes, 13.2, 5) == pytest.approx(32.0, abs=1e-6)


def test_gain_nd_insufficient_returns_none():
    assert ind.gain_nd([10.0, 11.0], 12.0, 5) is None


def test_ma_and_above_all():
    closes = [9, 9, 9, 9, 10, 10, 10, 10, 10, 11.0]  # len 10
    assert ind.ma(closes, 5) == pytest.approx(10.2, abs=1e-6)
    assert ind.above_all_ma(20.0, closes, windows=(5, 10)) is True
    assert ind.above_all_ma(1.0, closes, windows=(5, 10)) is False


def test_up_days():
    bars = [{"pct_chg": -1}, {"pct_chg": 2}, {"pct_chg": 3}, {"pct_chg": 5}]
    assert ind.up_days(bars) == 3


def test_dist_to_high_and_break():
    highs = [10, 11, 12, 11, 10.0]
    assert ind.dist_to_high(13.2, highs, 5) == pytest.approx(10.0, abs=1e-6)
    assert ind.broke_prior_high(13.0, highs, 5) is True
    assert ind.broke_prior_high(11.0, highs, 5) is False
