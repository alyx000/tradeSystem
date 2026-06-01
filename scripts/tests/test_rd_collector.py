"""research_digest.collector：A股聚合+打分；A股失败不致命（M1）；美股标记；美东窗口（H2）。"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from services.research_digest import collector


def _res(data, success=True, error=None):
    return SimpleNamespace(success=success, data=data, error=error)


class FakeReg:
    def __init__(self, mapping):
        self.mapping = mapping

    def call(self, method, *args):
        return self.mapping.get(method, _res([]))


def test_collect_cn_aggregates_and_scores():
    rows = [
        {"stock_code": "600519", "stock_name": "贵州茅台", "institution": "中信", "rating": "买入", "rating_change": "调高"},
        {"stock_code": "600519", "stock_name": "贵州茅台", "institution": "国君", "rating": "增持", "rating_change": "维持"},
        {"stock_code": "000001", "stock_name": "平安银行", "institution": "广发", "rating": "买入", "rating_change": "维持"},
    ]
    reg = FakeReg({"get_research_report_list": _res(rows)})
    items = collector.collect_cn(reg, "2026-05-29")
    assert items[0]["stock_code"] == "600519"  # 2 机构 + 调高 → 分高排首
    assert items[0]["org_count"] == 2
    assert "调高" in items[0]["rating_changes"]
    assert items[0]["score"] > items[1]["score"]


def test_first_coverage_scores_highest_and_tagged():
    """鞠磊 #1：同等机构数下『首次覆盖』分高于『调高』，且打 signal 标签。"""
    rows = [
        {"stock_code": "300999", "stock_name": "新覆盖股", "institution": "中信", "rating": "买入", "rating_change": "首次"},
        {"stock_code": "600519", "stock_name": "贵州茅台", "institution": "国君", "rating": "增持", "rating_change": "调高"},
    ]
    reg = FakeReg({"get_research_report_list": _res(rows)})
    items = collector.collect_cn(reg, "2026-05-29")
    by = {i["stock_code"]: i for i in items}
    assert by["300999"]["score"] > by["600519"]["score"]
    assert "首次覆盖" in by["300999"]["signals"]
    assert "评级上调" in by["600519"]["signals"]
    assert items[0]["stock_code"] == "300999"


def test_rating_change_deduped_per_firm():
    """M3：同机构同方向重复源行去重，不重复叠加 score。"""
    rows = [
        {"stock_code": "600519", "stock_name": "茅台", "institution": "中信", "rating": "买入", "rating_change": "调高"},
        {"stock_code": "600519", "stock_name": "茅台", "institution": "中信", "rating": "买入", "rating_change": "调高"},
    ]
    reg = FakeReg({"get_research_report_list": _res(rows)})
    items = collector.collect_cn(reg, "2026-05-29")
    assert items[0]["org_count"] == 1
    assert items[0]["rating_changes"] == ["调高"]
    assert items[0]["score"] == 2.5  # 1 org + 1×调高(1.5)，非 1 + 2×1.5


def test_collect_cn_error_returns_empty_not_raise():
    reg = FakeReg({"get_research_report_list": _res(None, success=False, error="dns fail")})
    assert collector.collect_cn(reg, "2026-05-29") == []


def test_collect_cn_enriches_viewpoint_from_research_reports():
    """A股 Top 标的补真实研报标题作 viewpoint（东财 get_research_reports）；优先当日报告。"""
    rows = [{"stock_code": "600030", "stock_name": "中信证券", "institution": "中信",
             "rating": "买入", "rating_change": "首次"}]
    reports = [
        {"date": "2026-05-29", "institution": "东吴证券", "rating": "买入", "title": "再融资落地，业务全面高增"},
        {"date": "2026-04-29", "institution": "华源证券", "rating": "买入", "title": "旧报告标题"},
    ]
    reg = FakeReg({"get_research_report_list": _res(rows), "get_research_reports": _res(reports)})
    items = collector.collect_cn(reg, "2026-05-29")
    vp = items[0]["viewpoint"]
    assert vp["title"] == "再融资落地，业务全面高增"  # 命中当日，非最旧
    assert vp["institution"] == "东吴证券"


def test_pick_viewpoint_prefers_same_day_else_latest():
    reports = [
        {"date": "2026-05-30", "institution": "A", "title": "更新"},   # em 默认倒序，最新在前
        {"date": "2026-05-29", "institution": "B", "title": "当日"},
    ]
    assert collector._pick_viewpoint(reports, "2026-05-29")["title"] == "当日"   # 命中当日
    assert collector._pick_viewpoint(reports, "2026-06-01")["title"] == "更新"   # 无当日 → 取首条(最新)
    assert collector._pick_viewpoint([], "2026-05-29") is None                   # 空列表
    assert collector._pick_viewpoint([{"date": "2026-05-29", "title": ""}], "2026-05-29") is None  # 空标题


def test_pick_viewpoint_does_not_rely_on_source_order():
    """中1：即便上游升序返回（最旧在前），显式排序后仍取最新，不被源排序改动影响。"""
    asc = [
        {"date": "2026-04-01", "institution": "旧", "title": "最旧"},
        {"date": "2026-05-30", "institution": "新", "title": "最新"},
    ]
    assert collector._pick_viewpoint(asc, "2026-06-01")["title"] == "最新"


