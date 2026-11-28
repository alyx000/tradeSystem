"""本机 LM Studio 纯文本适配器；成功仅输出正文，失败非零且隐藏原始响应。"""
from __future__ import annotations

import argparse
import os
import sys
from urllib.parse import urlsplit

import requests


class ResponseFailure(Exception):
    """只携带可安全记录的固定诊断。"""


def _endpoint() -> str:
    base = (os.getenv("LMSTUDIO_BASE_URL") or "http://127.0.0.1:1234/v1").strip().rstrip("/")
    try:
        url = urlsplit(base)
        valid = (url.scheme == "http" and url.hostname in {"127.0.0.1", "localhost", "::1"}
                 and url.username is None and url.password is None
                 and not url.query and not url.fragment and url.path == "/v1"
                 and (url.port is None or 1 <= url.port <= 65535))
    except ValueError:
        valid = False
    if not valid:
        raise ResponseFailure("misconfigured: LMSTUDIO_BASE_URL must be a loopback HTTP /v1 URL")
    return base + "/chat/completions"


def generate(prompt: str, *, model: str, timeout: int) -> str:
    endpoint = _endpoint()
    if not model.strip() or timeout <= 0:
        raise ResponseFailure("misconfigured: LLM_MODEL and positive timeout required")
    try:
        limit = int(os.getenv("LMSTUDIO_MAX_OUTPUT_TOKENS", "2048"))
    except ValueError:
        raise ResponseFailure("misconfigured: LMSTUDIO_MAX_OUTPUT_TOKENS invalid") from None
    if not 1 <= limit <= 32768:
        raise ResponseFailure("misconfigured: LMSTUDIO_MAX_OUTPUT_TOKENS outside 1..32768")
    # 本地认证独立，绝不复用 OPENAI_API_KEY；不使用环境代理或 .netrc 凭据。
    key = (os.getenv("LMSTUDIO_API_KEY") or "").strip()
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        with requests.Session() as session:
            session.trust_env = False
            response = session.post(
                endpoint,
                headers=headers,
                json={"model": model.strip(), "messages": [{"role": "user", "content": prompt}],
                      "stream": False, "max_tokens": limit, "temperature": 0},
                timeout=timeout,
                allow_redirects=False,
            )
    except requests.Timeout:
        raise ResponseFailure("timeout: LM Studio request timed out") from None
    except requests.RequestException:
        raise ResponseFailure("source_failed: LM Studio network error") from None
    if response.status_code in (401, 403):
        raise ResponseFailure("not authenticated: LM Studio access denied")
    if response.status_code == 429:
        raise ResponseFailure("code 429: LM Studio rate limit exceeded")
    if response.status_code != 200:
        raise ResponseFailure(f"source_failed: LM Studio HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError:
        raise ResponseFailure("source_failed: invalid LM Studio JSON response") from None
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise ResponseFailure("source_failed: invalid LM Studio choices")
    choice = choices[0]
    if choice.get("finish_reason") != "stop":
        raise ResponseFailure("source_failed: LM Studio response not completed")
    message = choice.get("message")
    if not isinstance(message, dict) or message.get("tool_calls") or message.get("refusal"):
        raise ResponseFailure("source_failed: invalid LM Studio message")
    # 不将 reasoning_content 当作最终正文；业务层继续执行自身 JSON/schema/红线校验。
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ResponseFailure("source_failed: empty LM Studio output")
    return content.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default="lmstudio")
    parser.add_argument("--model", required=True)
    parser.add_argument("--timeout", type=int, default=180)
    prompts = parser.add_mutually_exclusive_group(required=True)
    prompts.add_argument("--prompt")
    prompts.add_argument("--prompt-stdin", action="store_true")
    parser.add_argument("--unsupported-local-files", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.provider != "lmstudio":
            raise ResponseFailure("misconfigured: unsupported LLM_PROVIDER")
        if args.unsupported_local_files:
            raise ResponseFailure("source_failed: local files unavailable in text API")
        print(generate(sys.stdin.read() if args.prompt_stdin else args.prompt, model=args.model, timeout=args.timeout))
    except ResponseFailure as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
