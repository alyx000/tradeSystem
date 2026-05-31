"""volume_concentration formatter 单测:Markdown 片段断言。"""
from __future__ import annotations

from services.volume_concentration import formatter


def _record():
    return {
        "date": "2026-05-29",
        "top_n": 4,
        "total_amount_billion": 200.0,
        "market_total_billion": 10000.0,
        "stocks": [],
        "sector_summary": [
            {"industry": "电池", "count": 2, "amount_billion": 100.0, "share_in_top_n": 0.5, "codes": []},
            {"industry": "白酒Ⅱ", "count": 1, "amount_billion": 60.0, "share_in_top_n": 0.3, "codes": []},
            {"industry": "未分类", "count": 1, "amount_billion": 40.0, "share_in_top_n": 0.2, "codes": []},
        ],
        "source": {"industry_source": "tushare:index_member_all", "industry_coverage": 0.75,
                   "market_total_source": "tushare:index_daily"},
    }


def _trend(sufficient=True):
    if not sufficient:
        return {
            "days": 1, "sufficient": False,
            "sector_rotation": {"new": [], "dropped": [], "持续": []},
            "stock_rotation": {"new": [], "dropped": []},
            "sector_heat": [],
            "cr3_trend": {"current": 80.0, "previous": None, "delta_pp": None, "rank": 1,
                          "window": 1, "series": [80.0], "streak_dir": "flat", "streak_days": 0},
            "amount_trend": {"latest": 200.0, "previous": None, "change_pct": None,
                             "avg": 200.0, "vs_avg_pct": 0.0},
            "metabolism": {"core": 0, "fresh": 0, "new_by_sector": []},
            "stock_retention": [],
        }
    return {
        "days": 30, "sufficient": True,
        "sector_rotation": {"new": ["证券Ⅱ"], "dropped": ["白酒Ⅱ"], "持续": ["电池"]},
        "stock_rotation": {"new": [{"code": "A", "name": "甲"}], "dropped": [{"code": "Z", "name": "癸"}]},
        "sector_heat": [{"industry": "证券Ⅱ", "current": 30.0, "base": 18.0, "delta_pp": 12.0},
                        {"industry": "电池", "current": 10.0, "base": 11.0, "delta_pp": -1.0},
                        {"industry": "白酒Ⅱ", "current": 5.0, "base": 13.0, "delta_pp": -8.0}],
        "cr3_trend": {"current": 80.0, "previous": 72.0, "delta_pp": 8.0, "rank": 1, "window": 30,
                      "series": [70.0, 72.0, 80.0], "streak_dir": "up", "streak_days": 2},
        "amount_trend": {"latest": 200.0, "previous": 180.0, "change_pct": 11.11,
                         "avg": 190.0, "vs_avg_pct": 5.3},
        "metabolism": {"core": 14, "fresh": 4, "new_by_sector": [("证券Ⅱ", 1)]},
        "stock_retention": [{"code": "A", "name": "甲", "streak": 3}, {"code": "B", "name": "乙", "streak": 1}],
    }


def test_header_total_and_coverage():
    out = formatter.format_daily_report(_record(), _trend())
    assert "2026-05-29" in out
    assert "200.0" in out            # 合计成交额
    assert "覆盖率" in out and "75" in out
    assert "2.0%" in out             # 占两市 200/10000


def test_top3_excludes_unclassified_but_lists_it():
    out = formatter.format_daily_report(_record(), _trend())
    # 前3行业集中度 = 电池0.5 + 白酒0.3 = 80%(未分类不计)
    assert "80" in out
    assert "电池" in out and "白酒Ⅱ" in out
    # 未分类单列,标注不计入
    assert "未分类" in out and "不计入" in out


def test_sufficient_renders_all_analysis_blocks():
    """充分数据:CR3连升降 / 板块热度 / 头部资金 / 异动个股 / 连续在榜 全部渲染。"""
    out = formatter.format_daily_report(_record(), _trend(sufficient=True))
    # CR3 摘要行:环比 + 分位 + 连升
    assert "集中度 CR3" in out and "环比 +8.0pp" in out and "连升2日" in out
    # 🔥 板块热度:升温 / 降温
    assert "🔥 板块热度趋势" in out
    assert "证券Ⅱ +12.0pp" in out      # 升温
    assert "白酒Ⅱ -8.0pp" in out       # 降温
    # 头部资金:均值 / 新陈代谢 / 新进流向
    assert "近期均值 190.0 亿(+5.3%,放量)" in out
    assert "核心(在榜≥10日)14 只" in out and "今日新进4 只" in out
    assert "今日新进资金流向:证券Ⅱ×1" in out
    # 异动个股 + 连续在榜
    assert "异动个股" in out and "今日新进:甲" in out and "今日退出:癸" in out
    assert "连续在榜" in out and "甲(3天)" in out
    assert "乙" not in out              # streak 1 不进连续在榜


def test_insufficient_omits_analysis_blocks():
    """不足 2 日:只出兜底文案,不渲染热度/头部资金/异动等跨日块。"""
    out = formatter.format_daily_report(_record(), _trend(sufficient=False))
    assert "累积" in out and "1 天" in out
    assert "板块热度趋势" not in out and "头部资金" not in out and "异动个股" not in out


