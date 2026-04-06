"""采集底座 API。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException

from services.ingest_service import IngestService

router = APIRouter(prefix="/api/ingest", tags=["ingest"])


@router.get("/interfaces")
def list_ingest_interfaces():
    service = IngestService()
    return service.list_interfaces()


@router.get("/inspect")
def inspect_ingest(date: str, interface: Optional[str] = None, stage: Optional[str] = None):
    service = IngestService()
    return service.inspect(date, interface_name=interface, stage=stage)


@router.get("/runs")
def list_ingest_runs(date: str, interface: Optional[str] = None, stage: Optional[str] = None):
    service = IngestService()
    return service.inspect(date, interface_name=interface, stage=stage)["runs"]


@router.get("/errors")
def list_ingest_errors(date: str, interface: Optional[str] = None, stage: Optional[str] = None):
    service = IngestService()
    return service.inspect(date, interface_name=interface, stage=stage)["errors"]


@router.post("/run")
def run_ingest_stage(body: dict):
    service = IngestService()
    try:
        return service.execute_stage(
            stage=body["stage"],
            target_date=body["date"],
            triggered_by="api",
            input_by=body.get("input_by"),
        )
    except KeyError as exc:
        raise HTTPException(422, f"missing field: {exc}") from exc


@router.post("/run-interface")
def run_ingest_interface(body: dict):
    service = IngestService()
    try:
        result = service.execute_interface(
            name=body["name"],
            target_date=body["date"],
            triggered_by="api",
            input_by=body.get("input_by"),
        )
    except KeyError as exc:
        raise HTTPException(422, f"missing field: {exc}") from exc
    if result["status"] == "not_found":
        raise HTTPException(404, "interface not found")
    return result


@router.get("/retry")
def retry_ingest_summary(interface: Optional[str] = None, stage: Optional[str] = None):
    service = IngestService()
    return service.retry_summary(interface_name=interface, stage=stage)


@router.get("/health")
def ingest_health_summary(
    date: str,
    days: int = 7,
    limit: int = 10,
    stage: Optional[str] = None,
    interface: Optional[str] = None,
):
    service = IngestService()
    return service.health_summary(
        end_date=date,
        days=days,
        limit=limit,
        stage=stage,
        interface_name=interface,
    )


@router.post("/reconcile")
def reconcile_ingest_runs(body: Optional[dict] = None):
    service = IngestService()
    body = body or {}
    return service.reconcile_stale_runs(stale_minutes=int(body.get("stale_minutes", 5)))


@router.post("/retry-run")
def retry_ingest_groups(body: Optional[dict] = None):
    service = IngestService()
    body = body or {}
    limit = body.get("limit")
    return service.retry_unresolved_groups(
        limit=int(limit) if limit is not None else None,
        triggered_by="api",
        input_by=body.get("input_by"),
    )
