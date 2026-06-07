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


def test_huibo_digest_failure_does_not_block_base_digest(tmp_path):
    from services.research_digest import huibo

    reg = FakeReg({"get_research_report_list": _res([_CN_ROW]),
                   "get_us_rating_changes": _res([]),
                   "get_stock_sw_industry_map": _res({})})
    candidates = huibo.parse_hot_report_rows([
        {"报告名称": "A证券-机器人行业深度", "页数": "20页", "时间": "2026-06-03", "分类": "行业分析"},
    ])

    def source(_registry, _date, _window_days):
        return candidates, {}

    def fail_digest(*args, **kwargs):
        raise RuntimeError("huibo failed")

    old = huibo.run_huibo_digest
    huibo.run_huibo_digest = fail_digest
    try:
        out = service.run_daily_digest(
            reg,
            "2026-06-03",
            no_llm=False,
            llm_runner=lambda prompt, payload: {"items": []},
            huibo_mode="desktop_terminal",
            huibo_source=source,
            huibo_summary_dir=tmp_path,
        )
    finally:
        huibo.run_huibo_digest = old

    assert out.cn
    assert out.huibo_digest is None
    assert "A股机构评级" in out.markdown


def test_huibo_service_wraps_plain_llm_runner_as_role_runner(tmp_path, monkeypatch):
    from services.research_digest import huibo

    reg = FakeReg({"get_research_report_list": _res([]), "get_us_rating_changes": _res([])})
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF-1.4\nfixture")
    candidates = huibo.parse_hot_report_rows([
        {"报告名称": "A证券-机器人行业深度", "页数": "20页", "时间": "2026-06-03", "分类": "行业分析", "PDF路径": str(pdf)},
    ])
    prompts = []

    def source(_registry, _date, _window_days):
        return candidates, {}

    def prompt_runner(prompt, payload):
        prompts.append(prompt)
        if "行业研报聚合 agent" in prompt:
            return {"industries": []}
        if "热点变化聚合 agent" in prompt:
            return {"changes": []}
        if "研报推荐排序 agent" in prompt:
            return {"recommendations": [{"title": candidates[0].title, "reason": "已读"}]}
        raise AssertionError(prompt)

    monkeypatch.setattr(huibo, "_run_antigravity_pdf_reader", lambda payload: {
        "industry": "机器人",
        "key_points": ["产业链升温"],
        "mentioned_stocks": [],
        "read_score": 90,
    })

    out = service.run_daily_digest(
        reg,
        "2026-06-03",
        no_llm=False,
        llm_runner=prompt_runner,
        huibo_mode="desktop_terminal",
        huibo_source=source,
        huibo_summary_dir=tmp_path,
    )

    assert out.huibo_digest is not None
    assert prompts
    assert all(prompt != "report_reader" for prompt in prompts)
