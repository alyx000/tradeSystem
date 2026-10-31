"""镜像路由与失败语义回归，全 mock。"""
from unittest.mock import Mock

import pytest
import requests

from providers.tushare_transport import TushareMirrorClient


def test_mirror_uses_root_url_and_keeps_api_fields_and_pagination(monkeypatch):
    response = Mock()
    response.json.return_value = {"code": 0, "data": {
        "fields": ["ts_code"], "items": [["600000.SH"]]}}
    post = Mock(return_value=response)
    monkeypatch.setattr("providers.tushare_transport.requests.post", post)
    client = TushareMirrorClient("test-token")
    frame = client.stock_basic(fields="ts_code", list_status="L", limit=5000, offset=0)
    assert frame.to_dict("records") == [{"ts_code": "600000.SH"}]
    post.assert_called_once_with("http://tushare.xyz", json={
        "api_name": "stock_basic", "token": "test-token", "fields": "ts_code",
        "params": {"list_status": "L", "limit": 5000, "offset": 0}}, timeout=30)
    response.raise_for_status.assert_called_once()


def test_http_failure_is_not_swallowed_as_empty_dataframe(monkeypatch):
    response = Mock()
    response.raise_for_status.side_effect = requests.HTTPError("404")
    monkeypatch.setattr("providers.tushare_transport.requests.post", Mock(return_value=response))
    with pytest.raises(requests.HTTPError):
        TushareMirrorClient("fake").trade_cal()
    response.json.assert_not_called()


@pytest.mark.parametrize("payload", [None, {}, {"code": 0},
    {"code": 0, "data": {"fields": "wrong", "items": []}}])
def test_malformed_envelope_is_failure(monkeypatch, payload):
    response = Mock()
    response.json.return_value = payload
    monkeypatch.setattr("providers.tushare_transport.requests.post", Mock(return_value=response))
    with pytest.raises(ValueError):
        TushareMirrorClient("fake").query("stock_basic")


def test_valid_empty_query_and_source_error_are_distinct(monkeypatch):
    response = Mock()
    response.json.side_effect = [
        {"code": 0, "data": {"fields": ["ts_code"], "items": []}},
        {"code": -1, "msg": "Token已过期"},
    ]
    monkeypatch.setattr("providers.tushare_transport.requests.post", Mock(return_value=response))
    client = TushareMirrorClient("fake")
    assert client.query("stock_basic", list_status="P").empty
    with pytest.raises(RuntimeError, match="Token已过期"):
        client.query("stock_basic", list_status="L")
