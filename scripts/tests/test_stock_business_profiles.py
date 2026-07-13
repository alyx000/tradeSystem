"""个股主营资料 Provider 契约测试（全 mock，无真实网络）。"""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pandas as pd

from providers.akshare_provider import AkshareProvider
from providers.tushare_provider import TushareProvider


class _CompanyPro:
    def __init__(self, frames=None, errors=None):
        self.frames = frames or {}
        self.errors = errors or {}
        self.calls: list[dict] = []

    def stock_company(self, **kwargs):
        self.calls.append(kwargs)
        exchange = kwargs["exchange"]
        if exchange in self.errors:
            raise self.errors[exchange]
        return self.frames.get(exchange, pd.DataFrame())


def _tushare(pro: _CompanyPro | None = None, *, initialized: bool = True) -> TushareProvider:
    provider = TushareProvider({})
    provider.pro = pro
    provider._initialized = initialized
    return provider


def _akshare(api=None, *, initialized: bool = True) -> AkshareProvider:
    provider = AkshareProvider({})
    provider.ak = api
    provider._initialized = initialized
    return provider


def test_capability_is_declared_by_both_providers():
    assert "get_stock_business_profiles" in _tushare().get_capabilities()
    assert "get_stock_business_profiles" in _akshare().get_capabilities()


def test_tushare_groups_exchanges_normalizes_fields_and_filters_unrequested_rows():
    pro = _CompanyPro(
        frames={
            "SSE": pd.DataFrame(
                [
                    {
                        "ts_code": "600519.SH",
                        "introduction": " 白酒龙头 ",
                        "main_business": "酒类生产",
                        "business_scope": "食品销售",
                    },
                    {
                        "ts_code": "601318.SH",
                        "introduction": "不应保留",
                        "main_business": "保险",
                        "business_scope": "保险业务",
                    },
                ]
            ),
            "SZSE": pd.DataFrame(
                [
                    {
                        "ts_code": "000001.SZ",
                        "introduction": None,
                        "main_business": "银行业务",
                        "business_scope": float("nan"),
                    }
                ]
            ),
            "BSE": pd.DataFrame(),
        }
    )
    provider = _tushare(pro)

    result = provider.get_stock_business_profiles(
        ["600519", "000001.sz", "430047.BJ", "600519.SH"]
    )

    assert result.success
    assert set(result.data) == {"600519.SH", "000001.SZ", "430047.BJ"}
    assert result.data["600519.SH"] == {
        "ts_code": "600519.SH",
        "profile_status": "ok",
        "introduction": "白酒龙头",
        "main_business": "酒类生产",
        "business_scope": "食品销售",
        "product_types": [],
        "product_names": [],
        "source": "tushare:stock_company",
        "error": "",
    }
    assert result.data["000001.SZ"]["introduction"] == ""
    assert result.data["000001.SZ"]["business_scope"] == ""
    assert result.data["430047.BJ"]["profile_status"] == "missing"
    assert [call["exchange"] for call in pro.calls] == ["SSE", "SZSE", "BSE"]
    assert all(
        call["fields"] == "ts_code,introduction,main_business,business_scope"
        for call in pro.calls
    )


def test_tushare_partial_exchange_failure_is_per_stock_and_keeps_successful_exchange():
    pro = _CompanyPro(
        frames={
            "SSE": pd.DataFrame(
                [
                    {
                        "ts_code": "600519.SH",
                        "introduction": "简介",
                        "main_business": "主营",
                        "business_scope": "范围",
                    }
                ]
            )
        },
        errors={"SZSE": RuntimeError("sz down")},
    )

    result = _tushare(pro).get_stock_business_profiles(["600519.SH", "000001.SZ"])

    assert result.success
    assert result.data["600519.SH"]["profile_status"] == "ok"
    assert result.data["000001.SZ"]["profile_status"] == "source_failed"
    assert result.data["000001.SZ"]["error"] == "sz down"


def test_tushare_row_with_all_business_fields_empty_is_missing():
    pro = _CompanyPro(
        frames={
            "SSE": pd.DataFrame(
                [
                    {
                        "ts_code": "600519.SH",
                        "introduction": "   ",
                        "main_business": None,
                        "business_scope": float("nan"),
                    }
                ]
            )
        }
    )

    result = _tushare(pro).get_stock_business_profiles(["600519.SH"])

    assert result.success
    assert result.data["600519.SH"]["profile_status"] == "missing"
    assert result.data["600519.SH"]["introduction"] == ""
    assert result.data["600519.SH"]["main_business"] == ""
    assert result.data["600519.SH"]["business_scope"] == ""


def test_tushare_all_grouped_exchanges_failed_returns_top_level_error():
    pro = _CompanyPro(
        errors={
            "SSE": RuntimeError("sse down"),
            "SZSE": RuntimeError("sz down"),
        }
    )

    result = _tushare(pro).get_stock_business_profiles(["600519.SH", "000001.SZ"])

    assert not result.success
    assert result.data is None
    assert "all_exchanges_failed" in result.error
    assert "sse down" in result.error
    assert "sz down" in result.error


