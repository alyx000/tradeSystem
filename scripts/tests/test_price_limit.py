"""涨跌停价计算工具单测（utils/price_limit.py）。

覆盖：板块涨跌幅比例分类、ETF/LOF 无限价、基于前收盘的算价、ROUND_HALF_UP 舍入。
"""
from __future__ import annotations

import pytest

from utils.price_limit import limit_pct_for, compute_limit_prices


class TestLimitPctFor:
    def test_main_board_sh_is_10(self):
        assert limit_pct_for("600000.SH") == 10.0

    def test_main_board_sz_is_10(self):
        assert limit_pct_for("000001.SZ") == 10.0

    def test_star_market_688_is_20(self):
        assert limit_pct_for("688981.SH") == 20.0

    def test_chinext_300_is_20(self):
        assert limit_pct_for("300750.SZ") == 20.0

    def test_beijing_exchange_is_30(self):
        assert limit_pct_for("830799.BJ") == 30.0

    def test_beijing_exchange_920_prefix_is_30(self):
        # 北交所新代码段 920xxx：旧逻辑只认 8/43 前缀会落到 10%
        assert limit_pct_for("920001.BJ") == 30.0

    def test_st_name_is_5(self):
        assert limit_pct_for("600000.SH", "ST康美") == 5.0

    def test_etf_159_is_none(self):
        assert limit_pct_for("159516.SZ", "半导设备") is None

    def test_etf_510_is_none(self):
        assert limit_pct_for("510300.SH") is None

    # 板块 × ST 矩阵：只有主板 ST 才 5%，创业板/科创板/北交所 ST 仍按板块比例
    def test_main_board_st_is_5(self):
        assert limit_pct_for("600000.SH", "*ST康美") == 5.0

    def test_chinext_st_still_20(self):
        assert limit_pct_for("300750.SZ", "*ST宁德") == 20.0

    def test_star_market_st_still_20(self):
        assert limit_pct_for("688981.SH", "ST中芯") == 20.0

    def test_beijing_st_still_30(self):
        assert limit_pct_for("920001.BJ", "ST北证") == 30.0

    # is_st 权威标志优先于名称：名称不含 ST 但 is_st=True（来自 stock_st 名单）→ 主板按 5%
    def test_is_st_flag_overrides_name_main_board(self):
        assert limit_pct_for("600000.SH", "某主板股", is_st=True) == 5.0

    def test_is_st_false_overrides_name(self):
        # is_st 显式 False 时即便名称含 ST 也不按 ST（权威名单说不是 ST）
        assert limit_pct_for("600000.SH", "ST历史名", is_st=False) == 10.0


class TestComputeLimitPrices:
    def test_main_board_round_half_up(self):
        # 大族激光真实场景：prev_close 146.28，主板 10%
        out = compute_limit_prices(146.28, "002008.SZ", "大族激光")
        assert out["up_limit"] == 160.91   # 146.28 * 1.1 = 160.908 → 160.91
        assert out["down_limit"] == 131.65  # 146.28 * 0.9 = 131.652 → 131.65
        assert out["pre_close"] == 146.28

    def test_round_half_up_boundary(self):
        # 10.05 * 1.1 = 11.055 → ROUND_HALF_UP 11.06（Python 默认 round 会给 11.05/银行家舍入）
        out = compute_limit_prices(10.05, "600000.SH")
        assert out["up_limit"] == 11.06

    def test_star_market_20pct(self):
        out = compute_limit_prices(20.0, "688981.SH")
        assert out["up_limit"] == 24.0
        assert out["down_limit"] == 16.0

    def test_etf_no_limit(self):
        out = compute_limit_prices(1.28, "159516.SZ", "半导设备")
        assert out["up_limit"] is None
        assert out["down_limit"] is None
        assert out["pre_close"] == 1.28

    def test_missing_prev_close(self):
        out = compute_limit_prices(None, "600000.SH")
        assert out["up_limit"] is None
        assert out["down_limit"] is None
        assert out["pre_close"] is None

    def test_zero_prev_close_yields_none(self):
        out = compute_limit_prices(0.0, "600000.SH")
        assert out["up_limit"] is None
        assert out["down_limit"] is None

    def test_negative_prev_close_yields_none(self):
        out = compute_limit_prices(-5.0, "600000.SH")
        assert out["up_limit"] is None
        assert out["down_limit"] is None

    def test_nan_prev_close_yields_none(self):
        out = compute_limit_prices(float("nan"), "600000.SH")
        assert out["up_limit"] is None
        assert out["down_limit"] is None

    def test_beijing_920_compute(self):
        out = compute_limit_prices(10.0, "920001.BJ")
        assert out["up_limit"] == 13.0   # 北交所 30%
        assert out["down_limit"] == 7.0
