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
def inspect_ingest(date: str):
    service = IngestService()
    return service.inspect(date)


@router.get("/runs")
def list_ingest_runs(date: str):
    service = IngestService()
    return service.inspect(date)["runs"]


@router.get("/errors")
def list_ingest_errors(date: str):
    service = IngestService()
    return service.inspect(date)["errors"]


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
def retry_ingest_summary():
    service = IngestService()
    return service.retry_summary()

