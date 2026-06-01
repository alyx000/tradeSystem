"""research_digest.service 编排：no_llm 不调 runner（L2）；双空显式空报告（M1）；A股失败继续美股（M1）；
A股 narrator 默认关 / 美股默认开。"""
from __future__ import annotations

from types import SimpleNamespace

from services.research_digest import service


def _res(data, success=True, error=None):
    return SimpleNamespace(success=success, data=data, error=error)


class FakeReg:
    def __init__(self, mapping):
        self.mapping = mapping

    def call(self, method, *args):
        return self.mapping.get(method, _res([]))


_CN_ROW = {"stock_code": "600519", "stock_name": "贵州茅台", "institution": "中信", "rating": "买入", "rating_change": "首次"}
_US_ROW = {"ticker": "NVDA", "firm": "Morgan Stanley", "action": "up", "to_grade": "Buy", "grade_date": "2026-05-29"}


def test_no_llm_does_not_call_runner():
    calls = []

    def runner(prompt, payload):
        calls.append(1)
        return {"items": []}

    reg = FakeReg({"get_research_report_list": _res([_CN_ROW]),
                   "get_us_rating_changes": _res([_US_ROW])})
    d = service.run_daily_digest(reg, "2026-05-29", no_llm=True, llm_runner=runner, us_tickers=["NVDA"])
    assert calls == []
    assert d.cn and d.us


def test_double_empty_is_empty_report():
    reg = FakeReg({"get_research_report_list": _res([]), "get_us_rating_changes": _res([])})
    d = service.run_daily_digest(reg, "2026-05-29", no_llm=True, us_tickers=["NVDA"])
    assert d.is_empty
    assert "今日两市均无符合条件" in d.markdown


def test_cn_fail_continues_us():
    reg = FakeReg({"get_research_report_list": _res(None, success=False, error="dns fail"),
                   "get_us_rating_changes": _res([_US_ROW])})
    d = service.run_daily_digest(reg, "2026-05-29", no_llm=True, us_tickers=["NVDA"])
    assert d.cn == [] and len(d.us) == 1


def test_us_narrate_default_on_cn_off():
    calls = []

    def runner(prompt, payload):
        calls.append(payload)
        return {"items": [{"id": i, "theme": "t", "one_liner": "o"} for i in range(len(payload))]}

    reg = FakeReg({"get_research_report_list": _res([_CN_ROW]),
                   "get_us_rating_changes": _res([_US_ROW])})
    d = service.run_daily_digest(reg, "2026-05-29", no_llm=False, llm_runner=runner, us_tickers=["NVDA"])
    assert d.us[0].get("one_liner") == "o"      # 美股默认叙事
    assert "one_liner" not in d.cn[0]            # A股默认不叙事
    assert len(calls) == 1                        # 只 US 调一次
    # 防假绿：payload 确为 _facts_for_llm 的 US 格式（含 ticker、不含 A股 stock_code）
    assert calls[0][0]["ticker"] == "NVDA"
    assert "stock_code" not in calls[0][0]
