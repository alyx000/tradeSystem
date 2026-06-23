#!/usr/bin/env python3
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


DATE = "2026-06-23"
AUDIO_DIR = Path("/Users/alyx/tradeNote") / DATE
OUT_DIR = Path("/Users/alyx/tradeSystem/tmp/audio_transcripts") / DATE
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash").strip()
MAX_WORKERS = int(os.environ.get("GEMINI_TRANSCRIBE_WORKERS", "3"))

PROMPT = """请将这段中文交易复盘/课程音频完整转写为简体中文。

要求：
1. 输出逐字稿，不要摘要。
2. 尽量保留 A股、港股、短线交易、指数、板块、资金流、情绪周期、老师观点等术语。
3. 如果能判断时间段，请按段落加 [MM:SS] 或 [HH:MM:SS] 时间戳；不能精确判断也不要编造。
4. 如果出现多位说话人，请用“说话人A/说话人B”区分；不能判断则不用强行区分。
5. 对听不清的内容标注 [听不清]。
6. 不要给买卖建议，不要把音频外的信息补进去。
"""


def audio_files():
    suffixes = {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg", ".opus", ".mp4"}
    return sorted(path for path in AUDIO_DIR.iterdir() if path.suffix.lower() in suffixes)


def read_json_response(resp):
    return json.loads(resp.read().decode("utf-8"))


def request_json(req, timeout):
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return read_json_response(resp)


def start_upload(api_key, path, mime_type):
    meta = json.dumps({"file": {"display_name": path.name}}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/upload/v1beta/files?key={api_key}",
        data=meta,
        headers={
            "Content-Type": "application/json",
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(path.stat().st_size),
            "X-Goog-Upload-Header-Content-Type": mime_type,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        upload_url = resp.headers.get("X-Goog-Upload-URL")
    if not upload_url:
        raise RuntimeError("Gemini upload did not return X-Goog-Upload-URL")
    return upload_url


def finish_upload(upload_url, path):
    data = path.read_bytes()
    req = urllib.request.Request(
        upload_url,
        data=data,
        headers={
            "Content-Length": str(len(data)),
            "X-Goog-Upload-Offset": "0",
            "X-Goog-Upload-Command": "upload, finalize",
        },
        method="POST",
    )
    return request_json(req, timeout=300)["file"]


def get_file(api_key, file_name):
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/{file_name}?key={api_key}",
        method="GET",
    )
    return request_json(req, timeout=60)


def wait_for_file(api_key, file_obj):
    name = file_obj.get("name")
    state = file_obj.get("state")
    for _ in range(30):
        if state in (None, "ACTIVE"):
            return file_obj
        if state == "FAILED":
            raise RuntimeError(f"Gemini file processing failed for {name}")
        time.sleep(2)
        file_obj = get_file(api_key, name)
        state = file_obj.get("state")
    raise TimeoutError(f"Timed out waiting for Gemini file processing: {name}")


def upload_file(api_key, path):
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    upload_url = start_upload(api_key, path, mime_type)
    file_obj = finish_upload(upload_url, path)
    file_obj = wait_for_file(api_key, file_obj)
    return file_obj, mime_type


def extract_text(response):
    chunks = []
    for candidate in response.get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []):
            text = part.get("text")
            if text:
                chunks.append(text)
    return "\n".join(chunks).strip()


def generate_transcript(api_key, path, file_obj, mime_type):
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": PROMPT},
                    {"file_data": {"mime_type": mime_type, "file_uri": file_obj["uri"]}},
                ],
            }
        ],
        "generationConfig": {
            "temperature": 0,
            "maxOutputTokens": 65536,
        },
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={api_key}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    response = request_json(req, timeout=600)
    text = extract_text(response)
    if not text:
        raise RuntimeError(json.dumps(response, ensure_ascii=False)[:2000])
    return text


def transcribe_one(api_key, path):
    out_path = OUT_DIR / f"{path.stem}.gemini.md"
    if out_path.exists() and out_path.stat().st_size > 200:
        return path.name, "SKIPPED", out_path
    started = time.time()
    file_obj, mime_type = upload_file(api_key, path)
    text = generate_transcript(api_key, path, file_obj, mime_type)
    out_path.write_text(
        f"# {path.stem}\n\n"
        f"- source: {path}\n"
        f"- model: {MODEL}\n"
        f"- generated_by: Gemini API Files API\n\n"
        f"## Transcript\n\n{text}\n",
        encoding="utf-8",
    )
    return path.name, f"WROTE in {time.time() - started:.1f}s", out_path


def main():
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("ERROR: GEMINI_API_KEY/GOOGLE_API_KEY is missing", file=sys.stderr)
        return 2

    files = audio_files()
    if not files:
        print(f"No audio files found in {AUDIO_DIR}", file=sys.stderr)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Found {len(files)} audio files")
    print(f"Model: {MODEL}")
    print(f"Workers: {MAX_WORKERS}")
    print(f"Output: {OUT_DIR}")

    failed = False
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(transcribe_one, api_key, path): path for path in files}
        for future in as_completed(futures):
            path = futures[future]
            try:
                name, status, out_path = future.result()
                print(f"{status}: {name} -> {out_path}", flush=True)
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                print(f"FAILED: {path.name}: HTTP {exc.code}: {body[:1200]}", file=sys.stderr, flush=True)
                failed = True
            except Exception as exc:
                print(f"FAILED: {path.name}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
                failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
