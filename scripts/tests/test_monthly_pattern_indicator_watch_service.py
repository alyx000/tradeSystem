from __future__ import annotations

import sqlite3
from datetime import datetime
from types import SimpleNamespace

import pytest

from services.monthly_pattern import indicator_watch_service as watch_service


TARGET_DATE = "2026-06-30"
SEED_MONTH_END = "2026-05-29"


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


def _seed(code: str, name: str | None = None) -> dict:
    return {
        "stock_code": code,
        "stock_name": name or f"测试{code}",
        "monthly_status": "matched",
        "monthly_evidence": {
            "seed_month_end": SEED_MONTH_END,
            "preferred_pullback": True,
            "positive_month_streak": 5,
            "close": 10.2,
            "ma5": 10.0,
        },
    }


def _patch_seed_stage(
    monkeypatch: pytest.MonkeyPatch,
    seeds: list[dict],
) -> None:
    monkeypatch.setattr(
        watch_service,
        "resolve_target_date",
        lambda _registry, _requested: (TARGET_DATE, TARGET_DATE),
    )
    monkeypatch.setattr(
        watch_service,
        "_certified_month_ends",
        lambda _conn, _registry, _target, *, months: [SEED_MONTH_END],
    )
    monkeypatch.setattr(
        watch_service.repository,
        "load_effective_month_bars",
        lambda _conn, _month_ends: [{"certified": True}],
    )
    monkeypatch.setattr(
        watch_service.repository,
        "load_effective_no_trade_facts",
        lambda _conn, _month_ends: [],
    )
    monkeypatch.setattr(
        watch_service,
        "_certified_universe_count",
        lambda _conn, _month_end: len(seeds),
    )
    monkeypatch.setattr(
        watch_service,
        "_latest_universe_codes",
        lambda _registry, _month_end, *, expected_count: {
            seed["stock_code"] for seed in seeds
        },
    )

    def fake_monthly_seeds(
        _rows,
        *,
        max_seeds,
        expected_month_end=None,
        latest_universe_codes=None,
        no_trade_facts=None,
    ):
        return (
            seeds[:max_seeds] if max_seeds is not None else list(seeds),
            {
                "matched": len(seeds),
                "monthly_seed_total": len(seeds),
                "monthly_seed_scanned": (
                    min(len(seeds), max_seeds)
                    if max_seeds is not None
                    else len(seeds)
                ),
                "monthly_seed_truncated": (
                    max(0, len(seeds) - max_seeds)
                    if max_seeds is not None
                    else 0
                ),
            },
        )

    monkeypatch.setattr(
        watch_service,
        "_monthly_seeds",
        fake_monthly_seeds,
    )
    monkeypatch.setattr(
        watch_service,
        "_stock_identity_context",
        lambda _registry, _target, input_seeds: (
            set(),
            {
                seed["stock_code"]: seed.get("stock_name") or ""
                for seed in input_seeds
                if seed.get("stock_name")
            },
            {
                "st_status": "success",
                "st_source": "fixture:stock_st",
                "name_status": "success",
                "name_source": "fixture:stock_basic",
                "name_error": None,
                "semantics": "fixture",
            },
        ),
    )


@pytest.mark.parametrize("not_matched", [0, 2])
def test_run_monitor_blocks_before_downstream_sources_when_primary_counts_drift(
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    not_matched: int,
) -> None:
    _patch_seed_stage(monkeypatch, [_seed("600001")])
    monkeypatch.setattr(
        watch_service,
        "_monthly_seeds",
        lambda *_args, **_kwargs: (
            [],
            {
                "not_matched": not_matched,
                "monthly_seed_total": 0,
                "monthly_seed_scanned": 0,
                "monthly_seed_truncated": 0,
            },
        ),
    )
    downstream_calls = []
    monkeypatch.setattr(
        watch_service,
        "_mainline_context",
        lambda *_args, **_kwargs: downstream_calls.append(1),
    )

    summary = watch_service.run_monitor(conn, object(), TARGET_DATE)

    assert summary["status"] == "blocked"
    assert summary["source_status"] == {"critical": "blocked"}
    assert "A股月线主分类不守恒" in summary["error"]
    assert downstream_calls == []


