"""
NodeSignalAnalyzer 单元测试
"""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from analyzers.node_signals import NodeSignalAnalyzer, _safe_float, _safe_int


# ──────────────────────────────────────────────────────────────────────────────
# 辅助工厂
# ──────────────────────────────────────────────────────────────────────────────

def _make_day_yaml(base_dir: Path, date: str, *,
                   sh_close: float = 3300.0,
                   total_billion: float = 9000.0,
                   limit_up_count: int = 80,
                   seal_rate: float = 85.0,
                   broken_rate: float = 15.0,
                   advance: int = 2500,
                   total_stocks: int = 5000,
                   above_ma5: bool = True,
                   above_ma20: bool = True,
                   above_ma5w: bool = True) -> None:
    """在 base_dir/daily/date/ 写入一个最小化的 post-market.yaml"""
    day_dir = base_dir / "daily" / date
    day_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "raw_data": {
            "indices": {"shanghai": {"close": sh_close}},
            "moving_averages": {
                "shanghai": {
                    "ma5": sh_close - 50,
                    "ma20": sh_close - 100,
                    "ma5w": sh_close - 30,
                    "above_ma5": above_ma5,
                    "above_ma20": above_ma20,
                    "above_ma5w": above_ma5w,
                }
            },
            "total_volume": {"total_billion": total_billion},
            "limit_up": {
                "count": limit_up_count,
                "seal_rate_pct": seal_rate,
                "broken_rate_pct": broken_rate,
            },
            "breadth": {
                "advance": advance,
                "total": total_stocks,
            },
        }
    }
    pm_path = day_dir / "post-market.yaml"
    with open(pm_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True)


def _make_today_result(*, sh_close=3400.0, total_billion=9500.0,
                       limit_up_count=100, seal_rate=80.0, broken_rate=20.0,
                       advance=3000, total_stocks=5000,
                       above_ma5=True, above_ma20=True, above_ma5w=True,
                       ma5=3350.0, ma20=3300.0, ma5w=3370.0) -> dict:
    return {
        "moving_averages": {
            "shanghai": {
                "ma5": ma5, "ma20": ma20, "ma5w": ma5w,
                "above_ma5": above_ma5,
                "above_ma20": above_ma20,
                "above_ma5w": above_ma5w,
            }
        },
        "total_volume": {"total_billion": total_billion},
        "limit_up": {
            "count": limit_up_count,
            "seal_rate_pct": seal_rate,
            "broken_rate_pct": broken_rate,
        },
        "breadth": {"advance": advance, "total": total_stocks},
    }


# ──────────────────────────────────────────────────────────────────────────────
# 基础行为测试
# ──────────────────────────────────────────────────────────────────────────────

class TestNodeSignalAnalyzerBasic:
    def test_returns_list(self, tmp_path):
        analyzer = NodeSignalAnalyzer(tmp_path)
        result = analyzer.analyze({}, "2026-04-01")
        assert isinstance(result, list)

    def test_no_history_returns_empty(self, tmp_path):
        analyzer = NodeSignalAnalyzer(tmp_path)
        today = _make_today_result()
        signals = analyzer.analyze(today, "2026-04-01")
        assert signals == []

    def test_signal_structure(self, tmp_path):
        for i in range(5):
            date = f"2026-03-{20 + i:02d}"
            _make_day_yaml(tmp_path, date, sh_close=3300.0, above_ma20=False)

        today = _make_today_result(above_ma20=True, ma20=3350.0, sh_close=3400.0)
        analyzer = NodeSignalAnalyzer(tmp_path)
        signals = analyzer.analyze(today, "2026-04-01")

        for s in signals:
            assert "type" in s
            assert "signal" in s
            assert "direction" in s
            assert "value" in s
            assert "description" in s
            assert s["direction"] in ("positive", "negative", "neutral")


# ──────────────────────────────────────────────────────────────────────────────
# MA 突破/跌破信号
# ──────────────────────────────────────────────────────────────────────────────

class TestMaCrossSignals:
    def test_ma20_breakout_detected(self, tmp_path):
        for i in range(5):
            _make_day_yaml(tmp_path, f"2026-03-{20 + i:02d}", above_ma20=False)

        today = _make_today_result(above_ma20=True, ma20=3350.0)
        analyzer = NodeSignalAnalyzer(tmp_path)
        signals = analyzer.analyze(today, "2026-04-01")

        ma_signals = [s for s in signals if s["type"] == "ma_cross" and "MA20" in s["signal"]]
        assert len(ma_signals) == 1
        assert ma_signals[0]["direction"] == "positive"
        assert "突破" in ma_signals[0]["signal"]

    def test_ma20_breakdown_detected(self, tmp_path):
        for i in range(5):
            _make_day_yaml(tmp_path, f"2026-03-{20 + i:02d}", above_ma20=True)

        today = _make_today_result(above_ma20=False, ma20=3350.0)
        analyzer = NodeSignalAnalyzer(tmp_path)
        signals = analyzer.analyze(today, "2026-04-01")

        ma_signals = [s for s in signals if s["type"] == "ma_cross" and "MA20" in s["signal"]]
        assert len(ma_signals) == 1
        assert ma_signals[0]["direction"] == "negative"
        assert "跌破" in ma_signals[0]["signal"]

    def test_no_ma_cross_when_unchanged(self, tmp_path):
        for i in range(5):
            _make_day_yaml(tmp_path, f"2026-03-{20 + i:02d}", above_ma20=True)

        today = _make_today_result(above_ma20=True)
        analyzer = NodeSignalAnalyzer(tmp_path)
        signals = analyzer.analyze(today, "2026-04-01")

        ma_signals = [s for s in signals if s["type"] == "ma_cross" and "MA20" in s["signal"]]
        assert len(ma_signals) == 0


