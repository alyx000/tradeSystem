"""月线模式股票池所需的全市场财务快照 provider 契约测试。

核心边界：
- 仅按 as_of_date 当时已经披露的版本返回，不能用未来公告修订值；
- 三张 VIP 表按报告期全市场分页拉取，短页不等于结束；
- 一张表失败即整批失败，不能把源异常伪装成财务字段缺失；
- 输出按 (ts_code, end_date) 合并，并显式保留各组件 missing 状态。
"""
from __future__ import annotations

import pandas as pd

from providers.base import DataProvider
from providers.tushare_provider import TushareProvider


class _StubPro:
    """按 API + period 提供分页数据，支持镜像 cap/offset 异常模拟。"""

    def __init__(
        self,
        datasets: dict[tuple[str, str], list[dict]] | None = None,
        *,
        server_cap: int | None = None,
        ignore_offset: bool = False,
    ):
        self.datasets = datasets or {}
        self.server_cap = server_cap
        self.ignore_offset = ignore_offset
        self.query_calls: list[tuple[str, dict]] = []
        self.raise_on: tuple[str, str] | None = None
        self.return_none_on: tuple[str, str] | None = None

    def query(self, api_name: str, **params):
        self.query_calls.append((api_name, params))
        key = (api_name, params.get("period", ""))
        if key == self.raise_on:
            raise RuntimeError("mirror down")
        if key == self.return_none_on:
            return None
        rows = self.datasets.get(key, [])
        offset = 0 if self.ignore_offset else params.get("offset", 0)
        limit = params.get("limit", len(rows))
        if self.server_cap is not None:
            limit = min(limit, self.server_cap)
        return pd.DataFrame(rows[offset:offset + limit])

    def stock_basic(self, **params):
        self.query_calls.append(("stock_basic", params))
        status = params.get("list_status", "")
        key = ("stock_basic", status)
        if key == self.raise_on:
            raise RuntimeError("mirror down")
        if key == self.return_none_on:
            return None
        return pd.DataFrame(self.datasets.get(key, []))


def _provider(stub: _StubPro, *, initialized: bool = True) -> TushareProvider:
    provider = TushareProvider.__new__(TushareProvider)
    provider.name = "tushare"
    provider.priority = 1
    provider.config = {}
    provider.pro = stub if initialized else None
    provider._initialized = initialized
    return provider


def _financial_row(
    ts_code: str = "000001.SZ",
    *,
    ann_date: str = "20260401",
    f_ann_date: str = "",
    end_date: str = "20260331",
    update_flag: str = "0",
    **extra,
) -> dict:
    row = {
        "ts_code": ts_code,
        "ann_date": ann_date,
        "f_ann_date": f_ann_date,
        "end_date": end_date,
        "update_flag": update_flag,
    }
    row.update(extra)
    return row


def _patch_paging(monkeypatch, *, limit: int, max_pages: int = 20) -> None:
    import providers.tushare_provider as tp

    monkeypatch.setattr(tp, "FINANCIAL_PAGE_LIMIT", limit)
    monkeypatch.setattr(tp, "FINANCIAL_MAX_PAGES", max_pages)


def _patch_monthly_paging(monkeypatch, *, limit: int, max_pages: int = 20) -> None:
    import providers.tushare_provider as tp

    monkeypatch.setattr(tp, "MARKET_MONTHLY_PAGE_LIMIT", limit)
    monkeypatch.setattr(tp, "MARKET_MONTHLY_MAX_PAGES", max_pages)


def test_base_provider_declares_financial_snapshot_contract():
    assert hasattr(DataProvider, "get_financial_snapshots")


def test_capability_declares_financial_snapshots():
    provider = _provider(_StubPro())
    assert "get_financial_snapshots" in provider.get_capabilities()


