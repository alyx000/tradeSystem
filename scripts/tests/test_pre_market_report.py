"""ReportGenerator.generate_pre_market：mock 数据结构 + 临时目录写 YAML。"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from generators.report import ReportGenerator, _render_holding_risk_summary, _roman


def _minimal_market_data() -> dict:
    return {
        "global_indices": {
            "dow_jones": {"close": 100.0, "change_pct": 1.0},
            "nasdaq": {"close": 200.0, "change_pct": -0.5},
            "sp500": {"close": 300.0, "change_pct": 0.0},
            "a50": {"close": 400.0, "change_pct": 0.1},
        },
        "global_indices_apac": {
            "nikkei": {"close": 38000.0, "change_pct": 0.4},
            "kospi": {"close": 2600.0, "change_pct": -0.1},
        },
        "risk_indicators": {
            "vix": {"close": 15.0, "change_pct": -2.0},
            "us10y": {"close": 4.2, "change_bps": -3.0},
        },
        "us_china_assets": {
            "HXC": {"name": "HXC（金龙）", "close": 28.0, "change_pct": 1.1},
        },
        "commodities": {
            "gold": {"name": "黄金", "close": 2000.0, "change_pct": 0.5},
        },
        "forex": {
            "usd_cny": {"name": "USD/CNY", "close": 7.2, "change_pct": 0.0},
            "usd_cnh": {"name": "USD/CNH", "close": 7.22, "change_pct": 0.1},
        },
        "margin_data": {
            "trade_date": "2026-03-27",
            "margin_compare_date": "2026-03-26",
            "total_rzye_yi": 25000.0,
            "total_rqye_yi": 100.0,
            "total_rzrqye_yi": 25100.0,
            "delta_total_rzye_yi": 10.0,
            "delta_total_rqye_yi": -1.0,
            "delta_total_rzrqye_yi": 9.0,
            "exchanges": [
                {
                    "exchange_id": "SSE",
                    "rzye_yi": 12000.0,
                    "rqye_yi": 50.0,
                    "rzrqye_yi": 12050.0,
                    "delta_rzrqye_yi": 5.0,
                },
            ],
        },
    }


def test_generate_pre_market_sections_and_yaml(tmp_path: Path):
    gen = ReportGenerator()
    gen.daily_dir = tmp_path
    date = "2026-03-30"
    md, yaml_path = gen.generate_pre_market(
        date=date,
        market_data={
            **_minimal_market_data(),
            "prev_session_snapshot": {
                "date": "2026-03-29",
                "sh_index_close": 3200.0,
                "sh_index_change_pct": 0.5,
                "total_amount": 11000.0,
                "limit_up_count": 60,
                "limit_down_count": 5,
            },
            "prev_review_conclusion": ["昨日一句话结论", "三位一体补充"],
        },
        holdings_announcements={
            "000001.SZ": {
                "name": "测试银行",
                "announcements": [{"title": "测试公告", "ann_date": "20260328"}],
                "disclosure_dates": [{"ann_date": "20260420", "report_end": "20260331"}],
            }
        },
        watchlist_announcements={
            "688000.SH": {"name": "测试科创", "announcements": []},
        },
        holdings_info={
            "000001.SZ": {
                "name": "测试银行",
                "limit_prices": {"pre_close": 10.0, "up_limit": 11.0, "down_limit": 9.0},
                "news": [],
                "investor_qa": [],
                "research_reports": [],
            }
        },
        watchlist_info={
            "688000.SH": {
                "name": "测试科创",
                "limit_prices": {"pre_close": 20.0, "up_limit": 24.0, "down_limit": 16.0},
                "news": [],
                "investor_qa": [],
                "research_reports": [],
            }
        },
        holdings_signals={
            "date": date,
            "items": [
                {
                    "stock_code": "000001.SZ",
                    "stock_name": "测试银行",
                    "latest_task": {"trade_date": "2026-03-29", "action_plan": "若冲高减仓"},
                    "risk_flags": [
                        {"level": "high", "label": "财报临近", "reason": "20260420 有披露计划"},
                        {"level": "medium", "label": "跌破 MA5", "reason": "现价位于 MA5 下方"},
                        ],
                    },
                ],
            },
        news=[],
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
    assert "昨日（上一交易日）盘面摘要" in md
    assert "昨日复盘要点" in md
    assert "### 亚太股指" in md
    assert "日经225" in md
    assert "韩国综指" in md
    assert "## 二、美股中国金龙（隔夜）" in md
    assert "HXC" in md
    assert "## 四、融资融券（上一交易日）" in md
    assert "较 2026-03-26" in md
    assert "五、昨日计划未完成持仓" in md
    assert "六、持仓风险摘要" in md
    assert "七、持仓股公告" in md
    assert "持仓风险摘要" in md
    assert "2026-03-29 计划 若冲高减仓" in md
    assert "测试银行 (000001.SZ)：财报临近 / 跌破 MA5" in md
    assert "昨日计划：若冲高减仓" in md
    assert "八、持仓信息面" in md
    assert "今日涨停价 11.0 / 今日跌停价 9.0" in md
    assert "九、关注池公告" in md
    assert "十、关注池信息面" in md
    assert "今日涨停价 24.0 / 今日跌停价 16.0" in md
    assert "十一、今日日历" in md
    assert "预约披露: 20260420（报告期 20260331）" in md
    assert "财经新闻" not in md

    p = Path(yaml_path)
    assert p.exists()
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert data["date"] == date
    assert "watchlist_announcements" in data
    assert data["holdings_signals"]["items"][0]["stock_code"] == "000001.SZ"
    assert data["watchlist_announcements"]["688000.SH"]["name"] == "测试科创"


def test_generate_pre_market_us_china_error_and_empty_margin(tmp_path: Path):
    gen = ReportGenerator()
    gen.daily_dir = tmp_path
    md, _ = gen.generate_pre_market(
        date="2026-03-30",
        market_data={
            "global_indices": {k: {"close": 1, "change_pct": 0} for k in ["dow_jones", "nasdaq", "sp500", "a50"]},
            "global_indices_apac": {k: {"error": "fail"} for k in ["nikkei", "kospi"]},
            "risk_indicators": {},
            "us_china_assets": {"_error": "yfinance down"},
            "commodities": {},
            "forex": {},
            "margin_data": {},
        },
        holdings_announcements={},
    )
    assert "数据获取失败: yfinance down" in md
    assert "日经225: 数据获取失败" in md
    assert "（无融资融券汇总数据）" in md


def test_render_holding_risk_summary_task_only_without_high_medium_flags():
    """无 high/medium 风险标但有昨日计划时，风险摘要仍应输出待跟踪行。"""
    lines = _render_holding_risk_summary({
        "items": [
            {
                "stock_code": "000001.SZ",
                "stock_name": "测试银行",
                "risk_flags": [{"level": "low", "label": "低优先级", "reason": "x"}],
                "latest_task": {
                    "trade_date": "2026-04-05",
                    "action_plan": "观察缺口",
                },
            },
        ],
    })
    assert len(lines) == 1
    assert "昨日计划待跟踪" in lines[0]
    assert "观察缺口" in lines[0]


def test_roman_chinese_section_index():
    assert _roman(1) == "一"
    assert _roman(4) == "四"
    assert _roman(9) == "九"
    assert _roman(10) == "十"
    assert _roman(11) == "十一"
    assert _roman(19) == "十九"
    assert _roman(20) == "二十"
    assert _roman(21) == "二十一"
    assert _roman(99) == "九十九"
    assert _roman(100) == "100"