def test_trend_insufficient_fallback_text():
    out = formatter.format_daily_report(_record(), _trend(sufficient=False))
    assert "累积" in out and "1 天" in out


def _record_with_stocks():
    r = _record()
    r["stocks"] = [
        {"rank": 1, "code": "300308.SZ", "name": "中际旭创", "industry": "通信设备",
         "close": 1161.16, "amount_billion": 338.92, "change_pct": -3.07},
        {"rank": 2, "code": "300394.SZ", "name": "天孚通信", "industry": "通信设备",
         "close": 455.2, "amount_billion": 289.27, "change_pct": 1.71},
        {"rank": 3, "code": "688981.SH", "name": "", "industry": "半导体",
         "close": 100.0, "amount_billion": 200.0, "change_pct": 0.0},
    ]
    return r


def test_anomaly_stocks_block_shows_new_with_industry_and_change():
    """异动个股(替代逐只罗列):今日新进带行业+涨跌,从 record.stocks 取元数据。"""
    r = _record()
    r["stocks"] = [
        {"code": "300308.SZ", "name": "中际旭创", "industry": "通信设备",
         "amount_billion": 338.92, "change_pct": -3.07},
    ]
    t = _trend(sufficient=True)
    t["stock_rotation"] = {"new": [{"code": "300308.SZ", "name": "中际旭创"}], "dropped": []}
    out = formatter.format_daily_report(r, t)
    assert "异动个股" in out
    assert "中际旭创(通信设备 -3.07%)" in out   # 带行业 + 带符号涨跌


def test_anomaly_new_without_meta_falls_back_to_name_only():
    """新进股在 record.stocks 中无元数据 → 只显名称,不崩溃、不留空括号。"""
    t = _trend(sufficient=True)
    t["stock_rotation"] = {"new": [{"code": "X.SZ", "name": "某股"}], "dropped": []}
    out = formatter.format_daily_report(_record(), t)  # stocks=[]
    assert "今日新进:某股" in out
    assert "某股(" not in out   # 无元数据不渲染空括号


# ---- 报告优化 #1~#4 ----

def test_change_distribution_line():
    """#1 涨跌分布行:X 红 Y 绿 Z 平 + 均值(0 不带号)+ 最强/最弱。"""
    out = formatter.format_daily_report(_record_with_stocks(), _trend(sufficient=False))
    # _record_with_stocks: -3.07 / +1.71 / 0.0 → 1红1绿1平,均 (-3.07+1.71+0)/3=-0.45
    assert "1 红 1 绿" in out and "1 平" in out
    assert "均 -0.45%" in out
    assert "最强 天孚通信 +1.71%" in out
    assert "最弱 中际旭创 -3.07%" in out


def test_change_distribution_omitted_when_no_stocks():
    """无个股 → 涨跌分布行省略。"""
    out = formatter.format_daily_report(_record(), _trend(sufficient=False))  # stocks=[]
    assert "涨跌分布" not in out


def test_cr3_annotation_rendered_when_trend_has_cr3():
    """#3 CR3 环比 + 分位标注(trend 提供 cr3_trend 时)。"""
    t = _trend(sufficient=True)
    t["cr3_trend"] = {"current": 80.0, "previous": 72.0, "delta_pp": 8.0, "rank": 1, "window": 19}
    out = formatter.format_daily_report(_record(), t)
    assert "环比 +8.0pp" in out
    assert "近19日第1高" in out


def test_anomaly_dropped_stocks_rendered():
    """异动个股:今日新进/退出 同时渲染。"""
    t = _trend(sufficient=True)
    t["stock_rotation"] = {"new": [{"code": "300001.SZ", "name": "特锐德"}],
                           "dropped": [{"code": "600000.SH", "name": "浦发银行"}]}
    out = formatter.format_daily_report(_record(), t)
    assert "今日新进:特锐德" in out
    assert "今日退出:浦发银行" in out


def test_sector_heat_threshold_excludes_small_moves():
    """板块热度:|delta|≤1pp 不计入升温/降温(只报显著变动)。"""
    t = _trend(sufficient=True)
    t["sector_heat"] = [
        {"industry": "大涨", "current": 30.0, "base": 20.0, "delta_pp": 10.0},
        {"industry": "微动", "current": 10.0, "base": 9.5, "delta_pp": 0.5},   # ≤1,不计
        {"industry": "大跌", "current": 5.0, "base": 18.0, "delta_pp": -13.0},
    ]
    out = formatter.format_daily_report(_record(), t)
    assert "大涨 +10.0pp" in out and "大跌 -13.0pp" in out
    assert "微动" not in out


def test_fund_block_shrink_volume_label():
    """头部资金:今日 vs 近期均值为负 → 标『缩量』。"""
    t = _trend(sufficient=True)
    t["amount_trend"] = {"latest": 100.0, "previous": 120.0, "change_pct": -16.67,
                         "avg": 130.0, "vs_avg_pct": -23.1}
    out = formatter.format_daily_report(_record(), t)
    assert "近期均值 130.0 亿(-23.1%,缩量)" in out


