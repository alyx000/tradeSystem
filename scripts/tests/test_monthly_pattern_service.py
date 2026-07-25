from __future__ import annotations

import calendar
import hashlib
import json
import sqlite3
from datetime import date
from types import SimpleNamespace

import pytest

from db.schema import init_schema
from services.monthly_pattern import pool, repository, service
from services.volume_concentration import repo as concentration_repo


class _R:
    def __init__(self, data=None, *, success=True, error=None, source="fake"):
        self.data = data
        self.success = success
        self.error = error
        self.source = source
        self.note = None


def _month_ends(count: int) -> list[str]:
    rows = []
    year, month = 2023, 4
    for _ in range(count):
        rows.append(
            date(year, month, calendar.monthrange(year, month)[1]).isoformat()
        )
        month += 1
        if month == 13:
            year += 1
            month = 1
    return rows


class _Registry:
    def __init__(
        self,
        *,
        financial_success=True,
        financial_hard_gate_pass=True,
        fail_month=None,
        industry="半导体",
    ):
        self.month_ends = _month_ends(39)
        self.closes = [10.0 + i * 0.5 for i in range(36)] + [26.9, 26.3, 28.3]
        self.financial_success = financial_success
        self.financial_hard_gate_pass = financial_hard_gate_pass
        self.fail_month = fail_month
        self.industry = industry

    def call(self, capability, *args):
        if capability == "get_trade_calendar":
            year = str(args[0])[:4]
            return _R(
                [
                    {"cal_date": item.replace("-", ""), "is_open": 1}
                    for item in self.month_ends
                    if item.startswith(year)
                ]
            )
        if capability == "get_market_monthly_quotes":
            month_end = args[0]
            if month_end == self.fail_month:
                return _R(success=False, error="monthly timeout")
            index = self.month_ends.index(month_end)
            close = self.closes[index]
            return _R(
                [
                    {
                        "ts_code": "600000.SH",
                        "trade_date": month_end.replace("-", ""),
                        "open": 20.0 if index == 38 else close,
                        "high": 40.0 if index == 38 else close + 0.1,
                        "low": 19.5 if index == 38 else close - 0.1,
                        "close": close,
                        "vol": 300.0 if index == 38 else 100.0,
                        "amount": close * (300.0 if index == 38 else 100.0),
                    }
                ]
            )
        if capability == "get_adj_factor":
            return _R([{"ts_code": "600000.SH", "adj_factor": 1.0}])
        if capability == "get_stock_universe_as_of":
            return _R(
                [
                    {
                        "ts_code": "600000.SH",
                        "name": "浦发银行",
                        "list_date": "19991110",
                    }
                ],
                source="fake:stock_universe_as_of",
            )
        if capability == "get_financial_snapshots":
            if not self.financial_success:
                return _R(success=False, error="financial timeout")
            indicator = {
                "roe_waa": (
                    18.0 if self.financial_hard_gate_pass else 10.0
                ),
                "roe_yearly": (
                    18.0 if self.financial_hard_gate_pass else 10.0
                ),
                "debt_to_assets": 42.0,
                "netprofit_yoy": 26.0,
                "dt_netprofit_yoy": 22.0,
                "rd_exp": 12.0,
            }
            return _R(
                [
                    {
                        "ts_code": "600000.SH",
                        "report_period": "2025-12-31",
                        "financial_ann_date": "2026-03-28",
                        "fina_indicator": indicator,
                        "balancesheet": {"contract_liab": 120.0},
                        "income": {},
                    },
                    {
                        "ts_code": "600000.SH",
                        "report_period": "2026-03-31",
                        "financial_ann_date": "2026-04-30",
                        "fina_indicator": indicator,
                        "balancesheet": {"contract_liab": 120.0},
                        "income": {},
                    },
                ]
            )
        if capability == "get_stock_sw_industry_map":
            return _R(
                {
                    "600000.SH": {
                        "name": "浦发银行",
                        "sw_l2": self.industry,
                    }
                }
            )
        if capability == "get_stock_basic_batch":
            return _R([{"ts_code": "600000.SH", "name": "浦发银行"}])
        return _R(success=False, error=f"unsupported {capability}")


class _GapRegistry(_Registry):
    """保留全市场最小覆盖，但让主测试股票缺一个指定完成月。"""

    def __init__(self, *, missing_primary_month: str):
        super().__init__()
        self.missing_primary_month = missing_primary_month

    def call(self, capability, *args):
        result = super().call(capability, *args)
        if capability == "get_market_monthly_quotes" and result.success:
            month_end = args[0]
            primary = [] if month_end == self.missing_primary_month else result.data
            filler = {
                "ts_code": "000001.SZ",
                "trade_date": month_end.replace("-", ""),
                "open": 10.0,
                "high": 10.1,
                "low": 9.9,
                "close": 10.0,
                "vol": 100.0,
                "amount": 1000.0,
            }
            return _R([*primary, filler])
        if capability == "get_adj_factor" and result.success:
            return _R([*result.data, {"ts_code": "000001.SZ", "adj_factor": 1.0}])
        if capability == "get_stock_universe_as_of" and result.success:
            return _R(
                [
                    *result.data,
                    {
                        "ts_code": "000001.SZ",
                        "name": "平安银行",
                        "list_date": "19910403",
                    },
                ],
                source="fake:stock_universe_as_of",
            )
        return result


class _AliasRegistry(_Registry):
    def __init__(self):
        super().__init__()
        self.alias_month = self.month_ends[0]

    def call(self, capability, *args):
        result = super().call(capability, *args)
        if capability == "get_market_monthly_quotes" and result.success:
            month_end = args[0]
            if month_end != self.alias_month:
                return result
            canonical = {
                **result.data[0],
                "pre_close": result.data[0]["close"] - 0.1,
                "change": 0.1,
                "pct_chg": 1.0,
            }
            alias = {**canonical, "ts_code": "699999.SH"}
            return _R([canonical, alias])
        if capability == "get_adj_factor" and result.success:
            month_end = args[0]
            if month_end == self.alias_month:
                return _R(
                    [
                        *result.data,
                        {"ts_code": "699999.SH", "adj_factor": 1.0},
                    ]
                )
        return result


class _ShortCalendarRegistry(_Registry):
    def __init__(self, count: int):
        super().__init__()
        self.month_ends = _month_ends(count)


class _CalendarGapRegistry(_Registry):
    def __init__(self):
        super().__init__()
        all_months = _month_ends(40)
        # 仍返回 39 个月，但中间缺 2024-12，不能把更早一个月补进窗口冒充连续。
        self.month_ends = [item for item in all_months if item[:7] != "2024-12"]


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    for day in ("2026-06-26", "2026-06-27", "2026-06-30"):
        concentration_repo.save_concentration(
            conn,
            {
                "date": day,
                "total_amount_billion": 100.0,
                "sector_summary": [{"industry": "半导体"}],
            },
        )
    return conn


