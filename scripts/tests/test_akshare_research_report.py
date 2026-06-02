"""get_research_report_list 扩列（additive）：原 5 基础键不变 + 评级变化/前次/目标价 raw（F6/M5）。"""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from providers.akshare_provider import AkshareProvider


@pytest.fixture
def ak() -> AkshareProvider:
    p = AkshareProvider({})
    p._initialized = True
    p.ak = MagicMock()
    return p


def test_extended_fields_additive(ak: AkshareProvider):
    ak.ak.stock_rank_forecast_cninfo.return_value = pd.DataFrame([{
        "证券代码": "000001", "证券简称": "平安银行", "研究机构简称": "广发证券",
        "投资评级": "买入", "发布日期": "2026-05-29", "评级变化": "维持",
        "前一次投资评级": "买入", "目标价格-下限": 14.35, "目标价格-上限": 14.35,
    }])
    r = ak.get_research_report_list("2026-05-29")
    assert r.success and len(r.data) == 1
    row = r.data[0]
    # 原 5 基础键不变（M5：唯一消费方 market.py:675 只读 stock_code/stock_name，向后兼容）
    for k in ("stock_code", "stock_name", "institution", "rating", "date"):
        assert k in row
    assert row["stock_code"] == "000001"
    assert row["rating"] == "买入"
    assert row["date"] == "2026-05-29"
    # 扩列：评级方向直接复用源列（F6，不自算）
    assert row["rating_change"] == "维持"
    assert row["prev_rating"] == "买入"
    # 目标价入 raw（渲染层负责剔除，红线约束生成不约束取数）
    assert row["target_price_low"] == 14.35
    assert row["target_price_high"] == 14.35


def test_missing_extended_cols_graceful(ak: AkshareProvider):
    """老接口仅返 5 列时，扩列退化为空串/None，不抛。"""
    ak.ak.stock_rank_forecast_cninfo.return_value = pd.DataFrame([{
        "证券代码": "600519", "证券简称": "贵州茅台", "研究机构简称": "中信证券",
        "投资评级": "增持", "发布日期": "2026-05-29",
    }])
    r = ak.get_research_report_list("2026-05-29")
    assert r.success and len(r.data) == 1
    assert r.data[0]["rating_change"] == ""
    assert r.data[0]["prev_rating"] == ""
    assert r.data[0]["target_price_low"] is None


def test_empty_returns_empty_list(ak: AkshareProvider):
    ak.ak.stock_rank_forecast_cninfo.return_value = pd.DataFrame()
    r = ak.get_research_report_list("2026-05-29")
    assert r.success and r.data == []


def test_get_research_reports_keyerror_is_empty_not_error(ak: AkshareProvider):
    """零研报票：akshare 内部空表取 infoCode 列抛 KeyError，应按"无数据"返回成功空列表，
    而非冒充 error（否则 registry 误判失败 → 打 warning + 无意义降级）。"""
    ak.ak.stock_research_report_em.side_effect = KeyError("infoCode")
    r = ak.get_research_reports("000509.SZ")
    assert r.success and r.data == []
    assert r.error == ""
