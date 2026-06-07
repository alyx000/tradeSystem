"""研报速读 renderer 行业覆盖热度段 + service 接线。"""
from __future__ import annotations

from services.research_digest.renderer import render_md


def test_industry_section_present_and_positioned():
    """行业段在 Top3 之后、A股段之前；展示「N只/M篇」。"""
    cn_industry = [
        {"industry": "银行", "stock_count": 2, "report_count": 6},
        {"industry": "机械设备", "stock_count": 3, "report_count": 3},
    ]
    _, md = render_md("2026-05-29", cn_items=[], us_items=[], top3=[], cn_industry=cn_industry)
    assert "## 📊 行业覆盖热度" in md
    assert "银行 2只/6篇" in md
    assert "机械设备 3只/3篇" in md
    # 位置：行业段在 Top3 之后、A股段之前
    assert md.index("## 🏆 Top3") < md.index("## 📊 行业覆盖热度") < md.index("## 🇨🇳 A股机构评级")


def test_industry_section_caps_at_display_cap():
    """超过 INDUSTRY_DISPLAY_CAP 折叠为「…还有 N 个」。"""
    from services.research_digest.collector import INDUSTRY_DISPLAY_CAP
    cn_industry = [
        {"industry": f"行业{i}", "stock_count": 1, "report_count": 1}
        for i in range(INDUSTRY_DISPLAY_CAP + 3)
    ]
    _, md = render_md("2026-05-29", cn_items=[], us_items=[], top3=[], cn_industry=cn_industry)
    assert "…还有 3 个" in md


def test_no_fold_marker_when_exactly_at_cap():
    """恰好 = INDUSTRY_DISPLAY_CAP → 全展示，无「…还有 N 个」。"""
    from services.research_digest.collector import INDUSTRY_DISPLAY_CAP
    cn_industry = [
        {"industry": f"行业{i}", "stock_count": 1, "report_count": 1}
        for i in range(INDUSTRY_DISPLAY_CAP)
    ]
    _, md = render_md("2026-05-29", cn_items=[], us_items=[], top3=[], cn_industry=cn_industry)
    assert "## 📊 行业覆盖热度" in md
    assert "…还有" not in md


def test_exact_separator_string():
    """精确分隔串：「A N只/M篇 · B N只/M篇」。"""
    cn_industry = [
        {"industry": "银行", "stock_count": 2, "report_count": 6},
        {"industry": "机械设备", "stock_count": 3, "report_count": 3},
    ]
    _, md = render_md("2026-05-29", cn_items=[], us_items=[], top3=[], cn_industry=cn_industry)
    assert "银行 2只/6篇 · 机械设备 3只/3篇" in md


def test_no_industry_section_when_empty():
    """无行业数据 → 不出现行业段（默认 None 与显式 [] 都不渲染）。"""
    _, md = render_md("2026-05-29", cn_items=[], us_items=[], top3=[])
    assert "## 📊 行业覆盖热度" not in md
    _, md_empty = render_md("2026-05-29", cn_items=[], us_items=[], top3=[], cn_industry=[])
    assert "## 📊 行业覆盖热度" not in md_empty


def test_huibo_sections_render_recommendations_industries_stocks_and_trend():
    huibo_digest = {
        "recommendations": [
            {"title": "A证券-机器人行业深度", "reason": "AI数据中用电带动半体产业链", "source": "A证券 2026-06-03"},
            {"title": "B证券-算力行业专题", "reason": "首次覆盖+重点关注", "source": "B证券 2026-06-03"},
        ],
        "industry_summary": {
            "industries": [
                {"industry": "机器人", "viewpoint": "具身智能链条升温�", "sources": ["A证券-机器人行业深度"]},
            ],
        },
        "reader_results": [
            {
                "title": "A证券-机器人行业深度",
                "institution": "A证券",
                "date": "2026-06-03",
                "reader": {
                    "mentioned_stocks": [
                        {"name": "测试股份", "viewpoint": "供应链受益", "source": "第12页"},
                    ],
                },
            }
        ],
        "trend_summary": {"changes": ["机器人从分歧走向扩散"]},
    }
    _, md = render_md("2026-06-03", cn_items=[], us_items=[], top3=[], huibo_digest=huibo_digest)

    assert "## 📚 慧博深读 Top2" in md
    assert "A证券-机器人行业深度" in md
    assert "## 🧭 慧博行业观点聚合" in md
    assert "具身智能链条升温" in md
    assert "AI数据中心用电带动半导体产业链" in md
    assert "�" not in md
    assert "半体" not in md
    assert "## 🧾 慧博提及个股" in md
    assert "测试股份" in md and "第12页" in md
    assert "## 🔄 慧博近5交易日热点变化" in md
    assert "机器人从分歧走向扩散" in md


