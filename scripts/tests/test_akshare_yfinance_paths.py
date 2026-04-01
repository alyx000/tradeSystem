"""AkShareProvider：亚太 yfinance 与 get_us_tickers_overnight，mock yfinance，无东财。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from providers.akshare_provider import AkshareProvider


@pytest.fixture
def ak() -> AkshareProvider:
    p = AkshareProvider({})
    p._initialized = True
    p.ak = MagicMock()
    p._df_global_spot_em = None
    p._df_futures_global_spot_em = None
    return p


def _hist_df(closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2026-03-20", periods=len(closes), freq="D")
    return pd.DataFrame({"Close": closes}, index=idx)


@patch("yfinance.Ticker")
def test_get_global_index_hsi_uses_yfinance(mock_ticker, ak: AkshareProvider):
    mock_ticker.return_value.history.return_value = _hist_df([24000.0, 24500.0])
    r = ak.get_global_index("hsi")
    assert r.success
    assert r.data["close"] == 24500.0
    assert r.data["change_pct"] == pytest.approx(2.08, abs=0.02)
    assert "yfinance" in r.source


@patch("yfinance.Ticker")
def test_get_global_index_hstech_second_ticker_if_first_empty(mock_ticker, ak: AkshareProvider):
    t1 = MagicMock()
    t1.history.return_value = pd.DataFrame()
    t2 = MagicMock()
    t2.history.return_value = _hist_df([4600.0, 4700.0])
    mock_ticker.side_effect = [t1, t2]

    r = ak.get_global_index("hstech")
    assert r.success
    assert r.data["close"] == 4700.0
    assert mock_ticker.call_count == 2


@patch("yfinance.Ticker")
def test_get_global_index_kospi_uses_yfinance(mock_ticker, ak: AkshareProvider):
    mock_ticker.return_value.history.return_value = _hist_df([2550.0, 2600.0])
    r = ak.get_global_index("kospi")
    assert r.success
    assert r.data["close"] == 2600.0
    mock_ticker.assert_called()
    assert "^KS11" in str(mock_ticker.call_args)


@patch("yfinance.Ticker")
def test_get_us_tickers_overnight(mock_ticker, ak: AkshareProvider):
    def make_ticker(closes):
        m = MagicMock()
        m.history.return_value = _hist_df(closes)
        return m

    mock_ticker.side_effect = [
        make_ticker([27.0, 28.0]),
        make_ticker([35.0, 34.5]),
    ]

    r = ak.get_us_tickers_overnight(["KWEB", "FXI"])
    assert r.success
    assert r.data["KWEB"]["close"] == 28.0
    assert r.data["KWEB"]["change_pct"] > 0
    assert r.data["FXI"]["close"] == 34.5
    assert r.data["FXI"]["change_pct"] < 0


@patch("yfinance.Ticker")
def test_get_us_tickers_overnight_empty_history(mock_ticker, ak: AkshareProvider):
    mock_ticker.return_value.history.return_value = pd.DataFrame()
    r = ak.get_us_tickers_overnight(["KWEB"])
    assert r.success
    assert "error" in r.data["KWEB"]


# ---------------------------------------------------------------------------
# get_stock_announcements（东方财富 API）
# ---------------------------------------------------------------------------

def _mock_response(json_data, status_code=200):
    """构造 mock Response 对象。"""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status.return_value = None
    if status_code >= 400:
        from requests.exceptions import HTTPError
        resp.raise_for_status.side_effect = HTTPError(f"{status_code}")
    return resp


class TestGetStockAnnouncements:
    @patch("providers.akshare_provider.requests.get")
    def test_returns_announcements(self, mock_get, ak: AkshareProvider):
        mock_get.return_value = _mock_response({
            "data": {
                "total_hits": 2,
                "list": [
                    {"title": "关于回购股份的公告", "notice_date": "2026-03-27 00:00:00", "art_code": "AN202603271234"},
                    {"title": "董事会决议公告", "notice_date": "2026-03-25 00:00:00", "art_code": "AN202603251111"},
                ],
            }
        })

        r = ak.get_stock_announcements("600519.SH", "2026-03-20", "2026-03-27")
        assert r.success
        assert len(r.data) == 2
        assert r.data[0]["title"] == "关于回购股份的公告"
        assert r.data[0]["ann_date"] == "2026-03-27"
        assert "600519" in r.data[0]["url"]
        assert "AN202603271234" in r.data[0]["url"]

    @patch("providers.akshare_provider.requests.get")
    def test_empty_list(self, mock_get, ak: AkshareProvider):
        mock_get.return_value = _mock_response({"data": {"total_hits": 0, "list": []}})
        r = ak.get_stock_announcements("300750.SZ", "2026-03-20", "2026-03-27")
        assert r.success
        assert r.data == []

    @patch("providers.akshare_provider.requests.get")
    def test_caps_limit_to_10(self, mock_get, ak: AkshareProvider):
        items = [{"title": f"公告{i}", "notice_date": "2026-03-27", "art_code": f"CODE{i}"} for i in range(15)]
        mock_get.return_value = _mock_response({"data": {"list": items}})
        r = ak.get_stock_announcements("688041.SH", "2026-03-01", "2026-03-27")
        assert r.success
        assert len(r.data) == 10

    @patch("providers.akshare_provider.requests.get")
    def test_network_error(self, mock_get, ak: AkshareProvider):
        mock_get.side_effect = Exception("timeout")
        r = ak.get_stock_announcements("600519.SH", "2026-03-20", "2026-03-27")
        assert not r.success
        assert "timeout" in r.error

    @patch("providers.akshare_provider.requests.get")
    def test_http_error_status(self, mock_get, ak: AkshareProvider):
        mock_get.return_value = _mock_response({}, status_code=403)
        r = ak.get_stock_announcements("600519.SH", "2026-03-20", "2026-03-27")
        assert not r.success

    def test_capabilities_include_announcements(self, ak: AkshareProvider):
        caps = ak.get_capabilities()
        assert "get_stock_announcements" in caps


# ---------------------------------------------------------------------------
# get_stock_news
# ---------------------------------------------------------------------------

class TestGetStockNews:
    def test_returns_news(self, ak: AkshareProvider):
        df = pd.DataFrame({
            "新闻标题": ["新闻A", "新闻B"],
            "新闻内容": ["正文A", "正文B"],
            "发布时间": ["2026-03-27 09:00", "2026-03-27 10:00"],
            "文章来源": ["财联社", "东方财富"],
        })
        ak.ak.stock_news_em.return_value = df
        r = ak.get_stock_news("600519.SH", "2026-03-27", 5)
        assert r.success
        assert len(r.data) >= 1
        assert r.data[0]["title"] == "新闻A"

    def test_empty_df(self, ak: AkshareProvider):
        ak.ak.stock_news_em.return_value = pd.DataFrame()
        r = ak.get_stock_news("600519.SH", "2026-03-27", 5)
        assert r.success
        assert r.data == []

    def test_exception(self, ak: AkshareProvider):
        ak.ak.stock_news_em.side_effect = Exception("api down")
        r = ak.get_stock_news("600519.SH", "2026-03-27", 5)
        assert not r.success


# ---------------------------------------------------------------------------
# get_investor_qa
# ---------------------------------------------------------------------------

class TestGetInvestorQa:
    def test_returns_qa(self, ak: AkshareProvider):
        df = pd.DataFrame({
            "问题": ["Q1内容", "Q2内容"],
            "回答内容": ["A1内容", "A2内容"],
            "提问时间": ["2026-03-25 10:00:00", "2026-03-26 11:00:00"],
        })
        ak.ak.stock_irm_cninfo.return_value = df
        r = ak.get_investor_qa("600519.SH", "2026-03-20", "2026-03-27")
        assert r.success
        assert len(r.data) >= 1
        assert r.data[0]["question"] == "Q1内容"
        assert r.data[0]["answer"] == "A1内容"

    def test_empty_df(self, ak: AkshareProvider):
        ak.ak.stock_irm_cninfo.return_value = pd.DataFrame()
        r = ak.get_investor_qa("600519.SH", "2026-03-20", "2026-03-27")
        assert r.success
        assert r.data == []

    def test_exception(self, ak: AkshareProvider):
        ak.ak.stock_irm_cninfo.side_effect = Exception("fail")
        r = ak.get_investor_qa("600519.SH", "2026-03-20", "2026-03-27")
        assert not r.success


# ---------------------------------------------------------------------------
# get_research_reports
# ---------------------------------------------------------------------------

class TestGetResearchReports:
    def test_returns_reports(self, ak: AkshareProvider):
        df = pd.DataFrame({
            "日期": ["2026-03-20", "2026-03-15"],
            "机构": ["中信证券", "国泰君安"],
            "东财评级": ["买入", "增持"],
            "报告名称": ["深度报告A", "跟踪报告B"],
        })
        ak.ak.stock_research_report_em.return_value = df
        r = ak.get_research_reports("600519.SH")
        assert r.success
        assert len(r.data) == 2
        assert r.data[0]["institution"] == "中信证券"
        assert r.data[0]["rating"] == "买入"
        assert r.data[0]["title"] == "深度报告A"

    def test_empty_df(self, ak: AkshareProvider):
        ak.ak.stock_research_report_em.return_value = pd.DataFrame()
        r = ak.get_research_reports("600519.SH")
        assert r.success
        assert r.data == []

    def test_caps_at_5(self, ak: AkshareProvider):
        df = pd.DataFrame({
            "日期": [f"2026-03-{i:02d}" for i in range(1, 11)],
            "机构": [f"机构{i}" for i in range(10)],
            "东财评级": ["买入"] * 10,
            "报告名称": [f"报告{i}" for i in range(10)],
        })
        ak.ak.stock_research_report_em.return_value = df
        r = ak.get_research_reports("600519.SH")
        assert r.success
        assert len(r.data) == 5

    def test_exception(self, ak: AkshareProvider):
        ak.ak.stock_research_report_em.side_effect = Exception("error")
        r = ak.get_research_reports("600519.SH")
        assert not r.success
