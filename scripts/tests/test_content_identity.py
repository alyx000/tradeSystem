from __future__ import annotations

import hashlib

import pytest

from services.content_identity import canonical_content_sha256, canonicalize_raw_content


def test_canonicalize_raw_content_vectors() -> None:
    assert canonicalize_raw_content("  第一行\r\n第二行  \r\n\r\n") == "第一行\n第二行\n"
    assert canonicalize_raw_content("第一行\r第二行") == "第一行\n第二行\n"
    assert canonicalize_raw_content(" \t\r\n ") == ""


def test_canonical_content_sha256_uses_utf8_canonical_text() -> None:
    canonical = "观点一\n观点二\n"

    assert canonical_content_sha256("观点一\r\n观点二") == hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()


@pytest.mark.parametrize("value", [None, b"content", 42])
def test_content_identity_rejects_non_strings(value: object) -> None:
    with pytest.raises(TypeError):
        canonicalize_raw_content(value)  # type: ignore[arg-type]

    with pytest.raises(TypeError):
        canonical_content_sha256(value)  # type: ignore[arg-type]
