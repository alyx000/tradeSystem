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

    def _run_collect(self, stocks, open_map, tmp_path, top_volume=None):
        from collectors.premium import PremiumCollector

        prev_date = "2026-03-27"
        trade_date = "2026-03-28"

        day_dir = tmp_path / prev_date
        day_dir.mkdir()

        import yaml
        raw: dict = {"limit_up": {"count": len(stocks), "stocks": stocks}}
        if top_volume is not None:
            raw["top_volume_stocks"] = top_volume
        yaml_path = day_dir / "post-market.yaml"
        yaml_path.write_text(yaml.dump({"raw_data": raw}), encoding="utf-8")

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

    def test_capacity_top10_from_market_top_volume(self, tmp_path):
        """容量票 Top10 改为全市场成交额前10（raw_data.top_volume_stocks），非涨停池。

        B 修复：容量票度量「全市场大成交额票」次日溢价，不再局限于涨停股池。
        """
        # 涨停池里只有一只小成交额涨停股
        stocks = [_make_stock("LU1.SZ", "涨停1", 10.0, 10.0, amount_billion=1.0)]
        # 全市场成交额前10（含非涨停的大票，按 amount 已降序）
        top_volume = [
            {"code": f"BIG{i}.SH", "name": f"大票{i}", "close": 100.0,
             "amount": 9_000_000_000 - i}
            for i in range(10)
        ]
        open_map = {"LU1.SZ": 10.5}
        open_map.update({f"BIG{i}.SH": 101.0 for i in range(10)})  # 全部 +1% 高开

        result = self._run_collect(stocks, open_map, tmp_path, top_volume=top_volume)
        cap = result["capacity_top10"]
        assert cap["count"] == 10
        codes = [d["code"] for d in cap["detail"]]
        assert "BIG0.SH" in codes
        assert "LU1.SZ" not in codes, "涨停小票不应再进容量票（已改全市场口径）"
        # premium = (101-100)/100*100 = 1.0
        assert cap["premium_median"] == 1.0

    def test_st_excluded_from_first_board(self, tmp_path):
        """ST / *ST（5% 板）不应落入 10cm 首板桶（G 修复）。"""
        stocks = [
            _make_stock("ST1.SZ", "ST金科", 10.0, 5.0, limit_times=1),    # ST 5% 板
            _make_stock("ST2.SZ", "*ST海航", 10.0, 5.0, limit_times=1),   # *ST 5% 板
            _make_stock("N1.SZ", "正常股", 10.0, 10.0, limit_times=1),     # 正常 10cm 首板
        ]
        open_map = {"ST1.SZ": 10.2, "ST2.SZ": 10.2, "N1.SZ": 10.3}

        result = self._run_collect(stocks, open_map, tmp_path)
        codes_10cm = [d["code"] for d in result["first_board_10cm"]["detail"]]
        assert "ST1.SZ" not in codes_10cm
        assert "ST2.SZ" not in codes_10cm
        assert "N1.SZ" in codes_10cm
        assert result["first_board_10cm"]["count"] == 1

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
            "trade_date": "2026-03-28",
            "first_board": {"count": 68, "premium_median": 0.14, "premium_mean": 0.5, "open_up_rate": 0.52},
            "second_board": {"count": 7, "premium_median": 2.46, "premium_mean": 2.58, "open_up_rate": 0.71},
        }
        self._write_backfill(tmp_path, "2026-03-27", backfill)

        with patch("analyzers.style_factors.DAILY_DIR", tmp_path):
            result = sa.analyze(self._make_raw_data(), "2026-03-28")

        snap = result["premium_snapshot"]
        assert snap["first_board"]["premium_median"] == 0.14
        assert snap["second_board"]["open_up_rate"] == 0.71

    def test_popularity_injected_from_prev_yaml(self, tmp_path):
        """analyze 把 T-1 yaml 的 popularity_backfill 注入 result['popularity']"""
        import yaml
        from analyzers.style_factors import StyleAnalyzer
        sa = StyleAnalyzer()
        day_dir = tmp_path / "2026-03-27"
        day_dir.mkdir()
        pop = [
            {"code": "000003.SZ", "name": "连板A", "source": ["consecutive"],
             "prev_close": 8.0, "t_open_premium_pct": 3.0, "t_close_change_pct": 10.0,
             "t_is_limit_up": True, "t_is_limit_down": False},
        ]
        (day_dir / "post-market.yaml").write_text(
            yaml.dump({"popularity_backfill": pop}), encoding="utf-8")

        with patch("analyzers.style_factors.DAILY_DIR", tmp_path):
            result = sa.analyze(self._make_raw_data(), "2026-03-28")

        assert result["popularity"]
        assert result["popularity"][0]["name"] == "连板A"
        assert "popularity_provenance" not in result

    @pytest.mark.parametrize(
        (
            "directory_date", "analyze_date", "premium_backfill",
            "expected_provenance",
        ),
        [
            (
                "2026-03-27",
                "2026-03-28",
                {"trade_date": "2026-03-28", "prev_date": "2026-03-27"},
                {
                    "source_trade_date": "2026-03-27",
                    "outcome_trade_date": "2026-03-28",
                },
            ),
            (
                "2026-03-26",
                "2026-03-28",
                {"trade_date": "2026-03-28", "prev_date": "2026-03-26"},
                {
                    "source_trade_date": "2026-03-26",
                    "outcome_trade_date": "2026-03-28",
                },
            ),
            ("2026-03-27", "2026-03-28", None, None),
            (
                "2026-03-27",
                "2026-03-28",
                {"trade_date": "2026-03-27", "prev_date": "2026-03-27"},
                None,
            ),
            (
                "2026-03-27",
                "2026-03-28",
                {"trade_date": "2026-03-28", "prev_date": "2026-03-26"},
                None,
            ),
            (
                "2026-03-27",
                "2026-03-28",
                {"trade_date": "2026-02-30", "prev_date": "2026-03-27"},
                None,
            ),
            ("2026-03-27", "2026-03-28", "broken", None),
        ],
        ids=(
            "same-source",
            "older-source-directory",
            "missing-metadata",
            "wrong-outcome-date",
            "wrong-source-directory",
            "invalid-date",
            "invalid-metadata-type",
        ),
    )
    def test_popularity_provenance_requires_same_yaml_metadata(
        self,
        tmp_path,
        directory_date,
        analyze_date,
        premium_backfill,
        expected_provenance,
    ):
        import yaml
        from analyzers.style_factors import StyleAnalyzer

        day_dir = tmp_path / directory_date
        day_dir.mkdir()
        payload = {
            "popularity_backfill": [{
                "code": "000003.SZ",
                "name": "连板A",
                "source": ["consecutive"],
                "prev_close": 8.0,
                "t_open_premium_pct": 3.0,
                "t_close_change_pct": 10.0,
                "t_is_limit_up": True,
                "t_is_limit_down": False,
            }],
        }
        if premium_backfill is not None:
            payload["premium_backfill"] = premium_backfill
        (day_dir / "post-market.yaml").write_text(
            yaml.dump(payload),
            encoding="utf-8",
        )

        with patch("analyzers.style_factors.DAILY_DIR", tmp_path):
            result = StyleAnalyzer().analyze(self._make_raw_data(), analyze_date)

        assert result["popularity"][0]["name"] == "连板A"
        assert result.get("popularity_provenance") == expected_provenance

    def test_popularity_empty_when_absent(self, tmp_path):
        """无 popularity_backfill 时 result['popularity'] 为空列表，不报错"""
        from analyzers.style_factors import StyleAnalyzer
        sa = StyleAnalyzer()
        with patch("analyzers.style_factors.DAILY_DIR", tmp_path):
            result = sa.analyze(self._make_raw_data(), "2026-03-28")
        assert result.get("popularity") == []

    def test_popularity_no_reach_back_to_older_day(self, tmp_path):
        """T-1（最近前一日）yaml 无 popularity_backfill 时，不得回退读取 T-2 旧值。

        渲染语义固定为「T-1 高标 → T 日结局」，回退会把 T-2 数据伪装成 T-1→T。
        """
        import yaml
        from analyzers.style_factors import StyleAnalyzer
        sa = StyleAnalyzer()

        # T-2 (2026-03-26) 有 popularity_backfill
        d2 = tmp_path / "2026-03-26"
        d2.mkdir()
        (d2 / "post-market.yaml").write_text(
            yaml.dump({"popularity_backfill": [
                {"code": "000099.SZ", "name": "陈年旧标", "source": ["consecutive"],
                 "prev_close": 5.0, "t_open_premium_pct": 1.0, "t_close_change_pct": 2.0,
                 "t_is_limit_up": False, "t_is_limit_down": False},
            ]}), encoding="utf-8")

        # T-1 (2026-03-27) 存在但无 popularity_backfill（如当日 cmd_evening 未写入）
        d1 = tmp_path / "2026-03-27"
        d1.mkdir()
        (d1 / "post-market.yaml").write_text(
            yaml.dump({"premium_backfill": {"first_board": {"count": 0}}}), encoding="utf-8")

        with patch("analyzers.style_factors.DAILY_DIR", tmp_path):
            result = sa.analyze(self._make_raw_data(), "2026-03-28")

        assert result.get("popularity") == [], "T-1 缺失时不应回退读 T-2 旧人气股"

    def test_first_only_no_leak_when_t1_yaml_missing(self, tmp_path):
        """紧邻前一日(T-1)目录存在但 yaml 缺失时，first_only 不得回退读 T-2 的字段"""
        import yaml
        from analyzers.style_factors import StyleAnalyzer
        sa = StyleAnalyzer()

        # T-2 (2026-03-26) 有 promotion_backfill
        d2 = tmp_path / "2026-03-26"
        d2.mkdir()
        (d2 / "post-market.yaml").write_text(
            yaml.dump({"promotion_backfill": {"first_to_second": {"base": 9, "promoted": 9, "rate": 1.0}}}),
            encoding="utf-8")
        # T-1 (2026-03-27) 目录在，但 post-market.yaml 缺失（最近的前序目录）
        (tmp_path / "2026-03-27").mkdir()

        with patch("analyzers.style_factors.DAILY_DIR", tmp_path):
            result = sa.analyze(self._make_raw_data(), "2026-03-28")

        assert result.get("promotion") is None, "T-1 yaml 缺失时 first_only 不应回退读 T-2 的 promotion"

    def test_first_only_no_leak_when_t1_yaml_corrupt(self, tmp_path):
        """T-1 yaml 损坏（解析失败）时同样不回退"""
        import yaml
        from analyzers.style_factors import StyleAnalyzer
        sa = StyleAnalyzer()
        d2 = tmp_path / "2026-03-26"
        d2.mkdir()
        (d2 / "post-market.yaml").write_text(
            yaml.dump({"popularity_backfill": [{"code": "X", "name": "旧"}]}), encoding="utf-8")
        d1 = tmp_path / "2026-03-27"
        d1.mkdir()
        (d1 / "post-market.yaml").write_text("{ this is : : invalid yaml :", encoding="utf-8")

        with patch("analyzers.style_factors.DAILY_DIR", tmp_path):
            result = sa.analyze(self._make_raw_data(), "2026-03-28")

        assert result.get("popularity") == [], "T-1 yaml 损坏时不应回退读 T-2 旧人气股"

    @pytest.mark.parametrize(
        "payload",
        [[{"unexpected": True}], "unexpected", 7],
        ids=("list", "string", "number"),
    )
    def test_analyze_degrades_non_mapping_top_level_yaml(
        self,
        tmp_path,
        payload,
    ):
        """合法 YAML 标量/数组不是对象时，first_only 真实入口应按缺失降级。"""
        import yaml
        from analyzers.style_factors import StyleAnalyzer

        day_dir = tmp_path / "2026-03-27"
        day_dir.mkdir()
        (day_dir / "post-market.yaml").write_text(
            yaml.dump(payload),
            encoding="utf-8",
        )

        with patch("analyzers.style_factors.DAILY_DIR", tmp_path):
            result = StyleAnalyzer().analyze(
                self._make_raw_data(),
                "2026-03-28",
            )

        assert result["popularity"] == []
        assert result["promotion"] is None

    @pytest.mark.parametrize(
        "payload",
        [[{"unexpected": True}], "unexpected", 7],
        ids=("list", "string", "number"),
    )
    def test_non_first_loader_skips_non_mapping_top_level_yaml(
        self,
        tmp_path,
        payload,
    ):
        """可回退迭代器跳过非对象 YAML，并继续读取更早的合法对象。"""
        import yaml
        from analyzers.style_factors import StyleAnalyzer

        older_dir = tmp_path / "2026-03-26"
        older_dir.mkdir()
        (older_dir / "post-market.yaml").write_text(
            yaml.dump({"legacy_field": {"value": "older"}}),
            encoding="utf-8",
        )
        latest_dir = tmp_path / "2026-03-27"
        latest_dir.mkdir()
        (latest_dir / "post-market.yaml").write_text(
            yaml.dump(payload),
            encoding="utf-8",
        )

        with patch("analyzers.style_factors.DAILY_DIR", tmp_path):
            result = StyleAnalyzer()._load_prev_field(
                "2026-03-28",
                "legacy_field",
            )

        assert result == {"value": "older"}

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

    def test_premium_snapshot_strict_no_fallback_to_older(self, tmp_path):
        """T-1（紧邻前一日）无 premium_backfill 时，快照不回退读 T-2 旧值（D 修复）。"""
        # T-2 有 backfill（陈旧值，不应被取用）
        self._write_backfill(tmp_path, "2026-03-26", {
            "first_board": {"count": 68, "premium_median": 9.9, "premium_mean": 9.9, "open_up_rate": 0.5},
        })
        # T-1 存在但无 premium_backfill（当日 backfill 缺失，如节后真空日）
        import yaml
        (tmp_path / "2026-03-27").mkdir()
        (tmp_path / "2026-03-27" / "post-market.yaml").write_text(
            yaml.dump({"popularity_backfill": []}), encoding="utf-8")

        from analyzers.style_factors import StyleAnalyzer
        sa = StyleAnalyzer()
        with patch("analyzers.style_factors.DAILY_DIR", tmp_path):
            result = sa.analyze(self._make_raw_data(), "2026-03-28")

        assert result["premium_snapshot"] == {}, "T-1 缺 backfill 时不应回退读 T-2 陈旧快照"

    def test_premium_snapshot_count_zero_no_fallback(self, tmp_path):
        """T-1 backfill first_board.count==0（真空日）→ 快照空，不回退 T-2（D 修复）。"""
        self._write_backfill(tmp_path, "2026-03-26", {
            "first_board": {"count": 68, "premium_median": 9.9, "premium_mean": 9.9, "open_up_rate": 0.5},
        })
        self._write_backfill(tmp_path, "2026-03-27", {"first_board": {"count": 0}})

        from analyzers.style_factors import StyleAnalyzer
        sa = StyleAnalyzer()
        with patch("analyzers.style_factors.DAILY_DIR", tmp_path):
            result = sa.analyze(self._make_raw_data(), "2026-03-28")

        assert result["premium_snapshot"] == {}, "count==0 真空日不应回退 T-2"

    def test_premium_snapshot_trade_date_mismatch_guard(self, tmp_path):
        """T-1 backfill 的 trade_date 与当前 date 不一致（陈旧写入）→ 视为无回填（D 守卫）。"""
        self._write_backfill(tmp_path, "2026-03-27", {
            "trade_date": "2026-03-27",  # 错位：应为 2026-03-28
            "first_board": {"count": 68, "premium_median": 5.5, "premium_mean": 5.5, "open_up_rate": 0.5},
        })

        from analyzers.style_factors import StyleAnalyzer
        sa = StyleAnalyzer()
        with patch("analyzers.style_factors.DAILY_DIR", tmp_path):
            result = sa.analyze(self._make_raw_data(), "2026-03-28")

        assert result["premium_snapshot"] == {}, "trade_date 错位的陈旧 backfill 不应被取用"

    def test_premium_trend_dates_aligned(self, tmp_path):
        """premium_trend 输出与 medians 等长对齐的 dates（A/F 支撑前端旧→新带日期展示）。"""
        for i, med in enumerate([0.5, 0.3, 0.1, -0.1, -0.3]):
            date = f"2026-03-{20 + i:02d}"
            self._write_backfill(tmp_path, date, {
                "first_board": {"count": 68, "premium_median": med, "premium_mean": med, "open_up_rate": 0.5},
                "trade_date": f"2026-03-{21 + i:02d}",
            })

        from analyzers.style_factors import StyleAnalyzer
        sa = StyleAnalyzer()
        with patch("analyzers.style_factors.DAILY_DIR", tmp_path):
            result = sa.analyze(self._make_raw_data(), "2026-03-26")

        trend = result["premium_trend"]
        assert "dates" in trend
        assert len(trend["dates"]) == len(trend["first_board_median_5d"])

    def test_premium_trend_window_bounded_to_5_trading_days(self, tmp_path):
        """趋势窗口限定最近 5 个交易日目录，中间空日剔除后不回溯第 6 日（F 修复）。"""
        meds = {
            "2026-03-20": 11.0, "2026-03-21": 99.0, "2026-03-22": 0.1,
            "2026-03-23": 0.2, "2026-03-25": 0.3, "2026-03-26": 0.4,
        }
        for d, m in meds.items():
            self._write_backfill(tmp_path, d, {
                "first_board": {"count": 68, "premium_median": m, "premium_mean": m, "open_up_rate": 0.5},
            })
        # 03-24 为真空日（count==0），位于最近 5 个目录之内
        self._write_backfill(tmp_path, "2026-03-24", {"first_board": {"count": 0}})

        from analyzers.style_factors import StyleAnalyzer
        sa = StyleAnalyzer()
        with patch("analyzers.style_factors.DAILY_DIR", tmp_path):
            result = sa.analyze(self._make_raw_data(), "2026-03-27")

        # prev_dirs[:5] = [03-26,03-25,03-24,03-23,03-22]；03-24 空 → medians=[0.4,0.3,0.2,0.1]
        medians = result["premium_trend"]["first_board_median_5d"]
        assert 99.0 not in medians, "不应回溯到第 6 个交易日 03-21"
        assert 11.0 not in medians, "不应回溯到第 7 个交易日 03-20"
        assert len(medians) == 4

    def test_trend_direction_strengthening(self):
        from analyzers.style_factors import StyleAnalyzer
        assert StyleAnalyzer._judge_trend([-0.3, -0.1, 0.1, 0.3, 0.5]) == "走弱"
        assert StyleAnalyzer._judge_trend([0.5, 0.3, 0.1, -0.1, -0.3]) == "走强"
        assert StyleAnalyzer._judge_trend([0.1, -0.1, 0.2, -0.2]) == "震荡"

    def test_switch_signal_negative_premium(self, tmp_path):
        from analyzers.style_factors import StyleAnalyzer
        sa = StyleAnalyzer()
        self._write_backfill(tmp_path, "2026-03-27", {
            "trade_date": "2026-03-28",
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
            "trade_date": "2026-03-28",
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
            "trade_date": "2026-03-28",
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

    def test_render_popularity_table(self):
        """风格化章渲染昨日人气股今日表现具名表，来源标签映射 + 续板/跌停标记"""
        from generators.report import _render_style_factors

        raw_data = {
            "style_factors": {
                "popularity": [
                    {"code": "000003.SZ", "name": "连板A", "source": ["consecutive"],
                     "prev_close": 8.0, "t_open_premium_pct": 3.0,
                     "t_close_change_pct": 10.0, "t_is_limit_up": True,
                     "t_is_limit_down": False},
                    {"code": "000009.SZ", "name": "退潮B", "source": ["dragon_tiger", "volume_top10"],
                     "prev_close": 20.0, "t_open_premium_pct": -2.0,
                     "t_close_change_pct": -10.0, "t_is_limit_up": False,
                     "t_is_limit_down": True},
                ],
            }
        }
        lines = []
        new_idx = _render_style_factors(lines, raw_data, 8)
        text = "\n".join(lines)

        assert "人气股" in text
        assert "连板A" in text
        assert "退潮B" in text
        assert "连板" in text       # source consecutive → 连板
        assert "龙虎榜" in text     # source dragon_tiger → 龙虎榜
        assert new_idx == 9

    def test_render_popularity_capped_at_8(self):
        """钉钉单条减负：人气股表只取今收最强前 8（原无上限）。"""
        from generators.report import _render_style_factors
        raw_data = {
            "style_factors": {
                "popularity": [
                    {"code": f"00000{i}.SZ", "name": f"人气{i}", "source": ["consecutive"],
                     "prev_close": 8.0, "t_open_premium_pct": 1.0,
                     "t_close_change_pct": 20.0 - i,  # 降序：人气0 最强
                     "t_is_limit_up": False, "t_is_limit_down": False}
                    for i in range(12)
                ],
            }
        }
        lines = []
        _render_style_factors(lines, raw_data, 8)
        text = "\n".join(lines)
        assert "人气7" in text       # 前 8（人气0~7）保留
        assert "人气8" not in text   # 第 9 名起被裁

    def test_render_popularity_empty_name_falls_back_to_code(self):
        """name 为空串（量能前10 来源）时回退显示 code，不出现空名行"""
        from generators.report import _render_style_factors
        raw_data = {
            "style_factors": {
                "popularity": [
                    {"code": "300308.SZ", "name": "", "source": ["volume_top10"],
                     "prev_close": 1197.99, "t_open_premium_pct": 4.34,
                     "t_close_change_pct": 1.71, "t_is_limit_up": False,
                     "t_is_limit_down": False},
                ],
            }
        }
        lines = []
        _render_style_factors(lines, raw_data, 8)
        text = "\n".join(lines)
        assert "300308.SZ" in text, "name 为空时应回退显示 code"
        # 表格不应出现 "|  |"（空名单元格）后紧跟来源
        assert "|  | 量能前10" not in text

    def test_render_popularity_alone_triggers_section(self):
        """仅有 popularity（无溢价/偏好）时风格化章仍渲染"""
        from generators.report import _render_style_factors
        raw_data = {
            "style_factors": {
                "popularity": [
                    {"code": "000003.SZ", "name": "连板A", "source": ["consecutive"],
                     "prev_close": 8.0, "t_open_premium_pct": 3.0,
                     "t_close_change_pct": 10.0, "t_is_limit_up": True,
                     "t_is_limit_down": False},
                ],
            }
        }
        lines = []
        new_idx = _render_style_factors(lines, raw_data, 8)
        text = "\n".join(lines)
        assert "风格化赚钱效应" in text
        assert "连板A" in text
        assert new_idx == 9

    def test_render_empty_style(self):
        from generators.report import _render_style_factors

        lines = []
        new_idx = _render_style_factors(lines, {}, 5)
        assert new_idx == 5
        assert lines == []

    def test_render_trend_only_still_renders_section(self):
        """快照为空但趋势/信号有值时仍渲染风格章节（D 修复后缺口日 snap 常空，不能丢趋势）。"""
        from generators.report import _render_style_factors

        raw_data = {
            "style_factors": {
                "premium_snapshot": {},
                "premium_trend": {"direction": "走弱", "first_board_median_5d": [0.21, 1.83]},
                "switch_signals": ["近5日首板溢价趋势走弱"],
            }
        }
        lines: list = []
        new_idx = _render_style_factors(lines, raw_data, 8)
        assert new_idx == 9, "trend/signals 有值时不应早退跳过整段"
        assert any("走弱" in ln for ln in lines)

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