def test_monthly_classification_conservation_includes_no_trade_and_seed_identity() -> None:
    seed = _seed("600001")
    counts = {
        "matched": 1,
        "not_matched": 1,
        "blocked": 1,
        "insufficient_history": 1,
        "evidenced_not_applicable": 1,
        "monthly_seed_total": 1,
        "monthly_seed_scanned": 1,
        "monthly_seed_truncated": 0,
    }

    watch_service._assert_monthly_classification_conservation(
        counts,
        [seed],
        eligible_a_share_universe=5,
    )
    with pytest.raises(
        watch_service.IndicatorWatchSourceError,
        match="A股月线主分类不守恒",
    ):
        watch_service._assert_monthly_classification_conservation(
            {**counts, "monthly_seed_total": 2},
            [seed],
            eligible_a_share_universe=5,
        )


def _patch_missing_mainline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        watch_service,
        "_mainline_context",
        lambda *_args, **_kwargs: (
            {},
            [],
            {
                "status": "missing",
                "sectors": [],
                "source_dates": [],
                "industry_status": "source_failed",
                "industry_semantics": "unavailable",
            },
        ),
    )


def _successful_daily_result() -> dict:
    return {
        "stage": "monthly_seeded",
        "evidence": {
            "dynamic_monthly_ma5": {
                "status": "complete",
                "support_held": True,
            },
            "daily_macd": {"above_zero": False},
            "weekly_macd": {"above_zero": False},
        },
    }


class _CalendarRegistry:
    def call(self, method: str, *_args):
        assert method == "get_trade_calendar"
        return SimpleNamespace(
            success=True,
            data=[
                {"cal_date": "20260720", "is_open": 1},
                {"cal_date": "20260721", "is_open": 1},
                {"cal_date": "20260722", "is_open": 1},
            ],
            source="fixture:calendar",
            error=None,
        )


def test_default_target_uses_previous_open_day_before_close_cutoff() -> None:
    target, latest_closed = watch_service.resolve_target_date(
        _CalendarRegistry(),
        None,
        now=datetime(2026, 7, 21, 10, 0),
    )

    assert target == "2026-07-20"
    assert latest_closed == "2026-07-20"


def test_default_target_uses_today_after_close_cutoff() -> None:
    target, latest_closed = watch_service.resolve_target_date(
        _CalendarRegistry(),
        None,
        now=datetime(2026, 7, 21, 16, 0),
    )

    assert target == "2026-07-21"
    assert latest_closed == "2026-07-21"


def test_explicit_today_is_rejected_before_close_cutoff() -> None:
    with pytest.raises(ValueError, match="15:30"):
        watch_service.resolve_target_date(
            _CalendarRegistry(),
            "2026-07-21",
            now=datetime(2026, 7, 21, 10, 0),
        )


def test_latest_universe_must_match_certified_manifest_count() -> None:
    class Registry:
        def call(self, method: str, *_args):
            assert method == "get_stock_universe_as_of"
            return SimpleNamespace(
                success=True,
                data=[
                    {"ts_code": f"{index:06d}.SZ"}
                    for index in range(1, 4501)
                ],
                source="fixture:stock_basic",
                error=None,
            )

    with pytest.raises(
        watch_service.IndicatorWatchSourceError,
        match="certified 分母不一致",
    ):
        watch_service._latest_universe_codes(
            Registry(),
            SEED_MONTH_END,
            expected_count=5613,
        )


def test_missing_certified_month_returns_blocked(
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        watch_service,
        "resolve_target_date",
        lambda _registry, _requested: (TARGET_DATE, TARGET_DATE),
    )
    monkeypatch.setattr(
        watch_service.service,
        "_calendar_month_ends",
        lambda _registry, _cutoff, _months: [SEED_MONTH_END],
    )
    monkeypatch.setattr(
        watch_service.repository,
        "existing_month_ends",
        lambda _conn, _month_ends: set(),
    )

    summary = watch_service.run_monitor(
        conn,
        object(),
        TARGET_DATE,
        months=35,
    )

    assert summary["status"] == "blocked"
    assert summary["source_status"] == {"critical": "blocked"}
    assert summary["candidates"] == []
    assert "缺少有效 certified 收据" in summary["error"]
    assert all(value is False for value in summary["write_boundary"].values())


