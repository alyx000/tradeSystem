#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable


SUPPORTED_SUFFIXES = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".tif",
    ".tiff",
    ".heic",
}
IMAGE_SUFFIXES = SUPPORTED_SUFFIXES - {".pdf"}
DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_PROMPT = """请阅读这个交易相关图片或 PDF，并输出结构化结果。

要求：
1. 使用简体中文。
2. 分为 [事实]、[观点]、[判断]、[待确认] 四类；没有内容的类别写“无”。
3. [事实] 只写材料中明确出现的信息，不补充外部知识。
4. [观点] 标注说话人/作者/来源，如果材料可判断。
5. [判断] 必须说明依据，禁止给具体买卖建议或价格目标。
6. 对股票、板块、指数、日期、数字、机构名称尽量保留原文。
7. 如果有表格，请转成 Markdown 表格；无法可靠识别的单元格标 [待确认]。
"""


@dataclass(frozen=True)
class ProcessResult:
    source: Path
    mode: str
    raw_markdown: Path | None
    structured_markdown: Path | None
    metadata_json: Path


@dataclass(frozen=True)
class ProcessFailure:
    source: Path
    error_json: Path
    error: str


class PartialProcessingError(RuntimeError):
    def __init__(self, results: list[ProcessResult], failures: list[ProcessFailure]) -> None:
        self.results = results
        self.failures = failures
        super().__init__(f"{len(failures)} file(s) failed; see *.error.json")


@dataclass(frozen=True)
class ProcessingSummary:
    results: list[ProcessResult]
    failures: list[ProcessFailure]


@dataclass(frozen=True)
class SourceFingerprint:
    path: Path
    size: int
    mtime_ns: int
    sha256: str


def discover_inputs(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path] if input_path.suffix.lower() in SUPPORTED_SUFFIXES else []
    if not input_path.is_dir():
        raise FileNotFoundError(input_path)
    paths = [
        path
        for path in input_path.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    ]
    return sorted(paths, key=lambda p: str(p))


def safe_stem(path: Path) -> str:
    stem = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", path.stem, flags=re.UNICODE)
    return stem.strip("._") or "document"


def path_key(path: Path) -> str:
    return hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:10]


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def prompt_hash(prompt: str | None) -> str:
    return hashlib.sha1((prompt or DEFAULT_PROMPT).encode("utf-8")).hexdigest()


def default_output_dir() -> Path:
    return repo_root() / "tmp" / "document_intake" / date.today().isoformat()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_fingerprint(path: Path) -> SourceFingerprint:
    resolved = path.resolve()
    stat = path.stat()
    return SourceFingerprint(
        path=resolved,
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        sha256=file_sha256(path),
    )