def test_default_derives_latest_five_standard_periods_and_queries_all_components():
    stub = _StubPro()
    provider = _provider(stub)

    result = provider.get_financial_snapshots("2026-07-24")

    assert result.success
    assert result.data == []
    expected_periods = {
        "20260630",
        "20260331",
        "20251231",
        "20250930",
        "20250630",
    }
    expected_apis = {"fina_indicator_vip", "balancesheet_vip", "income_vip"}
    assert {(api, params["period"]) for api, params in stub.query_calls} == {
        (api, period) for api in expected_apis for period in expected_periods
    }
    # 空数据每个 API/报告期一次调用；禁止无界追溯更多历史报告期。
    assert len(stub.query_calls) == 15

    fields_by_api = {
        api: set(params["fields"].split(","))
        for api, params in stub.query_calls
        if params["period"] == "20260630"
    }
    assert {
        "ts_code", "ann_date", "end_date", "update_flag",
        "roe", "roe_yearly", "netprofit_yoy", "debt_to_assets",
        "rd_exp", "profit_dedt",
    } <= fields_by_api["fina_indicator_vip"]
    assert {
        "ts_code", "ann_date", "f_ann_date", "end_date", "update_flag",
        "total_assets", "total_liab", "total_hldr_eqy_exc_min_int",
    } <= fields_by_api["balancesheet_vip"]
    assert {
        "ts_code", "ann_date", "f_ann_date", "end_date", "update_flag",
        "total_revenue", "operate_profit", "n_income_attr_p",
    } <= fields_by_api["income_vip"]


def test_merges_components_dedupes_update_and_excludes_future_announcements():
    period = "20260331"
    stub = _StubPro({
        ("fina_indicator_vip", period): [
            _financial_row(update_flag="0", roe=8.0),
            _financial_row(update_flag="1", roe=10.0),
            _financial_row(ann_date="20260725", update_flag="1", roe=99.0),
            _financial_row(
                "000002.SZ", ann_date="20260725", update_flag="1", roe=88.0,
            ),
        ],
        ("balancesheet_vip", period): [
            _financial_row(
                ann_date="20260402",
                f_ann_date="20260403",
                report_type="1",
                total_assets=100.0,
                total_liab=40.0,
            ),
        ],
        ("income_vip", period): [
            _financial_row(
                ann_date="20260402",
                f_ann_date="20260405",
                report_type="1",
                update_flag="1",
                total_revenue=30.0,
                n_income_attr_p=5.0,
            ),
        ],
    })
    provider = _provider(stub)

    result = provider.get_financial_snapshots(
        "2026-07-24", report_periods=["2026-03-31"],
    )

    assert result.success
    assert len(result.data) == 1
    snapshot = result.data[0]
    assert snapshot["ts_code"] == "000001.SZ"
    assert snapshot["report_period"] == "2026-03-31"
    assert snapshot["financial_ann_date"] == "2026-04-05"
    assert snapshot["fina_indicator"]["status"] == "ok"
    assert snapshot["fina_indicator"]["update_flag"] == "1"
    assert snapshot["fina_indicator"]["roe"] == 10.0
    assert snapshot["balancesheet"]["ann_date"] == "20260402"
    assert snapshot["balancesheet"]["f_ann_date"] == "20260403"
    assert snapshot["income"]["f_ann_date"] == "20260405"
    assert snapshot["income"]["update_flag"] == "1"


def test_balance_and_income_prefer_adjusted_consolidated_report_type_four():
    """调整合并口径 4 是可信修订，应优先于普通合并口径 1。"""
    period = "20260331"
    provider = _provider(_StubPro({
        ("balancesheet_vip", period): [
            _financial_row(report_type="1", total_assets=100.0),
            _financial_row(report_type="4", total_assets=999.0),
        ],
        ("income_vip", period): [
            _financial_row(report_type="1", n_income_attr_p=5.0),
            _financial_row(report_type="6", n_income_attr_p=99.0),
        ],
    }))

    result = provider.get_financial_snapshots(
        "2026-07-24", report_periods=[period],
    )

    assert result.success
    assert len(result.data) == 1
    snapshot = result.data[0]
    assert snapshot["balancesheet"]["report_type"] == "4"
    assert snapshot["balancesheet"]["total_assets"] == 999.0
    assert snapshot["income"]["report_type"] == "1"
    assert snapshot["income"]["n_income_attr_p"] == 5.0
    assert snapshot["revision_sensitive"] is True


