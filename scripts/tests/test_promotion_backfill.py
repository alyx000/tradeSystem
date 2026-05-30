"""
晋级率（promotion_backfill）单元测试

验证 PremiumCollector._compute_promotion：
  - 首板→二板、二板→三板 晋级成功率（基于 T-1 涨停 list × T 日涨停 list 的 code join）
  - 边界：分母为 0、T 日涨停 list 获取失败、未晋级/炸板
渲染层：_render_style_factors 的晋级率段
"""
from unittest.mock import MagicMock

import pytest

from collectors.premium import PremiumCollector
from providers.base import DataResult


def _reg_with_tday_limit(t_day_stocks):
    """构造 registry：get_limit_up_list(T) 返回指定 T 日涨停 list。"""
    reg = MagicMock()

    def _call(method, *args, **kwargs):
        if method == "get_limit_up_list":
            return DataResult(
                data={"count": len(t_day_stocks), "stocks": t_day_stocks},
                source="test",
            )
        return DataResult(data=None, source="test", error="skip")

    reg.call = _call
    return reg


def _reg_limit_fail():
    reg = MagicMock()
    reg.call = MagicMock(
        return_value=DataResult(data=None, source="test", error="无涨停数据")
    )
    return reg


class TestComputePromotion:
    def test_first_to_second_and_second_to_third(self):
        prev_stocks = [
            {"code": "A.SZ", "name": "甲", "limit_times": 1},
            {"code": "B.SZ", "name": "乙", "limit_times": 1},
            {"code": "C.SZ", "name": "丙", "limit_times": 1},
            {"code": "D.SZ", "name": "丁", "limit_times": 2},
        ]
        # T 日：甲、乙 晋级二板；丙 炸板（不在涨停列表）；丁 晋级三板
        t_day = [
            {"code": "A.SZ", "name": "甲", "limit_times": 2},
            {"code": "B.SZ", "name": "乙", "limit_times": 2},
            {"code": "D.SZ", "name": "丁", "limit_times": 3},
        ]
        pc = PremiumCollector(_reg_with_tday_limit(t_day))
        promo = pc._compute_promotion(prev_stocks, "2026-03-28", "2026-03-27")

        assert promo is not None
        assert promo["trade_date"] == "2026-03-28"
        assert promo["prev_date"] == "2026-03-27"

        fts = promo["first_to_second"]
        assert fts["base"] == 3
        assert fts["promoted"] == 2
        assert fts["rate"] == round(2 / 3, 3)
        assert set(fts["promoted_names"]) == {"甲", "乙"}
        assert fts["failed_names"] == ["丙"]

        stt = promo["second_to_third"]
        assert stt["base"] == 1
        assert stt["promoted"] == 1
        assert stt["rate"] == 1.0

    def test_zero_base_rate_none(self):
        """T-1 无首板时，first_to_second.rate 为 None、base 为 0，不除零崩溃"""
        prev_stocks = [{"code": "D.SZ", "name": "丁", "limit_times": 2}]
        t_day = [{"code": "D.SZ", "name": "丁", "limit_times": 3}]
        pc = PremiumCollector(_reg_with_tday_limit(t_day))
        promo = pc._compute_promotion(prev_stocks, "2026-03-28", "2026-03-27")

        assert promo["first_to_second"]["base"] == 0
        assert promo["first_to_second"]["rate"] is None
        assert promo["first_to_second"]["promoted"] == 0

    def test_limit_times_missing_not_counted(self):
        """limit_times 缺失/None/0 的脏数据不计入任何档位分母（不虚增首板）"""
        prev_stocks = [
            {"code": "A.SZ", "name": "甲", "limit_times": 1},   # 正常首板
            {"code": "B.SZ", "name": "乙"},                      # 缺 limit_times → 不计
            {"code": "C.SZ", "name": "丙", "limit_times": None}, # None → 不计
            {"code": "E.SZ", "name": "戊", "limit_times": 0},    # 0 → 不计
        ]
        t_day = [{"code": "A.SZ", "name": "甲", "limit_times": 2}]
        pc = PremiumCollector(_reg_with_tday_limit(t_day))
        promo = pc._compute_promotion(prev_stocks, "2026-03-28", "2026-03-27")

        # 只有 A 是合法首板：base=1，而非把 B/C/E 也算进来变成 4
        assert promo["first_to_second"]["base"] == 1
        assert promo["first_to_second"]["promoted"] == 1
        assert promo["first_to_second"]["rate"] == 1.0

    def test_tday_limit_fail_returns_none(self):
        """T 日涨停 list 获取失败 → 返回 None（无法计算晋级率）"""
        prev_stocks = [{"code": "A.SZ", "name": "甲", "limit_times": 1}]
        pc = PremiumCollector(_reg_limit_fail())
        promo = pc._compute_promotion(prev_stocks, "2026-03-28", "2026-03-27")
        assert promo is None

    def test_all_failed_promotion(self):
        """昨日首板今日全炸板：promoted=0, rate=0.0"""
        prev_stocks = [
            {"code": "A.SZ", "name": "甲", "limit_times": 1},
            {"code": "B.SZ", "name": "乙", "limit_times": 1},
        ]
        t_day = []  # 今日无涨停
        pc = PremiumCollector(_reg_with_tday_limit(t_day))
        promo = pc._compute_promotion(prev_stocks, "2026-03-28", "2026-03-27")
        assert promo["first_to_second"]["base"] == 2
        assert promo["first_to_second"]["promoted"] == 0
        assert promo["first_to_second"]["rate"] == 0.0


class TestRenderPromotion:
    def test_render_promotion_block(self):
        from generators.report import _render_style_factors

        raw_data = {
            "style_factors": {
                "promotion": {
                    "trade_date": "2026-03-28",
                    "prev_date": "2026-03-27",
                    "first_to_second": {"base": 50, "promoted": 12, "rate": 0.24,
                                        "promoted_names": ["甲", "乙"], "failed_names": []},
                    "second_to_third": {"base": 8, "promoted": 3, "rate": 0.375,
                                        "promoted_names": ["丁"], "failed_names": []},
                },
            }
        }
        lines = []
        new_idx = _render_style_factors(lines, raw_data, 8)
        text = "\n".join(lines)

        assert "晋级率" in text
        assert "首板→二板" in text
        assert "12/50" in text
        assert "二板→三板" in text
        assert new_idx == 9

    def test_render_promotion_alone_triggers_section(self):
        from generators.report import _render_style_factors
        raw_data = {
            "style_factors": {
                "promotion": {
                    "first_to_second": {"base": 50, "promoted": 12, "rate": 0.24,
                                        "promoted_names": [], "failed_names": []},
                    "second_to_third": {"base": 0, "promoted": 0, "rate": None,
                                        "promoted_names": [], "failed_names": []},
                },
            }
        }
        lines = []
        new_idx = _render_style_factors(lines, raw_data, 8)
        text = "\n".join(lines)
        assert "风格化赚钱效应" in text
        assert "晋级率" in text
        assert new_idx == 9