def write_markdown(path: Path, title: str, source: Path, body: str, meta: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# {title}", "", f"- source: {source}"]
    for key, value in meta.items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", body.rstrip(), ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def extract_pdf_text(path: Path) -> str:
    try:
        import pypdf  # type: ignore

        reader = pypdf.PdfReader(str(path))
        pages = []
        for idx, page in enumerate(reader.pages, 1):
            text = page.extract_text() or ""
            pages.append(f"===== page {idx} =====\n{text.strip()}")
        return "\n\n".join(pages).strip()
    except ImportError:
        pass

    try:
        import fitz  # type: ignore

        doc = fitz.open(path)
        pages = []
        for idx, page in enumerate(doc, 1):
            text = page.get_text("text") or ""
            pages.append(f"===== page {idx} =====\n{text.strip()}")
        return "\n\n".join(pages).strip()
    except ImportError:
        return ""


def swift_vision_ocr(path: Path) -> str:
    if sys.platform != "darwin":
        raise RuntimeError("macOS Vision OCR requires macOS; use --mode gemini for image understanding on this platform")
    if not shutil.which("swift"):
        raise RuntimeError("macOS Vision OCR requires the swift command; install Xcode Command Line Tools or use --mode gemini")
    swift_source = r'''
import Foundation
import Vision

let path = CommandLine.arguments[1]
let url = URL(fileURLWithPath: path)
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true
if #available(macOS 11.0, *) {
    request.recognitionLanguages = ["zh-Hans", "zh-Hant", "en-US"]
}
do {
    let handler = VNImageRequestHandler(url: url, options: [:])
    try handler.perform([request])
    let observations = request.results ?? []
    for observation in observations {
        if let text = observation.topCandidates(1).first {
            print(text.string)
        }
    }
} catch {
    fputs("__OCR_ERROR__ \(error)\n", stderr)
    exit(1)
}
'''
    with tempfile.NamedTemporaryFile("w", suffix=".swift", encoding="utf-8", delete=False) as handle:
        handle.write(swift_source)
        swift_path = Path(handle.name)
    try:
        proc = subprocess.run(
            ["swift", str(swift_path), str(path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    finally:
        swift_path.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise RuntimeError(condense_swift_error(proc.stderr) or proc.stdout.strip() or "macOS Vision OCR failed")
    return proc.stdout.strip()


def condense_swift_error(stderr: str) -> str:
    text = stderr.strip()
    if not text:
        return ""
    match = re.search(r"Error raised at top level:\s*(.+)", text)
    if match:
        return match.group(1).strip()
    marker = "__OCR_ERROR__"
    if marker in text:
        return text.split(marker, 1)[1].strip().splitlines()[0].strip()
    return text.split("Stack dump:", 1)[0].strip().splitlines()[0].strip()


def extract_image_text(path: Path) -> str:
    return swift_vision_ocr(path)


def extract_local_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        text = extract_pdf_text(path)
        if text:
            return text
        return "[待确认] 未能从 PDF 抽取文本；如果这是扫描 PDF，请使用 --mode gemini 或安装 OCR 组件。"
    if suffix in IMAGE_SUFFIXES:
        return extract_image_text(path)
    raise ValueError(f"Unsupported file type: {path}")


def request_json(request: urllib.request.Request, timeout: int) -> dict:
    retry_statuses = {429, 500, 502, 503, 504}
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in retry_statuses or attempt == 3:
                raise
            time.sleep(2 ** attempt)
        except TimeoutError as exc:
            last_error = exc
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError(f"request failed after retries: {last_error}")


def request_json_no_retry(request: urllib.request.Request, timeout: int) -> dict:
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
        return json.loads(body) if body else {}


def authed_url(path: str) -> str:
    return f"https://generativelanguage.googleapis.com{path}"


def api_key_headers(api_key: str, headers: dict[str, str] | None = None) -> dict[str, str]:
    out = dict(headers or {})
    out["x-goog-api-key"] = api_key
    return out


def output_paths(path: Path, mode: str, out_dir: Path) -> tuple[Path, Path, Path, Path]:
    stem = safe_stem(path)
    doc_dir = out_dir / mode / f"{stem}-{path_key(path)}"
    raw_path = doc_dir / f"{stem}.raw.md"
    structured_path = doc_dir / f"{stem}.structured.md"
    metadata_path = doc_dir / f"{stem}.metadata.json"
    error_path = doc_dir / f"{stem}.error.json"
    return raw_path, structured_path, metadata_path, error_path


def metadata_payload(
    path: Path,
    mode: str,
    raw_path: Path | None,
    structured_path: Path | None,
    model: str | None,
    prompt: str | None,
    fingerprint: SourceFingerprint | None = None,
) -> dict:
    fingerprint = fingerprint or source_fingerprint(path)
    return {
        "source": str(fingerprint.path),
        "source_size": fingerprint.size,
        "source_mtime_ns": fingerprint.mtime_ns,
        "source_sha256": fingerprint.sha256,
        "mode": mode,
        "raw_markdown": str(raw_path) if raw_path else None,
        "structured_markdown": str(structured_path) if structured_path else None,
        "model": model or DEFAULT_MODEL if mode in {"gemini", "hybrid"} else None,
        "prompt_hash": prompt_hash(prompt) if mode in {"gemini", "hybrid"} else None,
    }


def metadata_for(
    path: Path,
    mode: str,
    raw_path: Path | None,
    structured_path: Path | None,
    model: str | None,
    prompt: str | None,
    fingerprint: SourceFingerprint | None = None,
) -> str:
    return json.dumps(
        metadata_payload(path, mode, raw_path, structured_path, model, prompt, fingerprint=fingerprint),
        ensure_ascii=False,
        indent=2,
    ) + "\n"


def completed_result(
    path: Path,
    mode: str,
    out_dir: Path,
    prompt: str | None = None,
    model: str | None = None,
) -> ProcessResult | None:
    raw_path, structured_path, metadata_path, _ = output_paths(path, mode, out_dir)
    raw_done = mode == "gemini" or (raw_path.exists() and raw_path.stat().st_size > 0)
    structured_done = mode == "local" or (structured_path.exists() and structured_path.stat().st_size > 0)
    if raw_done and structured_done and metadata_path.exists():
        try:
            existing = json.loads(metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        expected = metadata_payload(
            path,
            mode,
            raw_path if mode in {"local", "hybrid"} else None,
            structured_path if mode in {"gemini", "hybrid"} else None,
            model,
            prompt,
        )
        for key in ("source", "source_size", "source_mtime_ns", "source_sha256", "mode", "model", "prompt_hash"):
            if existing.get(key) != expected.get(key):
                return None
        return ProcessResult(
            source=path,
            mode=mode,
            raw_markdown=raw_path if mode in {"local", "hybrid"} else None,
            structured_markdown=structured_path if mode in {"gemini", "hybrid"} else None,
            metadata_json=metadata_path,
        )
    return None


def start_gemini_upload(api_key: str, path: Path, mime_type: str) -> str:
    metadata = json.dumps({"file": {"display_name": path.name}}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        authed_url("/upload/v1beta/files"),
        data=metadata,
        headers=api_key_headers(api_key, {
            "Content-Type": "application/json",
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(path.stat().st_size),
            "X-Goog-Upload-Header-Content-Type": mime_type,
        }),
        method="POST",
    )
    with request_with_retry(request, timeout=60) as response:
        upload_url = response.headers.get("X-Goog-Upload-URL")
    if not upload_url:
        raise RuntimeError("Gemini upload did not return X-Goog-Upload-URL")
    return upload_url


def request_with_retry(request: urllib.request.Request, timeout: int):
    retry_statuses = {429, 500, 502, 503, 504}
    for attempt in range(4):
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code not in retry_statuses or attempt == 3:
                raise
            time.sleep(2 ** attempt)
        except TimeoutError:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError("request failed after retries")


def finish_gemini_upload(upload_url: str, path: Path) -> dict:
    data = path.read_bytes()
    request = urllib.request.Request(
        upload_url,
        data=data,
        headers={
            "Content-Length": str(len(data)),
            "X-Goog-Upload-Offset": "0",
            "X-Goog-Upload-Command": "upload, finalize",
        },
        method="POST",
    )
    # Do not blindly retry finalize from offset 0; resumable retries need offset negotiation.
    return request_json_no_retry(request, timeout=300)["file"]


def get_gemini_file(api_key: str, file_name: str) -> dict:
    request = urllib.request.Request(
        authed_url(f"/v1beta/{file_name}"),
        headers=api_key_headers(api_key),
        method="GET",
    )
    return request_json(request, timeout=60)


def delete_gemini_file(api_key: str, file_name: str) -> None:
    request = urllib.request.Request(
        authed_url(f"/v1beta/{file_name}"),
        headers=api_key_headers(api_key),
        method="DELETE",
    )
    request_json(request, timeout=60)


def wait_for_gemini_file(api_key: str, file_obj: dict) -> dict:
    state = file_obj.get("state")
    for _ in range(30):
        if state in (None, "ACTIVE"):
            return file_obj
        if state == "FAILED":
            raise RuntimeError(f"Gemini file processing failed: {file_obj.get('name')}")
        time.sleep(2)
        file_obj = get_gemini_file(api_key, file_obj["name"])
        state = file_obj.get("state")
    raise TimeoutError(f"Timed out waiting for Gemini file processing: {file_obj.get('name')}")


def upload_gemini_file(api_key: str, path: Path) -> tuple[dict, str]:
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    upload_url = start_gemini_upload(api_key, path, mime_type)
    file_obj = finish_gemini_upload(upload_url, path)
    return wait_for_gemini_file(api_key, file_obj), mime_type


def extract_gemini_text(response: dict) -> str:
    chunks = []
    for candidate in response.get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []):
            text = part.get("text")
            if text:
                chunks.append(text)
    return "\n".join(chunks).strip()


def run_gemini_document_understanding(
    path: Path,
    raw_text: str | None = None,
    prompt: str | None = None,
    model: str | None = None,
    keep_remote_file: bool = False,
) -> str:
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY/GOOGLE_API_KEY is missing")
    file_obj, mime_type = upload_gemini_file(api_key, path)
    try:
        full_prompt = prompt or DEFAULT_PROMPT
        if raw_text:
            full_prompt += "\n\n下面是本地抽取到的原文，作为辅助证据；请优先以文件视觉内容为准：\n" + raw_text[:30000]
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": full_prompt},
                        {"file_data": {"mime_type": mime_type, "file_uri": file_obj["uri"]}},
                    ],
                }
            ],
            "generationConfig": {"temperature": 0, "maxOutputTokens": 65536},
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            authed_url(f"/v1beta/models/{model or DEFAULT_MODEL}:generateContent"),
            data=data,
            headers=api_key_headers(api_key, {"Content-Type": "application/json"}),
            method="POST",
        )
        response = request_json(request, timeout=600)
        text = extract_gemini_text(response)
        if not text:
            raise RuntimeError(json.dumps(response, ensure_ascii=False)[:2000])
        return text
    finally:
        if not keep_remote_file and file_obj.get("name"):
            delete_gemini_file(api_key, file_obj["name"])


