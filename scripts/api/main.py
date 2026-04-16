"""交易复盘系统 FastAPI 后端入口。"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.routes.review import router as review_router
from api.routes.search import router as search_router
from api.routes.crud import router as crud_router
from api.routes.planning import router as planning_router
from api.routes.ingest import router as ingest_router
from api.routes.meta import router as meta_router
from api.routes.regulatory_monitor import router as regulatory_monitor_router
from api.routes.cognition import router as cognition_router

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ATTACHMENTS_DIR = _REPO_ROOT / "data" / "attachments"

app = FastAPI(title="交易复盘系统", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(review_router)
app.include_router(search_router)
app.include_router(crud_router)
app.include_router(planning_router)
app.include_router(ingest_router)
app.include_router(meta_router)
app.include_router(regulatory_monitor_router)
app.include_router(cognition_router)

# 附件图片静态路由：data/attachments/ → /attachments/
_ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/attachments", StaticFiles(directory=str(_ATTACHMENTS_DIR)), name="attachments")


@app.get("/api/health")
def health():
    return {"status": "ok"}
