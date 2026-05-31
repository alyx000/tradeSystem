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
        return {"days": 1, "sufficient": False, "sector_rotation": {"new": [], "dropped": [], "持续": []},
                "amount_trend": {"latest": 200.0, "previous": None, "change_pct": None}, "stock_retention": []}
    return {
        "days": 30, "sufficient": True,
        "sector_rotation": {"new": ["证券Ⅱ"], "dropped": ["白酒Ⅱ"], "持续": ["电池"]},
        "amount_trend": {"latest": 200.0, "previous": 180.0, "change_pct": 11.11},
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


def test_trend_sufficient_renders_three_sections():
    out = formatter.format_daily_report(_record(), _trend(sufficient=True))
    assert "证券Ⅱ" in out      # 新进
    assert "环比" in out and "11.11" in out
    assert "甲" in out          # 留存 streak>=2
    assert "乙" not in out      # streak 1 不展示


def test_trend_insufficient_fallback_text():
    out = formatter.format_daily_report(_record(), _trend(sufficient=False))
    assert "累积" in out and "1 天" in out


def test_negative_change_pct_sign():
    """环比负增长:显示 -X%,不出现 +- 双符号。"""
    t = _trend()
    t["amount_trend"] = {"latest": 90.0, "previous": 100.0, "change_pct": -10.0}

    out = formatter.format_daily_report(_record(), t)

    assert "-10.0%" in out
    assert "+-" not in out


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


def test_stock_detail_table_lists_top_stocks_with_signed_change():
    """平铺 Top N 个股明细表:名称(代码) + 行业 + 成交额 + 带符号涨跌。"""
    out = formatter.format_daily_report(_record_with_stocks(), _trend(sufficient=False))
    assert "个股明细" in out
    assert "中际旭创(300308.SZ)" in out   # 名称(代码)
    assert "通信设备" in out               # 行业列
    assert "338.92" in out                 # 成交额
    assert "-3.07%" in out                 # 负涨幅
    assert "+1.71%" in out                 # 正涨幅带 + 号


def test_stock_detail_no_name_falls_back_to_code():
    out = formatter.format_daily_report(_record_with_stocks(), _trend(sufficient=False))
    assert "688981.SH" in out              # name 为空 → 仅展示代码


def test_stock_detail_omitted_when_no_stocks():
    out = formatter.format_daily_report(_record(), _trend(sufficient=False))  # stocks=[]
    assert "个股明细" not in out


def test_stock_detail_zero_change_no_plus_sign():
    """change_pct=0 渲染为 0.0%,不带 + 号(+ 仅用于正涨幅,审查中-6)。"""
    out = formatter.format_daily_report(_record_with_stocks(), _trend(sufficient=False))
    assert "0.0%" in out
    assert "+0.0%" not in out


def test_stock_detail_tolerates_missing_code_and_none_amount():
    """陈旧/异常数据:stock 缺 code 键、amount_billion 为 None → 不崩溃,优雅渲染(审查高-1/高-2)。"""
    r = _record()
    r["stocks"] = [
        {"rank": 1, "name": "", "industry": "未分类", "amount_billion": None, "change_pct": None},  # 无 code 键
    ]
    out = formatter.format_daily_report(r, _trend(sufficient=False))
    assert "个股明细" in out
    assert "None" not in out   # amount None 不渲染成字面 "None"


def test_stock_detail_omitted_when_stocks_none():
    """record["stocks"] 为 None(非空 list)→ 段省略,不崩溃(审查低-7)。"""
    r = _record()
    r["stocks"] = None
    out = formatter.format_daily_report(r, _trend(sufficient=False))
    assert "个股明细" not in out


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


def test_stock_rotation_rendered_in_trend():
    """#2 今日新进/退出 Top20 个股渲染。"""
    t = _trend(sufficient=True)
    t["stock_rotation"] = {"new": [{"code": "300001.SZ", "name": "特锐德"}],
                           "dropped": [{"code": "600000.SH", "name": "浦发银行"}]}
    out = formatter.format_daily_report(_record(), t)
    assert "今日 Top20 新进" in out
    assert "特锐德" in out and "浦发银行" in out


def test_trend_chixu_shows_count_not_full_list():
    """#4 瘦身:持续行业只显数量,不列全名。"""
    t = _trend(sufficient=True)
    t["sector_rotation"] = {"new": ["证券Ⅱ"], "dropped": [],
                            "持续": ["电池", "半导体", "通信设备", "白酒Ⅱ", "光伏设备"]}
    out = formatter.format_daily_report(_record(), t)
    assert "持续 5 个" in out
    assert "持续 电池、半导体" not in out   # 不再列全名


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
