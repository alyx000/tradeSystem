"""串阳首阴渲染：数据源失败必须明确可见。"""
from __future__ import annotations

import pytest


def test_source_failed_report_is_explicit_and_does_not_show_zero_hits() -> None:
    from services.string_yang import renderer

    md = renderer.render_daily({
        "status": "source_failed",
        "date": "2026-07-06",
        "main_sectors": ["半导体"],
        "mainline": {"status": "disabled"},
        "candidates": [],
        "source_errors": ["sw_map"],
    })

    assert "数据源失败，未完成扫描" in md
    assert "失败源：sw_map" in md
    assert "命中数量" not in md
    assert "今日无命中" not in md


def test_llm_call_failure_does_not_expose_internal_log_env_name() -> None:
    from services.string_yang import renderer

    md = renderer.render_daily({
        "status": "success",
        "date": "2026-07-14",
        "main_sectors": ["半导体"],
        "mainline": {
            "status": "llm_fallback",
            "source_errors": ["llm_call_failed"],
        },
        "candidates": [],
    })

    assert "LLM调用失败，降级成交额集中度" in md
    assert "ANTIGRAVITY_LOG_DIR" not in md


def test_read_legacy_fallback_preserves_date_and_reason(tmp_path):
    from services.string_yang import renderer

    date = "2026-11-16"
    md = renderer.render_daily({
        "date": date, "main_sectors": ["半导体"], "main_sector_degraded": True,
        "mainline": {"status": "llm_fallback", "source_errors": ["llm_call_failed"]},
    }).replace("LLM调用失败，", "LLM调用失败（诊断日志见 ANTIGRAVITY_LOG_DIR），")
    renderer.write_report(md, date, root=tmp_path)
    result = renderer.read_report_mainline(date, root=tmp_path)
    assert result.date == date
    assert result.status == "llm_fallback"
    assert result.main_sectors == ["半导体"]
    assert result.degraded is True
    assert result.source_errors == ["llm_call_failed"]

    renderer.write_report(md.replace(date, "2026-11-13"), date, root=tmp_path)
    with pytest.raises(ValueError, match="报告日期或标题不符"):
        renderer.read_report_mainline(date, root=tmp_path)


def test_mainline_data_gaps_survive_successful_report_roundtrip(tmp_path):
    from services.string_yang import renderer

    date = "2026-11-16"
    md = renderer.render_daily({
        "date": date, "main_sectors": ["半导体"],
        "mainline": {"status": "llm", "source_errors": ["concept_flow"]},
    })
    renderer.write_report(md, date, root=tmp_path)
    judgment = renderer.read_report_mainline(date, root=tmp_path)
    assert judgment.status == "llm"
    assert judgment.source_errors == ["concept_flow"]
