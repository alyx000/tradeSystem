from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from analyzers.board_break_feedback import collect_board_break_feedback
from generators.report import _render_board_break_feedback


CONNECTED_DATE = "2026-08-06"
BREAK_DATE = "2026-08-07"
OUTCOME_DATE = "2026-08-10"


@dataclass
class _Result:
    data: object = None
    source: str = "mock"
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.error is None


class _Registry:
    def __init__(self, quotes: dict[str, list[dict] | None]):
        self.quotes = quotes

    def call(self, method: str, *args):
        if method == "is_trade_day":
            return _Result(data=args[0] in {CONNECTED_DATE, BREAK_DATE, OUTCOME_DATE})
        if method == "get_market_daily_quotes":
            trade_date = args[0]
            data = self.quotes.get(trade_date)
            if data is None:
                return _Result(error=f"missing market quotes {trade_date}")
            return _Result(data=data, source="mock:daily")
        return _Result(error=f"unsupported {method}")


def _write_post(daily_dir: Path, trade_date: str, stocks: list[dict]) -> None:
    path = daily_dir / trade_date / "post-market.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {
                "date": trade_date,
                "raw_data": {
                    "limit_up": {
                        "count": len(stocks),
                        "stocks": stocks,
                        "_source": "mock:limit_list",
                    }
                },
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )


def _today_raw(*, limit_up: list[dict] | None = None, limit_down: list[dict] | None = None) -> dict:
    return {
        "limit_up": {"count": len(limit_up or []), "stocks": limit_up or [], "_source": "mock:limit_list"},
        "limit_down": {"count": len(limit_down or []), "stocks": limit_down or [], "_source": "mock:limit_list"},
    }


def _quote(
    code: str,
    trade_date: str,
    open_: float,
    close: float,
    change: float,
    *,
    pre_close: float,
) -> dict:
    return {
        "code": code,
        "trade_date": trade_date.replace("-", ""),
        "open": open_,
        "close": close,
        "pre_close": pre_close,
        # 与 TushareProvider.get_market_daily_quotes 的真实返回契约一致。
        "pct_chg": change,
    }


def test_collects_next_day_feedback_with_exact_timeline_and_excludes_st(tmp_path):
    daily_dir = tmp_path / "daily"
    _write_post(
        daily_dir,
        CONNECTED_DATE,
        [
            {"code": "000001.SZ", "name": "断板A", "limit_times": 3},
            {"code": "000002.SZ", "name": "续板B", "limit_times": 2},
            {"code": "000003.SZ", "name": "*ST样本", "limit_times": 5},
            {"code": "000004.SZ", "name": "首板D", "limit_times": 1},
        ],
    )
    _write_post(
        daily_dir,
        BREAK_DATE,
        [{"code": "000002.SZ", "name": "续板B", "limit_times": 3}],
    )
    registry = _Registry({
        BREAK_DATE: [_quote("000001.SZ", BREAK_DATE, 9.0, 10.0, -2.0, pre_close=10.2)],
        # pre_close=9 模拟除权参考价；若错误地用 T-1 原始 close=10，开盘反馈会变成 -5.5%。
        OUTCOME_DATE: [_quote("000001.SZ", OUTCOME_DATE, 9.45, 9.9, 10.0, pre_close=9.0)],
    })

    result = collect_board_break_feedback(
        registry,
        OUTCOME_DATE,
        _today_raw(limit_up=[{"code": "000001.SZ", "name": "断板A"}]),
        daily_dir=daily_dir,
    )

    assert result["status"] == "ok"
    assert result["source_connected_date"] == CONNECTED_DATE
    assert result["break_date"] == BREAK_DATE
    assert result["connected_count"] == 2
    assert result["break_count"] == result["sample_count"] == 1
    assert result["break_coverage_pct"] == result["feedback_coverage_pct"] == 100.0
    assert result["open_up_rate"] == result["close_up_rate"] == 1.0
    assert result["open_median_pct"] == 5.0
    assert result["close_median_pct"] == 10.0
    assert result["relimit_count"] == 1
    assert result["limit_down_count"] == 0
    assert result["height_bucket_definition"] == "低位=2板，中位=3-4板，高位=5板及以上"
    assert [bucket["key"] for bucket in result["height_buckets"]] == ["low", "mid", "high"]
    assert result["height_buckets"][1]["sample_count"] == 1
    assert result["height_buckets"][1]["close_median_pct"] == 10.0
    assert result["details"] == [{
        "code": "000001",
        "name": "断板A",
        "previous_height": 3,
        "height_bucket": "mid",
        "height_bucket_label": "中位",
        "break_change_pct": -2.0,
        "feedback_open_pct": 5.0,
        "feedback_close_pct": 10.0,
        "outcome": "再涨停",
    }]


