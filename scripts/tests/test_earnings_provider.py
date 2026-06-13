"""TushareProvider 业绩预告/快报接口单测。

fixture 字段以 STEP0 字段快照为准（2026-06-12 镜像实测）：
- forecast_vip：净利单位万元；同公告可能返回 update_flag 0/1 双行（raw 层须全保留）
- express_vip：营收/净利单位元
- 镜像 forecast/express 必填 ts_code，全市场必须走 *_vip；
  *_vip 支持 start_date/end_date 区间（实测 3 日 1920 行一次返回），窗口一次取整
"""
from __future__ import annotations

import pandas as pd
import pytest

from providers.tushare_provider import (
    EARNINGS_MAX_PAGES,
    EARNINGS_PAGE_LIMIT,
    TushareProvider,
)


def _forecast_row(ts_code="000017.SZ", ann_date="20260611", update_flag="0", **over):
    """STEP0 真实快照字段全集（防字段名漂移，亦作后续 normalize 测试参照）。"""
    row = {
        "ts_code": ts_code,
        "ann_date": ann_date,
        "end_date": "20260630",
        "type": "预增",
        "p_change_min": 113.71,
        "p_change_max": 220.57,
        "net_profit_min": 3600.0,
        "net_profit_max": 5400.0,
        "last_parent_net": 1684.52,
        "first_ann_date": ann_date,
        "summary": "预计:净利润3600-5400",
        "change_reason": "珠宝黄金业务营业收入增长",
        "update_flag": update_flag,
    }
    row.update(over)
    return row


def _express_row(ts_code="002651.SZ", ann_date="20260611", **over):
    row = {
        "ts_code": ts_code,
        "ann_date": ann_date,
        "end_date": "20260630",
        "revenue": 7.260454e8,
        "n_income": 57545000.0,
        "diluted_eps": 0.060,
        "diluted_roe": 2.08,
        "yoy_dedu_np": None,
        "perf_summary": None,
        "is_audit": 0,
        "update_flag": "0",
    }
    row.update(over)
    return row


class _StubPro:
    """持有完整行集，按 offset/limit 切片返回——忠实模拟服务端分页契约。

    server_cap：模拟"镜像真实单页上限 < 请求 limit"（codex review 场景）；
    ignore_offset：模拟"镜像不支持 offset，恒返回首页"。
    """

    def __init__(
        self,
        datasets: dict[tuple[str, str, str], list[dict]] | None = None,
        server_cap: int | None = None,
        ignore_offset: bool = False,
    ):
        self.datasets = datasets or {}
        self.server_cap = server_cap
        self.ignore_offset = ignore_offset
        self.query_calls: list[tuple[str, dict]] = []
        self.raise_on: tuple[str, str, str] | None = None

    def query(self, api_name: str, **params):
        self.query_calls.append((api_name, params))
        key = (api_name, params.get("start_date", ""), params.get("end_date", ""))
        if self.raise_on == key:
            raise RuntimeError("mirror down")
        rows = self.datasets.get(key, [])
        offset = 0 if self.ignore_offset else params.get("offset", 0)
        effective_limit = params.get("limit", len(rows))
        if self.server_cap is not None:
            effective_limit = min(effective_limit, self.server_cap)
        return pd.DataFrame(rows[offset:offset + effective_limit])


def _provider(stub: _StubPro, config: dict | None = None) -> TushareProvider:
    provider = TushareProvider.__new__(TushareProvider)
    provider.name = "tushare"
    provider.priority = 1
    provider.config = config or {}
    provider.pro = stub
    provider._initialized = True
    return provider


def test_capabilities_declare_earnings_methods():
    provider = _provider(_StubPro())
    caps = provider.get_capabilities()
    assert "get_earnings_forecast" in caps
    assert "get_earnings_express" in caps