# ──────────────────────────────────────────────────────────────────────────────
# 成交额极值信号
# ──────────────────────────────────────────────────────────────────────────────

class TestVolumeExtremeSignals:
    def test_5d_high_detected(self, tmp_path):
        for i in range(5):
            _make_day_yaml(tmp_path, f"2026-03-{20 + i:02d}", total_billion=8000.0)

        today = _make_today_result(total_billion=12000.0)
        analyzer = NodeSignalAnalyzer(tmp_path)
        signals = analyzer.analyze(today, "2026-04-01")

        vol_signals = [s for s in signals if s["type"] == "volume_extreme" and "新高" in s["signal"]]
        assert len(vol_signals) >= 1
        assert vol_signals[0]["direction"] == "positive"

    def test_5d_low_detected(self, tmp_path):
        for i in range(5):
            _make_day_yaml(tmp_path, f"2026-03-{20 + i:02d}", total_billion=9000.0)

        today = _make_today_result(total_billion=5000.0)
        analyzer = NodeSignalAnalyzer(tmp_path)
        signals = analyzer.analyze(today, "2026-04-01")

        vol_signals = [s for s in signals if s["type"] == "volume_extreme" and "新低" in s["signal"]]
        assert len(vol_signals) >= 1
        assert vol_signals[0]["direction"] == "negative"

    def test_volume_surge_30pct_above_ma5(self, tmp_path):
        for i in range(5):
            _make_day_yaml(tmp_path, f"2026-03-{20 + i:02d}", total_billion=9000.0)

        today = _make_today_result(total_billion=12500.0)
        analyzer = NodeSignalAnalyzer(tmp_path)
        signals = analyzer.analyze(today, "2026-04-01")

        surge_signals = [s for s in signals if "放量" in s.get("signal", "")]
        assert len(surge_signals) >= 1

    def test_volume_shrink_30pct_below_ma5(self, tmp_path):
        for i in range(5):
            _make_day_yaml(tmp_path, f"2026-03-{20 + i:02d}", total_billion=9000.0)

        today = _make_today_result(total_billion=5000.0)
        analyzer = NodeSignalAnalyzer(tmp_path)
        signals = analyzer.analyze(today, "2026-04-01")

        shrink_signals = [s for s in signals if "缩量" in s.get("signal", "")]
        assert len(shrink_signals) >= 1

    def test_zero_today_volume_not_treated_as_missing(self, tmp_path):
        """0.0 成交额是合法数值，不得因 truthiness 提前 return。"""
        for i in range(5):
            _make_day_yaml(tmp_path, f"2026-03-{20 + i:02d}", total_billion=9000.0)

        today = _make_today_result(total_billion=0.0)
        analyzer = NodeSignalAnalyzer(tmp_path)
        signals = analyzer.analyze(today, "2026-04-01")

        vol_signals = [s for s in signals if s["type"] == "volume_extreme" and "新低" in s["signal"]]
        assert len(vol_signals) >= 1

    def test_zero_history_volume_retained_for_five_day_window(self, tmp_path):
        """历史中某日成交额为 0.0 仍应计入 hist_vols，保证 5 日窗口长度与极值计算正确。"""
        for i in range(5):
            vol = 0.0 if i == 0 else 9000.0
            _make_day_yaml(tmp_path, f"2026-03-{20 + i:02d}", total_billion=vol)

        today = _make_today_result(total_billion=9500.0)
        analyzer = NodeSignalAnalyzer(tmp_path)
        signals = analyzer.analyze(today, "2026-04-01")

        vol_signals = [s for s in signals if s["type"] == "volume_extreme" and "新高" in s["signal"]]
        assert len(vol_signals) >= 1


# ──────────────────────────────────────────────────────────────────────────────
# 涨跌停极值信号
# ──────────────────────────────────────────────────────────────────────────────