def test_only_monthly_seeds_fetch_daily_data(
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        watch_service,
        "resolve_target_date",
        lambda _registry, _requested: (TARGET_DATE, TARGET_DATE),
    )
    monkeypatch.setattr(
        watch_service,
        "_certified_month_ends",
        lambda _conn, _registry, _target, *, months: [SEED_MONTH_END],
    )
    monkeypatch.setattr(
        watch_service.repository,
        "load_effective_month_bars",
        lambda _conn, _month_ends: [{"certified": True}],
    )
    monkeypatch.setattr(
        watch_service.repository,
        "load_effective_no_trade_facts",
        lambda _conn, _month_ends: [],
    )
    monkeypatch.setattr(
        watch_service,
        "_certified_universe_count",
        lambda _conn, _month_end: 2,
    )
    monkeypatch.setattr(
        watch_service,
        "_latest_universe_codes",
        lambda _registry, _month_end, *, expected_count: {"600001", "600002"},
    )
    monkeypatch.setattr(
        watch_service,
        "_stock_series",
        lambda _rows: (
            {
                "600001": [
                    SimpleNamespace(seed=True, end_date=SEED_MONTH_END)
                ],
                "600002": [
                    SimpleNamespace(seed=False, end_date=SEED_MONTH_END)
                ],
            },
            {
                "600001": "月线种子",
                "600002": "非月线种子",
            },
        ),
    )
    monkeypatch.setattr(
        watch_service.indicator_watch,
        "detect_monthly_seed",
        lambda bars: {
            "matched": bool(bars[0].seed),
            "status": "matched" if bars[0].seed else "not_matched",
            "evidence": {
                "seed_month_end": SEED_MONTH_END,
                "preferred_pullback": True,
                "positive_month_streak": 5,
                "close": 10.2,
                "ma5": 10.0,
            },
        },
    )
    _patch_missing_mainline(monkeypatch)
    monkeypatch.setattr(
        watch_service,
        "_stock_identity_context",
        lambda _registry, _target, _seeds: (
            set(),
            {"600001": "月线种子"},
            {
                "st_status": "success",
                "st_source": "fixture:stock_st",
                "name_status": "success",
                "name_source": "fixture:stock_basic",
                "name_error": None,
                "semantics": "fixture",
            },
        ),
    )
    fetched_codes: list[str] = []

    def fake_fetch(_registry, code, _start, _target):
        fetched_codes.append(code)
        return ([{"trade_date": TARGET_DATE}], {"status": "success"})

    monkeypatch.setattr(watch_service, "_fetch_adjusted_daily", fake_fetch)
    monkeypatch.setattr(
        watch_service.indicator_watch,
        "evaluate_daily_monitor",
        lambda *_args, **_kwargs: _successful_daily_result(),
    )

    summary = watch_service.run_monitor(
        conn,
        object(),
        TARGET_DATE,
        months=35,
    )

    assert fetched_codes == ["600001"]
    assert [item["stock_code"] for item in summary["candidates"]] == ["600001"]
    assert summary["counts"]["monthly_seed_total"] == 1
    assert summary["status"] == "complete"