def test_forecast_window_single_range_query(monkeypatch):
    """默认回看 3 自然日：一次区间查询 [T-2, T]，跨日行聚合返回。"""
    monkeypatch.delenv("EARNINGS_LOOKBACK_DAYS", raising=False)
    stub = _StubPro({
        ("forecast_vip", "20260610", "20260612"): [
            _forecast_row(ann_date="20260610"),
            _forecast_row(ts_code="000066.SZ", ann_date="20260612"),
        ],
    })
    provider = _provider(stub)
    result = provider.get_earnings_forecast("2026-06-12")
    assert result.success
    assert result.source == "tushare:forecast_vip"
    assert result.note == "ann_date_window=[20260610,20260612]"
    # 区间一次取整（非逐日 3 次）+ 一次空页终止确认 = 2 次调用
    assert len(stub.query_calls) == 2
    assert {r["ts_code"] for r in result.data} == {"000017.SZ", "000066.SZ"}
    # registry/collector 兼容字段
    assert all(r["code"] == r["ts_code"] for r in result.data)


def test_lookback_days_env_override(monkeypatch):
    monkeypatch.setenv("EARNINGS_LOOKBACK_DAYS", "1")
    stub = _StubPro({("forecast_vip", "20260612", "20260612"): [_forecast_row(ann_date="20260612")]})
    provider = _provider(stub)
    result = provider.get_earnings_forecast("2026-06-12")
    assert result.success
    assert len(result.data) == 1
    _, params = stub.query_calls[0]
    assert (params["start_date"], params["end_date"]) == ("20260612", "20260612")


def test_lookback_days_config_overrides_env(monkeypatch):
    """config 优先于 env（对齐 token 取值模式）。"""
    monkeypatch.setenv("EARNINGS_LOOKBACK_DAYS", "1")
    stub = _StubPro()
    provider = _provider(stub, config={"earnings_lookback_days": 2})
    result = provider.get_earnings_forecast("2026-06-12")
    assert result.success
    assert result.note == "ann_date_window=[20260611,20260612]"
    _, params = stub.query_calls[0]
    assert (params["start_date"], params["end_date"]) == ("20260611", "20260612")


def test_express_window_returns_records(monkeypatch):
    monkeypatch.setenv("EARNINGS_LOOKBACK_DAYS", "2")
    stub = _StubPro({
        ("express_vip", "20260611", "20260612"): [_express_row(ann_date="20260611")],
    })
    provider = _provider(stub)
    result = provider.get_earnings_express("2026-06-12")
    assert result.success
    assert result.source == "tushare:express_vip"
    assert result.note == "ann_date_window=[20260611,20260612]"
    assert len(result.data) == 1
    assert result.data[0]["n_income"] == 57545000.0


def test_update_flag_duplicate_rows_preserved(monkeypatch):
    """update_flag 0/1 双行是修正前后快照，raw 层不去重（normalize 才处理）。"""
    monkeypatch.setenv("EARNINGS_LOOKBACK_DAYS", "1")
    stub = _StubPro({
        ("forecast_vip", "20260612", "20260612"): [
            _forecast_row(update_flag="0"),
            _forecast_row(update_flag="1"),
        ],
    })
    provider = _provider(stub)
    result = provider.get_earnings_forecast("2026-06-12")
    assert len(result.data) == 2
    # update_flag 保持源值类型（字符串），_clean_scalar 不得改写
    assert sorted(r["update_flag"] for r in result.data) == ["0", "1"]


def _rows(n: int) -> list[dict]:
    return [_forecast_row(ts_code=f"{i:06d}.SZ") for i in range(n)]


def _patch_paging(monkeypatch, *, limit: int, max_pages: int) -> None:
    """缩小分页常量便于构造多页场景（实现读模块全局，patch 即生效）。"""
    import providers.tushare_provider as tp
    monkeypatch.setattr(tp, "EARNINGS_PAGE_LIMIT", limit)
    monkeypatch.setattr(tp, "EARNINGS_MAX_PAGES", max_pages)


def test_pagination_continues_until_empty_page(monkeypatch):
    """空页是唯一终止信号：满页之后必须再查到空页才算取完。"""
    monkeypatch.setenv("EARNINGS_LOOKBACK_DAYS", "1")
    _patch_paging(monkeypatch, limit=4, max_pages=10)
    stub = _StubPro({("forecast_vip", "20260612", "20260612"): _rows(9)})
    provider = _provider(stub)
    result = provider.get_earnings_forecast("2026-06-12")
    assert result.success
    assert len(result.data) == 9  # 4 + 4 + 1，最后查到空页终止
    offsets = [p.get("offset") for _, p in stub.query_calls]
    assert offsets == [0, 4, 8, 9]


