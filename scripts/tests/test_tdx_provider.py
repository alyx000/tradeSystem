"""TdxProvider（pytdx → 通达信 880003 平均股价）单元测试。

全程 mock pytdx.hq.TdxHq_API，不触真实网络。覆盖：
  - avg_price 周线归一化（datetime → YYYYMMDD，close float）
  - 非 avg_price 显式拒绝（tdx 只服务平均股价这一伪指数）
  - 多服务器 fallback（首个连不上 → 退到下一个）
  - TCP 可连但 880003 空数据时继续 fallback
  - 全部服务器失败 → 返回 error 而非抛异常
  - 无论成功失败都 disconnect（不泄漏连接）
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from providers.tdx_provider import TdxProvider


def _bars(rows):
    """[(datetime, close), ...] → pytdx get_index_bars 返回格式。"""
    return [
        {"datetime": dt, "close": c, "open": c, "high": c, "low": c, "vol": 1.0}
        for dt, c in rows
    ]


WEEKLY = _bars([
    ("2026-04-24 15:00", 30.26),
    ("2026-04-30 15:00", 30.92),
    ("2026-05-08 15:00", 32.36),
    ("2026-05-15 15:00", 32.62),
    ("2026-05-22 15:00", 32.96),
    ("2026-05-29 15:00", 31.28),
])


class TestTdxProviderAvgPrice:
    def test_avg_price_weekly_normalized(self):
        api = MagicMock()
        api.connect.return_value = True
        api.get_index_bars.return_value = WEEKLY
        with patch("pytdx.hq.TdxHq_API", return_value=api):
            prov = TdxProvider({"servers": [("1.1.1.1", 7709)]})
            r = prov.get_index_weekly("avg_price", "2026-03-30", "2026-05-29")
        assert r.success
        assert r.data[0]["trade_date"] == "20260424"
        assert r.data[-1]["trade_date"] == "20260529"
        assert r.data[-1]["close"] == 31.28
        # 末根=当周 partial，供 _compute_index_ma 取作 day_close
        api.disconnect.assert_called_once()

    def test_non_avg_price_rejected(self):
        with patch("pytdx.hq.TdxHq_API") as cls:
            prov = TdxProvider({"servers": [("1.1.1.1", 7709)]})
            r = prov.get_index_weekly("shanghai", "2026-03-30", "2026-05-29")
        assert not r.success
        assert "avg_price" in r.error
        cls.assert_not_called()  # 非目标 code 不该建连

    def test_server_fallback(self):
        failed_api = MagicMock()
        failed_api.connect.return_value = False
        data_api = MagicMock()
        data_api.connect.return_value = True
        data_api.get_index_bars.return_value = WEEKLY
        # 第一个服务器连失败（返回 False），第二个成功
        with patch("pytdx.hq.TdxHq_API", side_effect=[failed_api, data_api]):
            prov = TdxProvider({"servers": [("1.1.1.1", 7709), ("2.2.2.2", 7709)]})
            r = prov.get_index_weekly("avg_price", "2026-03-30", "2026-05-29")
        assert r.success
        failed_api.disconnect.assert_called_once()
        data_api.disconnect.assert_called_once()

    def test_server_fallback_when_connected_node_has_no_avg_price(self):
        empty_api = MagicMock()
        empty_api.connect.return_value = True
        empty_api.get_index_bars.return_value = []
        data_api = MagicMock()
        data_api.connect.return_value = True
        data_api.get_index_bars.return_value = WEEKLY
        with patch("pytdx.hq.TdxHq_API", side_effect=[empty_api, data_api]):
            prov = TdxProvider({"servers": [("1.1.1.1", 7709), ("2.2.2.2", 7709)]})
            r = prov.get_index_weekly("avg_price", "2026-03-30", "2026-05-29")
        assert r.success
        assert r.data[-1]["close"] == 31.28
        empty_api.disconnect.assert_called_once()
        data_api.disconnect.assert_called_once()

    def test_all_servers_fail_returns_error_not_raise(self):
        apis = [MagicMock(), MagicMock()]
        for api in apis:
            api.connect.return_value = False
        with patch("pytdx.hq.TdxHq_API", side_effect=apis):
            prov = TdxProvider({"servers": [("1.1.1.1", 7709), ("2.2.2.2", 7709)]})
            r = prov.get_index_weekly("avg_price", "2026-03-30", "2026-05-29")
        assert not r.success
        assert r.data is None
        for api in apis:
            api.disconnect.assert_called_once()  # 失败 connect 仍可能创建 socket

    def test_disconnect_on_fetch_exception(self):
        api = MagicMock()
        api.connect.return_value = True
        api.get_index_bars.side_effect = RuntimeError("boom")
        with patch("pytdx.hq.TdxHq_API", return_value=api):
            prov = TdxProvider({"servers": [("1.1.1.1", 7709)]})
            r = prov.get_index_weekly("avg_price", "2026-03-30", "2026-05-29")
        assert not r.success
        api.disconnect.assert_called_once()  # 取数抛异常也要释放连接