def test_partial_does_not_turn_missing_feedback_or_limit_status_into_zero(tmp_path):
    daily_dir = tmp_path / "daily"
    connected = [
        {"code": "000001.SZ", "name": "断板A", "limit_times": 3},
        {"code": "000005.SZ", "name": "断板E", "limit_times": 2},
    ]
    _write_post(daily_dir, CONNECTED_DATE, connected)
    _write_post(daily_dir, BREAK_DATE, [])
    registry = _Registry({
        BREAK_DATE: [
            _quote("000001.SZ", BREAK_DATE, 9.0, 10.0, -2.0, pre_close=10.2),
            _quote("000005.SZ", BREAK_DATE, 19.0, 20.0, -1.0, pre_close=20.2),
        ],
        OUTCOME_DATE: [
            _quote("000001.SZ", OUTCOME_DATE, 10.2, 10.5, 5.0, pre_close=10.0),
            # 000005 的反馈日行情故意缺失
        ],
    })
    current = {
        "limit_up": {"error": "limit source down"},
        "limit_down": {"error": "limit source down"},
    }

    result = collect_board_break_feedback(
        registry, OUTCOME_DATE, current, daily_dir=daily_dir
    )

    assert result["status"] == "partial"
    assert result["break_count"] == 2
    assert result["sample_count"] == 1
    assert result["coverage_pct"] == 50.0
    assert result["relimit_count"] is None
    assert result["limit_down_count"] is None
    assert result["details"][0]["outcome"] == "上涨（涨停状态未核验）"
    assert len(result["missing_outcome_quotes"]) == 1
    assert any("反馈日行情" in error for error in result["errors"])
    low, mid, high = result["height_buckets"]
    assert low["break_count"] == 1 and low["sample_count"] == 0
    assert low["feedback_coverage_pct"] == 0.0
    assert low["open_mean_pct"] is None and low["relimit_count"] is None
    assert mid["break_count"] == mid["sample_count"] == 1
    assert high["break_candidate_count"] == 0


def test_height_buckets_use_two_three_to_four_and_five_plus_boundaries(tmp_path):
    daily_dir = tmp_path / "daily"
    connected = [
        {"code": "000002.SZ", "name": "二板", "limit_times": 2},
        {"code": "000003.SZ", "name": "三板", "limit_times": 3},
        {"code": "000004.SZ", "name": "四板", "limit_times": 4},
        {"code": "000005.SZ", "name": "五板", "limit_times": 5},
        {"code": "000008.SZ", "name": "八板", "limit_times": 8},
    ]
    _write_post(daily_dir, CONNECTED_DATE, connected)
    _write_post(daily_dir, BREAK_DATE, [])
    break_quotes = [
        _quote(row["code"], BREAK_DATE, 10.0, 10.0, -1.0, pre_close=10.1)
        for row in connected
    ]
    outcome_changes = {
        "000002.SZ": (1.0, 2.0),
        "000003.SZ": (-1.0, -2.0),
        "000004.SZ": (3.0, 4.0),
        "000005.SZ": (-3.0, -4.0),
        "000008.SZ": (5.0, 6.0),
    }
    outcome_quotes = [
        _quote(
            row["code"],
            OUTCOME_DATE,
            10.0 * (1 + outcome_changes[row["code"]][0] / 100),
            10.0 * (1 + outcome_changes[row["code"]][1] / 100),
            outcome_changes[row["code"]][1],
            pre_close=10.0,
        )
        for row in connected
    ]
    registry = _Registry({BREAK_DATE: break_quotes, OUTCOME_DATE: outcome_quotes})

    result = collect_board_break_feedback(
        registry,
        OUTCOME_DATE,
        _today_raw(
            limit_up=[{"code": "000008.SZ", "name": "八板"}],
            limit_down=[{"code": "000005.SZ", "name": "五板"}],
        ),
        daily_dir=daily_dir,
    )

    assert result["status"] == "ok"
    low, mid, high = result["height_buckets"]
    assert (low["height_range"], low["sample_count"], low["close_mean_pct"]) == (
        "2板", 1, 2.0
    )
    assert (mid["height_range"], mid["sample_count"], mid["close_mean_pct"]) == (
        "3-4板", 2, 1.0
    )
    assert (high["height_range"], high["sample_count"], high["close_mean_pct"]) == (
        "5板及以上", 2, 1.0
    )
    assert high["relimit_count"] == high["limit_down_count"] == 1
    assert {row["height_bucket_label"] for row in result["details"]} == {"低位", "中位", "高位"}