def test_current_dynamic_ma5_failure_moves_seed_out_of_current_candidates(
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_seed_stage(
        monkeypatch,
        [_seed("600001", "守住票"), _seed("600002", "失守票")],
    )
    _patch_missing_mainline(monkeypatch)
    monkeypatch.setattr(
        watch_service,
        "_fetch_adjusted_daily",
        lambda *_args, **_kwargs: (
            [{"trade_date": TARGET_DATE}],
            {"status": "success"},
        ),
    )
    results = iter(
        [
            _successful_daily_result(),
            {
                "stage": "monthly_seeded",
                "evidence": {
                    "reason": "current_close_below_dynamic_month_ma5",
                    "dynamic_monthly_ma5": {
                        "status": "complete",
                        "support_held": False,
                    },
                },
            },
        ]
    )
    monkeypatch.setattr(
        watch_service.indicator_watch,
        "evaluate_daily_monitor",
        lambda *_args, **_kwargs: next(results),
    )

    summary = watch_service.run_monitor(
        conn,
        object(),
        TARGET_DATE,
        months=35,
    )

    assert summary["status"] == "complete"
    assert [item["stock_code"] for item in summary["candidates"]] == ["600001"]
    assert [
        item["stock_code"] for item in summary["waiting_monthly_reclaim"]
    ] == ["600002"]
    assert summary["counts"]["current_candidates"] == 1
    assert summary["counts"]["waiting_monthly_reclaim"] == 1
    assert summary["counts"]["current_month_ma5_held"] == 1
    assert summary["counts"]["current_month_ma5_not_held"] == 1
    assert summary["counts"]["current_month_ma5_unknown"] == 0
    assert summary["counts"]["monthly_seeded"] == 1


def test_unknown_dynamic_ma5_is_fail_closed_out_of_current_candidates(
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_seed_stage(monkeypatch, [_seed("600001", "样本不足票")])
    _patch_missing_mainline(monkeypatch)
    monkeypatch.setattr(
        watch_service,
        "_fetch_adjusted_daily",
        lambda *_args, **_kwargs: (
            [{"trade_date": TARGET_DATE}],
            {"status": "success"},
        ),
    )
    monkeypatch.setattr(
        watch_service.indicator_watch,
        "evaluate_daily_monitor",
        lambda *_args, **_kwargs: {
            "stage": "insufficient_history",
            "evidence": {
                "reason": "dynamic_month_ma5_history_insufficient",
                "dynamic_monthly_ma5": {
                    "status": "insufficient_history",
                    "support_held": None,
                },
            },
        },
    )

    summary = watch_service.run_monitor(
        conn,
        object(),
        TARGET_DATE,
        months=35,
    )

    assert summary["status"] == "partial"
    assert summary["candidates"] == []
    assert summary["waiting_monthly_reclaim"] == []
    assert [
        item["stock_code"]
        for item in summary["indeterminate_current_month_ma5"]
    ] == ["600001"]
    assert summary["counts"]["current_candidates"] == 0
    assert summary["counts"]["indeterminate_current_month_ma5"] == 1
    assert summary["counts"]["current_month_ma5_unknown"] == 1
    assert summary["counts"]["daily_insufficient"] == 1


@pytest.mark.parametrize(
    "st_rows",
    [
        [],
        [{}],
        [{"ts_code": "not-a-code"}],
    ],
)
def test_stock_identity_rejects_empty_or_invalid_st_facts(st_rows: list[dict]) -> None:
    class Registry:
        def call(self, method: str, *_args):
            assert method == "get_stock_st"
            return SimpleNamespace(
                success=True,
                data=st_rows,
                source="fixture:stock_st",
                error=None,
            )

    with pytest.raises(watch_service.IndicatorWatchSourceError):
        watch_service._stock_identity_context(
            Registry(),
            TARGET_DATE,
            [_seed("600001")],
        )


def test_stock_identity_uses_target_date_st_and_optional_name_source() -> None:
    class Registry:
        def call(self, method: str, *_args):
            if method == "get_stock_st":
                return SimpleNamespace(
                    success=True,
                    data=[{"ts_code": "600001.SH"}],
                    source="fixture:stock_st",
                    error=None,
                )
            assert method == "get_stock_basic_batch"
            return SimpleNamespace(
                success=True,
                data=[
                    {"ts_code": "600001.SH", "name": "*ST测试"},
                    {"ts_code": "600002.SH", "name": "正常股份"},
                ],
                source="fixture:stock_basic",
                error=None,
            )

    st_codes, names, context = watch_service._stock_identity_context(
        Registry(),
        TARGET_DATE,
        [_seed("600001"), _seed("600002")],
    )

    assert st_codes == {"600001"}
    assert names == {"600001": "*ST测试", "600002": "正常股份"}
    assert context["st_status"] == "success"
    assert context["name_status"] == "success"


def test_current_st_seed_is_excluded_before_daily_fetch(
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_seed_stage(
        monkeypatch,
        [_seed("600001", "*ST测试"), _seed("600002", "正常股份")],
    )
    monkeypatch.setattr(
        watch_service,
        "_stock_identity_context",
        lambda _registry, _target, _seeds: (
            {"600001"},
            {"600001": "*ST测试", "600002": "正常股份"},
            {
                "st_status": "success",
                "st_source": "fixture:stock_st",
                "name_status": "success",
                "name_source": "fixture:stock_basic",
                "name_error": None,
                "semantics": "fixture",
            },
        ),
    )
    monkeypatch.setattr(
        watch_service,
        "_mainline_context",
        lambda *_args, **_kwargs: (
            {
                "600001": {"name": "*ST测试", "sw_l2": "林业Ⅱ"},
                "600002": {"name": "正常股份", "sw_l2": "元件"},
            },
            ["元件"],
            {
                "status": "ok",
                "sectors": ["元件"],
                "source_dates": [TARGET_DATE],
                "industry_status": "current_snapshot",
                "industry_semantics": "fixture",
            },
        ),
    )
    fetched_codes: list[str] = []
    monkeypatch.setattr(
        watch_service,
        "_fetch_adjusted_daily",
        lambda _registry, code, *_args: (
            fetched_codes.append(code) or [{"trade_date": TARGET_DATE}],
            {"status": "success"},
        ),
    )
    monkeypatch.setattr(
        watch_service.indicator_watch,
        "evaluate_daily_monitor",
        lambda *_args, **_kwargs: _successful_daily_result(),
    )

    summary = watch_service.run_monitor(
        conn,
        object(),
        TARGET_DATE,
        months=35,
    )

    assert fetched_codes == ["600002"]
    assert summary["counts"]["st_excluded"] == 1
    assert summary["counts"]["monthly_seed_eligible"] == 1
    assert [item["stock_code"] for item in summary["candidates"]] == ["600002"]
    assert [
        item["stock_code"] for item in summary["st_excluded_items"]
    ] == ["600001"]


def test_missing_display_name_does_not_block_when_st_status_is_known(
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed = _seed("600001", "占位")
    seed["stock_name"] = ""
    _patch_seed_stage(monkeypatch, [seed])
    _patch_missing_mainline(monkeypatch)
    monkeypatch.setattr(
        watch_service,
        "_stock_identity_context",
        lambda _registry, _target, _seeds: (
            set(),
            {},
            {
                "st_status": "success",
                "st_source": "fixture:stock_st",
                "name_status": "source_failed",
                "name_source": None,
                "name_error": "fixture name failure",
                "semantics": "fixture",
            },
        ),
    )
    fetched_codes: list[str] = []
    monkeypatch.setattr(
        watch_service,
        "_fetch_adjusted_daily",
        lambda _registry, code, *_args: (
            fetched_codes.append(code) or [{"trade_date": TARGET_DATE}],
            {"status": "success"},
        ),
    )
    monkeypatch.setattr(
        watch_service.indicator_watch,
        "evaluate_daily_monitor",
        lambda *_args, **_kwargs: _successful_daily_result(),
    )

    summary = watch_service.run_monitor(
        conn,
        object(),
        TARGET_DATE,
        months=35,
    )

    assert summary["status"] == "complete"
    assert summary["counts"]["stock_name_unknown"] == 1
    assert fetched_codes == ["600001"]
    assert summary["candidates"][0]["stock_name"] == ""


def test_st_source_failure_blocks_before_daily_fetch(
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_seed_stage(monkeypatch, [_seed("600001")])
    monkeypatch.setattr(
        watch_service,
        "_stock_identity_context",
        lambda *_args: (_ for _ in ()).throw(
            watch_service.IndicatorWatchSourceError(
                "stock_st source_failed: fixture"
            )
        ),
    )
    monkeypatch.setattr(
        watch_service,
        "_fetch_adjusted_daily",
        lambda *_args: pytest.fail("ST 硬门失败后不应拉日线"),
    )

    summary = watch_service.run_monitor(
        conn,
        object(),
        TARGET_DATE,
        months=35,
    )

    assert summary["status"] == "blocked"
    assert "stock_st source_failed" in summary["error"]


@pytest.mark.parametrize("failure_status", ["qfq_failed", "daily_source_failed"])
def test_single_stock_daily_or_qfq_failure_makes_run_partial(
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    failure_status: str,
) -> None:
    _patch_seed_stage(
        monkeypatch,
        [_seed("600001", "完整票"), _seed("600002", "缺失票")],
    )
    _patch_missing_mainline(monkeypatch)

    def fake_fetch(_registry, code, _start, _target):
        if code == "600002":
            return None, {
                "status": failure_status,
                "error": "provider fixture failure",
            }
        return ([{"trade_date": TARGET_DATE}], {"status": "success"})

    monkeypatch.setattr(watch_service, "_fetch_adjusted_daily", fake_fetch)
    monkeypatch.setattr(
        watch_service.indicator_watch,
        "evaluate_daily_monitor",
        lambda *_args, **_kwargs: _successful_daily_result(),
    )

    summary = watch_service.run_monitor(
        conn,
        object(),
        TARGET_DATE,
        months=35,
    )

    assert summary["status"] == "partial"
    assert summary["source_status"]["daily"] == "partial"
    assert [item["stock_code"] for item in summary["candidates"]] == ["600001"]
    assert summary["counts"]["daily_complete"] == 1
    assert summary["counts"]["daily_blocked"] == 1
    assert summary["data_issues"][0]["stock_code"] == "600002"
    assert summary["data_issues"][0]["source"]["status"] == failure_status


def test_fetch_adjusted_daily_filters_rows_after_target_date() -> None:
    bars = [
        {
            "trade_date": "2026-06-27",
            "open": 10.0,
            "high": 10.5,
            "low": 9.8,
            "close": 10.2,
            "vol": 100.0,
        },
        {
            "trade_date": TARGET_DATE,
            "open": 10.2,
            "high": 10.8,
            "low": 10.1,
            "close": 10.7,
            "vol": 120.0,
        },
        {
            "trade_date": "2026-07-01",
            "open": 10.7,
            "high": 11.0,
            "low": 10.6,
            "close": 10.9,
            "vol": 140.0,
        },
    ]
    factors = [
        {"trade_date": row["trade_date"], "adj_factor": 1.0}
        for row in bars
    ]

    class Registry:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple]] = []

        def call(self, method: str, *args):
            self.calls.append((method, args))
            data = bars if method == "get_stock_daily_range" else factors
            return SimpleNamespace(
                success=True,
                data=data,
                source=f"fixture:{method}",
                error=None,
            )

    registry = Registry()

    adjusted, source = watch_service._fetch_adjusted_daily(
        registry,
        "600001",
        "2025-01-01",
        TARGET_DATE,
    )

    assert [row["trade_date"] for row in adjusted] == [
        "2026-06-27",
        TARGET_DATE,
    ]
    assert all(row["trade_date"] <= TARGET_DATE for row in adjusted)
    assert source["status"] == "success"
    assert source["bar_count"] == 2
    assert all(call_args[-1] == TARGET_DATE for _, call_args in registry.calls)


def test_fetch_adjusted_daily_suppresses_only_exact_same_day_duplicates() -> None:
    bar = {
        "trade_date": TARGET_DATE,
        "open": 10.0,
        "high": 10.8,
        "low": 9.9,
        "close": 10.7,
        "vol": 120.0,
        "amount": 1284.0,
    }
    factor = {"trade_date": TARGET_DATE, "adj_factor": 1.0}

    class Registry:
        def call(self, method: str, *_args):
            data = [dict(bar), dict(bar)] if method == "get_stock_daily_range" else [
                dict(factor),
                dict(factor),
            ]
            return SimpleNamespace(
                success=True,
                data=data,
                source=f"fixture:{method}",
                error=None,
            )

    adjusted, source = watch_service._fetch_adjusted_daily(
        Registry(),
        "600001",
        TARGET_DATE,
        TARGET_DATE,
    )

    assert adjusted is not None
    assert len(adjusted) == 1
    assert source["status"] == "success"
    assert source["deduplicated_daily_rows"] == 1
    assert source["deduplicated_factor_rows"] == 1


def test_fetch_adjusted_daily_blocks_conflicting_same_day_facts() -> None:
    bars = [
        {
            "trade_date": TARGET_DATE,
            "open": 10.0,
            "high": 10.8,
            "low": 9.9,
            "close": 10.7,
            "vol": 120.0,
        },
        {
            "trade_date": TARGET_DATE,
            "open": 10.0,
            "high": 10.8,
            "low": 9.9,
            "close": 10.6,
            "vol": 120.0,
        },
    ]

    class Registry:
        def call(self, method: str, *_args):
            data = bars if method == "get_stock_daily_range" else [
                {"trade_date": TARGET_DATE, "adj_factor": 1.0}
            ]
            return SimpleNamespace(
                success=True,
                data=data,
                source=f"fixture:{method}",
                error=None,
            )

    adjusted, source = watch_service._fetch_adjusted_daily(
        Registry(),
        "600001",
        TARGET_DATE,
        TARGET_DATE,
    )

    assert adjusted is None
    assert source["status"] == "duplicate_conflict"
    assert "同日事实冲突" in source["error"]


def test_monthly_seed_missing_global_latest_month_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        watch_service,
        "_stock_series",
        lambda _rows: (
            {
                "600001": [
                    SimpleNamespace(end_date="2026-04-30"),
                    SimpleNamespace(end_date="2026-05-29"),
                ]
            },
            {"600001": "测试股份"},
        ),
    )
    monkeypatch.setattr(
        watch_service.indicator_watch,
        "detect_monthly_seed",
        lambda _bars: pytest.fail("缺最新完成月时不应检测旧后缀"),
    )

    seeds, counts = watch_service._monthly_seeds(
        [{"certified": True}],
        max_seeds=None,
        expected_month_end="2026-06-30",
        latest_universe_codes={"600001"},
    )

    assert seeds == []
    assert counts["blocked"] == 1
    assert counts["blocked_missing_latest_month"] == 1


def test_code_outside_latest_asof_universe_is_not_counted_as_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        watch_service,
        "_stock_series",
        lambda _rows: (
            {
                "600001": [
                    SimpleNamespace(end_date="2026-05-29")
                ],
                "600002": [
                    SimpleNamespace(end_date="2026-06-30")
                ],
            },
            {"600001": "已退出股份", "600002": "存续股份"},
        ),
    )
    monkeypatch.setattr(
        watch_service.indicator_watch,
        "detect_monthly_seed",
        lambda _bars: {
            "matched": False,
            "status": "not_matched",
            "evidence": {"reason": "conditions_not_met"},
        },
    )

    seeds, counts = watch_service._monthly_seeds(
        [{"certified": True}],
        max_seeds=None,
        expected_month_end="2026-06-30",
        latest_universe_codes={"600002"},
    )

    assert seeds == []
    assert counts["out_of_scope_latest_universe"] == 1
    assert counts.get("blocked_missing_latest_month", 0) == 0
    assert counts.get("blocked", 0) == 0


def test_latest_universe_code_missing_entire_bar_window_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        watch_service,
        "_stock_series",
        lambda _rows: (
            {
                "600001": [
                    SimpleNamespace(end_date="2026-06-30")
                ]
            },
            {"600001": "有月线股份"},
        ),
    )
    monkeypatch.setattr(
        watch_service.indicator_watch,
        "detect_monthly_seed",
        lambda _bars: {
            "matched": False,
            "status": "not_matched",
            "evidence": {"reason": "conditions_not_met"},
        },
    )

    seeds, counts = watch_service._monthly_seeds(
        [{"certified": True}],
        max_seeds=None,
        expected_month_end="2026-06-30",
        latest_universe_codes={"600001", "600002"},
    )

    assert seeds == []
    assert counts["blocked"] == 1
    assert counts["blocked_missing_latest_month"] == 1
    assert counts["blocked_missing_entire_window"] == 1


