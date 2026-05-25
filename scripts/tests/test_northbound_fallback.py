"""AkshareProvider.get_northbound 降级路径回归测试。

背景：旧实现调用 akshare 1.18.x 已移除的 stock_hsgt_north_net_flow_in_em，
盘后 moneyflow_hsgt 走 akshare 降级时恒抛 AttributeError。本测试守护改用
现存 stock_hsgt_hist_em 的实现，并覆盖「停更日 NaN 不得伪装成 0.0 净流入」。

mock akshare，不触外网。
"""
from __future__ import annotations

import datetime as dt
from unittest.mock import Mock

import pandas as pd
import pytest

from providers.akshare_provider import AkshareProvider


def _provider(hist_df: pd.DataFrame) -> AkshareProvider:
    p = AkshareProvider({})
    p._initialized = True
    # spec 仅含新接口：访问旧名 stock_hsgt_north_net_flow_in_em 会真实抛 AttributeError，
    # 复现生产环境，确保实现已不再引用被移除的函数。
    p.ak = Mock(spec=["stock_hsgt_hist_em"])
    p.ak.stock_hsgt_hist_em.return_value = hist_df
    return p


def _hist(rows: list[tuple[str, float]]) -> pd.DataFrame:
    """构造 stock_hsgt_hist_em 形态的 df：日期为 datetime.date，金额单位亿元。"""
    return pd.DataFrame(
        {
            "日期": [dt.date.fromisoformat(d) for d, _ in rows],
            "当日成交净买额": [v for _, v in rows],
            "当日资金流入": [v for _, v in rows],
        }
    )


def test_get_northbound_matches_requested_date_in_yi():
    """按请求日期精确匹配，当日成交净买额单位为亿元，直接作为 net_buy_billion。"""
    p = _provider(_hist([("2024-08-15", 122.0584), ("2024-08-16", -67.7499)]))

    r = p.get_northbound("2024-08-16")

    assert r.success
    assert r.data["date"] == "2024-08-16"
    assert r.data["net_buy_billion"] == pytest.approx(-67.75, abs=0.01)
    assert "stock_hsgt_hist_em" in r.source
    p.ak.stock_hsgt_hist_em.assert_called_once_with(symbol="北向资金")


def test_get_northbound_date_absent_returns_error():
    """请求日期不在历史数据中（如停更后已无该日行）→ 优雅返回 error，不抛异常。"""
    p = _provider(_hist([("2024-08-15", 122.0584)]))

    r = p.get_northbound("2026-05-25")

    assert not r.success
    assert r.data is None
    assert "2026-05-25" in r.error


def test_get_northbound_nan_value_is_no_data_not_zero():
    """停更日（2024-08-16 后）行存在但金额为 NaN → 视为无数据，绝不伪装成 0.0 净流入。"""
    p = _provider(_hist([("2026-05-22", float("nan"))]))

    r = p.get_northbound("2026-05-22")

    assert not r.success
    assert r.data is None


def test_get_northbound_empty_df_returns_error():
    """接口返回空 df → 返回 error，不抛异常。"""
    p = _provider(pd.DataFrame())

    r = p.get_northbound("2024-08-16")

    assert not r.success