def test_break_day_coverage_uses_all_candidates_as_denominator(tmp_path):
    daily_dir = tmp_path / "daily"
    _write_post(daily_dir, CONNECTED_DATE, [
        {"code": "000001.SZ", "name": "断板A", "limit_times": 3},
        {"code": "000005.SZ", "name": "待核验E", "limit_times": 2},
    ])
    _write_post(daily_dir, BREAK_DATE, [])
    registry = _Registry({
        BREAK_DATE: [
            _quote("000001.SZ", BREAK_DATE, 9.0, 10.0, -2.0, pre_close=10.2),
        ],
        OUTCOME_DATE: [
            _quote("000001.SZ", OUTCOME_DATE, 10.2, 10.5, 5.0, pre_close=10.0),
        ],
    })

    result = collect_board_break_feedback(
        registry, OUTCOME_DATE, _today_raw(), daily_dir=daily_dir
    )

    assert result["status"] == "partial"
    assert result["break_candidate_count"] == 2
    assert result["break_count"] == 1
    assert result["break_coverage_pct"] == 50.0
    assert result["feedback_coverage_pct"] == 100.0
    assert len(result["missing_break_quotes"]) == 1


def test_missing_history_is_source_failed_not_empty(tmp_path):
    result = collect_board_break_feedback(
        _Registry({}),
        OUTCOME_DATE,
        _today_raw(),
        daily_dir=tmp_path / "daily",
    )

    assert result["status"] == "source_failed"
    assert "sample_count" not in result
    assert any("缺少" in error for error in result["errors"])


def test_all_dirty_connected_rows_are_source_failed_not_verified_empty(tmp_path):
    daily_dir = tmp_path / "daily"
    _write_post(
        daily_dir,
        CONNECTED_DATE,
        [{"code": "000001.SZ", "name": "", "limit_times": 3}],
    )
    _write_post(daily_dir, BREAK_DATE, [])

    result = collect_board_break_feedback(
        _Registry({}), OUTCOME_DATE, _today_raw(), daily_dir=daily_dir
    )

    assert result["status"] == "source_failed"
    assert result["dirty_source_count"] == 1
    assert any("非ST身份" in error for error in result["errors"])


def test_dirty_break_day_limit_row_fails_closed_instead_of_proving_absence(tmp_path):
    daily_dir = tmp_path / "daily"
    _write_post(
        daily_dir,
        CONNECTED_DATE,
        [{"code": "000001.SZ", "name": "连板A", "limit_times": 3}],
    )
    # 这条脏记录可能实际就是 000001 的续板；不能因代码解析失败而把它判成断板。
    _write_post(daily_dir, BREAK_DATE, [{"code": None, "name": "连板A", "limit_times": 4}])

    result = collect_board_break_feedback(
        _Registry({}), OUTCOME_DATE, _today_raw(), daily_dir=daily_dir
    )

    assert result["status"] == "source_failed"
    assert result["details"] == []
    assert result["dirty_break_limit_count"] == 1
    assert any("无法可靠判定断板" in error for error in result["errors"])


def test_no_breaks_is_verified_empty(tmp_path):
    daily_dir = tmp_path / "daily"
    rows = [{"code": "000001.SZ", "name": "续板A", "limit_times": 2}]
    _write_post(daily_dir, CONNECTED_DATE, rows)
    _write_post(daily_dir, BREAK_DATE, rows)

    result = collect_board_break_feedback(
        _Registry({}), OUTCOME_DATE, _today_raw(), daily_dir=daily_dir
    )

    assert result["status"] == "ok"
    assert result["empty_reason"] == "no_board_breaks"
    assert result["sample_count"] == 0


