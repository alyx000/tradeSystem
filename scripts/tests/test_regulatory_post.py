"""监管异动 overview 接入盘后命令的失败隔离测试。"""
from __future__ import annotations


def test_regulatory_overview_post_helper_returns_failed_instead_of_raising(
    monkeypatch,
):
    import main

    class _FailingService:
        def __init__(self, *, registry=None, db_path=None):
            self.registry = registry

        def build(self, target_date: str, *, persist: bool = True):
            raise RuntimeError("overview source unavailable")

    monkeypatch.setattr(
        "services.regulatory_overview.RegulatoryOverviewService",
        _FailingService,
    )
    registry = object()

    result = main._run_regulatory_overview_for_post(
        {},
        "2026-07-23",
        registry,
    )

    assert result["status"] == "failed"
    assert "overview source unavailable" in result["error"]


def test_regulatory_overview_post_helper_persists_with_initialized_registry(
    monkeypatch,
):
    import main

    calls: list[dict] = []

    class _Service:
        def __init__(self, *, registry=None, db_path=None):
            calls.append({"registry": registry, "db_path": db_path})

        def build(self, target_date: str, *, persist: bool = True):
            calls.append({"target_date": target_date, "persist": persist})
            return {"status": "success", "snapshot_date": target_date}

    monkeypatch.setattr(
        "services.regulatory_overview.RegulatoryOverviewService",
        _Service,
    )
    registry = object()

    result = main._run_regulatory_overview_for_post(
        {},
        "2026-07-23",
        registry,
    )

    assert result == {"status": "success", "snapshot_date": "2026-07-23"}
    assert calls[0]["registry"] is registry
    assert calls[1] == {"target_date": "2026-07-23", "persist": True}
