"""Source-aware, transaction-neutral teacher note creation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import sqlite3
from typing import Any, Optional
from zoneinfo import ZoneInfo

from db import queries as Q
from services.content_identity import (
    canonical_content_sha256,
    canonicalize_raw_content,
)


_PROVENANCE_FIELDS = (
    "source_platform",
    "source_url",
    "source_article_id",
    "published_at",
    "fetched_at",
    "content_sha256",
)
_SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class TeacherNoteWriteResult:
    note_id: int
    created: bool
    matched_by: Optional[str] = None


class TeacherNoteProvenanceConflict(ValueError):
    """The supplied source identity conflicts with an existing immutable note."""


def _required_text(label: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required")
    return value.strip()


def _offset_datetime(label: str, value: str) -> datetime:
    parse_value = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(parse_value)
    except ValueError as exc:
        raise ValueError(f"{label} must be a valid ISO datetime with offset") from exc
    if parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a UTC offset")
    return parsed


def _prepare_source_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    supplied = {
        key for key in _PROVENANCE_FIELDS
        if key in payload and payload.get(key) is not None
    }
    if not supplied:
        return None

    cleaned: dict[str, str] = {}
    for key in _PROVENANCE_FIELDS:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError("source provenance bundle must be complete and non-empty")
        cleaned[key] = value.strip()

    input_by = _required_text("input_by", payload.get("input_by"))
    note_date = _required_text("date", payload.get("date"))
    title = _required_text("title", payload.get("title"))
    raw_content = payload.get("raw_content")
    if not isinstance(raw_content, str):
        raise ValueError("raw_content must be a string for sourced notes")
    canonical_content = canonicalize_raw_content(raw_content)
    if not canonical_content:
        raise ValueError("raw_content must not be empty for sourced notes")

    published = _offset_datetime("published_at", cleaned["published_at"])
    _offset_datetime("fetched_at", cleaned["fetched_at"])
    shanghai_date = published.astimezone(_SHANGHAI).date().isoformat()
    if note_date != shanghai_date:
        raise ValueError(
            "date must equal published_at converted to Asia/Shanghai "
            f"({shanghai_date})"
        )

    expected_hash = canonical_content_sha256(raw_content)
    supplied_hash = cleaned["content_sha256"].lower()
    if supplied_hash != expected_hash:
        raise ValueError("content hash does not match canonical raw_content")

    prepared = dict(payload)
    prepared.update(cleaned)
    prepared.update({
        "date": note_date,
        "title": title,
        "raw_content": canonical_content,
        "content_sha256": expected_hash,
        "input_by": input_by,
    })
    return prepared


def _find_existing_source_note(
    conn: sqlite3.Connection,
    *,
    teacher_name: str,
    payload: dict[str, Any],
) -> TeacherNoteWriteResult | None:
    article_row = conn.execute(
        """
        SELECT id, content_sha256 FROM teacher_notes
        WHERE source_platform = ? AND source_article_id = ?
        """,
        (payload["source_platform"], payload["source_article_id"]),
    ).fetchone()
    url_row = conn.execute(
        "SELECT id, content_sha256 FROM teacher_notes WHERE source_url = ?",
        (payload["source_url"],),
    ).fetchone()

    if article_row is not None and url_row is not None and article_row["id"] != url_row["id"]:
        raise TeacherNoteProvenanceConflict("ambiguous_provenance")

    identity_row = article_row or url_row
    if identity_row is not None:
        existing_hash = str(identity_row["content_sha256"] or "").strip().lower()
        if existing_hash != payload["content_sha256"]:
            raise TeacherNoteProvenanceConflict("source_content_changed")
        matched_by = "source_article_id" if article_row is not None else "source_url"
        return TeacherNoteWriteResult(
            note_id=int(identity_row["id"]),
            created=False,
            matched_by=matched_by,
        )

    teacher_row = conn.execute(
        "SELECT id FROM teachers WHERE name = ?", (teacher_name,)
    ).fetchone()
    if teacher_row is None:
        return None
    fallback_row = conn.execute(
        """
        SELECT id FROM teacher_notes
        WHERE teacher_id = ? AND date = ? AND title = ? AND content_sha256 = ?
        """,
        (
            teacher_row["id"],
            payload["date"],
            payload["title"],
            payload["content_sha256"],
        ),
    ).fetchone()
    if fallback_row is None:
        return None
    return TeacherNoteWriteResult(
        note_id=int(fallback_row["id"]),
        created=False,
        matched_by="content_fallback",
    )


def create_teacher_note_idempotent(
    conn: sqlite3.Connection,
    *,
    teacher_name: str,
    payload: dict[str, Any],
) -> TeacherNoteWriteResult:
    """Validate provenance and insert once without owning the caller transaction."""
    if not isinstance(payload, dict):
        raise TypeError("payload must be a dict")
    normalized_teacher = _required_text("teacher_name", teacher_name)
    note_payload = dict(payload)
    note_payload["date"] = _required_text("date", note_payload.get("date"))
    note_payload["title"] = _required_text("title", note_payload.get("title"))
    source_payload = _prepare_source_payload(note_payload)

    if source_payload is None:
        teacher_id = Q.get_or_create_teacher(conn, normalized_teacher)
        note_id = Q.insert_teacher_note(
            conn,
            teacher_id=teacher_id,
            **note_payload,
        )
        return TeacherNoteWriteResult(note_id=int(note_id), created=True)

    existing = _find_existing_source_note(
        conn,
        teacher_name=normalized_teacher,
        payload=source_payload,
    )
    if existing is not None:
        return existing

    teacher_id = Q.get_or_create_teacher(conn, normalized_teacher)
    try:
        note_id = Q.insert_teacher_note(
            conn,
            teacher_id=teacher_id,
            **source_payload,
        )
    except sqlite3.IntegrityError:
        raced = _find_existing_source_note(
            conn,
            teacher_name=normalized_teacher,
            payload=source_payload,
        )
        if raced is None:
            raise
        return raced
    return TeacherNoteWriteResult(note_id=int(note_id), created=True)
