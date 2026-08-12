from __future__ import annotations

import json

from services.emotion_leader.renderer import render_daily, write_reports


def _result() -> dict:
    return {
        "date": "2026-07-23",
        "generated_at": "2026-07-23T22:00:00+08:00",
        "fact_source": "sqlite:daily_market.raw_data + provider:test",
        "status": "partial",
        "definition": "测试口径",
        "coverage": {"loaded_limit_days": 3, "expected_open_days": 4},
        "refresh": {
            "mode": "incremental",
            "previous_report_date": "2026-07-22",
            "discovered_count": 2,
            "metric_refresh_count": 1,
            "cached_archived_count": 1,
        },
        "source_errors": ["历史涨停事实缺1个开放日"],
        "summary": {
            "active_count": 1,
            "today_limit_up_count": 1,
            "new_peak_count": 1,
            "today_limit_down_count": 0,
            "interval_gain_median_pct": 51.0,
            "distance_from_peak_median_pct": -0.66,
        },
        "active": [{
            "name": "甲公司",
            "code": "000001.SZ",
            "board_type": "10cm",
            "wave_label": "二波",
            "manual_confirmed": False,
            "industry": "测试行业",
            "max_gain_pct": 52.0,
            "interval_gain_pct": 51.0,
            "distance_from_peak_pct": -0.66,
            "launch_date": "2026-07-20",
            "max_height": 3,
            "current_state": "涨停",
            "new_peak_today": True,
        }],
        "promoted_today": [],
        "new_candidates": [],
        "height_breakthrough": {
            "status": "triggered",
            "source_status": "complete",
            "lookback_open_days": 20,
            "previous_max_height": 2,
            "current_max_height": 3,
            "leaders": [{
                "name": "甲公司",
                "code": "000001.SZ",
                "launch_date": "2026-07-20",
                "launch_method": "limit_chain",
                "current_height": 3,
            }],
        },
    }


def test_renderer_keeps_fact_and_judgment_boundaries() -> None:
    text = render_daily(_result())
    assert "数值为 [事实]，波段为 [判断]" in text
    assert "甲公司 `000001.SZ`" in text
    assert "52.0%" in text
    assert "不构成买卖建议" in text
    assert "2026-07-23T22:00:00+08:00" in text
    assert "指标刷新：增量；本次 1/2 只；复用已归档 1 只" in text
    assert "[事实] 打开高度：甲公司（3板，启动日2026-07-20）" in text
    assert "[判断] 上述启动日列为情绪节点日候选" in text


def test_report_writer_creates_markdown_and_json(tmp_path) -> None:
    result = _result()
    markdown = render_daily(result)
    md_path, json_path = write_reports(result, markdown, root=tmp_path)

    assert md_path.read_text(encoding="utf-8") == markdown
    assert json.loads(json_path.read_text(encoding="utf-8"))["status"] == "partial"
