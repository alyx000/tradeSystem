import json
import os
import sys
from pathlib import Path

import pytest

from tools import document_intake


def test_discover_inputs_keeps_supported_files_only(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4\n")
    (tmp_path / "b.PNG").write_bytes(b"image")
    (tmp_path / "note.txt").write_text("ignore", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "c.jpg").write_bytes(b"image")

    paths = document_intake.discover_inputs(tmp_path)

    assert [p.name for p in paths] == ["a.pdf", "b.PNG", "c.jpg"]


def test_local_mode_writes_raw_markdown_and_json(tmp_path, monkeypatch):
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"%PDF-1.4\n")

    monkeypatch.setattr(document_intake, "extract_local_text", lambda path: "本地OCR原文")

    result = document_intake.process_file(
        source,
        mode="local",
        out_dir=tmp_path / "out",
    )

    assert result.raw_markdown.read_text(encoding="utf-8").endswith("本地OCR原文\n")
    assert result.metadata_json.exists()
    assert result.structured_markdown is None


def test_hybrid_mode_writes_raw_and_structured_outputs(tmp_path, monkeypatch):
    source = tmp_path / "teacher.png"
    source.write_bytes(b"image")

    monkeypatch.setattr(document_intake, "extract_local_text", lambda path: "截图原文")
    monkeypatch.setattr(
        document_intake,
        "run_gemini_document_understanding",
        lambda path, raw_text=None, prompt=None, model=None, keep_remote_file=False: "[事实] 结构化结果",
    )

    result = document_intake.process_file(
        source,
        mode="hybrid",
        out_dir=tmp_path / "out",
        model="gemini-test",
    )

    assert result.raw_markdown.read_text(encoding="utf-8").endswith("截图原文\n")
    assert "[事实] 结构化结果" in result.structured_markdown.read_text(encoding="utf-8")
    assert result.metadata_json.exists()


def test_process_inputs_can_run_with_workers(tmp_path, monkeypatch):
    for name in ["a.pdf", "b.png", "c.jpg"]:
        (tmp_path / name).write_bytes(b"fixture")

    seen = []

    def fake_process_file(path, mode, out_dir, prompt=None, model=None, force=False, keep_remote_file=False):
        seen.append(path.name)
        return document_intake.ProcessResult(
            source=path,
            mode=mode,
            raw_markdown=out_dir / f"{path.stem}.raw.md",
            structured_markdown=None,
            metadata_json=out_dir / f"{path.stem}.metadata.json",
        )

    monkeypatch.setattr(document_intake, "process_file", fake_process_file)

    results = document_intake.process_inputs(
        tmp_path,
        mode="local",
        out_dir=tmp_path / "out",
        workers=2,
    )

    assert sorted(seen) == ["a.pdf", "b.png", "c.jpg"]
    assert sorted(result.source.name for result in results) == ["a.pdf", "b.png", "c.jpg"]


def test_process_file_skips_completed_hybrid_output(tmp_path, monkeypatch):
    source = tmp_path / "done.pdf"
    source.write_bytes(b"%PDF-1.4\n")
    out_dir = tmp_path / "out"
    raw, structured, metadata, _ = document_intake.output_paths(source, "hybrid", out_dir)
    doc_dir = raw.parent
    doc_dir.mkdir(parents=True)
    raw.write_text("raw", encoding="utf-8")
    structured.write_text("structured", encoding="utf-8")
    metadata.write_text(
        document_intake.metadata_for(source, "hybrid", raw, structured, "gemini-2.5-flash", None),
        encoding="utf-8",
    )

    def fail_if_called(path):
        raise AssertionError("completed files should be skipped")

    monkeypatch.setattr(document_intake, "extract_local_text", fail_if_called)

    result = document_intake.process_file(source, mode="hybrid", out_dir=out_dir)

    assert result.raw_markdown == raw
    assert result.structured_markdown == structured
    assert result.metadata_json == metadata


