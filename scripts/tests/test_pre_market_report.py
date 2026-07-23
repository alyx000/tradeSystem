"""ReportGenerator.generate_pre_market：mock 数据结构 + 临时目录写 YAML。"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from generators.report import ReportGenerator, _render_holding_risk_summary, _roman
from utils.fx_validation import CHINAMONEY_C_SWAP_URL, CHINAMONEY_SPOT_URL


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
            "usd_cny": {
                "name": "USD/CNY（在岸即期）",
                "pair": "USD/CNY",
                "mid": 6.7729,
                "close": 6.7729,
                "bid": 6.7725,
                "ask": 6.7733,
                "snapshot_time": "2026-03-30 07:00:06",
                "source_date": "2026-03-30",
                "validated_for_date": "2026-03-30",
                "status": "latest_available",
                "price_kind": "computed_bid_ask_mid",
                "close_semantics": "computed_bid_ask_mid",
                "mid_method": "bid_ask_arithmetic_mean",
                "_source": "chinamoney:rfx-sp-quot",
                "_source_url": CHINAMONEY_SPOT_URL,
            },
            "usd_cnh": {"name": "USD/CNH", "close": 7.22, "change_pct": 0.1},
        },
        "fx_swaps": {
            "usd_cny_1y": {
                "name": "USD/CNY 1Y C-Swap定盘",
                "pair": "USD/CNY",
                "tenor": "1Y",
                "swap_point_pips": -1818.25,
                "forward_rate": 6.5918,
                "curve_time": "2026-03-27 16:30:00.0",
                "source_date": "2026-03-27",
                "validated_for_date": "2026-03-27",
                "status": "latest_available",
                "quote_source": "报价数据",
                "fixing_source": "报价数据",
                "price_kind": "c_swap_fixing",
                "_source": "chinamoney:fx-c-swap-fixing",
                "_source_url": CHINAMONEY_C_SWAP_URL,
            }
        },
        "_fx_context": {
            "phase": "pre",
            "spot_expected_date": "2026-03-30",
            "swap_expected_date": "2026-03-27",
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
        "macro_indicators": {
            "pmi": {
                "name": "采购经理人指数 PMI",
                "source": "macro_china_pmi",
                "period_col": "月份",
                "latest": {"月份": "202505", "制造业-指数": 49.5, "制造业-同比增长": -0.3, "period": "202505"},
                "trend": [
                    {"月份": "202504", "制造业-指数": 49.0, "制造业-同比增长": -0.5, "period": "202504"},
                    {"月份": "202505", "制造业-指数": 49.5, "制造业-同比增长": -0.3, "period": "202505"},
                ],
            },
            "lpr": {"name": "贷款市场报价利率 LPR", "source": "macro_china_lpr", "error": "backend down"},
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
                        {"level": "medium", "label": "临近止盈", "reason": "现价 192.00 接近止盈 193.00"},
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
    assert "USD/CNY（在岸即期）: 6.7729（系统按买 6.7725 / 卖 6.7733 计算中值，最新可用快照，数据页更新于 2026-03-30 07:00:06" in md
    assert "USD/CNY 1Y C-Swap定盘: -1818.25 Pips" in md
    assert "全价汇率 6.5918，报价数据，截至 2026-03-27 16:30:00.0" in md
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
    # 宏观经济指标段（无序号，置于「四」与「五」之间，不影响后续中文序号）
    assert "## 宏观经济指标" in md
    assert "### 采购经理人指数 PMI" in md
    assert "制造业-指数" in md
    assert "获取失败: backend down" in md  # 指标级失败优雅降级
    # 宏观段位于「四」之后、「五」之前
    assert md.index("## 宏观经济指标") > md.index("## 四、融资融券")
    assert md.index("## 宏观经济指标") < md.index("五、昨日计划未完成持仓")

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


def test_generate_pre_market_macro_large_value_no_scientific_notation(tmp_path: Path):
    """M2/社融 大数值（亿元量级）须以千分位呈现，不能输出科学计数法。"""
    gen = ReportGenerator()
    gen.daily_dir = tmp_path
    market_data = _minimal_market_data()
    market_data["macro_indicators"] = {
        "m2": {
            "name": "货币供应量 M2",
            "source": "macro_china_money_supply",
            "period_col": "月份",
            "latest": {"月份": "202505", "M2数量": 3200000.0, "period": "202505"},
            "trend": [{"月份": "202505", "M2数量": 3200000.0, "period": "202505"}],
        }
    }
    md, _ = gen.generate_pre_market(date="2026-03-30", market_data=market_data, holdings_announcements={})
    assert "3,200,000" in md
    assert "e+0" not in md  # 无科学计数法


def test_generate_pre_market_macro_whole_error(tmp_path: Path):
    """宏观整体失败 {"error": ...}：渲染单行错误，不抛异常、不出指标表。"""
    gen = ReportGenerator()
    gen.daily_dir = tmp_path
    market_data = _minimal_market_data()
    market_data["macro_indicators"] = {"error": "所有宏观指标获取失败"}
    md, _ = gen.generate_pre_market(date="2026-03-30", market_data=market_data, holdings_announcements={})
    assert "## 宏观经济指标" in md
    assert "- 宏观经济指标: 所有宏观指标获取失败" in md
    assert "### 采购经理人指数 PMI" not in md


def test_generate_pre_market_macro_missing_renders_placeholder(tmp_path: Path):
    """无 macro_indicators 键：渲染占位，不抛异常。"""
    gen = ReportGenerator()
    gen.daily_dir = tmp_path
    market_data = _minimal_market_data()
    market_data.pop("macro_indicators", None)
    md, _ = gen.generate_pre_market(date="2026-03-30", market_data=market_data, holdings_announcements={})
    assert "## 宏观经济指标" in md
    assert "（无宏观经济指标数据）" in md


def test_overnight_index_shows_as_of_date(tmp_path: Path):
    """隔夜指数带 as_of 时须标注「截至 日期」，让跨市场/跨节假日的数据日期可验证。"""
    gen = ReportGenerator()
    gen.daily_dir = tmp_path
    market_data = _minimal_market_data()
    market_data["global_indices"]["dow_jones"] = {
        "close": 50579.7, "change_pct": 0.58, "as_of": "2026-05-22",
    }
    md, _ = gen.generate_pre_market(date="2026-05-26", market_data=market_data, holdings_announcements={})
    assert "截至 2026-05-22" in md, "带 as_of 的隔夜指数应显示数据日期"


def test_golden_dragon_shows_as_of_date(tmp_path: Path):
    """金龙(HXC)走 PGJ ETF 历史时带 as_of，须显示「截至 日期」（隔夜跨节假日数据日期可验证）。"""
    gen = ReportGenerator()
    gen.daily_dir = tmp_path
    market_data = _minimal_market_data()
    market_data["us_china_assets"] = {
        "HXC": {"name": "HXC（纳斯达克中国金龙ETF·PGJ）", "close": 25.16,
                "change_pct": -2.14, "as_of": "2026-05-22"},
    }
    md, _ = gen.generate_pre_market(date="2026-05-26", market_data=market_data, holdings_announcements={})
    assert "截至 2026-05-22" in md
    assert "金龙ETF·PGJ" in md


def test_overnight_index_without_as_of_has_no_date_suffix(tmp_path: Path):
    """实时源（无 as_of，如 A50 夜盘期货）不应硬塞日期后缀。"""
    gen = ReportGenerator()
    gen.daily_dir = tmp_path
    market_data = _minimal_market_data()
    # a50 走期货实时报价，无 as_of
    market_data["global_indices"]["a50"] = {"close": 15714.0, "change_pct": -0.44}
    md, _ = gen.generate_pre_market(date="2026-05-26", market_data=market_data, holdings_announcements={})
    a50_line = next(line for line in md.splitlines() if "A50期货" in line and "15714" in line)
    assert "截至" not in a50_line, f"无 as_of 不应出现截至后缀: {a50_line}"


def test_generate_pre_market_stale_margin_annotation(tmp_path: Path):
    # 融资融券走 DB 回退（stale=True）时，报告须标注"最近一次入库（截至 X）"，不冒充当日。
    gen = ReportGenerator()
    gen.daily_dir = tmp_path
    market_data = _minimal_market_data()
    market_data["margin_data"] = {
        "trade_date": "2026-04-30", "as_of": "2026-04-30", "stale": True,
        "total_rzye_yi": 18000.0, "total_rqye_yi": 500.0, "total_rzrqye_yi": 18500.0,
        "exchanges": [],
    }
    md, _ = gen.generate_pre_market(date="2026-05-22", market_data=market_data, holdings_announcements={})
    assert "最近一次入库" in md
    assert "截至 2026-04-30" in md


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


def test_render_holding_risk_summary_prioritizes_stop_loss_before_other_medium_flags():
    lines = _render_holding_risk_summary({
        "items": [
            {
                "stock_code": "300750.SZ",
                "stock_name": "宁德时代",
                "risk_flags": [
                    {"level": "medium", "label": "跌破 MA5", "reason": "现价位于 MA5 下方"},
                    {"level": "medium", "label": "临近止盈", "reason": "现价接近止盈 193.00"},
                    {"level": "medium", "label": "临近止损", "reason": "现价接近止损 175.00"},
                ],
            },
        ],
    })
    assert len(lines) == 1
    assert "临近止损 / 跌破 MA5" in lines[0]
    assert "临近止盈" not in lines[0]


def test_pre_market_info_section_not_compact(tmp_path: Path):
    """端到端守护：盘后瘦身引入 compact 后，盘前简报的持仓信息面必须仍走 compact=False。

    若有人误把 generate_pre_market 的 _render_stock_info_section 调用改成 compact=True，
    本测试会失败（盘前应保留 3 条互动易 / 答案 150 字 / 盘前边界，不被盘后瘦身波及）。
    """
    gen = ReportGenerator()
    gen.daily_dir = tmp_path
    holdings_info = {
        "000001.SZ": {
            "name": "测试银行",
            "limit_prices": {"pre_close": 10.0, "up_limit": 11.0, "down_limit": 9.0},
            "investor_qa": [{"question": f"问题{i}", "answer": "答" * 200} for i in range(3)],
            "research_reports": [
                {"institution": f"机构{i}", "rating": "买入", "date": "20260328"} for i in range(3)
            ],
            "news": [{"title": f"新闻{i}", "time": "10:00"} for i in range(3)],
        }
    }
    md, _ = gen.generate_pre_market(
        "2026-03-30",
        {**_minimal_market_data()},
        holdings_announcements={},
        holdings_info=holdings_info,
    )
    assert "盘前边界" in md                            # 盘前保留盘前边界（compact 会删）
    assert "问题2" in md                              # 互动易 3 条（compact 仅 1 条）
    assert "答" * 150 in md and "答" * 151 not in md  # 答案 150 字（compact 截至 80）
    assert "机构2" in md                              # 研报 3 条（compact 仅 2 条）
    assert "新闻2" in md                              # 新闻 3 条（compact 仅 2 条）


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