def test_fund_block_flat_volume_label():
    """vs 近期均值=0 → 标『持平』,不误报放量(codex 中等)。"""
    t = _trend(sufficient=True)
    t["amount_trend"] = {"latest": 100.0, "previous": 100.0, "change_pct": 0.0,
                         "avg": 100.0, "vs_avg_pct": 0.0}
    out = formatter.format_daily_report(_record(), t)
    assert "近期均值 100.0 亿(0.0%,持平)" in out
    assert "放量" not in out


def test_retention_truncated_to_top_n():
    """#4 瘦身:连续在榜超过 8 只 → 只列前 8 + 等 M 只。"""
    t = _trend(sufficient=True)
    t["stock_retention"] = [{"code": f"C{i}", "name": f"股{i}", "streak": 20 - i} for i in range(12)]
    out = formatter.format_daily_report(_record(), t)
    assert "等 4 只" in out          # 12 - 8 截断
    assert "股0(20天)" in out        # streak 最高的在前


def test_change_distribution_all_flat_omits_extremes():
    """全平(审查高-2):最强/最弱无区分意义 → 省略。"""
    r = _record()
    r["stocks"] = [
        {"code": "A.SZ", "name": "甲", "industry": "电池", "amount_billion": 50.0, "change_pct": 0.0},
        {"code": "B.SZ", "name": "乙", "industry": "电池", "amount_billion": 40.0, "change_pct": 0.0},
    ]
    out = formatter.format_daily_report(r, _trend(sufficient=False))
    assert "0 红 0 绿 2 平" in out
    assert "最强" not in out and "最弱" not in out


def test_change_distribution_avg_zero_but_mixed_keeps_extremes():
    """正负相消均值=0 但非全平 → 保留最强/最弱,均值 0.0% 不带 +。"""
    r = _record()
    r["stocks"] = [
        {"code": "A.SZ", "name": "甲", "industry": "电池", "amount_billion": 50.0, "change_pct": 3.0},
        {"code": "B.SZ", "name": "乙", "industry": "电池", "amount_billion": 40.0, "change_pct": -3.0},
    ]
    out = formatter.format_daily_report(r, _trend(sufficient=False))
    assert "1 红 1 绿" in out
    assert "均 0.0%" in out          # avg=0 不带 +
    assert "最强 甲 +3.0%" in out


def test_anomaly_new_industry_empty_with_change_no_leading_space():
    """审查高-3 核实:新进股行业空但有涨跌 → "(-3.07%)" 无前导空格(strip 已处理)。"""
    r = _record()
    r["stocks"] = [{"code": "X.SZ", "name": "某股", "industry": "",
                    "amount_billion": 50.0, "change_pct": -3.07}]
    t = _trend(sufficient=True)
    t["stock_rotation"] = {"new": [{"code": "X.SZ", "name": "某股"}], "dropped": []}
    out = formatter.format_daily_report(r, t)
    assert "某股(-3.07%)" in out      # 行业空时 strip 去前导空格
    assert "某股( " not in out         # 无前导空格


def test_no_markdown_table_mobile_friendly():
    """钉钉手机端不渲染 markdown 表格 → 报告不得含表格分隔符,板块集中度改列表。"""
    out = formatter.format_daily_report(_record(), _trend(sufficient=True))
    assert "|---|" not in out        # 无表格分隔行
    assert "| 行业 |" not in out      # 无表头
    assert "电池" in out and "50.0%" in out   # 数据仍在(列表形式)


def test_heat_lines_have_red_green_emoji():
    """热度趋势升温/降温带红绿 emoji(A股红涨绿跌,跨端渲染)。"""
    out = formatter.format_daily_report(_record(), _trend(sufficient=True))
    assert "🔴" in out and "🟢" in out


def test_cr3_header_matches_cr3_trend_current_end_to_end():
    """codex 轻微:端到端锁口径 —— header「前3行业 X%」== compute_trend 的 cr3_trend.current。"""
    from services.volume_concentration import trend as trend_mod

    def _r(date, sectors):  # sectors=[(行业, share)]
        return {"date": date, "top_n": 20, "total_amount_billion": 100.0,
                "market_total_billion": 1000.0, "stocks": [],
                "sector_summary": [{"industry": i, "count": 1, "amount_billion": 10.0,
                                    "share_in_top_n": sh} for i, sh in sectors],
                "source": {"industry_coverage": 1.0}}

    records = [
        _r("2026-05-28", [("半导体", 0.30), ("通信", 0.20), ("电池", 0.10), ("未分类", 0.40)]),
        _r("2026-05-29", [("半导体", 0.35), ("通信", 0.20), ("电池", 0.10), ("未分类", 0.35)]),  # CR3=65
    ]
    tr = trend_mod.compute_trend(records)
    out = formatter.format_daily_report(records[-1], tr)

    assert f"前3行业 {tr['cr3_trend']['current']}%" in out   # header 数值 == cr3_trend.current(同口径)
