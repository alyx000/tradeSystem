"""串阳首阴扫描：只推已经出现第一根阴线的主线票。"""
from __future__ import annotations

import logging
import sqlite3
from types import SimpleNamespace

from db.migrate import migrate
from services.volume_concentration import repo as vc_repo


def _bar(day: int, open_: float, close: float, pct: float, amount: float = 100.0) -> dict:
    return {
        "trade_date": f"2026-06-{day:02d}",
        "open": open_,
        "close": close,
        "high": max(open_, close),
        "low": min(open_, close),
        "pct_chg": pct,
        "amount": amount,
        "vol": amount * 10,
    }


def _bars_for_first_yin(today_amount: float = 180.0) -> list[dict]:
    return [
        _bar(1, 10.0, 10.1, 1.0, 90),
        _bar(2, 10.1, 10.0, -1.0, 92),
        _bar(3, 10.0, 10.2, 2.0, 95),
        _bar(4, 10.2, 10.5, 2.9, 100),
        _bar(5, 10.5, 10.9, 3.8, 105),
        _bar(6, 10.9, 11.2, 2.8, 110),
        _bar(7, 11.2, 11.7, 4.5, 115),
        _bar(8, 11.7, 12.2, 4.3, 120),
        _bar(9, 12.4, 12.0, -1.6, today_amount),
    ]


def test_detect_setup_matches_five_yang_then_first_yin() -> None:
    from services.string_yang import scanner

    matched, detail = scanner.detect_setup(_bars_for_first_yin(), "600001")

    assert matched is True
    assert detail["yang_count"] == 6
    assert detail["max_yang_pct"] == 4.5
    assert detail["today_amount_ratio_vs_prev5_max"] > 1.0
    assert detail["today_amount_ratio_vs_prev_day"] > 1.0
    assert detail["today_amount_ratio_vs_prev5"] > 1.4


def test_detect_setup_rejects_when_today_is_not_first_yin() -> None:
    from services.string_yang import scanner

    bars = _bars_for_first_yin()
    bars[-1] = _bar(9, 12.0, 12.5, 4.1, 180)

    matched, detail = scanner.detect_setup(bars, "600001")

    assert matched is False
    assert detail["reason"] == "today_not_yin"


def test_detect_setup_rejects_when_string_yang_has_too_large_bar() -> None:
    from services.string_yang import scanner

    bars = _bars_for_first_yin()
    bars[-3] = _bar(7, 11.2, 12.2, 8.1, 115)

    matched, detail = scanner.detect_setup(bars, "600001")

    assert matched is False
    assert detail["reason"] == "yang_pct_too_high"


def test_detect_setup_rejects_when_first_yin_is_not_recent_max_volume() -> None:
    from services.string_yang import scanner

    matched, detail = scanner.detect_setup(_bars_for_first_yin(today_amount=119), "600001")

    assert matched is False
    assert detail["reason"] == "yin_volume_not_recent_max"
    assert detail["today_amount_ratio_vs_prev5_max"] < 1.0


def test_detect_setup_rejects_when_price_position_is_too_high() -> None:
    from services.string_yang import scanner

    low_base = [
        {
            "trade_date": f"2026-04-{i:02d}",
            "open": 5.0,
            "close": 5.0,
            "high": 5.1,
            "low": 4.9,
            "pct_chg": 0.0,
            "amount": 50.0,
            "vol": 500.0,
        }
        for i in range(1, 52)
    ]
    bars = low_base + _bars_for_first_yin(today_amount=220)

    matched, detail = scanner.detect_setup(bars, "600001")

    assert matched is False
    assert detail["reason"] == "price_position_too_high"
    assert detail["price_ma_ratio"] > 1.08


def test_detect_setup_rejects_recent_limit_up_before_string_yang() -> None:
    from services.string_yang import scanner

    bars = _bars_for_first_yin()
    bars[0] = _bar(1, 10.0, 10.1, 9.9, 90)

    matched, detail = scanner.detect_setup(bars, "600001")

    assert matched is False
    assert detail["reason"] == "recent_limit_up"


