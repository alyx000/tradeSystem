"""通达信平均股价分钟快照；不连接行情服务器。"""
from copy import deepcopy
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from providers.tdx_provider import AVG_PRICE_CODE, TdxProvider, _minute_quote
from utils.realtime_quote import SHANGHAI_TZ

NOW = datetime(2026, 11, 2, 10, tzinfo=SHANGHAI_TZ)


def minute_bars():
    return [
        {"datetime": "2026-11-02 09:59", "open": 25.8, "high": 26.1,
         "low": 25.7, "close": 26.0, "vol": 123, "amount": 456},
        {"datetime": "2026-11-02 10:00", "open": 26.0, "high": 26.2,
         "low": 25.8, "close": 26.1, "vol": 234, "amount": 567},
    ]


def test_minute_close_has_original_date_time_and_no_cumulative_amount_or_yesterday():
    quote = _minute_quote(minute_bars())
    assert quote == {"code": "880003.TDX", "name": "平均股价", "price": 26.1,
                     "quote_date": "2026-11-02", "quote_time": "10:00:00"}
    assert "pre_close" not in quote and "amount" not in quote and "volume" not in quote


@pytest.mark.parametrize("key,value", [
    ("close", None), ("close", "-"), ("close", True), ("close", 0),
    ("close", float("nan")), ("close", float("inf")), ("open", -1),
    ("high", 25), ("low", 27), ("datetime", None),
    ("datetime", "2026-11-02"), ("datetime", "2026-11-02 10:00:01"),
    ("datetime", "2026-11-02 08:00"), ("datetime", "2026-11-02 12:00"),
    ("code", "000001"), ("market", 0),
])
def test_invalid_minute_contract_rejected(key, value):
    bars = minute_bars()
    bars[-1][key] = value
    with pytest.raises(ValueError):
        _minute_quote(bars)


@pytest.mark.parametrize("kind", ["empty", "shallow", "reverse", "duplicate", "not_list", "bad_row"])
def test_bad_envelope_or_order_never_silently_selects_a_row(kind):
    bars = minute_bars()
    if kind == "empty":
        bars = []
    elif kind == "shallow":
        bars.pop()
    elif kind == "reverse":
        bars.reverse()
    elif kind == "duplicate":
        bars[-1]["datetime"] = bars[0]["datetime"]
    elif kind == "not_list":
        bars = {"bars": bars}
    else:
        bars[0] = None
    with pytest.raises(ValueError):
        _minute_quote(bars)


def test_request_is_explicit_one_minute_index_and_connection_always_closes():
    api = MagicMock()
    api.connect.return_value = True
    api.get_index_bars.return_value = minute_bars()
    with patch("pytdx.hq.TdxHq_API", return_value=api):
        provider = TdxProvider({"servers": [("test", 7709)]})
        result = provider.get_realtime_quotes([AVG_PRICE_CODE, AVG_PRICE_CODE], now=NOW)
    assert result.success and result.source == "tdx:880003_1min"
    assert result.data == [_minute_quote(minute_bars())]
    assert "get_realtime_quotes" in provider.get_capabilities()
    api.get_index_bars.assert_called_once_with(8, 1, "880003", 0, 2)
    api.disconnect.assert_called_once()


def test_unknown_or_mixed_instruments_rejected_before_network():
    with patch("pytdx.hq.TdxHq_API") as client:
        for codes in ([], ["avg_price"], ["000001.SH"], [AVG_PRICE_CODE, "000001.SH"]):
            result = TdxProvider().get_realtime_quotes(codes)
            assert not result.success and result.data is None
    client.assert_not_called()


def test_invalid_node_falls_back_and_disconnects_both():
    first, second = MagicMock(), MagicMock()
    first.connect.return_value = second.connect.return_value = True
    invalid = minute_bars()
    invalid[-1]["datetime"] = ""
    first.get_index_bars.return_value = invalid
    second.get_index_bars.return_value = minute_bars()
    with patch("pytdx.hq.TdxHq_API", side_effect=[first, second]):
        result = TdxProvider({"servers": [("a", 7709), ("b", 7709)]}).get_realtime_quotes([AVG_PRICE_CODE], now=NOW)
    assert result.success
    first.disconnect.assert_called_once()
    second.disconnect.assert_called_once()


def test_empty_nodes_fail_closed_and_attempt_each_only_once():
    api = MagicMock()
    api.connect.return_value = True
    api.get_index_bars.return_value = []
    with patch("pytdx.hq.TdxHq_API", return_value=api) as cls:
        result = TdxProvider({"servers": [("a", 7709), ("b", 7709)]}).get_realtime_quotes([AVG_PRICE_CODE], now=NOW)
    assert not result.success and result.data is None and "无" in result.error
    assert cls.call_count == api.disconnect.call_count == 2


def test_input_payload_not_mutated():
    bars = minute_bars()
    before = deepcopy(bars)
    _minute_quote(bars)
    assert bars == before


@pytest.mark.parametrize("bad_time", ["2026-10-30 10:00", "2026-11-02 09:49", "2026-11-02 11:00"])
def test_stale_or_future_node_cannot_hide_fresh_backup(bad_time):
    first, second = MagicMock(), MagicMock()
    first.connect.return_value = second.connect.return_value = True
    bad = minute_bars()
    bad[-1]["datetime"] = bad_time
    # 保留严格升序，不让结构错误掩盖节点新鲜度错误。
    from datetime import timedelta
    bad[0]["datetime"] = (datetime.strptime(bad_time, "%Y-%m-%d %H:%M") - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M")
    first.get_index_bars.return_value = bad
    second.get_index_bars.return_value = minute_bars()
    with patch("pytdx.hq.TdxHq_API", side_effect=[first, second]):
        result = TdxProvider({"servers": [("a", 7709), ("b", 7709)]}).get_realtime_quotes([AVG_PRICE_CODE], now=NOW)
    assert result.success and result.data[0]["price"] == 26.1
    second.get_index_bars.assert_called_once()
    first.disconnect.assert_called_once()
    second.disconnect.assert_called_once()


def test_all_stale_nodes_fail_without_quotes():
    api = MagicMock()
    api.connect.return_value = True
    bars = minute_bars()
    for row in bars:
        row["datetime"] = row["datetime"].replace("2026-11-02", "2026-10-30")
    api.get_index_bars.return_value = bars
    with patch("pytdx.hq.TdxHq_API", return_value=api):
        result = TdxProvider({"servers": [("a", 7709), ("b", 7709)]}).get_realtime_quotes([AVG_PRICE_CODE], now=NOW)
    assert not result.success and result.data is None and "陈旧" in result.error
    assert api.get_index_bars.call_count == api.disconnect.call_count == 2


def test_intraday_budget_does_not_retry_six_second_historical_timeouts(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("providers.tdx_provider.monotonic", lambda: clock[0])
    api = MagicMock()
    def connect(*args, **kwargs):
        assert 0 < kwargs["time_out"] <= 1
        clock[0] += kwargs["time_out"]
        return False
    api.connect.side_effect = connect
    with patch("pytdx.hq.TdxHq_API", return_value=api) as cls:
        result = TdxProvider({"servers": [(str(i), 7709) for i in range(20)]}).get_realtime_quotes([AVG_PRICE_CODE], now=NOW)
    assert not result.success and "预算" in result.error
    assert clock[0] == 5 and cls.call_count == api.disconnect.call_count == 5
