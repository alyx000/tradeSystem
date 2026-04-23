from __future__ import annotations

import argparse
import json
from contextlib import nullcontext

from db import cli as db_cli
from providers.base import DataResult
from services.stock_resolver import resolve_stock_codes, resolve_stock_names


class _StubRegistry:
    def __init__(self, batch_result: DataResult | None = None, list_result: DataResult | None = None):
        self.batch_result = batch_result
        self.list_result = list_result
        self.calls: list[tuple[str, tuple, dict]] = []
        self.initialized = False

    def initialize_all(self) -> dict[str, bool]:
        self.initialized = True
        return {"stub": True}

    def call(self, method_name: str, *args, **kwargs) -> DataResult:
        self.calls.append((method_name, args, kwargs))
        if method_name == "get_stock_basic_batch":
            assert self.batch_result is not None
            return self.batch_result
        if method_name == "get_stock_basic_list":
            assert self.list_result is not None
            return self.list_result
        raise AssertionError(f"unexpected method: {method_name}")


def test_resolve_stock_codes_uses_registry_batch_and_marks_missing():
    registry = _StubRegistry(
        batch_result=DataResult(
            data=[
                {"ts_code": "300750.SZ", "name": "宁德时代"},
                {"ts_code": "688041.SH", "name": "海光信息"},
            ],
            source="stub",
        )
    )

    result = resolve_stock_codes(registry, ["300750", "688041.SH", "999999"])

    assert [item["code"] for item in result["resolved"]] == ["300750.SZ", "688041.SH"]
    assert [item["query"] for item in result["not_found"]] == ["999999"]
    assert registry.calls == [("get_stock_basic_batch", (["300750", "688041.SH", "999999"],), {})]


def test_resolve_stock_names_uses_registry_list_and_marks_ambiguous():
    registry = _StubRegistry(
        list_result=DataResult(
            data=[
                {"ts_code": "300750.SZ", "name": "宁德时代"},
                {"ts_code": "000001.SZ", "name": "平安银行"},
                {"ts_code": "000002.SZ", "name": "平安银行"},
            ],
            source="stub",
        )
    )

    result = resolve_stock_names(registry, ["宁德时代", "平安银行", "不存在"], "2000-01-01")

    assert result["resolved"] == [
        {"query": "宁德时代", "code": "300750.SZ", "name": "宁德时代", "match_type": "name_exact"}
    ]
    assert result["ambiguous"] == [
        {
            "query": "平安银行",
            "candidates": [
                {"code": "000001.SZ", "name": "平安银行"},
                {"code": "000002.SZ", "name": "平安银行"},
            ],
        }
    ]
    assert result["not_found"] == [{"query": "不存在", "reason": "name_not_found"}]
    assert registry.calls == [("get_stock_basic_list", ("2000-01-01",), {})]


def test_cmd_stock_resolve_json_uses_initialized_registry(monkeypatch, capsys):
    registry = _StubRegistry(
        list_result=DataResult(
            data=[{"ts_code": "300394.SZ", "name": "天孚通信"}],
            source="stub",
        )
    )

    class _FakeMain:
        @staticmethod
        def load_config() -> dict:
            return {}

        @staticmethod
        def setup_providers(_config: dict):
            return registry

    monkeypatch.setattr(db_cli, "_resolve_main_runtime", lambda: _FakeMain)
    monkeypatch.setattr("utils.network_env.without_standard_http_proxy", lambda: nullcontext())

    args = argparse.Namespace(code=None, name=["天孚通信"], date="2000-01-01", json=True)
    db_cli._cmd_stock_resolve(args)

    payload = json.loads(capsys.readouterr().out)
    assert registry.initialized is True
    assert payload["resolved"] == [
        {"query": "天孚通信", "code": "300394.SZ", "name": "天孚通信", "match_type": "name_exact"}
    ]
