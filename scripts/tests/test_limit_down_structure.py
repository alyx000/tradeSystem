"""
跌停结构（连续跌停）单元测试

limit_down.stocks 原始数据只有 close/code/name/pct_chg，无连续跌停天数。
本模块验证基于多日 post-market.yaml join 计算 consecutive_down + down_ladder，
以及报告渲染。天地板因 yaml 无分时/最高最低价不做，仅做连续跌停计数。
"""
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from collectors.market import MarketCollector
from generators.report import ReportGenerator
from providers.registry import ProviderRegistry
from providers.base import DataResult


def _mock_registry():
    reg = MagicMock(spec=ProviderRegistry)
    reg.call = MagicMock(return_value=DataResult(data=None, source="mock", error="x"))
    return reg


def _write_limit_down_history(tmp_path: Path, date: str, codes_names: list[tuple]):
    """写入某交易日 post-market.yaml 的 limit_down.stocks"""
    day_dir = tmp_path / "daily" / date
    day_dir.mkdir(parents=True, exist_ok=True)
    stocks = [{"code": c, "name": n, "close": 10.0, "pct_chg": -10.0}
              for c, n in codes_names]
    data = {"date": date, "raw_data": {"limit_down": {"count": len(stocks), "stocks": stocks}}}
    with open(day_dir / "post-market.yaml", "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True)


class TestConsecutiveDown:
    def test_streak_counting(self):
        """X 连跌3日、Y 连跌2日、Z 仅今日跌停"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # T-2: X 跌停
            _write_limit_down_history(tmp_path, "2026-03-26", [("X.SZ", "甲")])
            # T-1: X, Y 跌停
            _write_limit_down_history(tmp_path, "2026-03-27", [("X.SZ", "甲"), ("Y.SZ", "乙")])

            with patch("collectors.market.BASE_DIR", tmp_path):
                collector = MarketCollector(_mock_registry())
                result = {
                    "limit_down": {
                        "count": 3,
                        "stocks": [
                            {"code": "X.SZ", "name": "甲", "pct_chg": -10.0},
                            {"code": "Y.SZ", "name": "乙", "pct_chg": -10.0},
                            {"code": "Z.SZ", "name": "丙", "pct_chg": -10.0},
                        ],
                    }
                }
                collector._enrich_limit_down_structure(result, "2026-03-28")

        ld = result["limit_down"]
        by_code = {s["code"]: s for s in ld["stocks"]}
        assert by_code["X.SZ"]["consecutive_down"] == 3
        assert by_code["Y.SZ"]["consecutive_down"] == 2
        assert by_code["Z.SZ"]["consecutive_down"] == 1
        assert ld["consecutive_down_max"] == 3
        # down_ladder 只收 >=2 连跌
        assert "甲" in ld["down_ladder"]["3"]
        assert "乙" in ld["down_ladder"]["2"]
        assert "1" not in ld["down_ladder"]

    def test_no_history_all_streak_one(self):
        """无历史时所有跌停股 streak=1，down_ladder 为空"""
        with tempfile.TemporaryDirectory() as tmp:
            with patch("collectors.market.BASE_DIR", Path(tmp)):
                collector = MarketCollector(_mock_registry())
                result = {"limit_down": {"count": 1, "stocks": [
                    {"code": "Z.SZ", "name": "丙", "pct_chg": -10.0}]}}
                collector._enrich_limit_down_structure(result, "2026-03-28")

        ld = result["limit_down"]
        assert ld["stocks"][0]["consecutive_down"] == 1
        assert ld["consecutive_down_max"] == 1
        assert ld["down_ladder"] == {}

    def test_gap_breaks_streak(self):
        """连续性断裂：X 今日跌停、T-1 未跌停、T-2 跌停 → streak=1（不跨断点累计）"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_limit_down_history(tmp_path, "2026-03-26", [("X.SZ", "甲")])
            _write_limit_down_history(tmp_path, "2026-03-27", [("Y.SZ", "乙")])  # X 不在 T-1

            with patch("collectors.market.BASE_DIR", tmp_path):
                collector = MarketCollector(_mock_registry())
                result = {"limit_down": {"count": 1, "stocks": [
                    {"code": "X.SZ", "name": "甲", "pct_chg": -10.0}]}}
                collector._enrich_limit_down_structure(result, "2026-03-28")

        assert result["limit_down"]["stocks"][0]["consecutive_down"] == 1

    def test_missing_yaml_breaks_streak_not_cross_gap(self):
        """T-1 yaml 缺失时不得跨缺口把 T-2 当相邻：X 今日跌停、T-1 无 yaml、T-2 跌停 → streak=1"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # 只有 T-2 有 yaml，T-1（2026-03-27）整份缺失
            _write_limit_down_history(tmp_path, "2026-03-26", [("X.SZ", "甲")])
            # 制造一个更近、但无 post-market.yaml 的目录代表 T-1（目录在，文件缺）
            (tmp_path / "daily" / "2026-03-27").mkdir(parents=True, exist_ok=True)

            with patch("collectors.market.BASE_DIR", tmp_path):
                collector = MarketCollector(_mock_registry())
                result = {"limit_down": {"count": 1, "stocks": [
                    {"code": "X.SZ", "name": "甲", "pct_chg": -10.0}]}}
                collector._enrich_limit_down_structure(result, "2026-03-28")

        assert result["limit_down"]["stocks"][0]["consecutive_down"] == 1, \
            "T-1 数据缺口下不应跨缺口累计 T-2 的跌停"

    def test_empty_limit_down_no_error(self):
        """跌停为空/error 字典时不报错"""
        with tempfile.TemporaryDirectory() as tmp:
            with patch("collectors.market.BASE_DIR", Path(tmp)):
                collector = MarketCollector(_mock_registry())
                result = {"limit_down": {"error": "API 不可用"}}
                collector._enrich_limit_down_structure(result, "2026-03-28")  # 不抛错
        assert "down_ladder" not in result["limit_down"]


class TestRenderLimitDownStructure:
    def test_render_consecutive_down_ladder(self):
        with tempfile.TemporaryDirectory() as tmp:
            gen = ReportGenerator()
            gen.daily_dir = Path(tmp) / "daily"
            raw = {
                "date": "2026-03-28",
                "indices": {},
                "total_volume": {},
                "limit_up": {},
                "limit_down": {
                    "count": 3,
                    "down_ladder": {"3": ["甲"], "2": ["乙", "丁"]},
                    "consecutive_down_max": 3,
                },
                "sector_industry": {"data": []},
                "sector_concept": {"data": []},
                "northbound": {},
                "dragon_tiger": {"data": []},
            }
            md, _ = gen.generate_post_market("2026-03-28", raw)

        assert "连续跌停" in md
        assert "甲" in md
        assert "乙" in md

    def test_render_no_ladder_no_section(self):
        """无 down_ladder 时不渲染连续跌停梯队（跌停家数行仍在）"""
        with tempfile.TemporaryDirectory() as tmp:
            gen = ReportGenerator()
            gen.daily_dir = Path(tmp) / "daily"
            raw = {
                "date": "2026-03-28",
                "indices": {},
                "total_volume": {},
                "limit_up": {},
                "limit_down": {"count": 5},
                "sector_industry": {"data": []},
                "sector_concept": {"data": []},
                "northbound": {},
                "dragon_tiger": {"data": []},
            }
            md, _ = gen.generate_post_market("2026-03-28", raw)

        assert "连续跌停梯队" not in md