def test_latest_universe_code_with_certified_no_trade_is_evidenced_not_applicable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        watch_service,
        "_stock_series",
        lambda _rows: ({}, {}),
    )

    seeds, counts = watch_service._monthly_seeds(
        [{"certified": True}],
        max_seeds=None,
        expected_month_end="2026-06-30",
        latest_universe_codes={"600002"},
        no_trade_facts={
            "600002": {
                "2026-06-30": {
                    "month_end": "2026-06-30",
                    "fact_status": "certified_no_trade",
                }
            }
        },
    )

    assert seeds == []
    assert counts.get("blocked", 0) == 0
    assert counts["evidenced_not_applicable"] == 1
    assert counts["evidenced_no_trade_latest"] == 1
    assert counts["evidenced_no_trade_entire_window"] == 1


def test_certified_no_trade_closes_recent_month_gap_without_fabricating_bar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bars = [
        SimpleNamespace(month="2026-04", end_date="2026-04-30"),
        SimpleNamespace(month="2026-06", end_date="2026-06-30"),
    ]
    monkeypatch.setattr(
        watch_service,
        "_stock_series",
        lambda _rows: ({"600001": bars}, {"600001": "测试股份"}),
    )
    monkeypatch.setattr(
        watch_service.indicator_watch,
        "detect_monthly_seed",
        lambda _bars: {
            "matched": False,
            "status": "blocked",
            "evidence": {
                "reason": "non_consecutive_completed_months: 2026-04->2026-06"
            },
        },
    )

    seeds, counts = watch_service._monthly_seeds(
        [{"certified": True}],
        max_seeds=None,
        expected_month_end="2026-06-30",
        latest_universe_codes={"600001"},
        no_trade_facts={
            "600001": {
                "2026-05-29": {
                    "month_end": "2026-05-29",
                    "fact_status": "certified_no_trade",
                }
            }
        },
    )

    assert seeds == []
    assert counts.get("blocked", 0) == 0
    assert counts["evidenced_not_applicable"] == 1
    assert counts["evidenced_no_trade_gap"] == 1


