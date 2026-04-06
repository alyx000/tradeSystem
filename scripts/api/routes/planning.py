"""交易计划与资料层 API。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_provider_registry
from services.knowledge_service import KnowledgeService
from services.planning_service import PlanningService

router = APIRouter(prefix="/api", tags=["planning"])


@router.post("/knowledge/assets")
def create_knowledge_asset(body: dict):
    service = KnowledgeService()
    try:
        return service.add_asset(
            asset_type=body.get("asset_type", "manual_note"),
            title=body["title"],
            content=body["content"],
            source=body.get("source"),
            tags=body.get("tags", []),
        )
    except KeyError as exc:
        raise HTTPException(422, f"missing field: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/knowledge/assets")
def list_knowledge_assets(
    limit: int = 50,
    offset: int = 0,
    asset_type: Optional[str] = None,
    keyword: Optional[str] = None,
    created_from: Optional[str] = None,
    created_to: Optional[str] = None,
):
    service = KnowledgeService()
    return service.list_assets(
        limit=limit,
        offset=offset,
        asset_type=asset_type,
        keyword=keyword,
        created_from=created_from,
        created_to=created_to,
    )


@router.post("/knowledge/assets/{asset_id}/draft")
def draft_from_asset(asset_id: str, body: Optional[dict] = None):
    service = KnowledgeService()
    payload = body or {}
    trade_date = payload.get("trade_date")
    if not trade_date:
        raise HTTPException(422, "missing field: trade_date")
    try:
        return service.draft_from_asset(
            asset_id=asset_id,
            trade_date=trade_date,
            input_by=payload.get("input_by"),
        )
    except KeyError:
        raise HTTPException(404, "asset not found")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.delete("/knowledge/assets/{asset_id}")
def delete_knowledge_asset(asset_id: str):
    service = KnowledgeService()
    n = service.delete_asset(asset_id)
    if n == 0:
        raise HTTPException(404, "asset not found")
    return {"ok": True}


@router.post("/knowledge/teacher-notes/{note_id}/draft")
def draft_from_teacher_note(note_id: int, body: Optional[dict] = None):
    service = KnowledgeService()
    payload = body or {}
    trade_date = payload.get("trade_date")
    if not trade_date:
        raise HTTPException(422, "missing field: trade_date")
    try:
        return service.draft_from_teacher_note(
            note_id=note_id,
            trade_date=trade_date,
            input_by=payload.get("input_by"),
        )
    except KeyError:
        raise HTTPException(404, "teacher note not found")


@router.post("/plans/drafts")
def create_plan_draft(body: dict):
    service = PlanningService()
    observation_ids = body.get("source_observation_ids", [])
    if not observation_ids:
        observation = service.create_observation(
            trade_date=body["trade_date"],
            source_type=body.get("source_type", "manual"),
            title=body.get("title"),
            market_facts=body.get("market_facts", {}),
            sector_facts=body.get("sector_facts", {}),
            stock_facts=body.get("stock_facts", []),
            judgements=body.get("judgements", []),
            input_by=body.get("input_by"),
        )
        observation_ids = [observation["observation_id"]]
    try:
        draft = service.create_draft(
            trade_date=body["trade_date"],
            source_observation_ids=observation_ids,
            title=body.get("title"),
            summary=body.get("summary"),
            input_by=body.get("input_by"),
        )
        return draft
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/plans/observations")
def list_plan_observations(date: Optional[str] = None, limit: int = 20):
    service = PlanningService()
    return service.list_observations(trade_date=date, limit=limit)


@router.put("/plans/observations/{observation_id}")
def update_plan_observation(observation_id: str, body: Optional[dict] = None):
    service = PlanningService()
    payload = body or {}
    try:
        return service.update_observation(
            observation_id,
            title=payload.get("title"),
            market_facts=payload.get("market_facts"),
            sector_facts=payload.get("sector_facts"),
            stock_facts=payload.get("stock_facts"),
            judgements=payload.get("judgements"),
            source_refs=payload.get("source_refs"),
            input_by=payload.get("input_by"),
        )
    except KeyError:
        raise HTTPException(404, "observation not found")


@router.get("/plans/drafts")
def list_plan_drafts(date: Optional[str] = None, limit: int = 20):
    service = PlanningService()
    return service.list_drafts(trade_date=date, limit=limit)


@router.get("/plans/drafts/{draft_id}")
def get_plan_draft(draft_id: str):
    service = PlanningService()
    draft = service.get_draft(draft_id=draft_id)
    if not draft:
        raise HTTPException(404, "draft not found")
    return draft


@router.put("/plans/drafts/{draft_id}")
def update_plan_draft(draft_id: str, body: Optional[dict] = None):
    service = PlanningService()
    payload = body or {}
    try:
        return service.update_draft(
            draft_id,
            title=payload.get("title"),
            summary=payload.get("summary"),
            market_view=payload.get("market_view"),
            sector_view=payload.get("sector_view"),
            stock_focus=payload.get("stock_focus"),
            style_view=payload.get("style_view"),
            assumptions=payload.get("assumptions"),
            ambiguities=payload.get("ambiguities"),
            missing_fields=payload.get("missing_fields"),
            watch_items=payload.get("watch_items"),
            fact_check_candidates=payload.get("fact_check_candidates"),
            judgement_check_candidates=payload.get("judgement_check_candidates"),
            status=payload.get("status"),
            input_by=payload.get("input_by"),
        )
    except KeyError:
        raise HTTPException(404, "draft not found")


@router.post("/plans/{draft_id}/confirm")
def confirm_plan(draft_id: str, body: Optional[dict] = None):
    service = PlanningService()
    payload = body or {}
    try:
        return service.confirm_plan(
            draft_id=draft_id,
            trade_date=payload["trade_date"],
            input_by=payload.get("input_by"),
        )
    except KeyError:
        raise HTTPException(404, "draft not found")


@router.get("/plans/{plan_id}")
def get_plan(plan_id: str):
    service = PlanningService()
    plan = service.get_plan(plan_id=plan_id)
    if not plan:
        raise HTTPException(404, "plan not found")
    return plan


@router.put("/plans/{plan_id}")
def update_plan(plan_id: str, body: Optional[dict] = None):
    service = PlanningService()
    payload = body or {}
    try:
        return service.update_plan(
            plan_id,
            title=payload.get("title"),
            market_bias=payload.get("market_bias"),
            main_themes=payload.get("main_themes"),
            focus_style=payload.get("focus_style"),
            watch_items=payload.get("watch_items"),
            risk_notes=payload.get("risk_notes"),
            invalidations=payload.get("invalidations"),
            execution_notes=payload.get("execution_notes"),
            status=payload.get("status"),
            input_by=payload.get("input_by"),
        )
    except KeyError:
        raise HTTPException(404, "plan not found")
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.get("/plans")
def list_plans(date: Optional[str] = None, limit: int = 20):
    service = PlanningService()
    return service.list_plans(trade_date=date, limit=limit)


@router.get("/plans/{plan_id}/diagnostics")
def get_plan_diagnostics(plan_id: str, registry=Depends(get_provider_registry)):
    service = PlanningService(registry=registry)
    diagnostics = service.diagnose_plan(plan_id=plan_id)
    if not diagnostics:
        raise HTTPException(404, "plan not found")
    return diagnostics


@router.post("/plans/{plan_id}/review")
def review_plan(plan_id: str, body: Optional[dict] = None):
    service = PlanningService()
    payload = body or {}
    try:
        return service.review_plan(
            plan_id=plan_id,
            trade_date=payload["trade_date"],
            outcome_summary=payload.get("outcome_summary", "待补充"),
            input_by=payload.get("input_by"),
        )
    except KeyError:
        raise HTTPException(404, "plan not found")
    except ValueError as exc:
        raise HTTPException(422, str(exc))
