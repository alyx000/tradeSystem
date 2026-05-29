"""AkShareProvider：亚太 yfinance 与 get_us_tickers_overnight，mock yfinance，无东财。"""
from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock, Mock, patch
from zoneinfo import ZoneInfo

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


@patch("yfinance.Ticker")
def test_get_us_tickers_overnight_hxc_uses_pgj_etf(mock_ticker, ak: AkshareProvider):
    """金龙改用 PGJ ETF：^HXC 在 yfinance 无历史 K 线，隔夜涨跌幅恒算成 0%。

    PGJ（跟踪同一金龙指数）有可靠日线；输出键仍保持 HXC 不动消费方。
    注意 close 是 ETF 价位（约 25），不是指数点位（约 6500）。
    """
    mock_ticker.return_value.history.return_value = _hist_df([25.71, 25.16])
    r = ak.get_us_tickers_overnight(["HXC"])
    assert r.success
    assert "PGJ" in str(mock_ticker.call_args)
    assert "^HXC" not in str(mock_ticker.call_args)
    assert "HXC" in r.data
    assert r.data["HXC"]["close"] == 25.16
    assert r.data["HXC"]["change_pct"] != 0.0, "有真实历史时不应再恒 0%"
    assert r.data["HXC"]["as_of"] == "2026-03-21"


@patch("yfinance.Ticker")
def test_get_us_tickers_overnight_hxc_all_nan(mock_ticker, ak: AkshareProvider):
    """全 NaN 时应返回 per-symbol error（dropna 后为空），不输出 nan。"""
    mock_ticker.return_value.history.return_value = _hist_df([float("nan"), float("nan")])
    r = ak.get_us_tickers_overnight(["HXC"])
    assert r.success
    assert "error" in r.data["HXC"]


@patch("yfinance.Ticker")
def test_get_us_tickers_overnight_single_row_is_honest_not_zero(mock_ticker, ak: AkshareProvider):
    """单行历史（^HXC 旧 bug 场景）应诚实报「无隔夜对比数据」，不编造 +0.0%。"""
    mock_ticker.return_value.history.return_value = _hist_df([6557.0])
    r = ak.get_us_tickers_overnight(["HXC"])
    assert r.success
    assert "error" in r.data["HXC"], "单行不该输出 change_pct=0.0 冒充"
    assert "change_pct" not in r.data["HXC"]


def _hist_with_dates(pairs: list[tuple[str, float]]) -> pd.DataFrame:
    idx = pd.to_datetime([p[0] for p in pairs])
    return pd.DataFrame({"Close": [p[1] for p in pairs]}, index=idx)


_ET = ZoneInfo("America/New_York")


def test_overnight_from_hist_uses_last_two_completed_sessions():
    from providers.akshare_provider import _overnight_from_hist
    h = _hist_with_dates([("2026-05-21", 25.71), ("2026-05-22", 25.16)])
    # 数据都在过去，now 任意都不剔
    close, chg, as_of = _overnight_from_hist(h, datetime(2026, 5, 26, 19, 0, tzinfo=_ET))
    assert close == pytest.approx(25.16)
    assert chg == pytest.approx(-2.14, abs=0.01)
    assert as_of == "2026-05-22"


def test_overnight_from_hist_drops_forming_today_bar_during_session():
    """盘中手动跑：末根是「美东今日」且未到 16:00 收盘 → 成形 bar，剔除用上一已收盘日。"""
    from providers.akshare_provider import _overnight_from_hist
    h = _hist_with_dates([("2026-05-26", 25.50), ("2026-05-27", 25.16), ("2026-05-28", 25.32)])
    now = datetime(2026, 5, 28, 12, 0, tzinfo=_ET)  # 美东 05-28 盘中（< 16:00）
    close, chg, as_of = _overnight_from_hist(h, now)
    assert as_of == "2026-05-27", "盘中今日(05-28)成形 bar 应被剔除，取上一已收盘日 05-27"
    assert close == pytest.approx(25.16)


