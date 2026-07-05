from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any

from services.daily_leaders.renderer import render_markdown

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ROOT = REPO_ROOT / "data" / "reports" / "daily-leaders"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _safe_date(value: Any) -> str:
    date = str(value or "").strip()
    if not DATE_RE.fullmatch(date):
        raise ValueError("date must be YYYY-MM-DD")
    try:
        parsed = dt.date.fromisoformat(date)
    except ValueError as exc:
        raise ValueError("date must be a valid YYYY-MM-DD date") from exc
    if parsed.isoformat() != date:
        raise ValueError("date must be YYYY-MM-DD")
    return date


def write_proposal(proposal: dict[str, Any], *, root: str | Path | None = None) -> dict[str, Path]:
    date = _safe_date(proposal.get("date"))

    output_root = Path(root) if root is not None else DEFAULT_ROOT
    output_root.mkdir(parents=True, exist_ok=True)

    json_path = output_root / f"{date}.json"
    markdown_path = output_root / f"{date}.md"
    json_path.write_text(json.dumps(proposal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(proposal), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}


def read_proposal(date: str, *, root: str | Path | None = None) -> dict[str, Any]:
    safe_date = _safe_date(date)
    input_root = Path(root) if root is not None else DEFAULT_ROOT
    json_path = input_root / f"{safe_date}.json"
    return json.loads(json_path.read_text(encoding="utf-8"))