def test_completed_output_is_invalidated_when_model_changes(tmp_path, monkeypatch):
    source = tmp_path / "done.pdf"
    source.write_bytes(b"%PDF-1.4\n")
    out_dir = tmp_path / "out"
    raw, structured, metadata, _ = document_intake.output_paths(source, "hybrid", out_dir)
    raw.parent.mkdir(parents=True)
    raw.write_text("raw", encoding="utf-8")
    structured.write_text("old structured", encoding="utf-8")
    metadata.write_text(
        document_intake.metadata_for(source, "hybrid", raw, structured, "old-model", None),
        encoding="utf-8",
    )

    monkeypatch.setattr(document_intake, "extract_local_text", lambda path: "new raw")
    monkeypatch.setattr(
        document_intake,
        "run_gemini_document_understanding",
        lambda path, raw_text=None, prompt=None, model=None, keep_remote_file=False: "new structured",
    )

    result = document_intake.process_file(source, mode="hybrid", out_dir=out_dir, model="new-model")

    assert result.structured_markdown.read_text(encoding="utf-8").endswith("new structured\n")


def test_completed_output_is_invalidated_when_content_hash_changes(tmp_path, monkeypatch):
    source = tmp_path / "done.pdf"
    source.write_bytes(b"aaaa")
    out_dir = tmp_path / "out"
    raw, structured, metadata, _ = document_intake.output_paths(source, "hybrid", out_dir)
    raw.parent.mkdir(parents=True)
    raw.write_text("raw", encoding="utf-8")
    structured.write_text("old structured", encoding="utf-8")
    metadata_text = document_intake.metadata_for(source, "hybrid", raw, structured, "gemini-2.5-flash", None)
    metadata.write_text(metadata_text, encoding="utf-8")
    original_stat = source.stat()
    source.write_bytes(b"bbbb")
    os.utime(source, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

    monkeypatch.setattr(document_intake, "extract_local_text", lambda path: "new raw")
    monkeypatch.setattr(
        document_intake,
        "run_gemini_document_understanding",
        lambda path, raw_text=None, prompt=None, model=None, keep_remote_file=False: "new structured",
    )

    result = document_intake.process_file(source, mode="hybrid", out_dir=out_dir)

    assert result.structured_markdown.read_text(encoding="utf-8").endswith("new structured\n")


def test_same_stem_in_nested_directories_has_distinct_outputs(tmp_path):
    first = tmp_path / "a" / "report.pdf"
    second = tmp_path / "b" / "report.pdf"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_bytes(b"%PDF-1.4\n")
    second.write_bytes(b"%PDF-1.4\n")

    first_paths = document_intake.output_paths(first, "local", tmp_path / "out")
    second_paths = document_intake.output_paths(second, "local", tmp_path / "out")

    assert first_paths[0].parent != second_paths[0].parent


def test_output_paths_are_separated_by_mode(tmp_path):
    source = tmp_path / "report.pdf"
    source.write_bytes(b"%PDF-1.4\n")

    local_paths = document_intake.output_paths(source, "local", tmp_path / "out")
    hybrid_paths = document_intake.output_paths(source, "hybrid", tmp_path / "out")

    assert local_paths[0].parent != hybrid_paths[0].parent


def test_default_output_dir_is_repo_relative():
    expected = Path(__file__).resolve().parents[2] / "tmp" / "document_intake"

    assert document_intake.default_output_dir().parent == expected


def test_process_inputs_raises_when_any_file_fails_by_default(tmp_path, monkeypatch):
    for name in ["ok.pdf", "bad.png"]:
        (tmp_path / name).write_bytes(b"fixture")

    def fake_process_file(path, mode, out_dir, prompt=None, model=None, force=False, keep_remote_file=False):
        if path.name == "bad.png":
            raise RuntimeError("broken image")
        return document_intake.ProcessResult(
            source=path,
            mode=mode,
            raw_markdown=out_dir / "ok.raw.md",
            structured_markdown=None,
            metadata_json=out_dir / "ok.metadata.json",
        )

    monkeypatch.setattr(document_intake, "process_file", fake_process_file)

    with pytest.raises(document_intake.PartialProcessingError) as exc:
        document_intake.process_inputs(tmp_path, mode="local", out_dir=tmp_path / "out", workers=2)

    assert len(exc.value.results) == 1
    assert len(exc.value.failures) == 1


def test_process_inputs_can_allow_partial_failures(tmp_path, monkeypatch):
    for name in ["ok.pdf", "bad.png"]:
        (tmp_path / name).write_bytes(b"fixture")

    def fake_process_file(path, mode, out_dir, prompt=None, model=None, force=False, keep_remote_file=False):
        if path.name == "bad.png":
            raise RuntimeError("broken image")
        return document_intake.ProcessResult(
            source=path,
            mode=mode,
            raw_markdown=out_dir / "ok.raw.md",
            structured_markdown=None,
            metadata_json=out_dir / "ok.metadata.json",
        )

    monkeypatch.setattr(document_intake, "process_file", fake_process_file)

    results = document_intake.process_inputs(
        tmp_path,
        mode="local",
        out_dir=tmp_path / "out",
        workers=2,
        allow_partial=True,
    )

    assert [result.source.name for result in results] == ["ok.pdf"]


def test_serialize_summary_includes_failures(tmp_path):
    result = document_intake.ProcessResult(
        source=tmp_path / "ok.pdf",
        mode="local",
        raw_markdown=tmp_path / "ok.raw.md",
        structured_markdown=None,
        metadata_json=tmp_path / "ok.metadata.json",
    )
    failure = document_intake.ProcessFailure(
        source=tmp_path / "bad.png",
        error_json=tmp_path / "bad.error.json",
        error="broken image",
    )

    payload = json.loads(document_intake.serialize_summary([result], [failure]))

    assert payload["status"] == "partial"
    assert payload["summary"] == {"succeeded": 1, "failed": 1}
    assert payload["failures"][0]["error_json"].endswith("bad.error.json")


def test_swift_error_message_is_condensed():
    stderr = """Swift/ErrorType.swift:254: Fatal error: Error raised at top level: Error Domain=com.apple.Vision Code=13 "bad image"
Stack dump:
0 swift-frontend
1 swift-frontend
"""

    assert document_intake.condense_swift_error(stderr) == 'Error Domain=com.apple.Vision Code=13 "bad image"'


def test_image_ocr_has_platform_guard(monkeypatch, tmp_path):
    image = tmp_path / "image.png"
    image.write_bytes(b"image")
    monkeypatch.setattr(document_intake.sys, "platform", "linux")

    with pytest.raises(RuntimeError, match="macOS Vision OCR requires macOS"):
        document_intake.extract_image_text(image)


def test_gemini_requests_use_header_key(monkeypatch, tmp_path):
    source = tmp_path / "report.pdf"
    source.write_bytes(b"%PDF-1.4\n")
    seen = {}

    def fake_urlopen(request, timeout=0):
        seen["full_url"] = request.full_url
        seen["headers"] = dict(request.header_items())

        class Response:
            headers = {"X-Goog-Upload-URL": "https://upload.example.test/session"}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b"{}"

        return Response()

    monkeypatch.setattr(document_intake.urllib.request, "urlopen", fake_urlopen)

    document_intake.start_gemini_upload("secret-key", source, "application/pdf")

    assert "secret-key" not in seen["full_url"]
    assert seen["headers"]["X-goog-api-key"] == "secret-key"


def test_gemini_remote_file_deleted_after_generation(monkeypatch, tmp_path):
    source = tmp_path / "report.pdf"
    source.write_bytes(b"%PDF-1.4\n")
    deleted = []

    monkeypatch.setenv("GEMINI_API_KEY", "secret")
    monkeypatch.setattr(
        document_intake,
        "upload_gemini_file",
        lambda api_key, path: ({"name": "files/abc", "uri": "gemini://abc"}, "application/pdf"),
    )
    monkeypatch.setattr(document_intake, "delete_gemini_file", lambda api_key, name: deleted.append((api_key, name)))
    monkeypatch.setattr(document_intake, "request_json", lambda request, timeout: {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})

    assert document_intake.run_gemini_document_understanding(source) == "ok"
    assert deleted == [("secret", "files/abc")]
