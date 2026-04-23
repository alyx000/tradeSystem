"""
板块资金流（get_sector_fund_flow）单元测试
"""
from unittest.mock import MagicMock

import pandas as pd
import pytest

from providers.akshare_provider import AkshareProvider


def _make_provider():
    p = AkshareProvider()
    p._initialized = True
    p.ak = MagicMock()
    return p


def test_get_sector_fund_flow_parses_current_akshare_columns():
    p = _make_provider()
    p.ak.stock_sector_fund_flow_rank = MagicMock(return_value=pd.DataFrame({
        "名称": ["半导体", "通信设备"],
        "今日涨跌幅": [2.93, 3.11],
        "今日主力净流入-净额": [15158000000, 14622000000],
    }))

    r = p.get_sector_fund_flow("2026-04-22")

    assert r.success
    assert r.data[0]["name"] == "半导体"
    assert r.data[0]["net_inflow_billion"] == pytest.approx(151.58, abs=0.01)
    assert r.data[0]["change_pct"] == pytest.approx(2.93, abs=0.01)


def test_get_sector_fund_flow_falls_back_to_ths_industry_flow():
    p = _make_provider()
    p.ak.stock_sector_fund_flow_rank = MagicMock(side_effect=Exception("eastmoney down"))
    p.ak.stock_fund_flow_industry = MagicMock(return_value=pd.DataFrame({
        "行业": ["元件", "通信设备"],
        "行业-涨跌幅": [3.35, 3.11],
        "净额": [64.10, 146.22],
    }))

    r = p.get_sector_fund_flow("2026-04-22")

    assert r.success
    assert r.source == "akshare:stock_fund_flow_industry"
    assert r.data[0]["name"] == "元件"
    assert r.data[0]["net_inflow_billion"] == pytest.approx(64.10, abs=0.01)
    assert r.data[0]["change_pct"] == pytest.approx(3.35, abs=0.01)