def test_max_seeds_reports_truncation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        watch_service,
        "_stock_series",
        lambda _rows: (
            {
                "600003": [{"seed": True}],
                "600001": [{"seed": True}],
                "600002": [{"seed": True}],
            },
            {
                "600001": "甲",
                "600002": "乙",
                "600003": "丙",
            },
        ),
    )
    monkeypatch.setattr(
        watch_service.indicator_watch,
        "detect_monthly_seed",
        lambda _bars: {
            "matched": True,
            "status": "matched",
            "evidence": {
                "preferred_pullback": True,
                "positive_month_streak": 5,
                "close": 10.2,
                "ma5": 10.0,
            },
        },
    )

    seeds, counts = watch_service._monthly_seeds(
        [{"certified": True}],
        max_seeds=2,
    )

    assert [item["stock_code"] for item in seeds] == ["600001", "600002"]
    assert counts["monthly_seed_total"] == 3
    assert counts["monthly_seed_scanned"] == 2
    assert counts["monthly_seed_truncated"] == 1


@pytest.mark.parametrize(
    ("reason", "bucket"),
    [
        ("price_shape_invalid: 2026-05", "blocked_price_shape"),
        (
            "non_consecutive_completed_months: 2026-04->2026-06",
            "blocked_month_gap",
        ),
        ("invalid OHLC: 2026-05", "blocked_other"),
    ],
)
def test_monthly_seed_blocked_reason_is_auditable(
    monkeypatch: pytest.MonkeyPatch,
    reason: str,
    bucket: str,
) -> None:
    monkeypatch.setattr(
        watch_service,
        "_stock_series",
        lambda _rows: ({"600001": [{"seed": True}]}, {"600001": "测试股份"}),
    )
    monkeypatch.setattr(
        watch_service.indicator_watch,
        "detect_monthly_seed",
        lambda _bars: {
            "matched": False,
            "status": "blocked",
            "evidence": {"reason": reason},
        },
    )

    seeds, counts = watch_service._monthly_seeds(
        [{"certified": True}],
        max_seeds=None,
    )

    assert seeds == []
    assert counts["blocked"] == 1
    assert counts[bucket] == 1