def process_file(
    path: Path,
    mode: str,
    out_dir: Path,
    prompt: str | None = None,
    model: str | None = None,
    force: bool = False,
    keep_remote_file: bool = False,
) -> ProcessResult:
    done = completed_result(path, mode, out_dir, prompt=prompt, model=model)
    if done and not force:
        return done
    initial_fingerprint = source_fingerprint(path)

    raw_output_path, structured_output_path, metadata_path, _ = output_paths(path, mode, out_dir)
    stem = safe_stem(path)
    doc_dir = raw_output_path.parent
    doc_dir.mkdir(parents=True, exist_ok=True)
    raw_path: Path | None = None
    structured_path: Path | None = None
    raw_text: str | None = None

    if mode in {"local", "hybrid"}:
        raw_text = extract_local_text(path)
        raw_path = raw_output_path
        write_markdown(
            raw_path,
            f"{stem} raw text",
            path,
            raw_text,
            {"mode": "local"},
        )

    if mode in {"gemini", "hybrid"}:
        structured_text = run_gemini_document_understanding(
            path,
            raw_text=raw_text,
            prompt=prompt,
            model=model,
            keep_remote_file=keep_remote_file,
        )
        structured_path = structured_output_path
        write_markdown(
            structured_path,
            f"{stem} structured understanding",
            path,
            structured_text,
            {"mode": mode, "model": model or DEFAULT_MODEL},
        )

    if source_fingerprint(path) != initial_fingerprint:
        raise RuntimeError(f"Source file changed during processing: {path}")
    metadata_path.write_text(
        metadata_for(path, mode, raw_path, structured_path, model, prompt, fingerprint=initial_fingerprint),
        encoding="utf-8",
    )
    return ProcessResult(path, mode, raw_path, structured_path, metadata_path)