def test_non_consolidated_statements_do_not_fill_missing_components():
    """只有母公司/调整前口径时必须 missing，不能用于合并财务硬门。"""
    period = "20260331"
    provider = _provider(_StubPro({
        ("fina_indicator_vip", period): [
            _financial_row(roe=10.0),
        ],
        ("balancesheet_vip", period): [
            _financial_row(report_type="6", total_assets=999.0),
        ],
        ("income_vip", period): [
            _financial_row(report_type="5", n_income_attr_p=99.0),
        ],
    }))

    result = provider.get_financial_snapshots(
        "2026-07-24", report_periods=[period],
    )

    assert result.success
    assert len(result.data) == 1
    snapshot = result.data[0]
    assert snapshot["balancesheet"] == {"status": "missing"}
    assert snapshot["income"] == {"status": "missing"}


def test_update_flag_one_precedes_a_later_original_version():
    """update_flag=1 是同报告期最新修订，不能被更晚返回的 flag=0 原始版覆盖。"""
    period = "20260331"
    provider = _provider(_StubPro({
        ("fina_indicator_vip", period): [
            _financial_row(ann_date="20260401", update_flag="1", roe=10.0),
            _financial_row(ann_date="20260402", update_flag="0", roe=99.0),
        ],
    }))

    result = provider.get_financial_snapshots(
        "2026-07-24", report_periods=[period],
    )

    assert result.success
    assert result.data[0]["fina_indicator"]["update_flag"] == "1"
    assert result.data[0]["fina_indicator"]["roe"] == 10.0


def test_missing_components_are_explicit_not_silent_empty_dicts():
    period = "20260331"
    provider = _provider(_StubPro({
        ("fina_indicator_vip", period): [
            _financial_row(roe=10.0),
        ],
    }))

    result = provider.get_financial_snapshots(
        "2026-07-24", report_periods=[period],
    )

    assert result.success
    assert len(result.data) == 1
    snapshot = result.data[0]
    assert snapshot["fina_indicator"]["status"] == "ok"
    assert snapshot["balancesheet"] == {"status": "missing"}
    assert snapshot["income"] == {"status": "missing"}
    assert snapshot["financial_ann_date"] == "2026-04-01"


def test_financial_pagination_uses_actual_page_size_until_empty(monkeypatch):
    _patch_paging(monkeypatch, limit=10)
    period = "20260331"
    rows = [
        _financial_row(f"{index:06d}.SZ", roe=float(index))
        for index in range(5)
    ]
    stub = _StubPro(
        {("fina_indicator_vip", period): rows},
        server_cap=2,
    )
    provider = _provider(stub)

    result = provider.get_financial_snapshots(
        "2026-07-24", report_periods=[period],
    )

    assert result.success
    assert len(result.data) == 5
    offsets = [
        params["offset"]
        for api, params in stub.query_calls
        if api == "fina_indicator_vip"
    ]
    assert offsets == [0, 2, 4, 5]


def test_financial_pagination_ignored_offset_fails_loudly(monkeypatch):
    _patch_paging(monkeypatch, limit=2)
    period = "20260331"
    stub = _StubPro(
        {
            ("fina_indicator_vip", period): [
                _financial_row(f"{index:06d}.SZ")
                for index in range(3)
            ],
        },
        ignore_offset=True,
    )
    provider = _provider(stub)

    result = provider.get_financial_snapshots(
        "2026-07-24", report_periods=[period],
    )

    assert not result.success
    assert result.data is None
    assert "fina_indicator_vip" in result.error
    assert "offset 未生效" in result.error


