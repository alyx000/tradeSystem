"""
风格化因子相关测试

- TestPremiumCollectorEnhanced: 验证 PremiumCollector 的新分组逻辑
- TestStyleAnalyzer: 验证 StyleAnalyzer 的各维度计算
- TestReportStyleSection: 验证风格化章节渲染
"""
import statistics
from unittest.mock import MagicMock, patch

import pytest


# ====================================================================
# 辅助工具
# ====================================================================

def _make_stock(code, name, close, pct_chg, limit_times=1,
                first_time="09:30:00", last_time="14:50:00",
                amount_billion=1.0):
    return {
        "code": code,
        "name": name,
        "close": close,
        "pct_chg": pct_chg,
        "limit_times": limit_times,
        "first_time": first_time,
        "last_time": last_time,
        "amount_billion": amount_billion,
    }


def _make_registry_with_open(open_price_map: dict):
    """构造一个返回指定开盘价的 mock registry。"""
    registry = MagicMock()

    def _call(method, *args, **kwargs):
        if method == "get_stock_daily":
            code = args[0]
            op = open_price_map.get(code, 0)
            if op:
                r = MagicMock()
                r.success = True
                r.data = {"open": op}
                return r
            r = MagicMock()
            r.success = False
            r.error = "no data"
            return r
        return MagicMock(success=False, error="unknown")

    registry.call.side_effect = _call
    return registry


# ====================================================================
# TestPremiumCollectorEnhanced
# ====================================================================

class TestPremiumCollectorEnhanced:
    """验证 PremiumCollector 的新分组逻辑"""

    def _run_collect(self, stocks, open_map, tmp_path):
        from collectors.premium import PremiumCollector

        prev_date = "2026-03-27"
        trade_date = "2026-03-28"

        day_dir = tmp_path / prev_date
        day_dir.mkdir()

        import yaml
        yaml_path = day_dir / "post-market.yaml"
        yaml_path.write_text(yaml.dump({
            "raw_data": {
                "limit_up": {"count": len(stocks), "stocks": stocks}
            }
        }), encoding="utf-8")

        registry = _make_registry_with_open(open_map)

        with patch("collectors.premium.DAILY_DIR", tmp_path):
            pc = PremiumCollector(registry)
            return pc.collect(trade_date, prev_date)

    def test_third_board_split(self, tmp_path):
        """三板/四板/五板+ 应分别统计"""
        stocks = [
            _make_stock("A.SZ", "A", 10.0, 10.0, limit_times=3),
            _make_stock("B.SZ", "B", 20.0, 10.0, limit_times=4),
            _make_stock("C.SZ", "C", 30.0, 10.0, limit_times=5),
            _make_stock("D.SZ", "D", 40.0, 10.0, limit_times=6),
        ]
        open_map = {"A.SZ": 10.5, "B.SZ": 21.0, "C.SZ": 31.0, "D.SZ": 42.0}

        result = self._run_collect(stocks, open_map, tmp_path)
        assert result is not None
        assert result["third_board_plus"]["count"] == 4
        assert result["third_board"]["count"] == 1
        assert result["fourth_board"]["count"] == 1
        assert result["fifth_board_plus"]["count"] == 2

    def test_first_board_yizi(self, tmp_path):
        """首板一字（limit_times=1, first_time==last_time）独立统计"""
        stocks = [
            _make_stock("Y.SZ", "Y", 10.0, 10.0, limit_times=1,
                        first_time="09:25:00", last_time="09:25:00"),
            _make_stock("N.SZ", "N", 10.0, 10.0, limit_times=1,
                        first_time="09:30:00", last_time="14:50:00"),
        ]
        open_map = {"Y.SZ": 10.5, "N.SZ": 10.2}

        result = self._run_collect(stocks, open_map, tmp_path)
        assert result["first_board_yizi"]["count"] == 1
        assert result["first_board_yizi"]["detail"][0]["code"] == "Y.SZ"

    def test_capacity_top10(self, tmp_path):
        """容量票 Top10 按 amount_billion 降序取前 10"""
        stocks = [
            _make_stock(f"S{i}.SZ", f"S{i}", 10.0, 10.0, amount_billion=float(i))
            for i in range(1, 15)
        ]
        open_map = {f"S{i}.SZ": 10.5 for i in range(1, 15)}

        result = self._run_collect(stocks, open_map, tmp_path)
        cap = result["capacity_top10"]
        assert cap["count"] == 10
        codes = [d["code"] for d in cap["detail"]]
        assert "S14.SZ" in codes
        assert "S1.SZ" not in codes

    def test_amount_billion_in_entry(self, tmp_path):
        """entry 中应包含 amount_billion 字段"""
        stocks = [_make_stock("X.SZ", "X", 10.0, 10.0, amount_billion=5.5)]
        open_map = {"X.SZ": 10.5}

        result = self._run_collect(stocks, open_map, tmp_path)
        detail = result["first_board"]["detail"]
        assert len(detail) == 1
        assert detail[0]["amount_billion"] == 5.5

    def test_format_report_new_groups(self, tmp_path):
        """format_report 应包含新增分组标签"""
        stocks = [
            _make_stock("A.SZ", "A", 10.0, 10.0, limit_times=3,
                        first_time="09:25:00", last_time="09:25:00"),
            _make_stock("B.SZ", "B", 10.0, 10.0, limit_times=1,
                        first_time="09:25:00", last_time="09:25:00"),
        ]
        open_map = {"A.SZ": 10.5, "B.SZ": 10.2}

        result = self._run_collect(stocks, open_map, tmp_path)

        from collectors.premium import PremiumCollector
        pc = PremiumCollector(MagicMock())
        report = pc.format_report(result)

        assert "首板一字" in report
        assert "三板" in report
        assert "容量票 Top10" in report


