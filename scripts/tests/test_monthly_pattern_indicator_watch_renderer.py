from __future__ import annotations

import pytest

from services.monthly_pattern import indicator_watch_renderer as renderer


TARGET_DATE = "2026-06-30"
ACTIONABLE_PHRASES = (
    "建议买入",
    "可以买入",
    "立即买入",
    "建议卖出",
    "立即卖出",
    "加仓",
    "减仓",
    "止损价",
    "目标价",
)


def _candidate() -> dict:
    return {
        "stock_code": "600001",
        "stock_name": "测试股份",
        "stage": "daily_reactivated",
        "monthly_evidence": {
            "positive_month_streak": 6,
            "pullback_month": "2026-05",
            "preferred_pullback": True,
            "low": 9.8,
            "close": 10.2,
            "ma5": 10.0,
            "ma10": 9.5,
            "ma20": 9.0,
        },
        "daily_evidence": {
            "dynamic_monthly_ma5": {
                "status": "complete",
                "as_of_date": TARGET_DATE,
                "current_month": "2026-06",
                "months": [
                    "2026-02",
                    "2026-03",
                    "2026-04",
                    "2026-05",
                    "2026-06",
                ],
                "current_close": 10.7,
                "current_month_low": 9.9,
                "ma5": 10.4,
                "support_held": True,
                "current_month_low_below_target_asof_ma5": True,
                "distance_pct": 2.8846,
            },
            "daily_macd": {
                "dif": 0.12,
                "dea": 0.08,
                "above_zero": True,
                "bullish_on_zero": True,
                "golden_cross": False,
            },
            "weekly_macd": {
                "dif": 0.20,
                "dea": 0.15,
                "above_zero": True,
                "bullish_on_zero": True,
                "golden_cross": True,
            },
            "reentry_date": "2026-06-26",
            "current_above_zero": True,
            "volume": {
                "windows": [5, 13],
                "bullish_bar": True,
                "volume_above_all_prior_mas": True,
                "confirmed": True,
            },
        },
        "industry": "半导体",
        "mainline_match": True,
    }


def _summary(status: str) -> dict:
    return {
        "requested_date": TARGET_DATE,
        "target_date": TARGET_DATE,
        "seed_month": "2026-05",
        "status": status,
        "source_status": {
            "calendar": "success",
            "monthly_seed": "certified",
            "daily": "success" if status == "complete" else status,
        },
        "counts": {"monthly_seed_total": 1},
        "mainline_context": {
            "sectors": ["半导体"],
            "source_dates": ["2026-06-30"],
            "industry_status": "current_snapshot",
            "industry_semantics": "仅用于最近开放日当前快照",
        },
        "candidates": [_candidate()] if status != "blocked" else [],
        "waiting_monthly_reclaim": [],
        "indeterminate_current_month_ma5": [],
        "data_issues": [],
        "unresolved_rules": [
            {
                "rule": "低位",
                "reason": "原文没有给出可机械复现阈值",
            }
        ],
        "error": "关键 certified 月缺失" if status == "blocked" else None,
    }


def _assert_no_actionable_advice(markdown: str) -> None:
    assert "不构成具体买卖建议" in markdown
    for phrase in ACTIONABLE_PHRASES:
        assert phrase not in markdown


def test_complete_renderer_layers_facts_judgments_and_teacher_view() -> None:
    markdown = renderer.render_monitor(_summary("complete"))

    assert "运行状态：[事实] complete" in markdown
    assert "月线种子：[事实]" in markdown
    assert "观察阶段：[判断]" in markdown
    assert "主线代理：[判断] 命中当前稳定前排代理" in markdown
    assert "当前动态 5 月线硬门：[事实·目标日 as-of]" in markdown
    assert "收盘不低于 MA5=是" in markdown
    assert "距离=2.88%" in markdown
    assert "仅作当前资格门，不新增完成月种子" in markdown
    assert "[老师观点·待核对] 低位" in markdown
    assert "[老师观点] 前一完成月之前连续至少 5 根阳月" in markdown
    assert "本次无规则命中" not in markdown
    _assert_no_actionable_advice(markdown)