def test_monthly_seed_primary_counts_conserve_universe_and_matched_does_not_grow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """三态重分类只能在 blocked/not_matched 间移动，不能扩大 matched 种子集合。"""
    latest_month = "2026-06-30"
    outcomes = {
        "600001": {
            "matched": True,
            "status": "matched",
            "evidence": {
                "preferred_pullback": True,
                "positive_month_streak": 5,
                "close": 10.2,
                "ma5": 10.0,
            },
        },
        "600002": {
            "matched": False,
            "status": "not_matched",
            "evidence": {
                "reason": "conditions_not_met",
                "failed_conditions": ["monthly_ma_alignment"],
                "shape_invalid_months": ["2026-05"],
            },
        },
        "600003": {
            "matched": False,
            "status": "blocked",
            "evidence": {"reason": "price_shape_invalid: 2026-05"},
        },
        "600004": {
            "matched": False,
            "status": "insufficient_history",
            "evidence": {"reason": "monthly_ma_history_insufficient"},
        },
    }
    series = {
        code: [SimpleNamespace(stock_code=code, end_date=latest_month)]
        for code in outcomes
    }
    series["699999"] = [
        SimpleNamespace(stock_code="699999", end_date=latest_month)
    ]
    monkeypatch.setattr(
        watch_service,
        "_stock_series",
        lambda _rows: (
            series,
            {code: f"测试{code}" for code in series},
        ),
    )
    monkeypatch.setattr(
        watch_service.indicator_watch,
        "detect_monthly_seed",
        lambda bars: outcomes[bars[0].stock_code],
    )

    latest_universe = {"600001", "600002", "600003", "600004", "600005"}
    seeds, counts = watch_service._monthly_seeds(
        [{"certified": True}],
        max_seeds=None,
        expected_month_end=latest_month,
        latest_universe_codes=latest_universe,
    )

    primary_total = sum(
        counts.get(status, 0)
        for status in (
            "matched",
            "not_matched",
            "blocked",
            "insufficient_history",
            "evidenced_not_applicable",
        )
    )
    assert primary_total == len(latest_universe)
    assert counts["matched"] == 1
    assert counts["not_matched"] == 1
    assert counts["shape_short_circuited_not_matched"] == 1
    assert counts["blocked"] == 2
    assert counts["insufficient_history"] == 1
    assert counts["blocked_price_shape"] == 1
    assert counts["blocked_missing_entire_window"] == 1
    assert counts["out_of_scope_latest_universe"] == 1
    assert counts["monthly_seed_total"] == counts["matched"] == len(seeds) == 1
    assert [seed["stock_code"] for seed in seeds] == ["600001"]


def test_missing_mainline_keeps_technical_candidate(
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_seed_stage(monkeypatch, [_seed("600001")])
    _patch_missing_mainline(monkeypatch)
    monkeypatch.setattr(
        watch_service,
        "_fetch_adjusted_daily",
        lambda *_args, **_kwargs: (
            [{"trade_date": TARGET_DATE}],
            {"status": "success"},
        ),
    )
    monkeypatch.setattr(
        watch_service.indicator_watch,
        "evaluate_daily_monitor",
        lambda *_args, **_kwargs: _successful_daily_result(),
    )

    summary = watch_service.run_monitor(
        conn,
        object(),
        TARGET_DATE,
        months=35,
    )

    assert summary["status"] == "complete"
    assert len(summary["candidates"]) == 1
    assert summary["candidates"][0]["stock_code"] == "600001"
    assert summary["candidates"][0]["stage"] == "monthly_seeded"
    assert summary["candidates"][0]["mainline_match"] is None
    assert summary["mainline_context"]["status"] == "missing"
