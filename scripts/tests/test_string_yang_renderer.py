"""串阳首阴渲染：数据源失败必须明确可见。"""
from __future__ import annotations


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