class TestLimitExtremeSignals:
    def test_limit_up_5d_high(self, tmp_path):
        for i in range(5):
            _make_day_yaml(tmp_path, f"2026-03-{20 + i:02d}", limit_up_count=50)

        today = _make_today_result(limit_up_count=200)
        analyzer = NodeSignalAnalyzer(tmp_path)
        signals = analyzer.analyze(today, "2026-04-01")

        limit_signals = [s for s in signals if s["type"] == "limit_extreme" and "涨停数" in s["signal"]]
        assert len(limit_signals) >= 1
        assert limit_signals[0]["direction"] == "positive"

    def test_broken_rate_high(self, tmp_path):
        for i in range(5):
            _make_day_yaml(tmp_path, f"2026-03-{20 + i:02d}")

        today = _make_today_result(broken_rate=60.0)
        analyzer = NodeSignalAnalyzer(tmp_path)
        signals = analyzer.analyze(today, "2026-04-01")

        broken_signals = [s for s in signals if "炸板率" in s.get("signal", "")]
        assert len(broken_signals) == 1
        assert broken_signals[0]["direction"] == "negative"

    def test_seal_rate_low(self, tmp_path):
        for i in range(5):
            _make_day_yaml(tmp_path, f"2026-03-{20 + i:02d}")

        today = _make_today_result(seal_rate=25.0)
        analyzer = NodeSignalAnalyzer(tmp_path)
        signals = analyzer.analyze(today, "2026-04-01")

        seal_signals = [s for s in signals if "封板率" in s.get("signal", "")]
        assert len(seal_signals) == 1
        assert seal_signals[0]["direction"] == "negative"


# ──────────────────────────────────────────────────────────────────────────────
# 市场宽度信号
# ──────────────────────────────────────────────────────────────────────────────

class TestBreadthExtremeSignals:
    def test_breadth_extreme_high(self, tmp_path):
        for i in range(3):
            _make_day_yaml(tmp_path, f"2026-03-{20 + i:02d}")

        today = _make_today_result(advance=4200, total_stocks=5000)
        analyzer = NodeSignalAnalyzer(tmp_path)
        signals = analyzer.analyze(today, "2026-04-01")

        b_signals = [s for s in signals if s["type"] == "breadth_extreme"]
        assert len(b_signals) == 1
        assert b_signals[0]["direction"] == "positive"
        assert b_signals[0]["value"] == pytest.approx(84.0, abs=0.5)

    def test_breadth_extreme_low(self, tmp_path):
        for i in range(3):
            _make_day_yaml(tmp_path, f"2026-03-{20 + i:02d}")

        today = _make_today_result(advance=800, total_stocks=5000)
        analyzer = NodeSignalAnalyzer(tmp_path)
        signals = analyzer.analyze(today, "2026-04-01")

        b_signals = [s for s in signals if s["type"] == "breadth_extreme"]
        assert len(b_signals) == 1
        assert b_signals[0]["direction"] == "negative"

    def test_no_breadth_signal_in_normal_range(self, tmp_path):
        for i in range(3):
            _make_day_yaml(tmp_path, f"2026-03-{20 + i:02d}")

        today = _make_today_result(advance=2500, total_stocks=5000)
        analyzer = NodeSignalAnalyzer(tmp_path)
        signals = analyzer.analyze(today, "2026-04-01")

        b_signals = [s for s in signals if s["type"] == "breadth_extreme"]
        assert len(b_signals) == 0

    def test_total_zero_no_zerodivision(self, tmp_path):
        """total=0 时不应触发 ZeroDivisionError，直接跳过宽度信号"""
        for i in range(3):
            _make_day_yaml(tmp_path, f"2026-03-{20 + i:02d}")

        today = _make_today_result(advance=0, total_stocks=0)
        analyzer = NodeSignalAnalyzer(tmp_path)
        # 不应抛出异常
        signals = analyzer.analyze(today, "2026-04-01")
        b_signals = [s for s in signals if s["type"] == "breadth_extreme"]
        assert len(b_signals) == 0

    def test_total_none_no_zerodivision(self, tmp_path):
        """breadth 字段缺失时也不应报错"""
        for i in range(3):
            _make_day_yaml(tmp_path, f"2026-03-{20 + i:02d}")

        today = _make_today_result()
        today["breadth"] = {}  # advance 和 total 均缺失
        analyzer = NodeSignalAnalyzer(tmp_path)
        signals = analyzer.analyze(today, "2026-04-01")
        b_signals = [s for s in signals if s["type"] == "breadth_extreme"]
        assert len(b_signals) == 0


# ──────────────────────────────────────────────────────────────────────────────
# 辅助函数测试
# ──────────────────────────────────────────────────────────────────────────────

class TestHelpers:
    def test_safe_float_none(self):
        assert _safe_float(None) is None

    def test_safe_float_string(self):
        assert _safe_float("3.14") == pytest.approx(3.14)

    def test_safe_float_invalid(self):
        assert _safe_float("abc") is None

    def test_safe_int_none(self):
        assert _safe_int(None) is None

    def test_safe_int_float(self):
        assert _safe_int(80.9) == 80