def test_renderer_separates_seed_waiting_for_dynamic_monthly_ma5() -> None:
    summary = _summary("complete")
    waiting = _candidate()
    dynamic = waiting["daily_evidence"]["dynamic_monthly_ma5"]
    dynamic.update(
        {
            "current_close": 9.0,
            "ma5": 10.0,
            "support_held": False,
            "distance_pct": -10.0,
        }
    )
    summary["candidates"] = []
    summary["waiting_monthly_reclaim"] = [waiting]

    markdown = renderer.render_monitor(summary)

    assert "本次没有通过当前动态 5 月线硬门的观察项" in markdown
    assert "## 历史种子 · 等待重新站回动态 5 月线" in markdown
    assert "不计入上方当前观察清单" in markdown
    assert "收盘不低于 MA5=否" in markdown
    assert "距离=-10.00%" in markdown
    assert "等待名单是无状态的同目标月日频快照" in markdown
    _assert_no_actionable_advice(markdown)


def test_renderer_fail_closes_indeterminate_dynamic_monthly_ma5() -> None:
    summary = _summary("partial")
    unknown = _candidate()
    unknown["stage"] = "insufficient_history"
    unknown["daily_evidence"]["dynamic_monthly_ma5"].update(
        {
            "status": "insufficient_history",
            "current_close": None,
            "ma5": None,
            "support_held": None,
            "distance_pct": None,
        }
    )
    summary["counts"]["indeterminate_current_month_ma5"] = 1
    summary["candidates"] = []
    summary["indeterminate_current_month_ma5"] = [unknown]

    markdown = renderer.render_monitor(summary)

    assert "## 当前动态 5 月线无法判定" in markdown
    assert "不进入门内观察清单，也不解释为已经失守" in markdown
    assert "当前动态 5 月线关键事实不足，不进入门内清单" in markdown
    assert "动态 5 月线无法判定 1 只" in markdown
    _assert_no_actionable_advice(markdown)


def test_volume_above_mas_is_not_conflated_with_bullish_confirmation() -> None:
    summary = _summary("complete")
    volume = summary["candidates"][0]["daily_evidence"]["volume"]
    volume["bullish_bar"] = False
    volume["volume_above_all_prior_mas"] = True
    volume["confirmed"] = False

    markdown = renderer.render_monitor(summary)

    assert "同时高于均量线=是" in markdown
    assert "阳线量能确认=否" in markdown


def test_partial_renderer_does_not_treat_failed_stock_as_rule_miss() -> None:
    summary = _summary("partial")
    summary["data_issues"] = [
        {
            "stock_code": "600002",
            "stock_name": "数据缺失票",
            "stage": "blocked",
            "source": {
                "status": "qfq_failed",
                "error": "复权因子错位",
            },
        }
    ]

    markdown = renderer.render_monitor(summary)

    assert "运行状态：[事实] partial" in markdown
    assert "不能把缺失票视为规则未命中" in markdown
    assert "## 单票数据异常" in markdown
    assert "[事实] qfq_failed；复权因子错位" in markdown
    assert "本次无规则命中" not in markdown
    _assert_no_actionable_advice(markdown)


def test_blocked_renderer_never_masquerades_as_true_empty_result() -> None:
    markdown = renderer.render_monitor(_summary("blocked"))

    assert "运行状态：[事实] blocked" in markdown
    assert "## 阻断" in markdown
    assert "[事实] 关键 certified 月缺失" in markdown
    assert "[判断] 该状态不等于真实空候选" in markdown
    assert "[事实] 当前不能确认真实空候选" in markdown
    assert "certified 月线种子与目标日数据均完整，本次无规则命中" not in markdown
    _assert_no_actionable_advice(markdown)


def test_partial_renderer_explains_monthly_quality_buckets() -> None:
    summary = _summary("partial")
    summary["counts"] = {
        "blocked": 3,
        "blocked_price_shape": 2,
        "blocked_month_gap": 1,
    }

    markdown = renderer.render_monitor(summary)

    assert "月内复权形态不可认证 2 只" in markdown
    assert "月份缺口 1 只" in markdown


@pytest.mark.parametrize("status", ["complete", "partial", "blocked"])
def test_renderer_has_no_actionable_instruction_for_any_status(status: str) -> None:
    _assert_no_actionable_advice(renderer.render_monitor(_summary(status)))