def test_huibo_recommendation_heading_uses_actual_count():
    huibo_digest = {
        "recommendations": [
            {"title": "A证券-机器人行业深度", "reason": "产业链观点清晰"},
            {"title": "B证券-算力行业专题", "reason": "首次覆盖"},
            {"title": "C证券-封装行业专题", "reason": "景气扩散"},
        ],
    }

    _, md = render_md("2026-06-03", cn_items=[], us_items=[], top3=[], huibo_digest=huibo_digest)

    assert "## 📚 慧博深读 Top3" in md
    assert "3. **C证券-封装行业专题**" in md


def test_huibo_digest_renders_failure_fallback_when_no_reader_success():
    huibo_digest = {
        "prescreened": [{"candidate": {"title": "A证券-机器人行业深度"}}],
        "reader_results": [
            {
                "title": "A证券-机器人行业深度",
                "reader": {"error": "reader_failed", "read_score": 0},
            }
        ],
        "industry_summary": {},
        "trend_summary": {},
        "recommendations": [],
    }

    _, md = render_md("2026-06-03", cn_items=[], us_items=[], top3=[], huibo_digest=huibo_digest)

    assert "## 📚 慧博深读" in md
    assert "已采集候选 1 篇；Antigravity 阅读成功 0 篇，失败 1 篇；未生成推荐。" in md
    assert "Reader 未完成：A证券-机器人行业深度" in md


def test_huibo_industry_sources_go_through_redline_filter():
    huibo_digest = {
        "industry_summary": {
            "industries": [
                {
                    "industry": "机器人",
                    "viewpoint": "具身智能链条升温",
                    "sources": ["A证券-机器人行业深度", "target price 100"],
                },
            ],
        },
    }

    _, md = render_md("2026-06-03", cn_items=[], us_items=[], top3=[], huibo_digest=huibo_digest)

    assert "A证券-机器人行业深度" in md
    assert "target price" not in md


def test_huibo_stock_source_fallback_uses_safe_report_metadata():
    huibo_digest = {
        "reader_results": [
            {
                "title": "A证券-target price 100",
                "institution": "A证券",
                "date": "2026-06-03",
                "reader": {
                    "mentioned_stocks": [
                        {"name": "测试股份", "viewpoint": "供应链受益", "source": ""},
                    ],
                },
            }
        ],
    }

    _, md = render_md("2026-06-03", cn_items=[], us_items=[], top3=[], huibo_digest=huibo_digest)

    assert "测试股份" in md
    assert "target price" not in md


def test_huibo_renderer_ignores_unexpected_llm_shapes():
    huibo_digest = {
        "recommendations": [{"title": "A证券-机器人行业深度", "reason": "产业链观点清晰"}],
        "industry_summary": {
            "industries": {"机器人": {"viewpoint": "dict shape should be ignored"}},
        },
        "reader_results": [
            {
                "title": "A证券-机器人行业深度",
                "reader": {
                    "mentioned_stocks": [
                        "bad-stock-shape",
                        {"name": "测试股份", "viewpoint": "供应链受益", "source": "第12页"},
                    ],
                },
            }
        ],
        "trend_summary": {"changes": {"机器人": "dict shape should be ignored"}},
    }

    _, md = render_md("2026-06-03", cn_items=[], us_items=[], top3=[], huibo_digest=huibo_digest)

    assert "## 📚 慧博深读 Top1" in md
    assert "测试股份" in md
    assert "dict shape should be ignored" not in md


def test_huibo_renderer_ignores_non_list_sources():
    huibo_digest = {
        "industry_summary": {
            "industries": [
                {"industry": "机器人", "viewpoint": "链条升温", "sources": "A证券-机器人行业深度"},
            ],
        },
    }

    _, md = render_md("2026-06-03", cn_items=[], us_items=[], top3=[], huibo_digest=huibo_digest)

    assert "链条升温" in md
    assert "来源：A；证；券" not in md


