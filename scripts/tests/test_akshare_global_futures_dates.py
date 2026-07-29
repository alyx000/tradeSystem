"""外盘期货日期化快照：目标日截断与实时降级。"""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from providers.akshare_provider import AkshareProvider


@pytest.fixture
def ak() -> AkshareProvider:
    provider = AkshareProvider({})
    provider._initialized = True
    provider.ak = MagicMock()
    return provider


def _history(
    name: str,
    rows: list[tuple[str, float, float]],
) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "日期": source_date,
                "代码": "TEST",
                "名称": name,
                "最新价": close,
                "涨幅": change_pct,
            }
            for source_date, close, change_pct in rows
        ]
    )


def test_a50_uses_latest_dated_row_not_later_than_report_date(
    ak: AkshareProvider,
):
    ak.ak.futures_global_hist_em.return_value = _history(
        "A50期指当月连续",
        [
            ("2026-07-27", 15215.0, 0.10),
            ("2026-07-28", 14819.0, -2.59),
            ("2026-07-29", 14826.0, 0.05),
        ],
    )

    result = ak.get_global_index("a50", "2026-07-28")

    assert result.success
    assert result.source == "akshare:futures_global_hist_em"
    assert result.data == {
        "name": "A50期指当月连续",
        "close": 14819.0,
        "change_pct": -2.59,
        "as_of": "2026-07-28",
    }
    ak.ak.futures_global_hist_em.assert_called_once_with(symbol="CN00Y")
    ak.ak.futures_global_spot_em.assert_not_called()


def test_a50_retries_transient_history_failure_before_fetch_only(
    ak: AkshareProvider,
):
    ak.ak.futures_global_hist_em.side_effect = [
        RuntimeError("transient"),
        _history(
            "A50期指当月连续",
            [("2026-07-28", 14819.0, -2.59)],
        ),
    ]

    result = ak.get_global_index("a50", "2026-07-28")

    assert result.success
    assert result.data["as_of"] == "2026-07-28"
    assert ak.ak.futures_global_hist_em.call_count == 2
    ak.ak.futures_global_spot_em.assert_not_called()


def test_a50_without_date_keeps_realtime_snapshot_semantics(
    ak: AkshareProvider,
):
    ak.ak.futures_global_spot_em.return_value = pd.DataFrame(
        [
            {
                "代码": "CN00Y",
                "名称": "A50期指当月连续",
                "最新价": 14826.0,
                "涨跌幅": 0.05,
            }
        ]
    )

    result = ak.get_global_index("a50")

    assert result.success
    assert result.source == "akshare:futures_global_spot_em"
    assert result.data["close"] == 14826.0
    assert "as_of" not in result.data
    ak.ak.futures_global_hist_em.assert_not_called()
    ak.ak.futures_global_spot_em.assert_called_once_with()


@pytest.mark.parametrize(
    ("name", "symbol", "display_name"),
    [
        ("gold", "QO00Y", "迷你黄金"),
        ("crude_oil", "QM00Y", "迷你原油"),
        ("copper", "HG00Y", "COMEX铜"),
    ],
)
def test_commodity_uses_dated_continuous_contract(
    ak: AkshareProvider,
    name: str,
    symbol: str,
    display_name: str,
):
    ak.ak.futures_global_hist_em.return_value = _history(
        display_name,
        [
            ("2026-07-28", 100.0, -1.25),
            ("2026-07-29", 101.0, 1.00),
        ],
    )

    result = ak.get_commodity(name, "2026-07-28")

    assert result.success
    assert result.data["as_of"] == "2026-07-28"
    assert result.data["close"] == 100.0
    assert result.data["change_pct"] == -1.25
    ak.ak.futures_global_hist_em.assert_called_once_with(symbol=symbol)
    ak.ak.futures_global_spot_em.assert_not_called()


def test_history_failure_falls_back_to_fetch_only_spot_snapshot(
    ak: AkshareProvider,
):
    ak.ak.futures_global_hist_em.side_effect = RuntimeError("history unavailable")
    ak.ak.futures_foreign_hist.side_effect = RuntimeError("backup unavailable")
    ak.ak.futures_global_spot_em.return_value = pd.DataFrame(
        [
            {
                "代码": "HG00Y",
                "名称": "COMEX铜",
                "最新价": 6.3045,
                "涨跌幅": -1.17,
            }
        ]
    )

    result = ak.get_commodity("copper", "2026-07-28")

    assert result.success
    assert result.source == "akshare:futures_global_spot_em"
    assert result.data["close"] == 6.3045
    assert result.data["change_pct"] == -1.17
    assert "as_of" not in result.data


def test_commodity_without_date_keeps_realtime_snapshot_semantics(
    ak: AkshareProvider,
):
    ak.ak.futures_global_spot_em.return_value = pd.DataFrame(
        [
            {
                "代码": "QO00Y",
                "名称": "迷你黄金",
                "最新价": 4024.0,
                "涨跌幅": -1.30,
            }
        ]
    )

    result = ak.get_commodity("gold")

    assert result.success
    assert result.source == "akshare:futures_global_spot_em"
    assert result.data["close"] == 4024.0
    assert "as_of" not in result.data
    ak.ak.futures_global_hist_em.assert_not_called()
    ak.ak.futures_foreign_hist.assert_not_called()
    ak.ak.futures_global_spot_em.assert_called_once_with()


@pytest.mark.parametrize(
    ("name", "symbol", "display_name", "closes", "expected_close"),
    [
        ("gold", "GC", "COMEX黄金", (4138.0, 4106.3), 4106.3),
        ("crude_oil", "CL", "NYMEX原油", (81.9, 78.43), 78.43),
        ("copper", "HG", "COMEX铜", (639.6, 636.55), 6.3655),
    ],
)
def test_commodity_uses_sina_dated_backup_before_fetch_only(
    ak: AkshareProvider,
    name: str,
    symbol: str,
    display_name: str,
    closes: tuple[float, float],
    expected_close: float,
):
    ak.ak.futures_global_hist_em.side_effect = RuntimeError("history unavailable")
    ak.ak.futures_foreign_hist.return_value = pd.DataFrame(
        [
            {"date": "2026-07-27", "close": closes[0]},
            {"date": "2026-07-28", "close": closes[1]},
            {"date": "2026-07-29", "close": closes[1] + 1},
        ]
    )

    result = ak.get_commodity(name, "2026-07-28")

    assert result.success
    assert result.source == "akshare:futures_foreign_hist"
    assert result.data["name"] == display_name
    assert result.data["as_of"] == "2026-07-28"
    assert result.data["close"] == expected_close
    assert result.data["change_pct"] == pytest.approx(
        (closes[1] - closes[0]) / closes[0] * 100,
        abs=0.0001,
    )
    ak.ak.futures_foreign_hist.assert_called_once_with(symbol=symbol)
    ak.ak.futures_global_spot_em.assert_not_called()


def test_invalid_or_future_only_history_does_not_forge_target_date(
    ak: AkshareProvider,
):
    ak.ak.futures_global_hist_em.return_value = _history(
        "A50期指当月连续",
        [
            ("not-a-date", 14800.0, -1.0),
            ("2026-07-29", 14826.0, 0.05),
        ],
    )
    ak.ak.futures_global_spot_em.return_value = pd.DataFrame(
        [
            {
                "代码": "CN00Y",
                "名称": "A50期指当月连续",
                "最新价": 14826.0,
                "涨跌幅": 0.05,
            }
        ]
    )

    result = ak.get_global_index("a50", "2026-07-28")

    assert result.success
    assert result.source == "akshare:futures_global_spot_em"
    assert "as_of" not in result.data
