"""OpenAI 纯文本 Responses 适配器；stdout 仅成功正文，失败非零且不输出密钥/响应原文。"""
from __future__ import annotations

import argparse
import os
import sys

import requests

ENDPOINT = "https://api.openai.com/v1/responses"


class ResponseFailure(Exception):
    """只携带可安全记录的固定诊断。"""


def generate(prompt: str, *, model: str, timeout: int) -> str:
    key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not key:
        raise ResponseFailure("not authenticated: OPENAI_API_KEY missing")
    if not model.strip() or timeout <= 0:
        raise ResponseFailure("misconfigured: LLM_MODEL and positive timeout required")
    try:
        limit = int(os.getenv("OPENAI_MAX_OUTPUT_TOKENS", "4096"))
    except ValueError:
        raise ResponseFailure("misconfigured: OPENAI_MAX_OUTPUT_TOKENS invalid") from None
    if not 1 <= limit <= 32768:
        raise ResponseFailure("misconfigured: OPENAI_MAX_OUTPUT_TOKENS outside 1..32768")
    try:
        # 固定官方 HTTPS；禁止重定向，避免凭据被配置或重定向送到其它主机。
        response = requests.post(
            ENDPOINT,
            headers={"Authorization": f"Bearer {key}"},
            json={"model": model.strip(), "input": prompt, "store": False,
                  "max_output_tokens": limit},
            timeout=timeout,
            allow_redirects=False,
        )
    except requests.Timeout:
        raise ResponseFailure("timeout: OpenAI request timed out") from None
    except requests.RequestException:
        raise ResponseFailure("source_failed: OpenAI network error") from None
    if response.status_code in (401, 403):
        raise ResponseFailure("not authenticated: OpenAI access denied")
    if response.status_code == 429:
        raise ResponseFailure("code 429: OpenAI quota or rate limit exceeded")
    if response.status_code != 200:
        raise ResponseFailure(f"source_failed: OpenAI HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError:
        raise ResponseFailure("source_failed: invalid OpenAI JSON response") from None
    if not isinstance(payload, dict) or payload.get("status") != "completed":
        raise ResponseFailure("source_failed: OpenAI response not completed")
    output = payload.get("output")
    if not isinstance(output, list):
        raise ResponseFailure("source_failed: invalid OpenAI output")
    texts = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        if item.get("status") != "completed" or not isinstance(item.get("content"), list):
            raise ResponseFailure("source_failed: incomplete OpenAI message")
        for part in item["content"]:
            if not isinstance(part, dict):
                raise ResponseFailure("source_failed: invalid OpenAI content")
            if part.get("type") == "refusal":
                raise ResponseFailure("source_failed: OpenAI refusal")
            if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                texts.append(part["text"])
    text = "\n".join(texts).strip()
    if not text:
        raise ResponseFailure("source_failed: empty OpenAI output")
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default="openai")
    parser.add_argument("--model", required=True)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--unsupported-local-files", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.provider != "openai":
            raise ResponseFailure("misconfigured: unsupported LLM_PROVIDER")
        if args.unsupported_local_files:
            raise ResponseFailure("source_failed: local files unavailable in text API")
        print(generate(args.prompt, model=args.model, timeout=args.timeout))
    except ResponseFailure as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