def test_run_daily_builds_verified_and_theme_pool_from_completed_months(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _conn()
    registry = _Registry()
    monkeypatch.setattr(service, "_is_historical_scan", lambda _scan_date: False)

    summary = service.run_daily(
        conn,
        registry,
        "2026-06-30",
        input_by="pytest",
        months=39,
        min_market_rows=1,
    )

    assert summary["status"] == "complete"
    assert summary["signal_month"] == "2026-06"
    assert summary["source_status"]["monthly_bars"] == {
        "status": "success",
        "fetched": 39,
        "cached": 0,
    }
    assert "monthly_bars_fetched" not in summary["source_status"]
    assert "monthly_bars_cached" not in summary["source_status"]
    strategies = {item["strategy_type"]: item for item in summary["candidates"]}
    assert (
        strategies["fundamental_monthly_trend"]["pool_status"]
        == "fundamental_verified"
    )
    assert (
        strategies["fundamental_monthly_trend"]["financial_evidence"]["status"]
        == "verified"
    )
    assert strategies["theme_monthly_attack"]["mainline_match"] is True
    assert strategies["theme_monthly_attack"]["pool_status"] == "active"
    fundamental = pool.get_open(conn, "600000", "fundamental_monthly_trend")
    assert fundamental["source_meta"]["input_by"] == "pytest"
    run = repository.get_run(conn, "2026-06-30")
    assert run["status"] == "complete"
    assert run["input_by"] == "pytest"


def test_run_daily_persists_only_the_canonical_bar_and_alias_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _conn()
    registry = _AliasRegistry()
    monkeypatch.setattr(service, "_is_historical_scan", lambda _scan_date: False)

    summary = service.run_daily(
        conn,
        registry,
        "2026-06-30",
        input_by="pytest",
        months=39,
        min_market_rows=1,
    )

    assert summary["status"] == "complete"
    bars = repository.load_month_bars(conn, [registry.alias_month])
    assert [row["stock_code"] for row in bars] == ["600000"]
    manifest = repository.load_month_bar_manifests(
        conn,
        [registry.alias_month],
    )[0]
    expected_receipt = {
        "normalization_type": "vendor_shadow_duplicate",
        "evidence_version": 2,
        "month_end": registry.alias_month,
        "alias_code": "699999",
        "canonical_code": "600000",
        "exchange": "SH",
        "quote_fields": [
            "open",
            "high",
            "low",
            "close",
            "pre_close",
            "change",
            "pct_chg",
            "vol",
            "amount",
        ],
        "cache_verifiable_quote_fields": [
            "open",
            "high",
            "low",
            "close",
            "vol",
            "amount",
        ],
        "quote_fingerprint": {
            "open": bars[0]["open"],
            "high": bars[0]["high"],
            "low": bars[0]["low"],
            "close": bars[0]["close"],
            "pre_close": bars[0]["close"] - 0.1,
            "change": 0.1,
            "pct_chg": 1.0,
            "vol": bars[0]["volume"],
            "amount": bars[0]["amount"],
        },
        "alias_adj_factor": 1.0,
        "canonical_adj_factor": 1.0,
        "evidence": "identical_complete_month_quote_and_adj_factor",
    }
    canonical_receipt = json.dumps(
        expected_receipt,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    expected_receipt["receipt_sha256"] = hashlib.sha256(
        canonical_receipt.encode("utf-8")
    ).hexdigest()
    assert manifest["source_meta"]["code_alias_evidence_schema_version"] == 2
    assert manifest["source_meta"]["code_alias_normalizations"] == [
        expected_receipt
    ]

    corrupted_meta = manifest["source_meta"]
    corrupted_meta["code_alias_normalizations"][0][
        "canonical_adj_factor"
    ] = 2.0
    conn.execute(
        """
        UPDATE monthly_pattern_bar_manifests
        SET source_meta_json = ?
        WHERE month_end = ?
        """,
        (
            json.dumps(corrupted_meta, ensure_ascii=False),
            registry.alias_month,
        ),
    )

    rerun = service.run_daily(
        conn,
        registry,
        "2026-06-30",
        input_by="pytest",
        months=39,
        min_market_rows=1,
    )

    assert rerun["status"] == "complete"
    assert rerun["source_status"]["monthly_bars"] == {
        "status": "success",
        "fetched": 1,
        "cached": 38,
    }

    manifest = repository.load_month_bar_manifests(
        conn,
        [registry.alias_month],
    )[0]
    stripped_meta = manifest["source_meta"]
    for field in (
        "code_alias_evidence_schema_version",
        "factor_coverage_denominator",
        "raw_joined_code_count",
        "normalized_joined_code_count",
        "code_alias_normalizations",
    ):
        stripped_meta.pop(field)
    conn.execute(
        """
        UPDATE monthly_pattern_bar_manifests
        SET source_meta_json = ?
        WHERE month_end = ?
        """,
        (
            json.dumps(stripped_meta, ensure_ascii=False),
            registry.alias_month,
        ),
    )

    missing_receipt_rerun = service.run_daily(
        conn,
        registry,
        "2026-06-30",
        input_by="pytest",
        months=39,
        min_market_rows=1,
    )

    assert missing_receipt_rerun["source_status"]["monthly_bars"] == {
        "status": "success",
        "fetched": 1,
        "cached": 38,
    }

    manifest = repository.load_month_bar_manifests(
        conn,
        [registry.alias_month],
    )[0]
    fingerprint_meta = manifest["source_meta"]
    fingerprint_meta["code_alias_normalizations"][0]["quote_fingerprint"][
        "pre_close"
    ] = 99.0
    conn.execute(
        """
        UPDATE monthly_pattern_bar_manifests
        SET source_meta_json = ?
        WHERE month_end = ?
        """,
        (
            json.dumps(fingerprint_meta, ensure_ascii=False),
            registry.alias_month,
        ),
    )

    fingerprint_rerun = service.run_daily(
        conn,
        registry,
        "2026-06-30",
        input_by="pytest",
        months=39,
        min_market_rows=1,
    )

    assert fingerprint_rerun["source_status"]["monthly_bars"] == {
        "status": "success",
        "fetched": 1,
        "cached": 38,
    }

    conn.execute(
        """
        UPDATE monthly_pattern_bars
        SET close = 'broken'
        WHERE month_end = ? AND stock_code = '600000'
        """,
        (registry.alias_month,),
    )

    scalar_rerun = service.run_daily(
        conn,
        registry,
        "2026-06-30",
        input_by="pytest",
        months=39,
        min_market_rows=1,
    )

    assert scalar_rerun["source_status"]["monthly_bars"] == {
        "status": "success",
        "fetched": 1,
        "cached": 38,
    }


def test_fundamental_verified_only_activates_on_strict_next_completed_month() -> None:
    financial_view = {"verified": True, "status": "verified"}
    open_row = {
        "status": "fundamental_verified",
        "signal_month": "2026-05",
        "technical_evidence": {"fundamental_verified_month": "2026-05"},
    }
    common = {
        "strategy": "fundamental_monthly_trend",
        "open_row": open_row,
        "financial_view": financial_view,
        "mainline_match": None,
        "industry_status": "success",
        "mainline_status": "ok",
    }

    same_month = service._resolve_candidate_status(
        **common,
        signal_month="2026-05",
        expected_months=["2026-04", "2026-05"],
    )
    next_month = service._resolve_candidate_status(
        **common,
        signal_month="2026-06",
        expected_months=["2026-04", "2026-05", "2026-06"],
    )
    skipped_month = service._resolve_candidate_status(
        **common,
        signal_month="2026-07",
        expected_months=["2026-04", "2026-05", "2026-06", "2026-07"],
    )

    assert same_month == "fundamental_verified"
    assert next_month == "active"
    assert skipped_month == "fundamental_verified"


def test_fundamental_verified_activates_after_next_completed_month_reconfirms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _conn()
    monkeypatch.setattr(service, "_is_historical_scan", lambda _scan_date: False)
    registry = _Registry()
    registry.month_ends = _month_ends(40)
    registry.closes.append(29.0)

    first = service.run_daily(
        conn,
        registry,
        "2026-06-30",
        input_by="pytest",
        months=39,
        min_market_rows=1,
    )
    first_fundamental = next(
        item
        for item in first["candidates"]
        if item["strategy_type"] == "fundamental_monthly_trend"
    )
    assert first_fundamental["pool_status"] == "fundamental_verified"

    second = service.run_daily(
        conn,
        registry,
        "2026-07-31",
        input_by="pytest",
        months=39,
        min_market_rows=1,
    )
    second_fundamental = next(
        item
        for item in second["candidates"]
        if item["strategy_type"] == "fundamental_monthly_trend"
    )
    assert second_fundamental["pool_status"] == "active"
    transition = next(
        item
        for item in second["transitions"]
        if item["strategy_type"] == "fundamental_monthly_trend"
    )
    assert transition["from_status"] == "fundamental_verified"
    assert transition["to_status"] == "active"


def test_never_active_fundamental_risk_recovery_restarts_two_stage_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _conn()
    monkeypatch.setattr(service, "_is_historical_scan", lambda _scan_date: False)
    pool.record(
        conn,
        stock_code="600000",
        stock_name="浦发银行",
        strategy_type="fundamental_monthly_trend",
        status="fundamental_verified",
        signal_month="2026-05",
        date="2026-05-31",
        technical_evidence={"fundamental_verified_month": "2026-05"},
        financial_evidence={"status": "verified"},
    )
    conn.commit()

    registry = _Registry(financial_hard_gate_pass=False)
    failed = service.run_daily(
        conn,
        registry,
        "2026-06-30",
        input_by="pytest",
        months=39,
        min_market_rows=1,
    )
    failed_candidate = next(
        item
        for item in failed["candidates"]
        if item["strategy_type"] == "fundamental_monthly_trend"
    )
    assert failed_candidate["pool_status"] == "risk"
    current = pool.get_open(conn, "600000", "fundamental_monthly_trend")
    assert current["source_meta"]["risk_from_status"] == "fundamental_verified"

    registry.financial_hard_gate_pass = True
    registry.month_ends = _month_ends(40)
    registry.closes.append(29.0)
    recovered = service.run_daily(
        conn,
        registry,
        "2026-07-31",
        input_by="pytest",
        months=39,
        min_market_rows=1,
    )
    recovered_candidate = next(
        item
        for item in recovered["candidates"]
        if item["strategy_type"] == "fundamental_monthly_trend"
    )
    assert recovered_candidate["pool_status"] == "fundamental_verified"
    assert (
        recovered_candidate["technical_evidence"]["fundamental_verified_month"]
        == "2026-07"
    )

    registry.month_ends = _month_ends(41)
    registry.closes.append(29.5)
    confirmed = service.run_daily(
        conn,
        registry,
        "2026-08-31",
        input_by="pytest",
        months=39,
        min_market_rows=1,
    )
    confirmed_candidate = next(
        item
        for item in confirmed["candidates"]
        if item["strategy_type"] == "fundamental_monthly_trend"
    )
    assert confirmed_candidate["pool_status"] == "active"


def test_requested_month_window_must_not_silently_shrink() -> None:
    conn = _conn()
    summary = service.run_daily(
        conn,
        _ShortCalendarRegistry(39),
        "2026-06-30",
        input_by="pytest",
        months=48,
        min_market_rows=1,
    )

    assert summary["status"] == "failed"
    assert "要求 48" in summary["error"]
    assert conn.execute("SELECT COUNT(*) FROM monthly_pattern_bars").fetchone()[0] == 0


def test_calendar_window_tail_must_match_latest_completed_month() -> None:
    conn = _conn()
    summary = service.run_daily(
        conn,
        _ShortCalendarRegistry(39),
        "2026-12-15",
        input_by="pytest",
        months=39,
        min_market_rows=1,
    )

    assert summary["status"] == "failed"
    assert "尾月" in summary["error"]
    assert "2026-11" in summary["error"]
    assert conn.execute("SELECT COUNT(*) FROM monthly_pattern_bars").fetchone()[0] == 0


def test_requested_month_window_must_be_calendar_consecutive() -> None:
    conn = _conn()
    summary = service.run_daily(
        conn,
        _CalendarGapRegistry(),
        "2026-07-31",
        input_by="pytest",
        months=39,
        min_market_rows=1,
    )

    assert summary["status"] == "failed"
    assert "不连续" in summary["error"]
    assert conn.execute("SELECT COUNT(*) FROM monthly_pattern_bars").fetchone()[0] == 0


def test_missing_current_month_does_not_match_or_advance_existing_pool() -> None:
    conn = _conn()
    _seed_open(
        conn,
        strategy="fundamental_monthly_trend",
        status="active",
    )
    registry = _GapRegistry(missing_primary_month=_month_ends(39)[-1])

    summary = service.run_daily(
        conn,
        registry,
        "2026-06-30",
        input_by="pytest",
        months=39,
        min_market_rows=1,
    )

    assert not any(item["stock_code"] == "600000" for item in summary["candidates"])
    current = pool.get_open(conn, "600000", "fundamental_monthly_trend")
    assert current["status"] == "active"
    assert current["last_seen_date"] == "2026-05-31"
    assert summary["transitions"] == []


def test_gap_in_required_recent_months_does_not_match_or_advance_pool() -> None:
    conn = _conn()
    _seed_open(
        conn,
        strategy="fundamental_monthly_trend",
        status="active",
    )
    registry = _GapRegistry(missing_primary_month=_month_ends(39)[-2])

    summary = service.run_daily(
        conn,
        registry,
        "2026-06-30",
        input_by="pytest",
        months=39,
        min_market_rows=1,
    )

    assert not any(item["stock_code"] == "600000" for item in summary["candidates"])
    current = pool.get_open(conn, "600000", "fundamental_monthly_trend")
    assert current["status"] == "active"
    assert current["last_seen_date"] == "2026-05-31"
    assert summary["transitions"] == []


def test_financial_and_industry_batches_below_market_coverage_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _conn()
    monkeypatch.setattr(service, "_is_historical_scan", lambda _scan_date: False)
    # 两只股票均有当月行情，但财务与行业批次只覆盖其中一只，覆盖率仅 50%。
    registry = _GapRegistry(missing_primary_month="1900-01-31")

    summary = service.run_daily(
        conn,
        registry,
        "2026-06-30",
        input_by="pytest",
        months=39,
        min_market_rows=1,
    )

    fundamental = next(
        item
        for item in summary["candidates"]
        if item["strategy_type"] == "fundamental_monthly_trend"
    )
    theme = next(
        item
        for item in summary["candidates"]
        if item["strategy_type"] == "theme_monthly_attack"
    )
    assert summary["status"] == "partial"
    assert summary["source_status"]["financials"] == "coverage_failed"
    assert summary["source_status"]["industry_map"] == "coverage_failed"
    assert fundamental["pool_status"] == "technical_candidate"
    assert fundamental["financial_evidence"] == {}
    assert theme["pool_status"] == "technical_candidate"
    assert theme["mainline_match"] is None


def test_two_latest_adjacent_completed_months_below_ma5_exit_risk_pool() -> None:
    conn = _conn()
    _seed_open(
        conn,
        strategy="fundamental_monthly_trend",
        status="risk",
    )
    registry = _Registry()
    registry.closes[-2:] = [20.0, 19.7]

    summary = service.run_daily(
        conn,
        registry,
        "2026-06-30",
        input_by="pytest",
        months=39,
        min_market_rows=1,
    )

    assert pool.get_open(conn, "600000", "fundamental_monthly_trend") is None
    exited = pool.list_pool(
        conn,
        stock_code="600000",
        strategy_type="fundamental_monthly_trend",
    )[-1]
    assert exited["status"] == "exited"
    transition = next(
        item
        for item in summary["transitions"]
        if item["strategy_type"] == "fundamental_monthly_trend"
    )
    assert transition["from_status"] == "risk"
    assert transition["to_status"] == "exited"


def test_empty_input_by_fails_before_fact_or_pool_writes() -> None:
    conn = _conn()

    with pytest.raises(ValueError, match="input_by"):
        service.run_daily(
            conn,
            _Registry(),
            "2026-06-30",
            input_by="  ",
            months=39,
            min_market_rows=1,
        )

    assert conn.execute("SELECT COUNT(*) FROM monthly_pattern_bars").fetchone()[0] == 0
    assert pool.list_pool(conn) == []
    assert repository.get_run(conn, "2026-06-30") is None


def test_invalid_scan_date_fails_before_fact_or_audit_writes() -> None:
    conn = _conn()

    with pytest.raises(ValueError, match="scan_date"):
        service.run_daily(
            conn,
            _Registry(),
            "2026-06",
            input_by="pytest",
            months=39,
            min_market_rows=1,
        )

    assert conn.execute("SELECT COUNT(*) FROM monthly_pattern_bars").fetchone()[0] == 0
    assert repository.get_run(conn, "2026-06") is None


def test_financial_failure_is_partial_and_does_not_fake_verified_status() -> None:
    conn = _conn()

    summary = service.run_daily(
        conn,
        _Registry(financial_success=False),
        "2026-06-30",
        input_by="pytest",
        months=39,
        min_market_rows=1,
    )

    assert summary["status"] == "partial"
    fundamental = next(
        item
        for item in summary["candidates"]
        if item["strategy_type"] == "fundamental_monthly_trend"
    )
    assert fundamental["pool_status"] == "technical_candidate"
    assert summary["source_status"]["financials"] == "source_failed"


def test_core_monthly_source_failure_records_failed_not_empty() -> None:
    conn = _conn()
    registry = _Registry(fail_month=_month_ends(39)[0])

    summary = service.run_daily(
        conn,
        registry,
        "2026-06-30",
        input_by="pytest",
        months=39,
        min_market_rows=1,
    )

    assert summary["status"] == "failed"
    assert summary["candidates"] == []
    run = repository.get_run(conn, "2026-06-30")
    assert run["status"] == "failed"
    assert "monthly timeout" in run["error"]
    assert pool.list_pool(conn) == []


def _seed_open(
    conn: sqlite3.Connection,
    *,
    strategy: str,
    status: str,
) -> None:
    pool.record(
        conn,
        stock_code="600000",
        stock_name="浦发银行",
        strategy_type=strategy,
        status=status,
        signal_month="2026-05",
        date="2026-05-31",
        technical_evidence={"seed": True},
    )
    conn.commit()


@pytest.mark.parametrize(
    ("existing_status", "expected_status"),
    [
        ("active", "risk"),
        ("fundamental_verified", "risk"),
        ("risk", "risk"),
    ],
)
def test_reliable_financial_hard_gate_failure_moves_existing_pool_to_risk(
    existing_status: str,
    expected_status: str,
) -> None:
    conn = _conn()
    _seed_open(
        conn,
        strategy="fundamental_monthly_trend",
        status=existing_status,
    )

    summary = service.run_daily(
        conn,
        _Registry(financial_hard_gate_pass=False),
        "2026-06-30",
        input_by="pytest",
        months=39,
        min_market_rows=1,
    )

    candidate = next(
        item
        for item in summary["candidates"]
        if item["strategy_type"] == "fundamental_monthly_trend"
    )
    assert candidate["financial_evidence"]["latest"]["status"] == "failed"
    assert candidate["pool_status"] == expected_status
    assert (
        pool.get_open(conn, "600000", "fundamental_monthly_trend")["status"]
        == expected_status
    )


def test_reliable_financial_hard_gate_failure_keeps_new_candidate_technical() -> None:
    conn = _conn()

    summary = service.run_daily(
        conn,
        _Registry(financial_hard_gate_pass=False),
        "2026-06-30",
        input_by="pytest",
        months=39,
        min_market_rows=1,
    )

    candidate = next(
        item
        for item in summary["candidates"]
        if item["strategy_type"] == "fundamental_monthly_trend"
    )
    assert candidate["pool_status"] == "technical_candidate"


def test_missing_financial_source_preserves_existing_fundamental_status() -> None:
    conn = _conn()
    pool.record(
        conn,
        stock_code="600000",
        stock_name="浦发银行",
        strategy_type="fundamental_monthly_trend",
        status="active",
        signal_month="2026-05",
        date="2026-05-31",
        report_period="2025-12-31",
        financial_ann_date="2026-03-28",
        technical_evidence={"seed": True},
        financial_evidence={"status": "verified", "roe_waa": 18.0},
    )
    conn.commit()

    summary = service.run_daily(
        conn,
        _Registry(financial_success=False),
        "2026-06-30",
        input_by="pytest",
        months=39,
        min_market_rows=1,
    )

    candidate = next(
        item
        for item in summary["candidates"]
        if item["strategy_type"] == "fundamental_monthly_trend"
    )
    assert summary["source_status"]["financials"] == "source_failed"
    assert candidate["pool_status"] == "active"
    assert candidate["financial_evidence"] == {
        "status": "verified",
        "roe_waa": 18.0,
    }
    current = pool.get_open(conn, "600000", "fundamental_monthly_trend")
    assert current["report_period"] == "2025-12-31"
    assert current["financial_ann_date"] == "2026-03-28"
    assert current["financial_evidence"] == {
        "status": "verified",
        "roe_waa": 18.0,
    }


@pytest.mark.parametrize(
    ("finance_status", "include_financial"),
    [("disabled", False), ("success", True)],
)
def test_disabled_or_insufficient_financial_view_preserves_existing_evidence(
    monkeypatch: pytest.MonkeyPatch,
    finance_status: str,
    include_financial: bool,
) -> None:
    conn = _conn()
    pool.record(
        conn,
        stock_code="600000",
        stock_name="浦发银行",
        strategy_type="fundamental_monthly_trend",
        status="active",
        signal_month="2026-05",
        date="2026-05-31",
        report_period="2025-12-31",
        financial_ann_date="2026-03-28",
        technical_evidence={"seed": True},
        financial_evidence={"status": "verified", "roe_waa": 18.0},
    )
    conn.commit()
    if include_financial:
        monkeypatch.setattr(
            service,
            "_financial_views",
            lambda *_args, **_kwargs: (
                {
                    "600000": {
                        "status": "insufficient",
                        "verified": False,
                        "report_period": "2026-03-31",
                        "financial_ann_date": "2026-04-30",
                    }
                },
                finance_status,
            ),
        )

    summary = service.run_daily(
        conn,
        _Registry(),
        "2026-06-30",
        input_by="pytest",
        months=39,
        min_market_rows=1,
        include_financial=include_financial,
    )

    candidate = next(
        item
        for item in summary["candidates"]
        if item["strategy_type"] == "fundamental_monthly_trend"
    )
    assert candidate["pool_status"] == "active"
    assert candidate["financial_evidence"] == {
        "status": "verified",
        "roe_waa": 18.0,
    }
    current = pool.get_open(conn, "600000", "fundamental_monthly_trend")
    assert current["report_period"] == "2025-12-31"
    assert current["financial_ann_date"] == "2026-03-28"
    assert current["financial_evidence"] == candidate["financial_evidence"]


def test_empty_financial_batch_does_not_reuse_cached_snapshot_for_new_candidate() -> None:
    conn = _conn()
    service.run_daily(
        conn,
        _Registry(),
        "2026-06-30",
        input_by="pytest",
        months=39,
        min_market_rows=1,
    )
    conn.execute("DELETE FROM monthly_pattern_pool")
    conn.commit()

    registry = _Registry()
    original_call = registry.call

    def empty_financial_call(capability, *args):
        if capability == "get_financial_snapshots":
            return _R([])
        return original_call(capability, *args)

    registry.call = empty_financial_call
    summary = service.run_daily(
        conn,
        registry,
        "2026-06-30",
        input_by="pytest",
        months=39,
        min_market_rows=1,
    )

    candidate = next(
        item
        for item in summary["candidates"]
        if item["strategy_type"] == "fundamental_monthly_trend"
    )
    assert summary["status"] == "partial"
    assert summary["source_status"]["financials"] == "source_ok_empty"
    assert candidate["pool_status"] == "technical_candidate"
    assert candidate["financial_evidence"] == {}


def test_current_financial_batch_never_backfills_a_missing_stock_from_cache() -> None:
    conn = _conn()
    common = {
        "fina_indicator": {
            "roe_waa": 18.0,
            "roe_yearly": 18.0,
            "debt_to_assets": 42.0,
            "netprofit_yoy": 26.0,
            "dt_netprofit_yoy": 22.0,
            "rd_exp": 12.0,
        },
        "balancesheet": {"contract_liab": 120.0},
        "income": {},
    }
    annual = {
        **common,
        "report_period": "2025-12-31",
        "financial_ann_date": "2026-03-28",
    }
    q1 = {
        **common,
        "report_period": "2026-03-31",
        "financial_ann_date": "2026-04-30",
    }
    repository.save_financial_snapshots(
        conn,
        [
            {"stock_code": "000009", **annual},
            {"stock_code": "000009", **q1},
        ],
        observed_date="2026-04-30",
    )
    conn.commit()
    current_rows = [
        {"stock_code": f"{index:06d}", **snapshot}
        for index in range(9)
        for snapshot in (annual, q1)
    ]

    class _FinancialRegistry:
        def call(self, capability, *_args):
            assert capability == "get_financial_snapshots"
            return _R(current_rows)

    views, status = service._financial_views(
        conn,
        _FinancialRegistry(),
        "2026-06-30",
        include_financial=True,
        market_codes={f"{index:06d}" for index in range(10)},
    )

    assert status == "success"
    assert "000009" not in views


def test_financial_batch_requires_each_component_to_cover_market_universe() -> None:
    conn = _conn()
    snapshot = {
        "report_period": "2025-12-31",
        "financial_ann_date": "2026-03-28",
        "fina_indicator": {
            "roe_waa": 18.0,
            "debt_to_assets": 42.0,
            "netprofit_yoy": 26.0,
            "dt_netprofit_yoy": 22.0,
            "rd_exp": 12.0,
        },
        "balancesheet": {"contract_liab": 120.0},
        "income": {},
    }
    current_rows = [
        {
            "stock_code": f"{index:06d}",
            **snapshot,
            "income": {"status": "missing"} if index >= 8 else {},
        }
        for index in range(10)
    ]

    class _FinancialRegistry:
        def call(self, capability, *_args):
            assert capability == "get_financial_snapshots"
            return _R(current_rows)

    views, status = service._financial_views(
        conn,
        _FinancialRegistry(),
        "2026-06-30",
        include_financial=True,
        market_codes={f"{index:06d}" for index in range(10)},
    )

    assert status == "coverage_failed"
    assert views == {}


def test_financial_component_coverage_is_checked_on_required_report_period() -> None:
    conn = _conn()

    def snapshot(code: str, period: str, *, income_ok: bool) -> dict:
        return {
            "stock_code": code,
            "report_period": period,
            "financial_ann_date": "2026-04-30",
            "fina_indicator": {
                "roe_waa": 18.0,
                "debt_to_assets": 42.0,
                "netprofit_yoy": 26.0,
                "dt_netprofit_yoy": 22.0,
            },
            "balancesheet": {"total_assets": 100.0},
            "income": (
                {"n_income_attr_p": 10.0}
                if income_ok
                else {"status": "missing"}
            ),
        }

    rows = [
        snapshot(f"{index:06d}", "2026-03-31", income_ok=True)
        for index in range(10)
    ] + [
        snapshot(
            f"{index:06d}",
            "2025-12-31",
            income_ok=index < 5,
        )
        for index in range(10)
    ]

    class _FinancialRegistry:
        def call(self, capability, *_args):
            assert capability == "get_financial_snapshots"
            return _R(rows)

    views, status = service._financial_views(
        conn,
        _FinancialRegistry(),
        "2026-06-30",
        include_financial=True,
        market_codes={f"{index:06d}" for index in range(10)},
    )

    assert status == "coverage_failed"
    assert views == {}


@pytest.mark.parametrize(
    ("scan_date", "required_period", "required_annual"),
    [
        ("2026-01-31", "2025-09-30", "2024-12-31"),
        ("2026-04-30", "2025-09-30", "2024-12-31"),
        ("2026-05-01", "2026-03-31", "2025-12-31"),
        ("2026-08-31", "2026-03-31", "2025-12-31"),
        ("2026-09-01", "2026-06-30", "2025-12-31"),
        ("2026-10-31", "2026-06-30", "2025-12-31"),
        ("2026-11-01", "2026-09-30", "2025-12-31"),
        ("2026-12-31", "2026-09-30", "2025-12-31"),
    ],
)
def test_financial_freshness_and_annual_period_boundaries(
    scan_date: str,
    required_period: str,
    required_annual: str,
) -> None:
    assert service._minimum_financial_period(scan_date) == required_period
    assert service._minimum_annual_period(scan_date) == required_annual


def test_april_financial_fetch_explicitly_includes_prior_required_annual() -> None:
    conn = _conn()
    calls = []

    class _FinancialRegistry:
        def call(self, capability, *args):
            calls.append((capability, args))
            return _R([])

    views, status = service._financial_views(
        conn,
        _FinancialRegistry(),
        "2026-04-02",
        include_financial=True,
        market_codes={"600000"},
    )

    assert views == {}
    assert status == "source_ok_empty"
    assert calls[0][0] == "get_financial_snapshots"
    assert calls[0][1][0] == "2026-04-02"
    requested_periods = calls[0][1][1]
    assert "2025-09-30" in requested_periods
    assert "2024-12-31" in requested_periods
    assert "2024-09-30" in requested_periods
    assert "2023-12-31" in requested_periods


def test_prior_year_annual_alone_is_stale_after_q1_is_due() -> None:
    rows = [
        {
            "stock_code": "600000",
            "report_period": "2025-12-31",
            "financial_ann_date": "2026-03-28",
            "fina_indicator": {
                "roe_waa": 18.0,
                "debt_to_assets": 42.0,
                "netprofit_yoy": 26.0,
                "dt_netprofit_yoy": 22.0,
            },
            "balancesheet": {"contract_liab": 120.0},
            "income": {},
        }
    ]

    view = service._latest_financial_views(
        rows,
        scan_date="2026-08-31",
    )["600000"]

    assert view["status"] == "stale"
    assert view["verified"] is False


def test_revision_without_proven_visibility_is_not_treated_as_historical_as_of() -> None:
    conn = _conn()
    q3_revision = {
        "stock_code": "600000",
        "report_period": "2025-09-30",
        "financial_ann_date": "2025-10-31",
        "fina_indicator": {
            "status": "ok",
            "update_flag": "1",
            "roe_waa": 18.0,
            "debt_to_assets": 42.0,
            "netprofit_yoy": 26.0,
            "dt_netprofit_yoy": 22.0,
        },
        "balancesheet": {"status": "ok", "total_assets": 100.0},
        "income": {"status": "ok", "n_income_attr_p": 10.0},
    }
    annual = {
        **q3_revision,
        "report_period": "2024-12-31",
        "financial_ann_date": "2025-03-31",
        "fina_indicator": {
            **q3_revision["fina_indicator"],
            "update_flag": "0",
        },
    }

    class _FinancialRegistry:
        def call(self, capability, *_args):
            assert capability == "get_financial_snapshots"
            return _R([annual, q3_revision])

    views, status = service._financial_views(
        conn,
        _FinancialRegistry(),
        "2026-04-30",
        include_financial=True,
        market_codes={"600000"},
    )

    assert status == "as_of_coverage_failed"
    assert views == {}


def test_missing_financial_does_not_advance_fundamental_confirmation_month(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _conn()
    monkeypatch.setattr(service, "_is_historical_scan", lambda _scan_date: False)
    pool.record(
        conn,
        stock_code="600000",
        stock_name="浦发银行",
        strategy_type="fundamental_monthly_trend",
        status="fundamental_verified",
        signal_month="2026-05",
        date="2026-05-31",
        technical_evidence={"fundamental_verified_month": "2026-05"},
        financial_evidence={"status": "verified"},
    )
    conn.commit()

    summary = service.run_daily(
        conn,
        _Registry(financial_success=False),
        "2026-06-30",
        input_by="pytest",
        months=39,
        min_market_rows=1,
    )

    candidate = next(
        item
        for item in summary["candidates"]
        if item["strategy_type"] == "fundamental_monthly_trend"
    )
    assert candidate["pool_status"] == "fundamental_verified"
    assert (
        candidate["technical_evidence"]["fundamental_verified_month"]
        == "2026-05"
    )


def test_missing_view_in_otherwise_successful_batch_preserves_open_evidence() -> None:
    open_row = {
        "report_period": "2025-12-31",
        "financial_ann_date": "2026-03-28",
        "financial_evidence": {"status": "verified", "roe_waa": 18.0},
    }

    evidence, report_period, ann_date = service._financial_payload(
        open_row,
        None,
        "success",
    )

    assert evidence == open_row["financial_evidence"]
    assert report_period == "2025-12-31"
    assert ann_date == "2026-03-28"


def test_latest_financial_view_breaks_same_announcement_date_tie_by_visibility() -> None:
    common = {
        "stock_code": "600000",
        "report_period": "2025-12-31",
        "financial_ann_date": "2026-03-28",
        "balancesheet": {"contract_liab": 120.0},
        "income": {},
    }
    rows = [
        {
            **common,
            "version_visible_date": "2026-03-28",
            "fina_indicator": {
                "roe_waa": 18.0,
                "debt_to_assets": 42.0,
                "netprofit_yoy": 26.0,
                "dt_netprofit_yoy": 22.0,
                "rd_exp": 12.0,
            },
        },
        {
            **common,
            "version_visible_date": "2026-06-01",
            "fina_indicator": {
                "roe_waa": 10.0,
                "debt_to_assets": 42.0,
                "netprofit_yoy": 26.0,
                "dt_netprofit_yoy": 22.0,
                "rd_exp": 12.0,
            },
        },
    ]

    view = service._latest_financial_views(rows)["600000"]

    assert view["latest"]["status"] == "failed"


def test_latest_financial_view_prefers_later_visible_revision_over_later_ann_date() -> None:
    common = {
        "stock_code": "600000",
        "report_period": "2025-12-31",
        "balancesheet": {"contract_liab": 120.0},
        "income": {},
    }
    rows = [
        {
            **common,
            "financial_ann_date": "2026-04-15",
            "version_visible_date": "2026-04-15",
            "version_observed_at": "2026-04-15T01:00:00Z",
            "fina_indicator": {
                "roe_waa": 18.0,
                "debt_to_assets": 42.0,
                "netprofit_yoy": 26.0,
                "dt_netprofit_yoy": 22.0,
                "rd_exp": 12.0,
            },
        },
        {
            **common,
            "financial_ann_date": "2026-03-28",
            "version_visible_date": "2026-05-10",
            "version_observed_at": "2026-05-10T01:00:00Z",
            "fina_indicator": {
                "roe_waa": 10.0,
                "debt_to_assets": 42.0,
                "netprofit_yoy": 26.0,
                "dt_netprofit_yoy": 22.0,
                "rd_exp": 12.0,
            },
        },
    ]

    view = service._latest_financial_views(rows)["600000"]

    assert view["latest"]["status"] == "failed"
    assert view["latest"]["financial_ann_date"] == "2026-03-28"


@pytest.mark.parametrize(
    ("scan_date", "latest_period"),
    [
        ("2026-04-30", "2025-06-30"),
        ("2026-08-31", "2025-09-30"),
        ("2026-10-31", "2026-03-31"),
        ("2026-12-31", "2026-06-30"),
    ],
)
def test_financial_view_fails_closed_when_latest_period_is_stale(
    scan_date: str,
    latest_period: str,
) -> None:
    rows = [
        {
            "stock_code": "600000",
            "report_period": latest_period,
            "financial_ann_date": scan_date,
            "fina_indicator": {
                "roe_waa": 18.0,
                "debt_to_assets": 42.0,
                "netprofit_yoy": 26.0,
                "dt_netprofit_yoy": 22.0,
                "rd_exp": 12.0,
            },
            "balancesheet": {"contract_liab": 120.0},
            "income": {},
        }
    ]

    view = service._latest_financial_views(rows, scan_date=scan_date)["600000"]

    assert view["status"] == "stale"
    assert view["verified"] is False


def test_existing_fundamental_moves_to_risk_on_new_hard_gate_failure_even_if_detector_no_longer_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _conn()
    _seed_open(
        conn,
        strategy="fundamental_monthly_trend",
        status="active",
    )
    original_detect = service.detectors.detect_pattern

    def no_fundamental_match(strategy, bars):
        result = original_detect(strategy, bars)
        if strategy == "fundamental_monthly_trend":
            return type(result)(
                pattern=result.pattern,
                matched=False,
                status="not_matched",
                evidence=result.evidence,
            )
        return result

    monkeypatch.setattr(service.detectors, "detect_pattern", no_fundamental_match)

    summary = service.run_daily(
        conn,
        _Registry(financial_hard_gate_pass=False),
        "2026-06-30",
        input_by="pytest",
        months=39,
        min_market_rows=1,
    )

    assert not any(
        item["strategy_type"] == "fundamental_monthly_trend"
        for item in summary["candidates"]
    )
    current = pool.get_open(conn, "600000", "fundamental_monthly_trend")
    assert current["status"] == "risk"
    assert current["financial_evidence"]["status"] == "failed"
    assert summary["counts"]["matched_active"] == 0
    assert summary["counts"]["pool_risk"] == 1
    transition = next(
        item
        for item in summary["transitions"]
        if item["strategy_type"] == "fundamental_monthly_trend"
    )
    assert {
        "from_status": "active",
        "to_status": "risk",
    }.items() <= transition.items()


def test_fundamental_financial_risk_cannot_reenter_when_financial_source_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _conn()
    pool.record(
        conn,
        stock_code="600000",
        stock_name="浦发银行",
        strategy_type="fundamental_monthly_trend",
        status="risk",
        signal_month="2026-05",
        date="2026-05-31",
        technical_evidence={"seed": True},
        financial_evidence={"status": "failed"},
    )
    conn.commit()
    original_detect = service.detectors.detect_pattern

    def no_fundamental_match(strategy, bars):
        result = original_detect(strategy, bars)
        if strategy == "fundamental_monthly_trend":
            return type(result)(
                pattern=result.pattern,
                matched=False,
                status="not_matched",
                evidence=result.evidence,
            )
        return result

    monkeypatch.setattr(service.detectors, "detect_pattern", no_fundamental_match)
    monkeypatch.setattr(
        service.detectors,
        "evaluate_pool_state",
        lambda _bars: SimpleNamespace(
            state="reentry",
            evidence={"as_of_month": "2026-06"},
        ),
    )

    service.run_daily(
        conn,
        _Registry(financial_success=False),
        "2026-06-30",
        input_by="pytest",
        months=39,
        min_market_rows=1,
    )

    current = pool.get_open(conn, "600000", "fundamental_monthly_trend")
    assert current["status"] == "risk"
    assert current["financial_evidence"]["status"] == "failed"


def test_theme_mainline_risk_cannot_reenter_while_reliable_mismatch_remains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _conn()
    monkeypatch.setattr(service, "_is_historical_scan", lambda _scan_date: False)
    pool.record(
        conn,
        stock_code="600000",
        stock_name="浦发银行",
        strategy_type="theme_monthly_attack",
        status="risk",
        signal_month="2026-05",
        date="2026-05-31",
        technical_evidence={"seed": True},
        source_meta={"mainline_match": False, "industry": "银行"},
    )
    conn.commit()
    original_detect = service.detectors.detect_pattern

    def no_theme_match(strategy, bars):
        result = original_detect(strategy, bars)
        if strategy == "theme_monthly_attack":
            return type(result)(
                pattern=result.pattern,
                matched=False,
                status="not_matched",
                evidence=result.evidence,
            )
        return result

    monkeypatch.setattr(service.detectors, "detect_pattern", no_theme_match)
    monkeypatch.setattr(
        service.detectors,
        "evaluate_pool_state",
        lambda _bars: SimpleNamespace(
            state="reentry",
            evidence={"as_of_month": "2026-06"},
        ),
    )

    service.run_daily(
        conn,
        _Registry(industry="银行"),
        "2026-06-30",
        input_by="pytest",
        months=39,
        min_market_rows=1,
    )

    current = pool.get_open(conn, "600000", "theme_monthly_attack")
    assert current["status"] == "risk"


def test_reliable_mainline_mismatch_moves_existing_theme_pool_to_risk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _conn()
    monkeypatch.setattr(service, "_is_historical_scan", lambda _scan_date: False)
    _seed_open(
        conn,
        strategy="theme_monthly_attack",
        status="active",
    )

    summary = service.run_daily(
        conn,
        _Registry(industry="银行"),
        "2026-06-30",
        input_by="pytest",
        months=39,
        min_market_rows=1,
    )

    candidate = next(
        item
        for item in summary["candidates"]
        if item["strategy_type"] == "theme_monthly_attack"
    )
    assert summary["source_status"]["industry_map"] == "success"
    assert summary["source_status"]["mainline"] == "ok"
    assert candidate["mainline_match"] is False
    assert candidate["pool_status"] == "risk"
    assert pool.get_open(conn, "600000", "theme_monthly_attack")["status"] == "risk"


def test_reliable_mainline_mismatch_moves_nonmatching_active_theme_to_risk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _conn()
    monkeypatch.setattr(service, "_is_historical_scan", lambda _scan_date: False)
    _seed_open(
        conn,
        strategy="theme_monthly_attack",
        status="active",
    )
    original_detect = service.detectors.detect_pattern

    def no_theme_match(strategy, bars):
        result = original_detect(strategy, bars)
        if strategy == "theme_monthly_attack":
            return type(result)(
                pattern=result.pattern,
                matched=False,
                status="not_matched",
                evidence=result.evidence,
            )
        return result

    monkeypatch.setattr(service.detectors, "detect_pattern", no_theme_match)
    monkeypatch.setattr(
        service.detectors,
        "evaluate_pool_state",
        lambda _bars: SimpleNamespace(
            state="healthy",
            evidence={"as_of_month": "2026-06"},
        ),
    )

    summary = service.run_daily(
        conn,
        _Registry(industry="银行"),
        "2026-06-30",
        input_by="pytest",
        months=39,
        min_market_rows=1,
    )

    assert not any(
        item["strategy_type"] == "theme_monthly_attack"
        for item in summary["candidates"]
    )
    current = pool.get_open(conn, "600000", "theme_monthly_attack")
    assert current["status"] == "risk"
    assert current["source_meta"]["mainline_match"] is False
    assert summary["counts"]["pool_risk"] == 1


def test_open_theme_without_current_match_keeps_missing_mainline_run_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _conn()
    conn.execute("DELETE FROM daily_volume_concentration")
    conn.commit()
    monkeypatch.setattr(service, "_is_historical_scan", lambda _scan_date: False)
    _seed_open(
        conn,
        strategy="theme_monthly_attack",
        status="active",
    )
    original_detect = service.detectors.detect_pattern

    def no_theme_match(strategy, bars):
        result = original_detect(strategy, bars)
        if strategy == "theme_monthly_attack":
            return type(result)(
                pattern=result.pattern,
                matched=False,
                status="not_matched",
                evidence=result.evidence,
            )
        return result

    monkeypatch.setattr(service.detectors, "detect_pattern", no_theme_match)

    summary = service.run_daily(
        conn,
        _Registry(),
        "2026-06-30",
        input_by="pytest",
        months=39,
        min_market_rows=1,
    )

    assert not any(
        item["strategy_type"] == "theme_monthly_attack"
        for item in summary["candidates"]
    )
    assert summary["source_status"]["mainline"] == "missing"
    assert summary["status"] == "partial"


def test_missing_mainline_evidence_preserves_existing_theme_status() -> None:
    conn = _conn()
    conn.execute("DELETE FROM daily_volume_concentration")
    conn.commit()
    _seed_open(
        conn,
        strategy="theme_monthly_attack",
        status="active",
    )

    summary = service.run_daily(
        conn,
        _Registry(),
        "2026-06-30",
        input_by="pytest",
        months=39,
        min_market_rows=1,
    )

    candidate = next(
        item
        for item in summary["candidates"]
        if item["strategy_type"] == "theme_monthly_attack"
    )
    assert summary["source_status"]["mainline"] == "missing"
    assert candidate["pool_status"] == "active"


def test_historical_scan_does_not_promote_theme_with_current_industry_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _conn()
    monkeypatch.setattr(service, "_is_historical_scan", lambda _scan_date: True)

    summary = service.run_daily(
        conn,
        _Registry(),
        "2026-06-30",
        input_by="pytest",
        months=39,
        min_market_rows=1,
    )

    theme = next(
        item
        for item in summary["candidates"]
        if item["strategy_type"] == "theme_monthly_attack"
    )
    assert summary["source_status"]["industry_map"] == "not_as_of"
    assert theme["mainline_match"] is None
    assert theme["pool_status"] == "technical_candidate"


def test_transitions_have_from_and_to_status_and_omit_refreshes() -> None:
    conn = _conn()
    first = service.run_daily(
        conn,
        _Registry(),
        "2026-06-30",
        input_by="pytest",
        months=39,
        min_market_rows=1,
    )

    assert first["transitions"]
    assert all("from_status" in item and "to_status" in item for item in first["transitions"])

    second = service.run_daily(
        conn,
        _Registry(),
        "2026-06-30",
        input_by="pytest",
        months=39,
        min_market_rows=1,
    )

    assert second["transitions"] == []


def test_historical_scan_before_pool_tail_fails_before_sources_or_run_write() -> None:
    conn = _conn()
    pool.record(
        conn,
        stock_code="600000",
        stock_name="浦发银行",
        strategy_type="fundamental_monthly_trend",
        status="active",
        signal_month="2026-07",
        date="2026-07-02",
        technical_evidence={"seed": True},
    )
    conn.commit()

    class _NoSourceRegistry:
        def call(self, *_args):
            pytest.fail("时间水位失败必须发生在任何外部采集前")

    with pytest.raises(
        service.MonthlyPatternTemporalOrderError,
        match="早于月线状态水位 2026-07-02",
    ):
        service.run_daily(
            conn,
            _NoSourceRegistry(),
            "2026-06-30",
            input_by="pytest",
            months=39,
            min_market_rows=1,
        )

    current = pool.get_open(conn, "600000", "fundamental_monthly_trend")
    assert current["status"] == "active"
    assert current["last_seen_date"] == "2026-07-02"
    assert repository.get_run(conn, "2026-06-30") is None


def test_historical_scan_before_empty_candidate_run_tail_also_fails_closed() -> None:
    conn = _conn()
    repository.save_run(
        conn,
        scan_date="2026-07-02",
        signal_month="2026-06",
        status="complete",
        input_by="pytest",
        source_status={"monthly_bars": "success"},
        counts={"matched_candidates": 0},
        error=None,
    )
    conn.commit()
    assert pool.list_pool(conn) == []

    with pytest.raises(
        service.MonthlyPatternTemporalOrderError,
        match="早于月线状态水位 2026-07-02",
    ):
        service.run_daily(
            conn,
            _Registry(),
            "2026-06-30",
            input_by="pytest",
            months=39,
            min_market_rows=1,
        )

    assert repository.get_run(conn, "2026-06-30") is None
    assert pool.list_pool(conn) == []


def test_state_watermark_is_rechecked_after_sources_before_any_pool_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _conn()
    original_industry_context = service._industry_context

    def advance_newer_run_during_sources(*args, **kwargs):
        context = original_industry_context(*args, **kwargs)
        repository.save_run(
            conn,
            scan_date="2026-07-02",
            signal_month="2026-06",
            status="complete",
            input_by="concurrent",
            source_status={"monthly_bars": "success"},
            counts={"matched_candidates": 0},
            error=None,
        )
        conn.commit()
        return context

    monkeypatch.setattr(
        service,
        "_industry_context",
        advance_newer_run_during_sources,
    )

    with pytest.raises(
        service.MonthlyPatternTemporalOrderError,
        match="早于月线状态水位 2026-07-02",
    ):
        service.run_daily(
            conn,
            _Registry(),
            "2026-06-30",
            input_by="pytest",
            months=39,
            min_market_rows=1,
        )

    assert repository.get_run(conn, "2026-06-30") is None
    assert repository.get_run(conn, "2026-07-02")["input_by"] == "concurrent"
    assert pool.list_pool(conn) == []


@pytest.mark.parametrize("failure_stage", ["detection", "pool_update", "run_audit"])
def test_unexpected_scan_failure_rolls_back_pool_and_records_failed_run(
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    conn = _conn()
    marker = f"{failure_stage} exploded"

    if failure_stage == "detection":
        def fail_detection(*args, **kwargs):
            raise RuntimeError(marker)

        monkeypatch.setattr(service.detectors, "detect_pattern", fail_detection)
    elif failure_stage == "pool_update":
        original_record = service.pool.record
        calls = 0

        def fail_second_record(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError(marker)
            return original_record(*args, **kwargs)

        monkeypatch.setattr(service.pool, "record", fail_second_record)
    else:
        original_save_run = service.repository.save_run
        calls = 0

        def fail_first_audit(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError(marker)
            return original_save_run(*args, **kwargs)

        monkeypatch.setattr(service.repository, "save_run", fail_first_audit)

    summary = service.run_daily(
        conn,
        _Registry(),
        "2026-06-30",
        input_by="pytest",
        months=39,
        min_market_rows=1,
    )

    assert summary["status"] == "failed"
    assert summary["candidates"] == []
    assert marker in summary["error"]
    assert pool.list_pool(conn) == []
    run = repository.get_run(conn, "2026-06-30")
    assert run["status"] == "failed"
    assert marker in run["error"]
