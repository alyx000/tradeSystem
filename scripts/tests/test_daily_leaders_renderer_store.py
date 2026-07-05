from __future__ import annotations

import pytest

from services.daily_leaders.llm import enrich_with_llm_reason
from services.daily_leaders.renderer import render_markdown
from services.daily_leaders.store import DEFAULT_ROOT, read_proposal, write_proposal


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
    md = render_markdown(_proposal())
    assert "每日最票候选确认稿 · 2026-07-03" in md
    assert "[事实]" in md
    assert "[判断]" in md
    assert "老师观点对照：支持" in md
    assert "可回复：确认，全部录入" in md


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
    assert str(DEFAULT_ROOT).endswith("/tradeSystem/data/reports/daily-leaders")


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

    assert out == proposal
