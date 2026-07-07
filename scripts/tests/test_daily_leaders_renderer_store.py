from __future__ import annotations

import pytest

from services.daily_leaders.llm import enrich_with_llm_reason
from services.daily_leaders.renderer import render_markdown
from services.daily_leaders.store import DEFAULT_ROOT, REPO_ROOT, read_proposal, write_proposal


def _proposal():
    return {
        "date": "2026-07-03",
        "top_leaders": [
            {
                "stock": "688041 海光信息",
                "sector": "半导体",
                "attribute_type": "走势引领",
                "attribute": "启动日主动引领",
                "clarity": "高",
                "position": "主升初期",
                "is_new": True,
                "teacher_alignment": "支持",
                "evidence": [
                    {"label": "[事实]", "text": "半导体候选板块"},
                    {"label": "[判断]", "text": "走势引领候选"},
                ],
                "llm_reason": "",
            }
        ],
    }


def test_render_markdown_contains_labels_and_confirmation_instruction():
    proposal = _proposal()
    proposal["top_leaders"][0]["llm_rank"] = 1
    proposal["top_leaders"][0]["llm_role"] = "趋势中军"
    proposal["top_leaders"][0]["risk_flags"] = ["容量需复核"]
    md = render_markdown(proposal)
    assert "每日最票候选确认稿 · 2026-07-03" in md
    assert "[事实]" in md
    assert "[判断]" in md
    assert "老师观点对照：支持" in md
    assert "LLM裁判：[判断] 排序 1 / 最票属性 趋势中军" in md
    assert "风险标签：[判断] 容量需复核" in md
    assert "可回复：确认，全部录入" in md


def test_render_markdown_shows_skip_reason():
    md = render_markdown({
        "date": "2026-07-05",
        "top_leaders": [],
        "skipped": {"reason": "non_trading_day", "prev_trade_date": "2026-07-03"},
    })

    assert "跳过原因：[判断] non_trading_day" in md
    assert "上一交易日：2026-07-03" in md


def test_render_markdown_shows_candidate_limit_summary():
    proposal = _proposal()
    proposal["candidate_limit"] = {
        "max_candidates": 30,
        "original_count": 148,
        "deduped_count": 92,
        "duplicate_trimmed_count": 56,
        "trimmed_count": 118,
    }

    md = render_markdown(proposal)

    assert "候选收敛：[判断] 原始候选 148 条，按股票去重后 92 条，展示前 30 条，已折叠 118 条。" in md


def test_render_markdown_shows_min_amount_filter_summary():
    proposal = _proposal()
    proposal["candidate_filters"] = {"min_amount_yi": 20.0}

    md = render_markdown(proposal)

    assert "候选过滤：[判断] 已过滤成交额低于 20.0 亿或缺少可验证成交额的个股。" in md


def test_store_round_trip(tmp_path):
    paths = write_proposal(_proposal(), root=tmp_path)
    loaded = read_proposal("2026-07-03", root=tmp_path)
    assert paths["json"].name == "2026-07-03.json"
    assert paths["markdown"].name == "2026-07-03.md"
    assert loaded["top_leaders"][0]["stock"] == "688041 海光信息"


def test_store_rejects_invalid_dates(tmp_path):
    invalid_dates = ["../2026-07-03", "2026-07-03/foo", "/tmp/x", "20260703"]

    for invalid_date in invalid_dates:
        proposal = _proposal()
        proposal["date"] = invalid_date
        with pytest.raises(ValueError):
            write_proposal(proposal, root=tmp_path)
        with pytest.raises(ValueError):
            read_proposal(invalid_date, root=tmp_path)

    assert list(tmp_path.rglob("*")) == []


def test_default_root_is_repo_relative():
    assert DEFAULT_ROOT == REPO_ROOT / "data" / "reports" / "daily-leaders"


def test_llm_fallback_returns_original_when_disabled():
    proposal = _proposal()
    out = enrich_with_llm_reason(proposal, enabled=False)
    assert out == proposal