def test_any_component_exception_fails_whole_batch_not_empty_success():
    period = "20260331"
    stub = _StubPro({
        ("fina_indicator_vip", period): [_financial_row(roe=10.0)],
    })
    stub.raise_on = ("balancesheet_vip", period)
    provider = _provider(stub)

    result = provider.get_financial_snapshots(
        "2026-07-24", report_periods=[period],
    )

    assert not result.success
    assert result.data is None
    assert "balancesheet_vip" in result.error
    assert period in result.error
    assert "mirror down" in result.error


def test_financial_none_response_is_source_failure_not_empty_success():
    period = "20260331"
    stub = _StubPro()
    stub.return_none_on = ("income_vip", period)
    provider = _provider(stub)

    result = provider.get_financial_snapshots(
        "2026-07-24", report_periods=[period],
    )

    assert not result.success
    assert result.data is None
    assert "income_vip" in result.error
    assert "None" in result.error


def test_uninitialized_financial_provider_returns_clear_error():
    provider = _provider(_StubPro(), initialized=False)

    result = provider.get_financial_snapshots("2026-07-24")

    assert not result.success
    assert result.error == "provider_not_initialized: get_financial_snapshots"


def test_base_and_capability_declare_market_monthly_quotes():
    provider = _provider(_StubPro())

    assert hasattr(DataProvider, "get_market_monthly_quotes")
    assert "get_market_monthly_quotes" in provider.get_capabilities()


def test_base_and_capability_declare_stock_universe_as_of():
    provider = _provider(_StubPro())

    assert hasattr(DataProvider, "get_stock_universe_as_of")
    assert "get_stock_universe_as_of" in provider.get_capabilities()


def test_stock_universe_as_of_includes_stocks_traded_during_target_month_and_caches():
    provider = _provider(_StubPro({
        ("stock_basic", "L"): [
            {
                "ts_code": "000001.SZ",
                "name": "在市股",
                "list_date": "20200101",
                "delist_date": "",
                "list_status": "L",
            },
            {
                "ts_code": "000004.SZ",
                "name": "次月上市",
                "list_date": "20260701",
                "delist_date": "",
                "list_status": "L",
            },
        ],
        ("stock_basic", "D"): [
            {
                "ts_code": "000002.SZ",
                "name": "月中退市",
                "list_date": "20200101",
                "delist_date": "20260615",
                "list_status": "D",
            },
            {
                "ts_code": "000003.SZ",
                "name": "此前退市",
                "list_date": "20200101",
                "delist_date": "20260531",
                "list_status": "D",
            },
        ],
        ("stock_basic", "P"): [
            {
                "ts_code": "000005.SZ",
                "name": "待上市",
                "list_date": "20260801",
                "delist_date": "",
                "list_status": "P",
            },
        ],
    }))

    june = provider.get_stock_universe_as_of("2026-06-30")
    july = provider.get_stock_universe_as_of("2026-07-31")

    assert june.success
    assert june.source == "tushare:stock_basic:L+D+P:as_of"
    assert [row["ts_code"] for row in june.data] == ["000001.SZ", "000002.SZ"]
    assert [row["ts_code"] for row in july.data] == ["000001.SZ", "000004.SZ"]
    assert [api for api, _ in provider.pro.query_calls].count("stock_basic") == 3


def test_stock_universe_as_of_fails_if_any_status_source_is_none():
    stub = _StubPro()
    stub.return_none_on = ("stock_basic", "D")
    provider = _provider(stub)

    result = provider.get_stock_universe_as_of("2026-06-30")

    assert not result.success
    assert result.data is None
    assert "list_status=D" in result.error
    assert "None" in result.error