def test_overnight_from_hist_keeps_today_bar_after_close():
    """定时简报场景（北京次日 07:00 = 美东今日 19:00 已收盘）：当日 bar 是隔夜数据，必须保留。

    回归 off-by-one：05-29 早间简报应取 05-28（而非误剔成 05-27）。
    """
    from providers.akshare_provider import _overnight_from_hist
    h = _hist_with_dates([("2026-05-27", 25.58), ("2026-05-28", 25.45)])
    now = datetime(2026, 5, 28, 19, 0, tzinfo=_ET)  # 美东 05-28 19:00，已过 16:00 收盘
    close, chg, as_of = _overnight_from_hist(h, now)
    assert as_of == "2026-05-28", "已收盘的当日 session 不能被当成形 bar 剔掉"
    assert close == pytest.approx(25.45)


def test_overnight_from_hist_insufficient_after_drop_returns_none():
    """盘中只有「昨收 + 今日成形」，剔今日后仅剩 1 行，无法算隔夜对比 → None。"""
    from providers.akshare_provider import _overnight_from_hist
    h = _hist_with_dates([("2026-05-26", 25.16), ("2026-05-27", 25.32)])
    now = datetime(2026, 5, 27, 12, 0, tzinfo=_ET)
    assert _overnight_from_hist(h, now) is None


def test_overnight_from_hist_single_row_returns_none():
    """^HXC 式单点报价：无昨收可比 → None（诚实），不编 0.0%。"""
    from providers.akshare_provider import _overnight_from_hist
    h = _hist_with_dates([("2026-05-26", 6557.0)])
    assert _overnight_from_hist(h, datetime(2026, 5, 26, 19, 0, tzinfo=_ET)) is None


@patch("yfinance.Ticker")
def test_index_from_yfinance_skips_trailing_nan(mock_ticker, ak: AkshareProvider):
    """末根 K 线 Close 为 NaN（^KS11 实测场景）时，取前一根有效收盘。"""
    mock_ticker.return_value.history.return_value = _hist_df([2550.0, 2600.0, float("nan")])
    r = ak._index_from_yfinance("^KS11", "韩国综指")
    assert r is not None
    assert r.data["close"] == 2600.0


@patch("yfinance.Ticker")
def test_index_from_yfinance_all_nan_returns_none(mock_ticker, ak: AkshareProvider):
    """全为 NaN 时返回 None，让注册表继续降级。"""
    mock_ticker.return_value.history.return_value = _hist_df([float("nan"), float("nan")])
    r = ak._index_from_yfinance("^N225", "日经225")
    assert r is None


# ---------------------------------------------------------------------------
# get_limit_up_list（涨停榜：封单金额列名 = 「封板资金」，非「封单额」）
# ---------------------------------------------------------------------------


def test_get_limit_up_list_reads_seal_amount_from_fengban_zijin(ak: AkshareProvider):
    """东财 stock_zt_pool_em 的封单金额列名是「封板资金」（原始元），不是「封单额」。

    回归：曾误取「封单额」（不存在的列）→ seal_amount 恒为 0.0。
    """
    ak.ak.stock_zt_pool_em.return_value = pd.DataFrame([
        {"代码": "300001", "名称": "高标A", "涨跌幅": 20.0, "成交额": 6.2e8,
         "换手率": 12.0, "首次封板时间": "093000", "最后封板时间": "093000",
         "连板数": 3, "封板资金": 1.23e8},
    ])
    r = ak.get_limit_up_list("2026-05-29")
    assert r.success
    stock = r.data["stocks"][0]
    assert stock["seal_amount"] == pytest.approx(1.23e8), "封单金额应取自「封板资金」列"


# ---------------------------------------------------------------------------
# is_trade_day（交易日历，tool_trade_date_hist_sina 返回 datetime.date）
# ---------------------------------------------------------------------------


