"""TushareProvider.get_margin_data：完整性保证（窗口区间查询 + 回退到最近完整交易日）。

mock pro.margin（返回窗口区间 df），无 token。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from providers.tushare_provider import TushareProvider


def _row(trade_date, ex, rzye, rqye, rzrqye):
    return {
        "trade_date": trade_date,
        "exchange_id": ex,
        "rzye": rzye,
        "rqye": rqye,
        "rzrqye": rzrqye,
    }


@pytest.fixture
def provider_with_margin_df():
    """绕过 initialize，仅注入 mock pro。"""
    ts = object.__new__(TushareProvider)
    ts.name = "tushare"
    ts.config = {}
    ts.pro = MagicMock()
    return ts


def test_complete_latest_date_returned_as_is(provider_with_margin_df):
    """请求日当天三所齐全 → 直接返回当天，is_complete=True。"""
    df = pd.DataFrame(
        [
            _row("20260617", "SSE", 1.0e11, 2.0e9, 1.02e11),
            _row("20260617", "SZSE", 9.0e10, 1.0e9, 9.1e10),
            _row("20260617", "BSE", 1.0e9, 1.0e7, 1.01e9),
            _row("20260616", "SSE", 9.9e10, 2.0e9, 1.01e11),
            _row("20260616", "SZSE", 8.9e10, 1.0e9, 9.0e10),
            _row("20260616", "BSE", 0.9e9, 1.0e7, 0.91e9),
        ]
    )
    provider_with_margin_df.pro.margin.return_value = df

    r = TushareProvider.get_margin_data(provider_with_margin_df, "2026-06-17")
    assert r.success
    assert r.data["trade_date"] == "2026-06-17"
    assert r.data["requested_date"] == "2026-06-17"
    assert r.data["market_scope"] == "BSE+SSE+SZSE"
    assert len(r.data["exchanges"]) == 3
    # 三所合计：1.0e11+9.0e10+1.0e9 = 1.91e11 → 1910 亿
    assert r.data["total_rzye_yi"] == pytest.approx(1910.0)
    # 区间查询（非单日 trade_date）
    _, kwargs = provider_with_margin_df.pro.margin.call_args
    assert "start_date" in kwargs and "end_date" in kwargs
    assert kwargs["end_date"] == "20260617"


def test_incomplete_latest_falls_back_to_complete_date(provider_with_margin_df):
    """核心修复：最新日仅沪市（SZSE/BSE 滞后）→ 回退到上一完整日，避免总额腰斩。"""
    df = pd.DataFrame(
        [
            # 最新日仅 SSE（深市/北交所未发布）
            _row("20260618", "SSE", 1.5e11, 2.0e9, 1.52e11),
            # 上一日三所齐全
            _row("20260617", "SSE", 1.0e11, 2.0e9, 1.02e11),
            _row("20260617", "SZSE", 9.0e10, 1.0e9, 9.1e10),
            _row("20260617", "BSE", 1.0e9, 1.0e7, 1.01e9),
        ]
    )
    provider_with_margin_df.pro.margin.return_value = df

    r = TushareProvider.get_margin_data(provider_with_margin_df, "2026-06-18")
    assert r.success
    # 回退到 06-17，但 requested_date 仍记录 06-18
    assert r.data["trade_date"] == "2026-06-17"
    assert r.data["requested_date"] == "2026-06-18"
    # 三所合计 1910 亿，而非仅沪市 1500 亿（腰斩已修）
    assert r.data["total_rzye_yi"] == pytest.approx(1910.0)
    assert len(r.data["exchanges"]) == 3


def test_no_complete_date_returns_error(provider_with_margin_df):
    """窗口内无任何完整日 → 返回 error，半额绝不冒充全市场总额。"""
    df = pd.DataFrame(
        [
            _row("20260618", "SSE", 1.5e11, 2.0e9, 1.52e11),
            # 历史里出现过 SZSE/BSE → 应到集合含三所，但没有任何一天三所齐全
            _row("20260617", "SSE", 1.0e11, 2.0e9, 1.02e11),
            _row("20260617", "SZSE", 9.0e10, 1.0e9, 9.1e10),
            _row("20260616", "BSE", 1.0e9, 1.0e7, 1.01e9),
        ]
    )
    provider_with_margin_df.pro.margin.return_value = df

    r = TushareProvider.get_margin_data(provider_with_margin_df, "2026-06-18")
    assert not r.success
    assert "无完整融资融券数据" in r.error


def test_whole_window_single_exchange_errors(provider_with_margin_df):
    """系统性降级：整窗只返沪市 SSE → 应到集合以沪深为下限不塌缩 → 返 error，半额不自我认证完整。"""
    df = pd.DataFrame(
        [
            _row("20260618", "SSE", 1.5e11, 2.0e9, 1.52e11),
            _row("20260617", "SSE", 1.49e11, 2.0e9, 1.51e11),
            _row("20260616", "SSE", 1.48e11, 2.0e9, 1.50e11),
        ]
    )
    provider_with_margin_df.pro.margin.return_value = df

    r = TushareProvider.get_margin_data(provider_with_margin_df, "2026-06-18")
    assert not r.success
    assert "无完整融资融券数据" in r.error


def test_empty_df(provider_with_margin_df):
    provider_with_margin_df.pro.margin.return_value = pd.DataFrame()
    r = TushareProvider.get_margin_data(provider_with_margin_df, "2026-06-18")
    assert not r.success
    assert "无融资融券汇总" in r.error


def test_exception(provider_with_margin_df):
    provider_with_margin_df.pro.margin.side_effect = RuntimeError("network")
    r = TushareProvider.get_margin_data(provider_with_margin_df, "2026-06-18")
    assert not r.success
    assert "network" in r.error