def test_tushare_empty_input_does_not_require_initialization():
    result = _tushare(None, initialized=False).get_stock_business_profiles([])
    assert result.success
    assert result.data == {}


def test_tushare_semantically_empty_input_does_not_require_initialization():
    result = _tushare(None, initialized=False).get_stock_business_profiles([None, ""])
    assert result.success
    assert result.data == {}


def test_uninitialized_providers_return_stable_top_level_error():
    expected = "provider_not_initialized: get_stock_business_profiles"

    tushare_result = _tushare(None, initialized=False).get_stock_business_profiles(["600519.SH"])
    akshare_result = _akshare(None, initialized=False).get_stock_business_profiles(["600519.SH"])

    assert tushare_result.data is None
    assert tushare_result.error == expected
    assert akshare_result.data is None
    assert akshare_result.error == expected


def test_akshare_one_stock_failure_does_not_affect_other_and_products_are_deduped():
    api = MagicMock()

    def _fetch(symbol: str):
        if symbol == "000001":
            raise RuntimeError("one stock failed")
        return pd.DataFrame(
            [
                {
                    "主营业务": "动力电池",
                    "经营范围": "电池制造",
                    "产品类型": "电池；储能, 电池",
                    "产品名称": "麒麟电池/储能柜、麒麟电池",
                }
            ]
        )

    api.stock_zyjs_ths.side_effect = _fetch

    result = _akshare(api).get_stock_business_profiles(["000001", "300750.SZ"])

    assert result.success
    assert result.data["000001.SZ"]["profile_status"] == "source_failed"
    assert result.data["000001.SZ"]["error"] == "one stock failed"
    assert result.data["300750.SZ"] == {
        "ts_code": "300750.SZ",
        "profile_status": "ok",
        "introduction": "",
        "main_business": "动力电池",
        "business_scope": "电池制造",
        "product_types": ["电池", "储能"],
        "product_names": ["麒麟电池", "储能柜"],
        "source": "akshare:stock_zyjs_ths",
        "error": "",
    }


def test_akshare_empty_dataframe_is_missing_and_bse_code_is_normalized():
    api = MagicMock()
    api.stock_zyjs_ths.return_value = pd.DataFrame()

    result = _akshare(api).get_stock_business_profiles(["430047"])

    assert result.success
    assert result.data["430047.BJ"]["profile_status"] == "missing"
    assert result.data["430047.BJ"]["source"] == "akshare:stock_zyjs_ths"


def test_akshare_row_with_all_business_fields_empty_is_missing():
    api = MagicMock()
    api.stock_zyjs_ths.return_value = pd.DataFrame(
        [
            {
                "主营业务": None,
                "经营范围": "   ",
                "产品类型": float("nan"),
                "产品名称": "",
            }
        ]
    )

    result = _akshare(api).get_stock_business_profiles(["600519.SH"])

    assert result.success
    profile = result.data["600519.SH"]
    assert profile["profile_status"] == "missing"
    assert profile["main_business"] == ""
    assert profile["business_scope"] == ""
    assert profile["product_types"] == []
    assert profile["product_names"] == []


def test_akshare_hard_timeout_terminates_worker_process(monkeypatch):
    import providers.akshare_provider as module

    monkeypatch.setattr(module, "BUSINESS_PROFILE_TIMEOUT_SECONDS", 0.05)
    api = MagicMock()

    def _slow(symbol: str):
        assert symbol == "600519"
        time.sleep(1)
        return pd.DataFrame([{"主营业务": "不应按时返回"}])

    api.stock_zyjs_ths.side_effect = _slow
    provider = _akshare(api)

    started = time.monotonic()
    result = provider.get_stock_business_profiles(["600519.SH"])
    elapsed = time.monotonic() - started

    assert elapsed < 0.5
    assert result.success
    assert result.data["600519.SH"]["profile_status"] == "source_failed"
    assert result.data["600519.SH"]["error"] == "timeout"


def test_akshare_timeout_keeps_profiles_completed_before_deadline(monkeypatch):
    import providers.akshare_provider as module

    monkeypatch.setattr(module, "BUSINESS_PROFILE_TIMEOUT_SECONDS", 0.15)
    api = MagicMock()

    def _mixed(symbol: str):
        if symbol == "000001":
            time.sleep(1)
        return pd.DataFrame([{"主营业务": f"主营-{symbol}"}])

    api.stock_zyjs_ths.side_effect = _mixed

    result = _akshare(api).get_stock_business_profiles(["000001.SZ", "600519.SH"])

    assert result.success
    assert result.data["600519.SH"]["profile_status"] == "ok"
    assert result.data["600519.SH"]["main_business"] == "主营-600519"
    assert result.data["000001.SZ"]["profile_status"] == "source_failed"
    assert result.data["000001.SZ"]["error"] == "timeout"