class _Registry:
    def __init__(self, bars_by_code: dict[str, list[dict]]):
        self.bars_by_code = bars_by_code

    def call(self, name: str, *args, **kwargs):
        if name == "get_stock_sw_industry_map":
            return SimpleNamespace(success=True, data={
                "600001.SH": {"name": "主线首阴A", "sw_l2": "半导体"},
                "600002.SH": {"name": "主线首阴B", "sw_l2": "半导体"},
                "600003.SH": {"name": "非主线", "sw_l2": "银行"},
                "600005.SH": {"name": "ST风险", "sw_l2": "半导体"},
            })
        if name == "get_stock_daily_range":
            code = args[0].split(".")[0]
            return SimpleNamespace(success=True, data=self.bars_by_code.get(code, []))
        if name == "get_ths_member":
            return SimpleNamespace(success=True, data=[
                {"con_code": "600003.SH", "index_name": "CPO"},
                {"con_code": "600004.SH", "index_name": "CPO"},
            ])
        if name == "get_concept_moneyflow_ths":
            return SimpleNamespace(success=True, data=[
                {"name": "CPO", "net_amount": 8_000_000_000},
            ])
        raise AssertionError(f"unexpected provider call: {name}")


def _conn_with_main_sector() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    migrate(conn)
    vc_repo.save_concentration(conn, {
        "date": "2026-06-09",
        "top_n": 20,
        "total_amount_billion": 1000,
        "sector_summary": [
            {"industry": "半导体", "amount_billion": 120},
            {"industry": "银行", "amount_billion": 10},
        ],
        "stocks": [],
        "source": {"provider": "pytest"},
    })
    return conn


def test_run_daily_returns_only_mainline_first_yin_sorted_by_amount_ratio() -> None:
    from services.string_yang import scanner

    bars_a = _bars_for_first_yin(today_amount=160)
    bars_b = _bars_for_first_yin(today_amount=240)
    bars_non_main = _bars_for_first_yin(today_amount=999)
    registry = _Registry({"600001": bars_a, "600002": bars_b, "600003": bars_non_main})
    conn = _conn_with_main_sector()

    result = scanner.run_daily(conn, registry, "2026-06-09", top_k=1)

    assert result["status"] == "ok"
    assert [c["code"] for c in result["candidates"]] == ["600002", "600001"]
    assert result["candidates"][0]["amount_ratio_vs_prev5"] > result["candidates"][1]["amount_ratio_vs_prev5"]
    assert result["rejects"]["not_main_sector"] == 1


def test_run_daily_excludes_st_or_delist_candidates() -> None:
    from services.string_yang import scanner

    bars_regular = _bars_for_first_yin(today_amount=160)
    bars_st = _bars_for_first_yin(today_amount=999)
    registry = _Registry({"600001": bars_regular, "600005": bars_st})
    conn = _conn_with_main_sector()

    result = scanner.run_daily(conn, registry, "2026-06-09", top_k=1)

    assert result["status"] == "ok"
    assert [c["code"] for c in result["candidates"]] == ["600001"]
    assert result["rejects"]["st_or_delist"] == 1


def test_run_daily_llm_mainline_allows_ths_concept_branch_candidate() -> None:
    from services.string_yang import scanner

    bars_main_l2 = _bars_for_first_yin(today_amount=160)
    bars_branch = _bars_for_first_yin(today_amount=220)
    registry = _Registry({"600001": bars_main_l2, "600003": bars_branch})
    conn = _conn_with_main_sector()

    def runner(_prompt, _payload):
        return {
            "main_l2": ["半导体"],
            "main_concepts": ["CPO"],
            "evidence": ["成交额集中在半导体，CPO 是老师点名分支"],
            "confidence": 0.8,
        }

    result = scanner.run_daily(
        conn,
        registry,
        "2026-06-09",
        top_k=1,
        use_llm=True,
        llm_runner=runner,
    )

    assert result["status"] == "ok"
    assert [c["code"] for c in result["candidates"]] == ["600003", "600001"]
    assert result["candidates"][0]["sw_l2"] == "银行"
    assert result["candidates"][0]["branch_concepts"] == ["CPO"]
    assert result["mainline"]["main_concepts"] == ["CPO"]
    assert result["mainline"]["status"] == "llm"


def test_run_daily_logs_stock_fetch_progress(caplog) -> None:
    from services.string_yang import scanner

    registry = _Registry({
        "600001": _bars_for_first_yin(today_amount=160),
        "600002": _bars_for_first_yin(today_amount=240),
    })
    conn = _conn_with_main_sector()
    caplog.set_level(logging.INFO, logger="services.string_yang.scanner")

    scanner.run_daily(conn, registry, "2026-06-09", top_k=1)

    assert "[string-yang] 扫描个股 1/2 600001 主线首阴A" in caplog.text
