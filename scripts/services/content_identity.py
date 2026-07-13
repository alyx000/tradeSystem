"""Canonical text identity shared by note ingestion and source collectors."""
from __future__ import annotations

import hashlib


def canonicalize_raw_content(raw_content: str) -> str:
    """Normalize raw text for durable storage and identity comparisons."""
    if not isinstance(raw_content, str):
        raise TypeError("raw_content must be a string")

    normalized = raw_content.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return ""
    return normalized + "\n"


def canonical_content_sha256(raw_content: str) -> str:
    """Return the lowercase SHA-256 digest of canonical UTF-8 text."""
    canonical = canonicalize_raw_content(raw_content)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
