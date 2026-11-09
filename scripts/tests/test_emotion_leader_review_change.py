from __future__ import annotations

import json
from pathlib import Path

import yaml

from services.emotion_leader.review_change import build, render_html


def _calendar(end: str) -> list[dict[str, object]]:
    days = ["2026-07-13", "2026-07-14", "2026-07-15", "2026-07-16"]
    return [{"date": day, "is_open": 1} for day in days if day <= end]


def _core(code: str, name: str, day: str, pct: float, label: str) -> dict[str, object]:
    return {
        "code": code,
        "name": name,
        "metric_status": "ok",
        "metric_as_of": day,
        "current_state": "涨停" if label == "强正反馈" else "震荡",
        "today_pct_chg": pct,
        "feedback_type": label,
        "max_height": 4,
        "max_gain_pct": 45.0,
        "last_limit_up_date": day,
        "launch_date": "2026-07-13",
        "new_peak_today": label == "强正反馈",
        "archived": False,
        "manual_confirmed": False,
    }


def _report(day: str, rows: list[dict[str, object]], *, height: dict | None = None) -> dict[str, object]:
    return {
        "date": day,
        "status": "ok",
        "active": rows,
        "archived": [],
        "tracking_calendar": _calendar(day),
        "height_breakthrough": height or {
            "status": "none",
            "source_status": "complete",
            "as_of": day,
            "lookback_open_days": 20,
            "current_max_height": 4,
            "previous_max_height": 4,
        },
        "source_errors": [],
    }


def _promotion(day: str, prev: str, first: tuple[int, int], second: tuple[int, int]) -> dict[str, object]:
    return {
        "trade_date": day,
        "prev_date": prev,
        "first_to_second": {
            "base": first[0],
            "promoted": first[1],
            "rate": round(first[1] / first[0], 3),
        },
        "second_to_third": {
            "base": second[0],
            "promoted": second[1],
            "rate": round(second[1] / second[0], 3),
        },
    }