def test_report_renders_metrics_and_source_failure_without_fake_zero():
    ok = {
        "board_break_feedback": {
            "status": "ok",
            "source_connected_date": CONNECTED_DATE,
            "break_date": BREAK_DATE,
            "outcome_date": OUTCOME_DATE,
            "break_count": 1,
            "sample_count": 1,
            "break_candidate_count": 1,
            "break_coverage_pct": 100.0,
            "feedback_coverage_pct": 100.0,
            "open_up_count": 1,
            "open_up_rate": 1.0,
            "open_mean_pct": 2.0,
            "open_median_pct": 2.0,
            "close_up_count": 1,
            "close_up_rate": 1.0,
            "close_mean_pct": 5.0,
            "close_median_pct": 5.0,
            "relimit_count": 0,
            "relimit_rate": 0.0,
            "limit_down_count": 0,
            "limit_down_rate": 0.0,
            "height_buckets": [
                {
                    "key": "low", "label": "低位", "height_range": "2板",
                    "break_candidate_count": 0, "break_count": 0, "sample_count": 0,
                    "break_coverage_pct": None, "feedback_coverage_pct": None,
                    "open_up_count": 0, "open_up_rate": None,
                    "open_mean_pct": None, "open_median_pct": None,
                    "close_up_count": 0, "close_up_rate": None,
                    "close_mean_pct": None, "close_median_pct": None,
                    "relimit_count": None, "relimit_rate": None,
                    "limit_down_count": None, "limit_down_rate": None,
                },
                {
                    "key": "mid", "label": "中位", "height_range": "3-4板",
                    "break_candidate_count": 1, "break_count": 1, "sample_count": 1,
                    "break_coverage_pct": 100.0, "feedback_coverage_pct": 100.0,
                    "open_up_count": 1, "open_up_rate": 1.0,
                    "open_mean_pct": 2.0, "open_median_pct": 2.0,
                    "close_up_count": 1, "close_up_rate": 1.0,
                    "close_mean_pct": 5.0, "close_median_pct": 5.0,
                    "relimit_count": 0, "relimit_rate": 0.0,
                    "limit_down_count": 0, "limit_down_rate": 0.0,
                },
                {
                    "key": "high", "label": "高位", "height_range": "5板及以上",
                    "break_candidate_count": 0, "break_count": 0, "sample_count": 0,
                    "break_coverage_pct": None, "feedback_coverage_pct": None,
                    "open_up_count": 0, "open_up_rate": None,
                    "open_mean_pct": None, "open_median_pct": None,
                    "close_up_count": 0, "close_up_rate": None,
                    "close_mean_pct": None, "close_median_pct": None,
                    "relimit_count": None, "relimit_rate": None,
                    "limit_down_count": None, "limit_down_rate": None,
                },
            ],
            "details": [{
                "code": "000001", "name": "样本A", "previous_height": 3,
                "height_bucket": "mid", "height_bucket_label": "中位",
                "break_change_pct": -2.0, "feedback_open_pct": 2.0,
                "feedback_close_pct": 5.0, "outcome": "上涨",
            }],
        }
    }
    lines: list[str] = []
    assert _render_board_break_feedback(lines, ok, 5) == 6
    text = "\n".join(lines)
    assert "连板断板次日反馈 [事实]" in text
    assert "断板候选核验: **1/1只**" in text
    assert "反馈行情: **1/1只**" in text
    assert "低位=2板、中位=3-4板、高位=5板及以上" in text
    assert "按断板前连板高度分层" in text
    assert "| 中位（3-4板） | 1/1（100.0%） | 1/1（100.0%） |" in text
    assert "| 样本A(000001) | 中位 | 3板 |" in text
    assert "样本A(000001)" in text

    failed_lines: list[str] = []
    failed = {
        "board_break_feedback": {
            "status": "source_failed",
            "source_connected_date": CONNECTED_DATE,
            "break_date": BREAK_DATE,
            "outcome_date": OUTCOME_DATE,
            "errors": ["缺少历史归档"],
            "details": [],
        }
    }
    _render_board_break_feedback(failed_lines, failed, 5)
    failed_text = "\n".join(failed_lines)
    assert "本项未计算" in failed_text
    assert "样本为 0" in failed_text
    assert "有效样本" not in failed_text
