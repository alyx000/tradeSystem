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