def _write_daily(
    root: Path,
    day: str,
    *,
    limit_up: int,
    limit_down: int,
    continuation: int,
    highest: int,
    broken_rate: float,
    promotion: dict[str, object],
    mid_count: int,
    board: dict[str, object] | None = None,
) -> None:
    target = root / day
    target.mkdir(parents=True)
    ladder = {1: [f"首板{i}" for i in range(limit_up - continuation)], 2: [f"二板{i}" for i in range(mid_count)]}
    payload = {
        "date": day,
        "raw_data": {
            "limit_up": {
                "count_ex_st": limit_up,
                "consecutive_board_count_ex_st": continuation,
                "highest_board_ex_st": highest,
                "broken_rate_pct_ex_st": broken_rate,
                "board_ladder_ex_st": ladder,
            },
            "limit_down": {"count_ex_st": limit_down},
            "style_factors": {"promotion": promotion},
        },
    }
    if board is not None:
        payload["raw_data"]["board_break_feedback"] = board
    (target / "post-market.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def test_builds_fixed_five_part_change_card_from_strict_t_minus_one(tmp_path):
    emotion_dir = tmp_path / "emotion"
    daily_dir = tmp_path / "daily"
    emotion_dir.mkdir()
    previous = _report(
        "2026-07-15",
        [
            _core("000001.SZ", "修复票", "2026-07-15", -5.0, "负反馈"),
            _core("000002.SZ", "转弱票", "2026-07-15", 6.0, "强正反馈"),
        ],
    )
    current = _report(
        "2026-07-16",
        [
            _core("000001.SZ", "修复票", "2026-07-16", 6.0, "强正反馈"),
            _core("000002.SZ", "转弱票", "2026-07-16", -4.0, "负反馈"),
        ],
        height={
            "status": "triggered",
            "source_status": "complete",
            "as_of": "2026-07-16",
            "lookback_open_days": 20,
            "current_max_height": 5,
            "previous_max_height": 4,
        },
    )
    (emotion_dir / "2026-07-15.json").write_text(
        json.dumps(previous, ensure_ascii=False), encoding="utf-8"
    )
    _write_daily(
        daily_dir,
        "2026-07-15",
        limit_up=10,
        limit_down=20,
        continuation=3,
        highest=4,
        broken_rate=40.0,
        promotion=_promotion("2026-07-15", "2026-07-14", (10, 2), (4, 1)),
        mid_count=2,
    )
    board = {
        "status": "ok",
        "outcome_date": "2026-07-16",
        "sample_count": 2,
        "relimit_count": 0,
        "limit_down_count": 0,
        "errors": [],
        "details": [
            {
                "code": "000003",
                "name": "弱转强票",
                "feedback_open_pct": -3.0,
                "feedback_close_pct": 5.0,
                "outcome": "上涨",
            },
            {
                "code": "000004",
                "name": "继续走弱票",
                "feedback_open_pct": -2.0,
                "feedback_close_pct": -1.0,
                "outcome": "下跌",
            },
        ],
    }
    _write_daily(
        daily_dir,
        "2026-07-16",
        limit_up=25,
        limit_down=5,
        continuation=6,
        highest=5,
        broken_rate=18.0,
        promotion=_promotion("2026-07-16", "2026-07-15", (12, 3), (4, 2)),
        mid_count=4,
        board=board,
    )

    result = build(
        current,
        "2026-07-16",
        emotion_dir=emotion_dir,
        daily_dir=daily_dir,
    )

    assert result["status"] == "complete"
    assert result["previous_date"] == "2026-07-15"
    assert result["stage"] == "冰点后首次修复"
    assert result["stop_signal"] == "增强"
    assert result["new_cycle_signal"] == "增强"
    assert [row["name"] for row in result["board"]["weak_to_strong"]] == ["弱转强票"]
    fragment, gap = render_html(result, "2026-07-16")
    assert gap is None
    assert 'data-emotion-change="v1"' in fragment
    assert "阶段结论" in fragment and "止跌信号" in fragment and "新周期确认" in fragment
    assert "代表个股" in fragment and "数据状态" in fragment
    assert "修复票" in fragment and "转弱票" in fragment and "弱转强票" in fragment

    current["height_breakthrough"] = {
        "status": "missing_data",
        "source_status": "partial",
        "reason": "高度窗口有缺口",
    }
    partial = build(
        current,
        "2026-07-16",
        emotion_dir=emotion_dir,
        daily_dir=daily_dir,
    )
    assert partial["status"] == "partial"
    assert partial["new_cycle_signal"] == "未计算"
    assert "高度窗口有缺口" in partial["height_fact"]
    partial_fragment, partial_gap = render_html(partial, "2026-07-16")
    assert 'data-source-status="partial"' in partial_fragment
    assert partial_gap is not None


def test_complete_height_without_numeric_window_degrades_to_uncomputed(tmp_path):
    emotion_dir = tmp_path / "emotion"
    daily_dir = tmp_path / "daily"
    emotion_dir.mkdir()
    previous = _report(
        "2026-07-15",
        [_core("000001.SZ", "甲", "2026-07-15", -2.0, "负反馈")],
    )
    current = _report(
        "2026-07-16",
        [_core("000001.SZ", "甲", "2026-07-16", 2.0, "弱正反馈")],
        height={"status": "triggered", "source_status": "complete"},
    )
    (emotion_dir / "2026-07-15.json").write_text(
        json.dumps(previous, ensure_ascii=False), encoding="utf-8"
    )
    _write_daily(
        daily_dir,
        "2026-07-15",
        limit_up=10,
        limit_down=10,
        continuation=3,
        highest=4,
        broken_rate=30.0,
        promotion=_promotion("2026-07-15", "2026-07-14", (10, 2), (4, 1)),
        mid_count=2,
    )
    _write_daily(
        daily_dir,
        "2026-07-16",
        limit_up=12,
        limit_down=8,
        continuation=4,
        highest=5,
        broken_rate=25.0,
        promotion=_promotion("2026-07-16", "2026-07-15", (10, 3), (4, 2)),
        mid_count=3,
        board={
            "status": "empty",
            "outcome_date": "2026-07-16",
            "sample_count": 0,
        },
    )

    result = build(current, "2026-07-16", emotion_dir=emotion_dir, daily_dir=daily_dir)

    assert result["status"] == "partial"
    assert result["new_cycle_signal"] == "未计算"
    assert "高度比较=未计算" in result["height_fact"]


def test_missing_strict_previous_report_is_data_insufficient(tmp_path):
    current = _report("2026-07-16", [_core("000001.SZ", "甲", "2026-07-16", 1.0, "弱正反馈")])
    result = build(
        current,
        "2026-07-16",
        emotion_dir=tmp_path / "emotion",
        daily_dir=tmp_path / "daily",
    )
    fragment, gap = render_html(result, "2026-07-16")
    assert result["stage"] == "数据不足"
    assert result["stop_signal"] == "未计算"
    assert result["new_cycle_signal"] == "未计算"
    assert 'data-emotion-change="missing-data"' in fragment
    assert "缺失不补0" in fragment
    assert gap is not None


def test_missing_sources_never_render_zero_as_a_computed_result(tmp_path):
    result = build(None, "2026-07-16", emotion_dir=tmp_path, daily_dir=tmp_path)
    for payload in (None, result):
        fragment, gap = render_html(payload, "2026-07-16")
        assert "旧核心修复未计算" in fragment
        assert "断板低开转收涨未计算" in fragment
        assert "正常计算后为0" not in fragment
        assert 'data-repair-count="unavailable"' in fragment
        assert gap is not None


def test_complete_empty_sources_keep_real_zero_even_if_other_sources_fail():
    fragment, _ = render_html({
        "date": "2026-07-16", "status": "source_failed",
        "transitions": {"status": "complete", "rows": []},
        "board": {"status": "empty", "rows": [], "sample_count": 0},
    }, "2026-07-16")
    assert "旧核心修复0只" in fragment
    assert "断板低开转收涨0只" in fragment
    assert "修复：正常计算后为0" in fragment
    assert "断板股盘中弱转强：正常计算后为0" in fragment


def test_partial_empty_categories_do_not_claim_complete_zero():
    fragment, _ = render_html({
        "date": "2026-07-16", "status": "partial",
        "transitions": {"status": "partial", "rows": [], "missing": ["缺失票"]},
        "board": {"status": "partial", "rows": [], "sample_count": 0},
    }, "2026-07-16")
    assert "正常计算后为0" not in fragment
    assert "旧核心修复未计算（部分覆盖）" in fragment
    assert "断板低开转收涨未计算（部分覆盖）" in fragment


def test_partial_observations_retain_verified_nonzero_counts():
    fragment, _ = render_html({
        "date": "2026-07-16", "status": "partial",
        "transitions": {"status": "partial", "rows": [
            {"code": "000001.SZ", "name": "已核验修复票", "transition": "repaired"},
        ]},
        "board": {"status": "partial", "rows": [
            {"code": "000002", "name": "已核验断板票", "open_pct": -1, "close_pct": 2},
        ], "sample_count": 1},
    }, "2026-07-16")
    assert "旧核心修复已核验1只（部分覆盖）" in fragment
    assert "断板低开转收涨已核验1只（部分覆盖）" in fragment
    assert "已核验修复票" in fragment and "已核验断板票" in fragment
    assert "正常计算后为0" not in fragment


def test_missing_core_report_preserves_independent_valid_board_feedback(tmp_path):
    _write_daily(
        tmp_path, "2026-07-16", limit_up=10, limit_down=2,
        continuation=3, highest=4, broken_rate=20, mid_count=2,
        promotion=_promotion("2026-07-16", "2026-07-15", (10, 2), (4, 1)),
        board={"status": "ok", "outcome_date": "2026-07-16", "sample_count": 1,
               "relimit_count": 0, "limit_down_count": 0, "details": [
                   {"code": "000002", "name": "独立有效票", "feedback_open_pct": -2,
                    "feedback_close_pct": 3, "outcome": "上涨"}]},
    )
    result = build(None, "2026-07-16", emotion_dir=tmp_path, daily_dir=tmp_path)
    fragment, gap = render_html(result, "2026-07-16")
    assert result["status"] == "source_failed"
    assert "旧核心修复未计算" in fragment
    assert "断板低开转收涨1只" in fragment
    assert "独立有效票" in fragment
    assert gap is not None


def test_failed_core_with_valid_market_and_board_passes_missing_contract(tmp_path, monkeypatch):
    from datetime import date
    import importlib.util
    import sys
    from services.emotion_leader import review_change

    previous = _report("2026-07-15", [])
    (tmp_path / "2026-07-15.json").write_text(json.dumps(previous))
    current = _report("2026-07-16", [])
    current["active"] = None  # Cannot compute transitions from a malformed core source.
    market = {"limit_up": 10, "limit_down": 2, "first": 7, "continuation": 3,
              "highest": 4, "broken_rate": 20,
              "promotion": _promotion("2026-07-16", "2026-07-15", (10, 2), (4, 1))}
    monkeypatch.setattr(review_change, "_load_market_snapshot", lambda *args: market)
    monkeypatch.setattr(review_change, "_load_board_feedback", lambda *args: {
        "status": "empty", "rows": [], "sample_count": 0, "errors": []})
    result = build(current, "2026-07-16", emotion_dir=tmp_path, daily_dir=tmp_path)
    assert result["transitions"]["status"] == "source_failed"
    assert "旧核心修复未计算" in result["facts"][-1]
    assert "断板低开转收涨0只" in result["facts"][-1]
    fragment, gap = render_html(result, "2026-07-16")
    assert 'data-source-status="source_failed"' in fragment
    assert 'data-repair-count="unavailable"' in fragment
    assert gap is not None

    path = Path(__file__).resolve().parents[2] / ".agents/skills/daily-review/references/html-report-template/assemble_report.py"
    spec = importlib.util.spec_from_file_location("emotion_change_contract_assembler", path)
    assembler = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, assembler)
    spec.loader.exec_module(assembler)
    parser = assembler._ReportParser()
    parser.feed('<section id="s3">' + fragment + '</section>')
    parser._validate_emotion_change_contract(date(2026, 7, 16))
