"""ProviderRegistry：优先级与降级，无真实数据源。"""
from __future__ import annotations

from providers.base import DataProvider, DataResult
from providers.registry import ProviderRegistry


class _StubProvider(DataProvider):
    """可配置返回值与 capabilities 的桩。"""

    def __init__(self, name: str, priority: int, caps: list[str], handler):
        super().__init__()
        self.name = name
        self.priority = priority
        self._caps = caps
        self._handler = handler

    def initialize(self) -> bool:
        return True

    def get_capabilities(self) -> list[str]:
        return self._caps

    def get_data(self, x: str) -> DataResult:
        return self._handler(x)


def test_call_falls_back_to_second_provider():
    reg = ProviderRegistry()

    def hi(_x):
        return DataResult(data=None, source="hi", error="upstream fail")

    def lo(x):
        return DataResult(data={"v": x}, source="lo")

    reg.register(_StubProvider("hi", 1, ["get_data"], hi))
    reg.register(_StubProvider("lo", 2, ["get_data"], lo))

    r = reg.call("get_data", "ping")
    assert r.success
    assert r.data == {"v": "ping"}
    assert r.source == "lo"


def test_call_skips_provider_without_capability():
    reg = ProviderRegistry()

    def only_lo(x):
        return DataResult(data={"ok": x}, source="lo")

    reg.register(_StubProvider("no_method", 1, ["other"], lambda _: DataResult(error="x")))
    reg.register(_StubProvider("lo", 2, ["get_data"], only_lo))

    r = reg.call("get_data", "a")
    assert r.success and r.data == {"ok": "a"}


def test_call_all_fail_returns_registry_error():
    reg = ProviderRegistry()

    def fail(_):
        return DataResult(data=None, source="x", error="bad")

    reg.register(_StubProvider("a", 1, ["get_data"], fail))
    reg.register(_StubProvider("b", 2, ["get_data"], fail))

    r = reg.call("get_data", "z")
    assert not r.success
    assert "所有数据源均失败" in r.error
    assert "a: bad" in r.error and "b: bad" in r.error


def test_call_specific_no_fallback():
    reg = ProviderRegistry()
    reg.register(_StubProvider("only", 1, ["get_data"], lambda x: DataResult(data=x, source="only")))
    r = reg.call_specific("only", "get_data", "direct")
    assert r.success and r.data == "direct"

    r2 = reg.call_specific("missing", "get_data", "x")
    assert not r2.success and "未找到数据源" in r2.error