# ====================================================================
# TestStyleAnalyzer
# ====================================================================

class TestStyleAnalyzer:
    """验证 StyleAnalyzer 各维度计算"""

    def _make_raw_data(self, **overrides):
        data = {
            "limit_up": {
                "count": 78,
                "first_board_count": 68,
                "first_board_10cm": 63,
                "first_board_20cm": 5,
                "first_board_30cm": 0,
            },
            "indices": {
                "csi300": {"change_pct": 0.5, "close": 4000},
                "csi1000": {"change_pct": 1.2, "close": 7000},
            },
        }
        data.update(overrides)
        return data

    def _write_backfill(self, tmp_path, date, backfill):
        import yaml
        day_dir = tmp_path / date
        day_dir.mkdir(exist_ok=True)
        (day_dir / "post-market.yaml").write_text(
            yaml.dump({"premium_backfill": backfill}),
            encoding="utf-8",
        )

    def test_board_preference(self):
        from analyzers.style_factors import StyleAnalyzer
        sa = StyleAnalyzer()
        raw = self._make_raw_data()
        bp = sa._build_board_preference(raw)
        assert bp["dominant_type"] == "10cm"
        assert bp["pct_10cm"] > 90

    def test_board_preference_20cm_dominant(self):
        from analyzers.style_factors import StyleAnalyzer
        sa = StyleAnalyzer()
        raw = self._make_raw_data(limit_up={
            "count": 50, "first_board_count": 50,
            "first_board_10cm": 5, "first_board_20cm": 40, "first_board_30cm": 5,
        })
        bp = sa._build_board_preference(raw)
        assert bp["dominant_type"] == "20cm"

    def test_cap_preference_small(self):
        from analyzers.style_factors import StyleAnalyzer
        sa = StyleAnalyzer()
        raw = self._make_raw_data()
        cp = sa._build_cap_preference(raw)
        assert cp["relative"] == "偏小盘"
        assert cp["spread"] == 0.7

    def test_cap_preference_large(self):
        from analyzers.style_factors import StyleAnalyzer
        sa = StyleAnalyzer()
        raw = self._make_raw_data(indices={
            "csi300": {"change_pct": 1.5, "close": 4000},
            "csi1000": {"change_pct": 0.2, "close": 7000},
        })
        cp = sa._build_cap_preference(raw)
        assert cp["relative"] == "偏大盘"

    def test_cap_preference_balanced(self):
        from analyzers.style_factors import StyleAnalyzer
        sa = StyleAnalyzer()
        raw = self._make_raw_data(indices={
            "csi300": {"change_pct": 1.0, "close": 4000},
            "csi1000": {"change_pct": 1.2, "close": 7000},
        })
        cp = sa._build_cap_preference(raw)
        assert cp["relative"] == "均衡"

    def test_premium_snapshot(self, tmp_path):
        from analyzers.style_factors import StyleAnalyzer
        sa = StyleAnalyzer()
        backfill = {
            "first_board": {"count": 68, "premium_median": 0.14, "premium_mean": 0.5, "open_up_rate": 0.52},
            "second_board": {"count": 7, "premium_median": 2.46, "premium_mean": 2.58, "open_up_rate": 0.71},
        }
        self._write_backfill(tmp_path, "2026-03-27", backfill)

        with patch("analyzers.style_factors.DAILY_DIR", tmp_path):
            result = sa.analyze(self._make_raw_data(), "2026-03-28")

        snap = result["premium_snapshot"]
        assert snap["first_board"]["premium_median"] == 0.14
        assert snap["second_board"]["open_up_rate"] == 0.71

    def test_premium_trend(self, tmp_path):
        from analyzers.style_factors import StyleAnalyzer
        sa = StyleAnalyzer()

        for i, med in enumerate([0.5, 0.3, 0.1, -0.1, -0.3]):
            date = f"2026-03-{20 + i:02d}"
            self._write_backfill(tmp_path, date, {
                "first_board": {"count": 68, "premium_median": med, "premium_mean": med, "open_up_rate": 0.5},
            })

        with patch("analyzers.style_factors.DAILY_DIR", tmp_path):
            result = sa.analyze(self._make_raw_data(), "2026-03-26")

        trend = result["premium_trend"]
        assert len(trend["first_board_median_5d"]) == 5
        assert trend["direction"] == "走弱"

    def test_trend_direction_strengthening(self):
        from analyzers.style_factors import StyleAnalyzer
        assert StyleAnalyzer._judge_trend([-0.3, -0.1, 0.1, 0.3, 0.5]) == "走弱"
        assert StyleAnalyzer._judge_trend([0.5, 0.3, 0.1, -0.1, -0.3]) == "走强"
        assert StyleAnalyzer._judge_trend([0.1, -0.1, 0.2, -0.2]) == "震荡"

    def test_switch_signal_negative_premium(self, tmp_path):
        from analyzers.style_factors import StyleAnalyzer
        sa = StyleAnalyzer()
        self._write_backfill(tmp_path, "2026-03-27", {
            "first_board": {"count": 68, "premium_median": -0.5, "premium_mean": -0.3, "open_up_rate": 0.35},
        })

        with patch("analyzers.style_factors.DAILY_DIR", tmp_path):
            result = sa.analyze(self._make_raw_data(), "2026-03-28")

        signals = result["switch_signals"]
        assert any("溢价转负" in s for s in signals)
        assert any("高开率" in s for s in signals)

    def test_switch_signal_board_crash(self, tmp_path):
        from analyzers.style_factors import StyleAnalyzer
        sa = StyleAnalyzer()
        self._write_backfill(tmp_path, "2026-03-27", {
            "first_board": {"count": 68, "premium_median": 0.5, "premium_mean": 0.5, "open_up_rate": 0.6},
            "third_board_plus": {"count": 3, "premium_median": -5.0, "premium_mean": -4.0, "open_up_rate": 0.3},
        })

        with patch("analyzers.style_factors.DAILY_DIR", tmp_path):
            result = sa.analyze(self._make_raw_data(), "2026-03-28")

        signals = result["switch_signals"]
        assert any("连板" in s for s in signals)

    def test_switch_signal_cap_shift(self, tmp_path):
        from analyzers.style_factors import StyleAnalyzer
        sa = StyleAnalyzer()
        self._write_backfill(tmp_path, "2026-03-27", {
            "first_board": {"count": 68, "premium_median": 0.5, "premium_mean": 0.5, "open_up_rate": 0.6},
        })
        raw = self._make_raw_data(indices={
            "csi300": {"change_pct": 2.0, "close": 4000},
            "csi1000": {"change_pct": 0.5, "close": 7000},
        })

        with patch("analyzers.style_factors.DAILY_DIR", tmp_path):
            result = sa.analyze(raw, "2026-03-28")

        signals = result["switch_signals"]
        assert any("容量票" in s for s in signals)

    def test_no_data_graceful(self, tmp_path):
        from analyzers.style_factors import StyleAnalyzer
        sa = StyleAnalyzer()
        with patch("analyzers.style_factors.DAILY_DIR", tmp_path):
            result = sa.analyze({"limit_up": {}, "indices": {}}, "2026-03-28")
        assert result["premium_snapshot"] == {}
        assert result["board_preference"] == {}
        assert result["cap_preference"] == {}
        assert result["switch_signals"] == []