def test_pagination_server_cap_below_limit_no_truncation(monkeypatch):
    """codex 回归：服务器单页 cap < 请求 limit 时短页≠取完，须继续翻到空页（防静默截断）。"""
    monkeypatch.setenv("EARNINGS_LOOKBACK_DAYS", "1")
    _patch_paging(monkeypatch, limit=10, max_pages=10)
    stub = _StubPro(
        {("forecast_vip", "20260612", "20260612"): _rows(7)},
        server_cap=3,  # 每页最多 3 行 < limit=10
    )
    provider = _provider(stub)
    result = provider.get_earnings_forecast("2026-06-12")
    assert result.success
    assert len(result.data) == 7  # 3 + 3 + 1 全量取齐，而非首页 3 行即停
    offsets = [p.get("offset") for _, p in stub.query_calls]
    assert offsets == [0, 3, 6, 7]  # offset 按实际行数推进


def test_pagination_ignored_offset_fails_loudly(monkeypatch):
    """codex 回归：镜像忽略 offset（恒返首页）→ 重复页检测显式报错，不静默重复。"""
    monkeypatch.setenv("EARNINGS_LOOKBACK_DAYS", "1")
    _patch_paging(monkeypatch, limit=3, max_pages=10)
    stub = _StubPro(
        {("forecast_vip", "20260612", "20260612"): _rows(8)},
        ignore_offset=True,
    )
    provider = _provider(stub)
    result = provider.get_earnings_forecast("2026-06-12")
    assert not result.success
    assert "offset 未生效" in result.error


def test_pagination_exceeding_max_pages_returns_error(monkeypatch):
    """页数上限兜底 → 显式报错，拒绝静默截断。

    用 server_cap=1 且每行不同构造"恒有新数据"的超长翻页（绕过重复页检测）。
    """
    monkeypatch.setenv("EARNINGS_LOOKBACK_DAYS", "1")
    _patch_paging(monkeypatch, limit=5, max_pages=3)
    stub = _StubPro(
        {("forecast_vip", "20260612", "20260612"): _rows(100)},
        server_cap=1,
    )
    provider = _provider(stub)
    result = provider.get_earnings_forecast("2026-06-12")
    assert not result.success
    assert "分页" in result.error


def test_empty_window_returns_empty_success(monkeypatch):
    monkeypatch.setenv("EARNINGS_LOOKBACK_DAYS", "3")
    provider = _provider(_StubPro())
    result = provider.get_earnings_forecast("2026-06-12")
    assert result.success
    assert result.data == []


def test_query_exception_returns_error(monkeypatch):
    monkeypatch.setenv("EARNINGS_LOOKBACK_DAYS", "2")
    stub = _StubPro()
    stub.raise_on = ("forecast_vip", "20260611", "20260612")
    provider = _provider(stub)
    result = provider.get_earnings_forecast("2026-06-12")
    assert not result.success
    assert "mirror down" in result.error


def test_not_initialized_guard():
    provider = _provider(_StubPro())
    provider.pro = None
    provider._initialized = False
    result = provider.get_earnings_forecast("2026-06-12")
    assert not result.success
    assert "provider_not_initialized" in result.error


def test_invalid_lookback_env_falls_back_to_default(monkeypatch):
    """非法 env 值回退默认 3 天（不抛异常）。"""
    monkeypatch.setenv("EARNINGS_LOOKBACK_DAYS", "abc")
    stub = _StubPro()
    provider = _provider(stub)
    result = provider.get_earnings_forecast("2026-06-12")
    assert result.success
    _, params = stub.query_calls[0]
    assert (params["start_date"], params["end_date"]) == ("20260610", "20260612")


def test_config_null_lookback_falls_back_without_crash(monkeypatch):
    """config 显式 null（None）→ TypeError 兜底回退默认，不崩溃。"""
    monkeypatch.delenv("EARNINGS_LOOKBACK_DAYS", raising=False)
    stub = _StubPro()
    provider = _provider(stub, config={"earnings_lookback_days": None})
    result = provider.get_earnings_forecast("2026-06-12")
    assert result.success
    _, params = stub.query_calls[0]
    assert (params["start_date"], params["end_date"]) == ("20260610", "20260612")