class TestIsTradeDay:
    def _cal_df(self) -> pd.DataFrame:
        # tool_trade_date_hist_sina 实际返回 datetime.date 对象（非字符串）
        return pd.DataFrame({"trade_date": [date(2026, 5, 22), date(2026, 5, 26)]})

    def test_recognizes_trading_day_despite_date_object(self, ak: AkshareProvider):
        """日历是 datetime.date，astype(str) 得 '2026-05-26'；不得因去横杠比较而误判。"""
        ak.ak.tool_trade_date_hist_sina.return_value = self._cal_df()
        r = ak.is_trade_day("2026-05-26")
        assert r.success
        assert r.data is True

    def test_non_trading_day_returns_false(self, ak: AkshareProvider):
        ak.ak.tool_trade_date_hist_sina.return_value = self._cal_df()
        r = ak.is_trade_day("2026-05-24")  # 周日，不在日历内
        assert r.success
        assert r.data is False

    def test_accepts_compact_date_input(self, ak: AkshareProvider):
        """对无横杠输入（20260526）也应正确判定，避免调用方格式差异再次踩坑。"""
        ak.ak.tool_trade_date_hist_sina.return_value = self._cal_df()
        r = ak.is_trade_day("20260526")
        assert r.success
        assert r.data is True


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
        ak._fetch_irm_cninfo_raw = Mock(return_value=[
            {"mainContent": "Q1内容", "attachedContent": "A1内容", "pubDate": 1774252800000},
            {"mainContent": "Q2内容", "attachedContent": "A2内容", "pubDate": 1774339200000},
        ])
        r = ak.get_investor_qa("600519.SH", "2026-03-20", "2026-03-27")
        assert r.success
        assert len(r.data) == 2
        assert r.data[0]["question"] == "Q1内容"
        assert r.data[0]["answer"] == "A1内容"
        assert "2026-03-2" in r.data[0]["date"]

    def test_empty_result(self, ak: AkshareProvider):
        ak._fetch_irm_cninfo_raw = Mock(return_value=[])
        r = ak.get_investor_qa("600519.SH", "2026-03-20", "2026-03-27")
        assert r.success
        assert r.data == []

    def test_exception(self, ak: AkshareProvider):
        ak._fetch_irm_cninfo_raw = Mock(side_effect=Exception("fail"))
        r = ak.get_investor_qa("600519.SH", "2026-03-20", "2026-03-27")
        assert not r.success

    def test_unanswered_question(self, ak: AkshareProvider):
        ak._fetch_irm_cninfo_raw = Mock(return_value=[
            {"mainContent": "Q未答", "attachedContent": None, "pubDate": 1774252800000},
        ])
        r = ak.get_investor_qa("300750.SZ", "2026-03-20", "2026-03-27")
        assert r.success
        assert len(r.data) == 1
        assert r.data[0]["answer"] == ""

    def test_no_pubdate_still_included(self, ak: AkshareProvider):
        ak._fetch_irm_cninfo_raw = Mock(return_value=[
            {"mainContent": "Q无日期", "attachedContent": "A无日期", "pubDate": None},
        ])
        r = ak.get_investor_qa("002594.SZ", "2026-03-20", "2026-03-27")
        assert r.success
        assert len(r.data) == 1
        assert r.data[0]["date"] == ""

    def test_out_of_range_filtered(self, ak: AkshareProvider):
        ak._fetch_irm_cninfo_raw = Mock(return_value=[
            {"mainContent": "Q旧", "attachedContent": "A旧", "pubDate": 1711339200000},
        ])
        r = ak.get_investor_qa("002594.SZ", "2026-03-20", "2026-03-27")
        assert r.success
        assert r.data == []

    def test_truncation(self, ak: AkshareProvider):
        long_q = "Q" * 500
        long_a = "A" * 800
        ak._fetch_irm_cninfo_raw = Mock(return_value=[
            {"mainContent": long_q, "attachedContent": long_a, "pubDate": 1774252800000},
        ])
        r = ak.get_investor_qa("002594.SZ", "2026-03-20", "2026-03-27")
        assert r.success
        assert len(r.data[0]["question"]) == 300
        assert len(r.data[0]["answer"]) == 500


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