# ====================================================================
# TestReportStyleSection
# ====================================================================

class TestReportStyleSection:
    """验证风格化赚钱效应章节的 Markdown 渲染"""

    def test_render_style_factors(self):
        from generators.report import _render_style_factors

        raw_data = {
            "style_factors": {
                "premium_snapshot": {
                    "first_board": {"count": 68, "premium_median": 0.14, "premium_mean": 0.5, "open_up_rate": 0.52},
                    "second_board": {"count": 7, "premium_median": 2.46, "premium_mean": 2.58, "open_up_rate": 0.71},
                },
                "premium_trend": {
                    "first_board_median_5d": [0.14, 0.3, -0.1],
                    "direction": "震荡",
                },
                "board_preference": {
                    "dominant_type": "10cm",
                    "pct_10cm": 92.6,
                    "pct_20cm": 7.4,
                    "pct_30cm": 0.0,
                },
                "cap_preference": {
                    "csi300_chg": 0.5,
                    "csi1000_chg": 1.2,
                    "spread": 0.7,
                    "relative": "偏小盘",
                },
                "switch_signals": ["近5日首板溢价趋势走弱"],
            }
        }

        lines = []
        new_idx = _render_style_factors(lines, raw_data, 8)

        text = "\n".join(lines)
        assert "风格化赚钱效应" in text
        assert "首板合计" in text
        assert "溢价趋势" in text
        assert "10cm 为主" in text
        assert "偏小盘" in text
        assert "风格切换信号" in text
        assert new_idx == 9

    def test_render_empty_style(self):
        from generators.report import _render_style_factors

        lines = []
        new_idx = _render_style_factors(lines, {}, 5)
        assert new_idx == 5
        assert lines == []

    def test_auto_analysis_includes_style(self):
        from generators.report import _generate_auto_analysis

        raw_data = {
            "style_factors": {
                "premium_snapshot": {
                    "first_board": {"count": 68, "premium_median": 0.14, "open_up_rate": 0.52},
                },
                "cap_preference": {
                    "csi300_chg": 0.5,
                    "csi1000_chg": 1.2,
                    "relative": "偏小盘",
                },
            }
        }

        items = _generate_auto_analysis(raw_data)
        text = " ".join(items)
        assert "首板溢价中位" in text
        assert "偏小盘" in text