def test_huibo_renderer_ignores_bad_recommendation_items():
    huibo_digest = {
        "recommendations": [
            "bad-rec-shape",
            {"title": "A证券-机器人行业深度", "reason": "产业链观点清晰"},
        ],
    }

    _, md = render_md("2026-06-03", cn_items=[], us_items=[], top3=[], huibo_digest=huibo_digest)

    assert "## 📚 慧博深读 Top1" in md
    assert "bad-rec-shape" not in md
    assert "A证券-机器人行业深度" in md


def test_huibo_failure_fallback_ignores_bad_reader_rows():
    huibo_digest = {
        "prescreened": [{"candidate": {"title": "A证券-机器人行业深度"}}],
        "reader_results": [
            "bad-reader-shape",
            {"title": "A证券-机器人行业深度", "reader": {"error": "reader_failed"}},
        ],
    }

    _, md = render_md("2026-06-03", cn_items=[], us_items=[], top3=[], huibo_digest=huibo_digest)

    assert "已采集候选 1 篇；Antigravity 阅读成功 0 篇，失败 2 篇；未生成推荐。" in md
    assert "Reader 未完成：A证券-机器人行业深度" in md
    assert "bad-reader-shape" not in md


def test_service_computes_cn_industry():
    from unittest.mock import MagicMock
    from providers.base import DataResult
    from services.research_digest import service

    reg = MagicMock()

    def call_side(method, *a, **k):
        if method == "get_research_report_list":
            return DataResult(data=[
                {"stock_code": "600519", "stock_name": "贵州茅台", "institution": "中信"},
            ], source="mock")
        if method == "get_stock_sw_industry_map":
            return DataResult(data={"600519.SH": {"name": "贵州茅台", "sw_l1": "食品饮料", "sw_l2": "白酒Ⅱ"}}, source="mock")
        if method == "get_us_rating_changes":
            return DataResult(data=[], source="mock")
        return DataResult(data=[], source="mock")

    reg.call.side_effect = call_side
    out = service.run_daily_digest(reg, "2026-05-29", no_llm=True)
    assert out.cn_industry
    assert out.cn_industry[0]["industry"] == "食品饮料"
    assert "## 📊 行业覆盖热度" in out.markdown


def test_service_runs_huibo_source_with_isolated_runner(tmp_path):
    from unittest.mock import MagicMock
    from providers.base import DataResult
    from services.research_digest import huibo, service

    reg = MagicMock()
    reg.call.side_effect = lambda method, *a, **k: DataResult(data=[], source="mock")
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF-1.4\nfixture")
    candidates = huibo.parse_hot_report_rows([
        {"报告名称": "A证券-机器人行业深度：重点推荐产业链", "报告评级": "推荐", "页数": "20页", "时间": "2026-06-03", "分类": "行业分析", "PDF路径": str(pdf)},
    ])
    texts = {candidates[0].report_id: "核心观点：重点推荐机器人产业链。"}
    roles = []

    def source(_registry, date, window_days):
        assert date == "2026-06-03"
        assert window_days == 5
        return candidates, texts

    def runner(role, payload):
        roles.append(role)
        if role == "report_reader":
            return {"industry": "机器人", "viewpoint": "链条升温", "mentioned_stocks": [], "read_score": 90}
        if role == "industry_aggregator":
            return {"industries": [{"industry": "机器人", "viewpoint": "链条升温"}]}
        if role == "trend_aggregator":
            return {"changes": ["机器人升温"]}
        if role == "ranker":
            return {"recommendations": [{"title": payload["reports"][0]["title"], "reason": "链条升温"}]}
        raise AssertionError(role)

    out = service.run_daily_digest(
        reg,
        "2026-06-03",
        no_llm=False,
        llm_runner=lambda prompt, payload: {},
        huibo_llm_runner=runner,
        huibo_mode="desktop_terminal",
        huibo_source=source,
        huibo_summary_dir=tmp_path,
    )

    assert roles == ["report_reader", "industry_aggregator", "trend_aggregator", "ranker"]
    assert out.huibo_digest is not None
    assert "## 📚 慧博深读 Top1" in out.markdown