def write_error(path: Path, mode: str, out_dir: Path, exc: Exception) -> ProcessFailure:
    _, _, _, error_path = output_paths(path, mode, out_dir)
    error_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": str(path),
        "mode": mode,
        "error_type": type(exc).__name__,
        "error": str(exc),
    }
    error_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return ProcessFailure(path, error_path, str(exc))


def process_inputs_summary(
    input_path: Path,
    mode: str,
    out_dir: Path,
    prompt: str | None = None,
    model: str | None = None,
    workers: int = 1,
    fail_fast: bool = False,
    force: bool = False,
    keep_remote_file: bool = False,
) -> ProcessingSummary:
    files = discover_inputs(input_path)
    if not files:
        raise RuntimeError(f"No supported image/PDF files found: {input_path}")
    if workers <= 1 or fail_fast:
        results = []
        failures = []
        for path in files:
            try:
                results.append(process_file(
                    path,
                    mode=mode,
                    out_dir=out_dir,
                    prompt=prompt,
                    model=model,
                    force=force,
                    keep_remote_file=keep_remote_file,
                ))
            except Exception as exc:
                if fail_fast:
                    raise
                failures.append(write_error(path, mode, out_dir, exc))
                print(f"FAILED {path}: {exc}", file=sys.stderr, flush=True)
        if failures:
            print(f"Completed with {len(failures)} failed file(s); see *.error.json", file=sys.stderr)
        return ProcessingSummary(results, failures)

    results: list[ProcessResult] = []
    failures: list[ProcessFailure] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                process_file,
                path,
                mode=mode,
                out_dir=out_dir,
                prompt=prompt,
                model=model,
                force=force,
                keep_remote_file=keep_remote_file,
            ): path
            for path in files
        }
        for future in as_completed(futures):
            path = futures[future]
            try:
                results.append(future.result())
                print(f"PROCESSED {path}", file=sys.stderr, flush=True)
            except Exception as exc:
                failures.append(write_error(path, mode, out_dir, exc))
                print(f"FAILED {path}: {exc}", file=sys.stderr, flush=True)
    if failures:
        print(f"Completed with {len(failures)} failed file(s); see *.error.json", file=sys.stderr)
    return ProcessingSummary(sorted(results, key=lambda result: str(result.source)), failures)


