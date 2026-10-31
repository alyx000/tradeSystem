"""七天横盘不创新低报告与落盘收据。"""
from __future__ import annotations

import json

import pytest

from services.no_new_low import constants, renderer


def _candidate(code="600001"):
    return {
        "code": code, "name": "示例", "industry": "电力", "anchor_date": "2026-08-01",
        "base_range_pct": 8.0, "low_floor_margin_pct": 1.0,
        "latest_low_lift_pct": 2.0, "window_return_pct": 3.0,
        "abs_window_return_pct": 3.0, "max_close_deviation_pct": 4.0,
        "volume_ratio": 0.8, "today_amount_yi": 20.0, "horizontal_rank": 1,
    }


def _result():
    candidate = _candidate()
    return {
        "status": "ok", "date": "2026-08-10", "universe_count": 5000,
        "candidate_count": 1, "candidates": [candidate], "pk_pool": [candidate],
        "pk_pool_count": 1, "pk_excluded_count": 0, "snapshot_dates": ["d"] * 20,
        "industry": {"status": "ok", "basis": "current_membership_snapshot",
                     "candidate_rows": 1, "matched_rows": 1, "coverage": 1.0},
        "coverage_by_date": {"2026-08-10": {"joint_rows": 5000, "target_universe_coverage": 0.99}},
        "rejects": {"new_low_within_seven_bars": 3000},
    }


def test_render_labels_fact_judgement_and_empty_semantics():
    markdown = renderer.render_daily(_result(), {"status": "skipped", "wins": {}, "ranks": None,
                                                        "attempted": 0, "total": 0, "valid_ratio": 0})
    assert "[事实·机械筛选]" in markdown
    assert "[判断]" in markdown
    assert "总振幅≤12%" in markdown
    assert "不奖励上涨幅度" in markdown
    assert "横盘事实名次" in markdown
    assert "申万二级[事实]" in markdown
    assert "电力" in markdown
    assert "仅展示，不参与筛选/PK" in markdown
    assert "不写关注池或计划层" in markdown


def test_save_artifacts_keeps_full_json(tmp_path):
    result = _result()
    md_path, json_path = renderer.save_artifacts(
        "# report", result, None, result["date"], out_root=str(tmp_path))
    assert md_path.read_text(encoding="utf-8") == "# report"
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["scan"]["candidates"][0]["code"] == "600001"
    assert payload["scan"]["candidates"][0]["industry"] == "电力"
    assert payload["scan"]["industry"]["basis"] == "current_membership_snapshot"
    assert payload["scan"]["industry"]["coverage"] == 1.0
    assert (tmp_path / f"{result['date']}.html").exists()


def test_save_artifacts_rejects_date_path_traversal(tmp_path):
    with pytest.raises(ValueError):
        renderer.save_artifacts(
            "# report", {"status": "source_failed"}, None,
            "../../escape", out_root=str(tmp_path),
        )


def test_push_body_respects_byte_budget():
    result = _result()
    result["pk_pool"] = [_candidate(str(600000 + index)) for index in range(12)]
    result["pk_pool_count"] = 12
    pushed = renderer.render_push(result, None)
    assert len(pushed.encode("utf-8")) <= constants.PUSH_BODY_MAX_BYTES
    assert "申万二级[事实]" in pushed
    assert "当前成分快照" in pushed
    assert "电力" in pushed


def test_source_failed_says_not_empty():
    markdown = renderer.render_source_failed({
        "date": "2026-08-10", "failed_sources": {"daily": "timeout"}})
    assert "不代表没有候选" in markdown
    assert "timeout" in markdown


def test_industry_failure_is_rendered_as_auxiliary_degradation():
    result = _result()
    result["industry"] = {
        "status": "source_failed", "candidate_rows": 1, "matched_rows": 0,
        "coverage": 0.0, "error": "industry timeout",
    }
    result["pk_pool"][0]["industry"] = "未分类"
    markdown = renderer.render_daily(result, None)
    assert "板块标签：source_failed" in markdown
    assert "未分类" in markdown
    assert "industry timeout" in markdown
    pushed = renderer.render_push(result, None)
    assert "板块标签：source_failed" in pushed
    assert "未分类" in pushed


def test_full_candidates_outside_pool_are_visible_before_appendix():
    result = _result()
    drug = dict(_candidate("603259"), name="药明康德", industry="医疗服务", horizontal_rank=322)
    result["candidates"].append(drug)
    result.update(candidate_count=2, pk_excluded_count=1)
    original = json.dumps(result, sort_keys=True)
    markdown = renderer.render_daily(result, None)
    assert markdown.index("药明康德") < markdown.index("## 附表")
    assert "医疗服务 · 1 只" in markdown
    page = renderer.render_html(result, None)
    assert 'data-search="药明康德 603259 医疗服务"' in page
    assert 'id="search"' in page
    assert "显示 2 / 2 只" in page
    assert json.dumps(result, sort_keys=True) == original
    pushed = renderer.render_push(result, None)
    assert "本文为摘要，未列出全量候选" in pushed
    assert "其余 1 只仍属合格候选" in pushed


def test_html_escapes_source_text_and_keeps_unknown_sector():
    result = _result()
    result["candidates"][0].update(name='<script>alert("x")</script>', industry="")
    page = renderer.render_html(result, None)
    assert '<script>alert("x")</script>' not in page
    assert "&lt;script&gt;" in page
    assert "未分类" in page


def test_failed_html_does_not_claim_empty_or_show_stale_candidates():
    result = _result()
    result.update(status="source_failed", failed_sources={"daily": "timeout"})
    page = renderer.render_html(result, None)
    assert "本次不代表没有候选" in page
    assert "timeout" in page
    assert 'id="search"' not in page
    assert '<tr data-search=' not in page
    assert "真实空候选" not in page


def test_sector_order_is_for_lookup_not_horizontal_rank():
    result = _result()
    result["candidates"] = [dict(_candidate("600009"), horizontal_rank=1),
                            dict(_candidate("600002"), horizontal_rank=500)]
    markdown = renderer.render_daily(result, None).split("## 附表")[0]
    assert markdown.index("600002") < markdown.index("600009")