def test_market_monthly_quotes_queries_official_monthly_fields_and_limit():
    stub = _StubPro({
        ("monthly", ""): [{
            "ts_code": "000001.SZ",
            "trade_date": "20260630",
            "open": 10.0,
            "high": 12.0,
            "low": 9.0,
            "close": 11.0,
            "pre_close": 10.0,
            "change": 1.0,
            "pct_chg": 10.0,
            "vol": 100.0,
            "amount": 1100.0,
        }],
    })
    provider = _provider(stub)

    result = provider.get_market_monthly_quotes("2026-06-30")

    assert result.success
    assert result.source == "tushare:monthly"
    assert len(result.data) == 1
    api_name, params = stub.query_calls[0]
    assert api_name == "monthly"
    assert params["trade_date"] == "20260630"
    assert params["limit"] == 4500
    assert {
        "ts_code", "trade_date", "open", "high", "low", "close",
        "pre_close", "change", "pct_chg", "vol", "amount",
    } == set(params["fields"].split(","))


def test_market_monthly_paginates_until_empty_using_actual_page_size(monkeypatch):
    _patch_monthly_paging(monkeypatch, limit=10)
    rows = [
        {
            "ts_code": f"{index:06d}.SZ",
            "trade_date": "20260630",
            "close": float(index),
        }
        for index in range(5)
    ]
    stub = _StubPro({("monthly", ""): rows}, server_cap=2)
    provider = _provider(stub)

    result = provider.get_market_monthly_quotes("20260630")

    assert result.success
    assert len(result.data) == 5
    assert [params["offset"] for _, params in stub.query_calls] == [0, 2, 4, 5]


def test_market_monthly_ignored_offset_returns_error(monkeypatch):
    _patch_monthly_paging(monkeypatch, limit=2)
    rows = [
        {
            "ts_code": f"{index:06d}.SZ",
            "trade_date": "20260630",
            "close": float(index),
        }
        for index in range(3)
    ]
    stub = _StubPro(
        {("monthly", ""): rows},
        ignore_offset=True,
    )
    provider = _provider(stub)

    result = provider.get_market_monthly_quotes("20260630")

    assert not result.success
    assert result.data is None
    assert "monthly" in result.error
    assert "offset 未生效" in result.error


def test_market_monthly_source_exception_is_not_empty_success():
    stub = _StubPro()
    stub.raise_on = ("monthly", "")
    provider = _provider(stub)

    result = provider.get_market_monthly_quotes("20260630")

    assert not result.success
    assert result.data is None
    assert "monthly" in result.error
    assert "mirror down" in result.error


def test_market_monthly_none_response_is_source_failure_not_empty_success():
    stub = _StubPro()
    stub.return_none_on = ("monthly", "")
    provider = _provider(stub)

    result = provider.get_market_monthly_quotes("20260630")

    assert not result.success
    assert result.data is None
    assert "monthly" in result.error
    assert "None" in result.error


def test_market_monthly_rejects_rows_from_a_different_trade_date():
    provider = _provider(_StubPro({
        ("monthly", ""): [{
            "ts_code": "000001.SZ",
            "trade_date": "20260529",
            "close": 11.0,
        }],
    }))

    result = provider.get_market_monthly_quotes("20260630")

    assert not result.success
    assert result.data is None
    assert "trade_date" in result.error
    assert "20260529" in result.error


def test_market_monthly_preserves_duplicate_codes_for_consumer_validation():
    duplicate = {
        "ts_code": "000001.SZ",
        "trade_date": "20260630",
        "close": 11.0,
    }
    provider = _provider(_StubPro({
        ("monthly", ""): [duplicate, dict(duplicate)],
    }))

    result = provider.get_market_monthly_quotes("20260630")

    assert result.success
    assert len(result.data) == 2
    assert [row["ts_code"] for row in result.data] == ["000001.SZ", "000001.SZ"]


def test_uninitialized_market_monthly_provider_returns_clear_error():
    provider = _provider(_StubPro(), initialized=False)

    result = provider.get_market_monthly_quotes("20260630")

    assert not result.success
    assert result.error == "provider_not_initialized: get_market_monthly_quotes"
