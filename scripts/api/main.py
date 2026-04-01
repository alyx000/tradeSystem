"""交易复盘系统 FastAPI 后端入口。"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes.review import router as review_router
from api.routes.search import router as search_router
from api.routes.crud import router as crud_router

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


@app.get("/api/health")
def health():
    return {"status": "ok"}
