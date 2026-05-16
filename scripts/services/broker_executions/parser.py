from __future__ import annotations

import csv
import io
from pathlib import Path

from .models import RawRow


def detect_format(path: Path) -> str:
    data = path.read_bytes()
    bom_result = _detect_bom_text(data)
    if bom_result is None:
        if data.startswith(b"PK"):
            return "xlsx"
        encoding_label, text = _detect_text(data)
    else:
        encoding_label, text = bom_result
    if "\t" in text:
        return f"tsv-{encoding_label}"
    if "<table" in text.lower():
        return "html-table"
    return f"tsv-{encoding_label}"


def parse_file(path: Path) -> tuple[list[RawRow], dict]:
    source_format = detect_format(path)
    meta = {"source_format": source_format}
    if source_format == "xlsx":
        raise NotImplementedError("xlsx parsing not implemented in v1")
    if source_format == "html-table":
        raise NotImplementedError("html-table parsing not implemented in v1")

    text = _decode_text(path.read_bytes(), source_format)
    reader = csv.reader(io.StringIO(text), delimiter="\t")
    header: list[str] | None = None
    rows: list[RawRow] = []

    for row_index, row in enumerate(reader, start=1):
        if _is_empty_row(row) or _is_summary_row(row):
            continue
        stripped = [value.strip() for value in row]
        if header is None:
            header = [
                name.strip() if name.strip() else f"_unnamed_{i}"
                for i, name in enumerate(stripped)
            ]
            continue
        if stripped == [(h if not h.startswith("_unnamed_") else "") for h in header]:
            continue
        payload = {
            header[i]: stripped[i] if i < len(stripped) else ""
            for i in range(len(header))
        }
        rows.append(RawRow(row_index=row_index, payload=payload))

    return rows, meta


def _detect_text(data: bytes) -> tuple[str, str]:
    bom_result = _detect_bom_text(data)
    if bom_result is not None:
        return bom_result

    for encoding, label in (("gbk", "gbk"), ("utf-8", "utf8"), ("utf-16", "utf16")):
        try:
            return label, data.decode(encoding, errors="strict")
        except UnicodeDecodeError:
            continue
    raise ValueError("unsupported text encoding: tried gbk, utf-8, utf-16")


def _detect_bom_text(data: bytes) -> tuple[str, str] | None:
    if data[:3] == b"\xef\xbb\xbf":
        return "utf8", data.decode("utf-8-sig", errors="strict")
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return "utf16", data.decode("utf-16", errors="strict")
    return None


def _decode_text(data: bytes, source_format: str | None = None) -> str:
    encoding = _encoding_from_source_format(source_format)
    if encoding is not None:
        return data.decode(encoding, errors="strict")
    return _detect_text(data)[1]


def _encoding_from_source_format(source_format: str | None) -> str | None:
    if not source_format or not source_format.startswith("tsv-"):
        return None
    label = source_format.removeprefix("tsv-")
    if label == "utf8":
        return "utf-8-sig"
    if label == "utf16":
        return "utf-16"
    if label == "gbk":
        return "gbk"
    return None


def _is_empty_row(row: list[str]) -> bool:
    return all(not value.strip() for value in row)


def _is_summary_row(row: list[str]) -> bool:
    return any("合计" in value or "统计" in value for value in row)
