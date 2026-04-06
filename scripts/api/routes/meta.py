"""仓库元数据路由。"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException


router = APIRouter(prefix="/api/meta", tags=["meta"])

_REPO_ROOT = Path(__file__).resolve().parents[3]
_COMMANDS_JSON = _REPO_ROOT / "docs" / "commands.json"


@router.get("/commands")
def get_commands_index():
    if not _COMMANDS_JSON.exists():
        raise HTTPException(404, "commands.json not found; run make commands-doc")
    try:
        return json.loads(_COMMANDS_JSON.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(500, f"invalid commands.json: {exc}") from exc
