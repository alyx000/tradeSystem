"""get_us_rating_changes：mock yfinance；registry 路由（M4）；单只异常不中断（M9）；冻结返空。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from providers.akshare_provider import AkshareProvider
from providers.registry import ProviderRegistry

WIN = ("2026-05-28", "2026-05-29")


@pytest.fixture
def ak() -> AkshareProvider:
    p = AkshareProvider({})
    p._initialized = True
    p.ak = MagicMock()
    return p


def _ud(rows):
    idx = pd.to_datetime([r[0] for r in rows])
    return pd.DataFrame({
        "Firm": [r[1] for r in rows],
        "FromGrade": [r[2] for r in rows],
        "ToGrade": [r[3] for r in rows],
        "Action": [r[4] for r in rows],
        "currentPriceTarget": [r[5] for r in rows],
        "priorPriceTarget": [r[6] for r in rows],
    }, index=idx)


@patch("yfinance.Ticker")
def test_window_inner_events_returned(mock_ticker, ak: AkshareProvider):
    mock_ticker.return_value.upgrades_downgrades = _ud([
        ("2026-05-29", "Morgan Stanley", "Hold", "Buy", "up", 300.0, 250.0),
    ])
    r = ak.get_us_rating_changes(["NVDA"], WIN)
    assert r.success and r.source == "yfinance"
    assert len(r.data) == 1
    assert r.data[0]["ticker"] == "NVDA"
    assert r.data[0]["source_quality"] == "high"
    assert r.data[0]["action"] == "up"
    assert r.data[0]["current_pt"] == 300.0  # raw 留存（渲染层负责剔除）


@patch("yfinance.Ticker")
def test_frozen_meta_like_returns_empty(mock_ticker, ak: AkshareProvider):
    """META 式整体冻结（仅 2024）→ 窗口过滤后空，不报错。"""
    mock_ticker.return_value.upgrades_downgrades = _ud([
        ("2024-09-30", "Cantor Fitzgerald", "OW", "OW", "reit", None, None),
    ])
    r = ak.get_us_rating_changes(["META"], WIN)
    assert r.success and r.data == []


@patch("yfinance.Ticker")
def test_single_ticker_error_does_not_break_others(mock_ticker, ak: AkshareProvider):
    """M9：单 ticker 拉取异常（如 429）跳过，不中断其余。"""
    def fake(sym):
        if sym == "BADX":
            raise RuntimeError("429 rate limited")
        m = MagicMock()
        m.upgrades_downgrades = _ud([("2026-05-29", "MS", "Hold", "Buy", "up", 1, 1)])
        return m
    mock_ticker.side_effect = fake
    r = ak.get_us_rating_changes(["BADX", "NVDA"], WIN)
    assert r.success
    assert [d["ticker"] for d in r.data] == ["NVDA"]


@patch("yfinance.Ticker")
def test_maintain_only_window_returns_empty(mock_ticker, ak: AkshareProvider):
    mock_ticker.return_value.upgrades_downgrades = _ud([
        ("2026-05-29", "A", "Buy", "Buy", "main", 1, 1),
    ])
    r = ak.get_us_rating_changes(["AAPL"], WIN)
    assert r.success and r.data == []


def test_bad_window_returns_error(ak: AkshareProvider):
    r = ak.get_us_rating_changes(["AAPL"], (None, None))
    assert not r.success
    assert "date_window" in (r.error or "")


def test_bad_window_single_element(ak: AkshareProvider):
    r = ak.get_us_rating_changes(["AAPL"], ("2026-05-28",))
    assert not r.success
    assert "date_window" in (r.error or "")


def test_bad_window_empty(ak: AkshareProvider):
    r = ak.get_us_rating_changes(["AAPL"], ())
    assert not r.success


def test_bad_window_three_elements(ak: AkshareProvider):
    """M-2：3 元素窗口不静默用前两个，明确报错（防调用方参数组装错误被吞）。"""
    r = ak.get_us_rating_changes(["AAPL"], ("2026-05-27", "2026-05-28", "2026-05-29"))
    assert not r.success
    assert "date_window" in (r.error or "")


@patch("yfinance.Ticker")
def test_all_tickers_fail_returns_error_not_empty(mock_ticker, ak: AkshareProvider):
    """M-1：全部 ticker 抓取失败（限流）→ 返 error，不返 success 空列表冒充"无变动"。"""
    mock_ticker.side_effect = RuntimeError("429 rate limited")
    r = ak.get_us_rating_changes(["NVDA", "AAPL", "MSFT"], WIN)
    assert not r.success
    assert "拉取失败" in (r.error or "")


@patch("yfinance.Ticker")
def test_partial_failure_still_returns_successes(mock_ticker, ak: AkshareProvider):
    """部分失败：成功的照常返回，不因个别 429 整体判失败。"""
    def fake(sym):
        if sym == "BADX":
            raise RuntimeError("429")
        m = MagicMock()
        m.upgrades_downgrades = _ud([("2026-05-29", "MS", "Hold", "Buy", "up", 1, 1)])
        return m
    mock_ticker.side_effect = fake
    r = ak.get_us_rating_changes(["BADX", "NVDA"], WIN)
    assert r.success
    assert [d["ticker"] for d in r.data] == ["NVDA"]


@patch("yfinance.Ticker")
def test_frozen_logs_when_old(mock_ticker, ak: AkshareProvider, caplog):
    """H5：最新评级距窗口末 > 120 天 → 打"疑似冻结"日志（供精选池体检）。"""
    import logging
    mock_ticker.return_value.upgrades_downgrades = _ud([
        ("2024-01-01", "A", "Buy", "Strong Buy", "up", None, None),
    ])
    with caplog.at_level(logging.INFO):
        r = ak.get_us_rating_changes(["OLDX"], WIN)
    assert r.success and r.data == []
    assert "疑似冻结" in caplog.text


@patch("yfinance.Ticker")
def test_recent_no_change_not_flagged_frozen(mock_ticker, ak: AkshareProvider, caplog):
    """对照：窗口内无方向变动但标的并不冻结（最近有维持事件）→ 不打冻结日志。"""
    import logging
    mock_ticker.return_value.upgrades_downgrades = _ud([
        ("2026-05-29", "A", "Buy", "Buy", "main", 1, 1),
    ])
    with caplog.at_level(logging.INFO):
        r = ak.get_us_rating_changes(["AAPL"], WIN)
    assert r.success and r.data == []
    assert "疑似冻结" not in caplog.text


def test_capability_declared(ak: AkshareProvider):
    assert "get_us_rating_changes" in ak.get_capabilities()
    assert ak.supports("get_us_rating_changes")


@patch("yfinance.Ticker")
def test_registry_routes_to_yfinance(mock_ticker, ak: AkshareProvider):
    """M4：经 registry.call 路由，断 source=='yfinance' 且方法真被调（非纯字符串断言）。

    防 capability 漏声明/方法名拼写不一致导致 registry 静默跳过。
    """
    mock_ticker.return_value.upgrades_downgrades = _ud([
        ("2026-05-29", "Morgan Stanley", "Hold", "Buy", "up", 1, 1),
    ])
    reg = ProviderRegistry()
    reg.register(ak)
    r = reg.call("get_us_rating_changes", ["NVDA"], WIN)
    assert r.success and r.source == "yfinance"
    assert r.data[0]["ticker"] == "NVDA"
