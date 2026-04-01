"""utils.network_env：采集阶段临时关闭代理环境变量。"""
from __future__ import annotations

import os

import pytest

from utils.network_env import use_http_proxy_for_data_collection, without_standard_http_proxy


def test_without_standard_http_proxy_removes_and_restores(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    with without_standard_http_proxy():
        assert "HTTP_PROXY" not in os.environ
        assert "http_proxy" not in os.environ
    assert os.environ.get("HTTP_PROXY") == "http://127.0.0.1:9"
    assert os.environ.get("http_proxy") == "http://127.0.0.1:9"


def test_without_standard_http_proxy_respects_tradesystem_flag(monkeypatch):
    monkeypatch.setenv("TRADESYSTEM_USE_HTTP_PROXY", "1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    with without_standard_http_proxy():
        assert os.environ.get("HTTPS_PROXY") == "http://127.0.0.1:9"


def test_use_http_proxy_for_data_collection(monkeypatch):
    monkeypatch.delenv("TRADESYSTEM_USE_HTTP_PROXY", raising=False)
    assert use_http_proxy_for_data_collection() is False
    monkeypatch.setenv("TRADESYSTEM_USE_HTTP_PROXY", "true")
    assert use_http_proxy_for_data_collection() is True