def process_inputs(
    input_path: Path,
    mode: str,
    out_dir: Path,
    prompt: str | None = None,
    model: str | None = None,
    workers: int = 1,
    fail_fast: bool = False,
    allow_partial: bool = False,
    force: bool = False,
    keep_remote_file: bool = False,
) -> list[ProcessResult]:
    summary = process_inputs_summary(
        input_path,
        mode=mode,
        out_dir=out_dir,
        prompt=prompt,
        model=model,
        workers=workers,
        fail_fast=fail_fast,
        force=force,
        keep_remote_file=keep_remote_file,
    )
    if summary.failures and not allow_partial:
        raise PartialProcessingError(summary.results, summary.failures)
    return summary.results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract or understand local image/PDF documents.")
    parser.add_argument("--input", required=True, type=Path, help="Image/PDF file or directory.")
    parser.add_argument(
        "--mode",
        choices=["local", "gemini", "hybrid"],
        default="hybrid",
        help="local=OCR/text only, gemini=Gemini only, hybrid=both.",
    )
    parser.add_argument("--out-dir", type=Path, default=default_output_dir())
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--prompt-file", type=Path, help="Optional custom Gemini prompt.")
    parser.add_argument("--workers", type=int, default=1, help="Number of files to process in parallel.")
    parser.add_argument("--fail-fast", action="store_true", help="Stop on the first failed file.")
    parser.add_argument("--allow-partial", action="store_true", help="Return exit code 0 when some files fail.")
    parser.add_argument("--force", action="store_true", help="Reprocess files even when output metadata matches.")
    parser.add_argument("--keep-remote-file", action="store_true", help="Do not delete uploaded Gemini Files API files.")
    return parser


def serialize_results(results: list[ProcessResult]) -> str:
    return serialize_summary(results, [])


def result_to_dict(result: ProcessResult) -> dict:
    return result.__dict__ | {
        "source": str(result.source),
        "raw_markdown": str(result.raw_markdown) if result.raw_markdown else None,
        "structured_markdown": str(result.structured_markdown) if result.structured_markdown else None,
        "metadata_json": str(result.metadata_json),
    }


def failure_to_dict(failure: ProcessFailure) -> dict:
    return {
        "source": str(failure.source),
        "error_json": str(failure.error_json),
        "error": failure.error,
    }


def serialize_summary(results: list[ProcessResult], failures: list[ProcessFailure] | None = None) -> str:
    failures = failures or []
    status = "ok" if not failures else ("failed" if not results else "partial")
    return json.dumps(
        {
            "status": status,
            "summary": {"succeeded": len(results), "failed": len(failures)},
            "results": [result_to_dict(result) for result in results],
            "failures": [failure_to_dict(failure) for failure in failures],
        },
        ensure_ascii=False,
        indent=2,
    )


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        prompt = args.prompt_file.read_text(encoding="utf-8") if args.prompt_file else None
        summary = process_inputs_summary(
            args.input,
            mode=args.mode,
            out_dir=args.out_dir,
            prompt=prompt,
            model=args.model,
            workers=args.workers,
            fail_fast=args.fail_fast,
            force=args.force,
            keep_remote_file=args.keep_remote_file,
        )
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(serialize_summary(summary.results, summary.failures))
    if summary.failures and not args.allow_partial:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
