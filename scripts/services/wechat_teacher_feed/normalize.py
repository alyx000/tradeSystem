from __future__ import annotations

from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from services.content_identity import canonical_content_sha256, canonicalize_raw_content

from .constants import SOURCE_PLATFORM
from .models import FeedError, WeRSSArticleMeta, WechatTeacherArticle


SHANGHAI = ZoneInfo("Asia/Shanghai")
_STABLE_WECHAT_QUERY_KEYS = {"__biz", "mid", "idx", "sn", "chksm"}
_BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "div",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "li",
    "main",
    "p",
    "pre",
    "section",
    "table",
    "td",
    "tr",
    "ul",
    "ol",
}
_IGNORED_TAGS = {"script", "style", "template", "noscript"}


class _ReadableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        name = tag.lower()
        if name in _IGNORED_TAGS:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        if name == "br" or name in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        if name in _IGNORED_TAGS:
            if self._ignored_depth:
                self._ignored_depth -= 1
            return
        if not self._ignored_depth and name in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self.parts.append(data)


def normalize_wechat_url(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("source_url is required")
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError as exc:
        raise ValueError("source_url is invalid") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or (parsed.hostname or "").lower() != "mp.weixin.qq.com"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 80, 443}
    ):
        raise ValueError("source_url must be a mp.weixin.qq.com article URL")
    path = parsed.path.rstrip("/")
    if not path.startswith("/s") or path not in {"/s"} and not path.startswith("/s/"):
        raise ValueError("source_url must use the WeChat article path")
    query = ""
    if path == "/s":
        stable = [
            (key, val)
            for key, val in parse_qsl(parsed.query, keep_blank_values=True)
            if key in _STABLE_WECHAT_QUERY_KEYS
        ]
        stable.sort()
        if not stable:
            raise ValueError("source_url has no stable article identity")
        query = urlencode(stable)
    return urlunsplit(("https", "mp.weixin.qq.com", path, query, ""))


def normalize_publish_time(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        raise ValueError("publish_time is missing or invalid")
    try:
        timestamp = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("publish_time is missing or invalid") from exc
    if timestamp <= 0:
        raise ValueError("publish_time is missing or invalid")
    if timestamp > 10_000_000_000:
        timestamp = timestamp / 1000
    try:
        parsed = datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone(SHANGHAI)
    except (OverflowError, OSError, ValueError) as exc:
        raise ValueError("publish_time is missing or invalid") from exc
    return parsed.isoformat(timespec="seconds")


def html_to_text(raw_html: str) -> str:
    if not isinstance(raw_html, str):
        raise TypeError("raw_html must be a string")
    parser = _ReadableHTMLParser()
    parser.feed(raw_html)
    parser.close()
    lines = []
    for raw_line in "".join(parser.parts).replace("\xa0", " ").splitlines():
        line = " ".join(raw_line.split())
        if line:
            lines.append(line)
    return canonicalize_raw_content("\n".join(lines))


def _normalize_fetched_at(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("fetched_at must be timezone-aware")
    return value.astimezone(SHANGHAI).isoformat(timespec="seconds")


def normalize_article(
    *,
    teacher_name: str,
    source_account_id: str,
    metadata: WeRSSArticleMeta,
    detail: dict[str, Any],
    fetched_at: datetime,
) -> WechatTeacherArticle:
    detail_id = str(detail.get("id") or "")
    detail_mp_id = str(detail.get("mp_id") or "")
    if detail_id != metadata.article_id or detail_mp_id != metadata.mp_id:
        raise FeedError("source_failed", "article_identity_mismatch")
    if source_account_id != metadata.mp_id:
        raise FeedError("source_failed", "article_source_mismatch")
    raw_html = str(detail.get("content") or "")
    if not raw_html.strip():
        raw_html = str(detail.get("content_html") or "")
    raw_content = html_to_text(raw_html)
    if not raw_content:
        raise FeedError("content_missing", "empty_article_content")
    try:
        source_url = normalize_wechat_url(metadata.url)
        published_at = normalize_publish_time(metadata.publish_time)
    except ValueError as exc:
        reason = "invalid_publish_time" if "publish_time" in str(exc) else "invalid_source_url"
        raise FeedError("source_failed", reason) from exc
    return WechatTeacherArticle(
        teacher_name=teacher_name,
        source_platform=SOURCE_PLATFORM,
        source_account_id=source_account_id,
        source_article_id=metadata.article_id,
        source_url=source_url,
        title=metadata.title.strip(),
        published_at=published_at,
        fetched_at=_normalize_fetched_at(fetched_at),
        raw_content=raw_content,
        raw_html=raw_html,
        content_sha256=canonical_content_sha256(raw_content),
    )
