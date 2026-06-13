"""L2: 业绩预告/快报接口注册 + 落库联通测试。"""
from __future__ import annotations

import json

from db.connection import get_connection
from db.migrate import migrate
from ingest.registry import get_interface
from providers.base import DataResult
from services.ingest_service import IngestService


class _FakeProvider:
    name = "fake"

    def __init__(self, supported: set[str], results: dict[str, DataResult]):
        self._supported = supported
        self._results = results

    def supports(self, method_name: str) -> bool:
        return method_name in self._supported


class _FakeRegistry:
    def __init__(self, provider: _FakeProvider):
        self.providers = [provider]
        self._provider = provider
        self.calls: list[tuple[str, tuple]] = []

    def call(self, method_name: str, *args, **kwargs) -> DataResult:
        self.calls.append((method_name, args))
        return self._provider._results[method_name]


def _forecast_rows():
    return [
        {"ts_code": "000017.SZ", "code": "000017.SZ", "ann_date": "20260611",
         "end_date": "20260630", "type": "预增", "net_profit_min": 3600.0,
         "net_profit_max": 5400.0, "update_flag": "0"},
        {"ts_code": "000066.SZ", "code": "000066.SZ", "ann_date": "20260612",
         "end_date": "20260630", "type": "扭亏", "net_profit_min": 10000.0,
         "net_profit_max": 14500.0, "update_flag": "0"},
    ]


def _service(tmp_path, results: dict[str, DataResult]):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    migrate(conn)
    conn.close()
    provider = _FakeProvider(set(results), results)
    registry = _FakeRegistry(provider)
    return IngestService(str(db_path), registry=registry), registry, db_path


def test_registry_entries_registered():
    """两接口注册且字段符合方案：post_extended / date_range_day / 不进默认主流程。"""
    for name, method in (
        ("earnings_forecast", "get_earnings_forecast"),
        ("earnings_express", "get_earnings_express"),
    ):
        cfg = get_interface(name)
        assert cfg is not None, f"{name} 未注册"
        assert cfg["provider_method"] == method
        assert cfg["stage"] == "post_extended"
        assert cfg["params_policy"] == "date_range_day"
        assert cfg["dedupe_keys"] == ["start_date", "end_date"]
        assert cfg["raw_table"] == f"raw_{name}"
        assert cfg["enabled_by_default"] is False
        # 审计 params 标称单日 vs provider 实际回看窗口的映射声明必须写进 notes
        assert "窗口" in cfg["notes"]


def test_execute_interface_stores_payload(tmp_path):
    service, registry, db_path = _service(tmp_path, {
        "get_earnings_forecast": DataResult(
            data=_forecast_rows(), source="tushare:forecast_vip",
            note="ann_date_window=[20260610,20260612]",
        ),
    })
    result = service.execute_interface("earnings_forecast", "2026-06-12", input_by="manual")
    assert result["status"] == "success"
    assert result["run"]["row_count"] == 2

    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT payload_json, row_count, status, source_meta_json FROM raw_interface_payloads"
        " WHERE interface_name = 'earnings_forecast'"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row["row_count"] == 2
    assert row["status"] == "success"
    payload = json.loads(row["payload_json"])
    assert {r["ts_code"] for r in payload["rows"]} == {"000017.SZ", "000066.SZ"}
    # provider 的实际取数窗口（note）须随 source_meta 存档，保证可回溯
    assert "ann_date_window" in row["source_meta_json"]
    # registry 收到的是 target_date 单参（窗口由 provider 内部算）
    assert registry.calls == [("get_earnings_forecast", ("2026-06-12",))]


def test_execute_interface_idempotent_upsert(tmp_path):
    """同日重跑 = payload 覆盖更新，不产生第二行（dedupe 幂等）。"""
    service, _, db_path = _service(tmp_path, {
        "get_earnings_forecast": DataResult(data=_forecast_rows(), source="tushare:forecast_vip"),
    })
    service.execute_interface("earnings_forecast", "2026-06-12")
    service.execute_interface("earnings_forecast", "2026-06-12")

    conn = get_connection(db_path)
    count = conn.execute(
        "SELECT COUNT(*) AS c FROM raw_interface_payloads WHERE interface_name = 'earnings_forecast'"
    ).fetchone()["c"]
    runs = conn.execute(
        "SELECT COUNT(*) AS c FROM ingest_runs WHERE interface_name = 'earnings_forecast'"
    ).fetchone()["c"]
    conn.close()
    assert count == 1  # payload 级覆盖
    assert runs == 2   # 审计每次都记


def test_empty_rerun_does_not_erase_success_payload(tmp_path):
    """codex 回归：同日 empty 重跑不得抹掉已有 success payload（事实层数据保留）。"""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    migrate(conn)
    conn.close()
    results = {"get_earnings_forecast": DataResult(data=_forecast_rows(), source="tushare:forecast_vip")}
    provider = _FakeProvider(set(results), results)
    registry = _FakeRegistry(provider)
    service = IngestService(str(db_path), registry=registry)
    first = service.execute_interface("earnings_forecast", "2026-06-12")
    assert first["status"] == "success"

    # 同日重跑撞上数据源瞬时空窗
    provider._results["get_earnings_forecast"] = DataResult(data=[], source="tushare:forecast_vip")
    second = service.execute_interface("earnings_forecast", "2026-06-12")
    assert second["status"] == "empty"  # 审计如实记录本次空跑

    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT row_count, status FROM raw_interface_payloads WHERE interface_name='earnings_forecast'"
    ).fetchone()
    conn.close()
    assert row["row_count"] == 2      # 数据未被空结果抹掉
    assert row["status"] == "success"


def test_nonempty_rerun_still_overwrites(tmp_path):
    """非空新结果照常覆盖（修正后的更多行场景不受守卫影响）。"""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    migrate(conn)
    conn.close()
    results = {"get_earnings_forecast": DataResult(data=_forecast_rows()[:1], source="t")}
    provider = _FakeProvider(set(results), results)
    service = IngestService(str(db_path), registry=_FakeRegistry(provider))
    service.execute_interface("earnings_forecast", "2026-06-12")
    provider._results["get_earnings_forecast"] = DataResult(data=_forecast_rows(), source="t")
    service.execute_interface("earnings_forecast", "2026-06-12")

    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT row_count FROM raw_interface_payloads WHERE interface_name='earnings_forecast'"
    ).fetchone()
    conn.close()
    assert row["row_count"] == 2


def test_execute_interface_empty_window(tmp_path):
    """空窗口（淡季无公告）→ status=empty，不算失败。"""
    service, _, _ = _service(tmp_path, {
        "get_earnings_express": DataResult(data=[], source="tushare:express_vip"),
    })
    result = service.execute_interface("earnings_express", "2026-06-12")
    assert result["status"] == "empty"


def test_execute_interface_provider_error_recorded(tmp_path):
    """provider 失败 → run failed + ingest_errors 落审计（可重试）。"""
    service, _, db_path = _service(tmp_path, {
        "get_earnings_forecast": DataResult(
            data=None, source="tushare", error="forecast_vip window=[...] 分页超过 50 页上限，疑似异常",
        ),
    })
    result = service.execute_interface("earnings_forecast", "2026-06-12")
    assert result["status"] == "failed"

    conn = get_connection(db_path)
    err = conn.execute(
        "SELECT error_message, retryable FROM ingest_errors WHERE interface_name = 'earnings_forecast'"
    ).fetchone()
    conn.close()
    assert err is not None
    assert "分页" in err["error_message"]
    assert err["retryable"] == 1