def test_llm_enrichment_uses_runner_mapping():
    proposal = _proposal()
    out = enrich_with_llm_reason(
        proposal,
        enabled=True,
        runner=lambda prompt: {"688041 海光信息|半导体": "走势引领清晰，老师观点支持，仍需人工确认。"},
    )
    assert out["top_leaders"][0]["llm_reason"] == "走势引领清晰，老师观点支持，仍需人工确认。"
    assert out["llm_status"] == {"ok": True}


def test_llm_judgement_reorders_and_labels_candidates():
    proposal = {
        "date": "2026-07-03",
        "top_leaders": [
            {"stock": "拓普集团", "sector": "同花顺出海50", "evidence": []},
            {"stock": "绿的谐波", "sector": "同花顺新质50", "evidence": []},
        ],
    }

    out = enrich_with_llm_reason(
        proposal,
        enabled=True,
        runner=lambda prompt: {
            "拓普集团|同花顺出海50": {
                "rank": 2,
                "role": "备选",
                "reason": "涨停但板块角色不如机器人分支清晰，仍需人工确认。",
                "risk_flags": ["题材归属偏宽"],
            },
            "绿的谐波|同花顺新质50": {
                "rank": 1,
                "role": "20cm",
                "reason": "新质生产力与机器人分支共振，领涨幅度更强，仍需人工确认。",
                "risk_flags": ["容量需复核"],
            },
        },
    )

    assert [item["stock"] for item in out["top_leaders"]] == ["绿的谐波", "拓普集团"]
    assert out["top_leaders"][0]["llm_rank"] == 1
    assert out["top_leaders"][0]["llm_role"] == "20cm"
    assert out["top_leaders"][0]["risk_flags"] == ["容量需复核"]
    assert out["top_leaders"][1]["llm_rank"] == 2


def test_llm_judgement_rejects_old_role_names_and_prompt_lists_new_attributes():
    proposal = _proposal()

    def runner(prompt):
        assert "趋势中军|小票弹性（连板）|20cm|30cm|10cm|备选|剔除" in prompt
        assert "排序优先级依次为：涨停/涨幅强度、成交额、板块主线审美" in prompt
        assert "概念资金流和老师观点只作为辅助证据" in prompt
        assert "走势引领|容量中军|分支核心" not in prompt
        return {
            "688041 海光信息|半导体": {
                "rank": 1,
                "role": "走势引领",
                "reason": "旧角色名应被忽略，仍需人工确认。",
            }
        }

    out = enrich_with_llm_reason(proposal, enabled=True, runner=runner)

    assert out["top_leaders"][0]["llm_rank"] == 1
    assert "llm_role" not in out["top_leaders"][0]


def test_llm_enrichment_drops_redline_reason():
    proposal = _proposal()
    out = enrich_with_llm_reason(
        proposal,
        enabled=True,
        runner=lambda prompt: {"688041 海光信息|半导体": "建议买入，目标价上看，仍需人工确认。"},
    )

    assert "llm_reason" not in out["top_leaders"][0] or not out["top_leaders"][0]["llm_reason"]


def test_llm_fail_closed_when_runner_raises():
    proposal = _proposal()

    def raising_runner(prompt):
        raise RuntimeError("llm unavailable")

    out = enrich_with_llm_reason(proposal, enabled=True, runner=raising_runner)

    assert out["top_leaders"] == proposal["top_leaders"]
    assert out["llm_status"] == {"ok": False, "reason": "exception"}


def test_llm_fail_closed_marks_empty_mapping_and_renderer_warning():
    proposal = _proposal()
    out = enrich_with_llm_reason(proposal, enabled=True, runner=lambda prompt: None)

    assert out["top_leaders"] == proposal["top_leaders"]
    assert out["llm_status"] == {"ok": False, "reason": "empty_mapping"}
    md = render_markdown(out)
    assert "LLM裁判未生效" in md
    assert "empty_mapping" in md
