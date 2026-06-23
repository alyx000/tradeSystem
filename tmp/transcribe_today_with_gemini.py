#!/usr/bin/env python3
import base64
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


DATE = "2026-06-23"
AUDIO_DIR = Path("/Users/alyx/tradeNote") / DATE
OUT_DIR = Path("/Users/alyx/tradeSystem/tmp/audio_transcripts") / DATE
MODELS = [
    os.environ.get("GEMINI_MODEL", "").strip(),
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
]
MODELS = [model for i, model in enumerate(MODELS) if model and model not in MODELS[:i]]

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


def request_json(url, payload, timeout=600):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def extract_text(response):
    chunks = []
    for candidate in response.get("candidates", []):
        content = candidate.get("content", {})
        for part in content.get("parts", []):
            text = part.get("text")
            if text:
                chunks.append(text)
    return "\n".join(chunks).strip()


def transcribe(api_key, model, path):
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": PROMPT},
                    {"inline_data": {"mime_type": mime_type, "data": encoded}},
                ],
            }
        ],
        "generationConfig": {
            "temperature": 0,
            "maxOutputTokens": 65536,
        },
    }
    return request_json(url, payload)


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
    print(f"Output: {OUT_DIR}")

    selected_model = None
    for path in files:
        out_path = OUT_DIR / f"{path.stem}.gemini.md"
        print(f"\nTRANSCRIBING {path.name} -> {out_path.name}", flush=True)
        last_error = None
        for model in ([selected_model] if selected_model else MODELS):
            if not model:
                continue
            try:
                started = time.time()
                response = transcribe(api_key, model, path)
                text = extract_text(response)
                if not text:
                    raise RuntimeError(json.dumps(response, ensure_ascii=False)[:2000])
                out_path.write_text(
                    f"# {path.stem}\n\n"
                    f"- source: {path}\n"
                    f"- model: {model}\n"
                    f"- generated_by: Gemini API\n\n"
                    f"## Transcript\n\n{text}\n",
                    encoding="utf-8",
                )
                selected_model = model
                print(f"WROTE {out_path} ({time.time() - started:.1f}s)", flush=True)
                break
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                last_error = f"HTTP {exc.code} using {model}: {body[:1000]}"
                print(f"MODEL_FAILED {model}: HTTP {exc.code}", flush=True)
                if exc.code not in (400, 404):
                    break
            except Exception as exc:
                last_error = f"{type(exc).__name__} using {model}: {exc}"
                print(f"MODEL_FAILED {model}: {type(exc).__name__}", flush=True)
        else:
            print(f"ERROR: {last_error}", file=sys.stderr)
            return 1
        if last_error and not out_path.exists():
            print(f"ERROR: {last_error}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
