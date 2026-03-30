"""ReportGenerator.generate_pre_market：mock 数据结构 + 临时目录写 YAML。"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from generators.report import ReportGenerator


def _minimal_market_data() -> dict:
    return {
        "global_indices": {
            "dow_jones": {"close": 100.0, "change_pct": 1.0},
            "nasdaq": {"close": 200.0, "change_pct": -0.5},
            "sp500": {"close": 300.0, "change_pct": 0.0},
            "a50": {"close": 400.0, "change_pct": 0.1},
        },
        "global_indices_apac": {
            "hsi": {"close": 25000.0, "change_pct": 0.2},
            "hstech": {"close": 5000.0, "change_pct": -0.3},
            "nikkei": {"close": 38000.0, "change_pct": 0.4},
        },
        "risk_indicators": {
            "vix": {"close": 15.0, "change_pct": -2.0},
            "us10y": {"close": 4.2, "change_bps": -3.0},
        },
        "us_china_assets": {
            "KWEB": {"name": "KWEB", "close": 28.0, "change_pct": 1.1},
            "FXI": {"name": "FXI", "close": 35.0, "change_pct": -0.2},
        },
        "commodities": {
            "gold": {"name": "黄金", "close": 2000.0, "change_pct": 0.5},
        },
        "forex": {
            "usd_cny": {"name": "USD/CNY", "close": 7.2, "change_pct": 0.0},
        },
        "margin_data": {
            "trade_date": "2026-03-27",
            "total_rzye_yi": 25000.0,
            "total_rqye_yi": 100.0,
            "total_rzrqye_yi": 25100.0,
            "exchanges": [
                {"exchange_id": "SSE", "rzye_yi": 12000.0, "rqye_yi": 50.0, "rzrqye_yi": 12050.0},
            ],
        },
    }


def test_generate_pre_market_sections_and_yaml(tmp_path: Path):
    gen = ReportGenerator()
    gen.daily_dir = tmp_path
    date = "2026-03-30"
    md, yaml_path = gen.generate_pre_market(
        date=date,
        market_data=_minimal_market_data(),
        holdings_announcements={
            "000001.SZ": {
                "name": "测试银行",
                "announcements": [{"title": "测试公告", "ann_date": "20260328"}],
            }
        },
        watchlist_announcements={
            "688000.SH": {"name": "测试科创", "announcements": []},
        },
        news=[{"title": "头条", "time": "09:00", "source": "测试源"}],
        calendar_events=[
            {
                "event": "CPI",
                "region": "美国",
                "time": "20:30",
                "importance": "高",
                "expected": "3.0",
                "prior": "2.9",
            }
        ],
    )

    assert "# 盘前简报 2026-03-30" in md
    assert "### 亚太股指" in md
    assert "恒生指数" in md
    assert "## 二、美股中国资产相关" in md
    assert "KWEB" in md
    assert "## 四、融资融券（上一交易日）" in md
    assert "五、持仓股公告" in md
    assert "六、关注池公告" in md
    assert "七、财经新闻" in md
    assert "八、今日日历" in md

    p = Path(yaml_path)
    assert p.exists()
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert data["date"] == date
    assert "watchlist_announcements" in data
    assert data["watchlist_announcements"]["688000.SH"]["name"] == "测试科创"


def test_generate_pre_market_us_china_error_and_empty_margin(tmp_path: Path):
    gen = ReportGenerator()
    gen.daily_dir = tmp_path
    md, _ = gen.generate_pre_market(
        date="2026-03-30",
        market_data={
            "global_indices": {k: {"close": 1, "change_pct": 0} for k in ["dow_jones", "nasdaq", "sp500", "a50"]},
            "global_indices_apac": {k: {"error": "fail"} for k in ["hsi", "hstech", "nikkei"]},
            "risk_indicators": {},
            "us_china_assets": {"_error": "yfinance down"},
            "commodities": {},
            "forex": {},
            "margin_data": {},
        },
        holdings_announcements={},
    )
    assert "数据获取失败: yfinance down" in md
    assert "恒生指数: 数据获取失败" in md
    assert "（无融资融券汇总数据）" in md