def test_akshare_unexpected_worker_exit_marks_pending_profiles(monkeypatch):
    import os
    import providers.akshare_provider as module

    def _crash(_ak, _codes, _queue):
        os._exit(3)

    monkeypatch.setattr(module, "_akshare_business_profile_worker", _crash)

    result = _akshare(MagicMock()).get_stock_business_profiles(["600519.SH"])

    assert result.success
    assert result.data["600519.SH"]["profile_status"] == "source_failed"
    assert result.data["600519.SH"]["error"] == "worker_process_failed"


def test_akshare_sigterm_ignoring_worker_is_killed_and_reaped(monkeypatch):
    import multiprocessing
    import os
    import signal
    import providers.akshare_provider as module

    real_context = multiprocessing.get_context("fork")
    captured_processes = []
    captured_pids: list[int] = []

    class _CapturingProcess:
        def __init__(self, process):
            self._process = process

        def start(self):
            self._process.start()
            captured_pids.append(self._process.pid)

        def is_alive(self):
            try:
                return self._process.is_alive()
            except ValueError:
                return False

        def __getattr__(self, name):
            return getattr(self._process, name)

    class _CapturingContext:
        def Queue(self):
            return real_context.Queue()

        def Process(self, *args, **kwargs):
            process = _CapturingProcess(real_context.Process(*args, **kwargs))
            captured_processes.append(process)
            return process

    def _ignore_sigterm(_ak, _codes, result_queue):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        result_queue.put(("ready", "", None))
        time.sleep(5)

    monkeypatch.setattr(module, "BUSINESS_PROFILE_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(module, "_akshare_business_profile_worker", _ignore_sigterm)
    monkeypatch.setattr(module.multiprocessing, "get_context", lambda _method: _CapturingContext())

    try:
        result = _akshare(MagicMock()).get_stock_business_profiles(["600519.SH"])
        assert result.success
        assert result.data["600519.SH"]["error"] == "timeout"
        assert captured_pids
        assert all(not process.is_alive() for process in captured_processes)
        assert all(child.pid not in captured_pids for child in multiprocessing.active_children())
        for pid in captured_pids:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            raise AssertionError(f"worker pid still alive: {pid}")
    finally:
        for process in captured_processes:
            try:
                if process.is_alive():
                    process.kill()
                    process.join(timeout=1)
            except ValueError:
                pass


def test_akshare_broken_result_queue_returns_worker_process_failed(monkeypatch):
    import providers.akshare_provider as module

    class _BrokenQueue:
        def get(self, **_kwargs):
            raise EOFError("queue pipe closed")

        def get_nowait(self):
            raise EOFError("queue pipe closed")

        def cancel_join_thread(self):
            raise RuntimeError("cancel failed")

        def close(self):
            raise RuntimeError("close failed")

    class _DeadProcess:
        exitcode = 3

        def start(self):
            return None

        def is_alive(self):
            return False

        def join(self, **_kwargs):
            return None

        def close(self):
            return None

    class _BrokenContext:
        def Queue(self):
            return _BrokenQueue()

        def Process(self, **_kwargs):
            return _DeadProcess()

    monkeypatch.setattr(module.multiprocessing, "get_context", lambda _method: _BrokenContext())

    result = _akshare(MagicMock()).get_stock_business_profiles(["600519.SH"])

    assert result.success
    assert result.data["600519.SH"]["profile_status"] == "source_failed"
    assert result.data["600519.SH"]["error"] == "worker_process_failed"


def test_akshare_unreapable_worker_returns_clear_top_level_error(monkeypatch):
    import queue
    import providers.akshare_provider as module

    class _EmptyQueue:
        def get(self, **_kwargs):
            raise queue.Empty

        def cancel_join_thread(self):
            return None

        def close(self):
            return None

    class _ImmortalProcess:
        def start(self):
            return None

        def is_alive(self):
            return True

        def terminate(self):
            return None

        def kill(self):
            return None

        def join(self, **_kwargs):
            return None

        def close(self):
            raise AssertionError("still-live process must not be closed")

    class _ImmortalContext:
        def Queue(self):
            return _EmptyQueue()

        def Process(self, **_kwargs):
            return _ImmortalProcess()

    monkeypatch.setattr(module, "BUSINESS_PROFILE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(module.multiprocessing, "get_context", lambda _method: _ImmortalContext())

    result = _akshare(MagicMock()).get_stock_business_profiles(["600519.SH"])

    assert not result.success
    assert result.data is None
    assert result.error.startswith("worker_cleanup_failed:")
    assert "worker still alive after kill" in result.error


def test_akshare_fork_unavailable_is_a_clear_top_level_error(monkeypatch):
    import providers.akshare_provider as module

    def _unavailable(_method: str):
        raise ValueError("fork disabled")

    monkeypatch.setattr(module.multiprocessing, "get_context", _unavailable)

    result = _akshare(MagicMock()).get_stock_business_profiles(["600519.SH"])

    assert not result.success
    assert result.data is None
    assert result.error == "fork_unavailable: fork disabled"


def test_akshare_empty_input_does_not_require_initialization():
    result = _akshare(None, initialized=False).get_stock_business_profiles([])
    assert result.success
    assert result.data == {}