def test_enrich_viewpoint_no_cross_stock_mixup():
    """低2：每只标的拿到自己的研报（按 code 路由），不张冠李戴。"""
    rows = [
        {"stock_code": "600030", "stock_name": "中信", "institution": "x", "rating_change": "首次"},
        {"stock_code": "002594", "stock_name": "比亚迪", "institution": "y", "rating_change": "首次"},
    ]
    per_code = {
        "600030": _res([{"date": "2026-05-29", "institution": "东吴", "title": "中信的报告"}]),
        "002594": _res([{"date": "2026-05-29", "institution": "太平洋", "title": "比亚迪的报告"}]),
    }

    class ArgReg:
        def call(self, method, *args):
            if method == "get_research_report_list":
                return _res(rows)
            if method == "get_research_reports":
                return per_code.get(args[0], _res([]))
            return _res([])
    by = {i["stock_code"]: i for i in collector.collect_cn(ArgReg(), "2026-05-29")}
    assert by["600030"]["viewpoint"]["title"] == "中信的报告"
    assert by["002594"]["viewpoint"]["title"] == "比亚迪的报告"


def test_enrich_viewpoint_graceful_when_reports_fail():
    """逐股研报采集抛异常 → 不附 viewpoint、不中断聚合（不致命）。"""
    rows = [{"stock_code": "600519", "stock_name": "茅台", "institution": "中信",
             "rating": "买入", "rating_change": "调高"}]

    class Boom:
        def call(self, method, *args):
            if method == "get_research_report_list":
                return _res(rows)
            raise RuntimeError("em down")  # get_research_reports 抛
    items = collector.collect_cn(Boom(), "2026-05-29")
    assert items and "viewpoint" not in items[0]  # 优雅降级，无 viewpoint


def test_enrich_adds_first_coverage_signal_from_title():
    """em 标题写"首次覆盖"但 cninfo 漏标 → 补进 signals 置首（鞠磊 #1，更全更显著）。"""
    rows = [{"stock_code": "600132", "stock_name": "重庆啤酒", "institution": "万联",
             "rating": "买入", "rating_change": "维持"}]  # cninfo 只标"维持"，无首次
    reports = [{"date": "2026-05-29", "institution": "万联证券", "title": "首次覆盖：嘉士伯赋能，全国化推进"}]
    reg = FakeReg({"get_research_report_list": _res(rows), "get_research_reports": _res(reports)})
    items = collector.collect_cn(reg, "2026-05-29")
    assert items[0]["signals"][0] == "首次覆盖"  # 置首


def test_enrich_first_coverage_not_false_matched_by_首次():
    """收紧：标题含"首次"但非"首次覆盖"（首次盈利/首次进入指数）→ 不误判为首次覆盖。"""
    rows = [{"stock_code": "600001", "stock_name": "X", "institution": "中信",
             "rating": "买入", "rating_change": "维持"}]
    for title in ("一季报点评：首次实现扭亏为盈", "首次进入沪深300指数", "首次与英伟达达成合作"):
        reg = FakeReg({"get_research_report_list": _res(rows),
                       "get_research_reports": _res([{"date": "2026-05-29", "institution": "中信", "title": title}])})
        items = collector.collect_cn(reg, "2026-05-29")
        assert "首次覆盖" not in items[0]["signals"], f"误判: {title}"


def test_enrich_no_duplicate_first_coverage_signal():
    """cninfo 已标首次 + 标题也含首次 → 不重复加。"""
    rows = [{"stock_code": "300999", "stock_name": "X", "institution": "中信",
             "rating": "买入", "rating_change": "首次"}]
    reports = [{"date": "2026-05-29", "institution": "中信", "title": "首次覆盖：xxx"}]
    reg = FakeReg({"get_research_report_list": _res(rows), "get_research_reports": _res(reports)})
    items = collector.collect_cn(reg, "2026-05-29")
    assert items[0]["signals"].count("首次覆盖") == 1


def test_collect_us_marks_market():
    us = [{"ticker": "NVDA", "firm": "MS", "action": "up", "to_grade": "Buy", "grade_date": "2026-05-29"}]
    reg = FakeReg({"get_us_rating_changes": _res(us)})
    out = collector.collect_us(reg, ["NVDA"], ("2026-05-25", "2026-05-29"))
    assert out[0]["market"] == "US" and out[0]["ticker"] == "NVDA"


def test_collect_us_error_returns_empty():
    reg = FakeReg({"get_us_rating_changes": _res(None, success=False, error="all failed")})
    assert collector.collect_us(reg, ["NVDA"], ("2026-05-25", "2026-05-29")) == []


def test_us_date_window_uses_eastern_calendar():
    now = datetime(2026, 6, 1, 18, 0, tzinfo=ZoneInfo("America/New_York"))
    start, end = collector.us_date_window(lookback_days=5, now_et=now)
    assert end == "2026-06-01"
    assert start == "2026-05-27"
