from __future__ import annotations

from datetime import datetime, timezone

import pytest
import requests

from services.content_identity import canonical_content_sha256
from services.wechat_teacher_feed.client import WeRSSClient
from services.wechat_teacher_feed.constants import WHITELIST
from services.wechat_teacher_feed.models import FeedError, WeRSSArticleMeta
from services.wechat_teacher_feed.normalize import (
    html_to_text,
    normalize_article,
    normalize_publish_time,
    normalize_wechat_url,
)


class FakeResponse:
    def __init__(self, payload=None, *, status_code: int = 200, json_error: Exception | None = None):
        self._payload = payload
        self.status_code = status_code
        self._json_error = json_error

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.trust_env = True

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _ok(data):
    return FakeResponse({"code": 0, "message": "success", "data": data})


def test_whitelist_is_exact_and_immutable() -> None:
    assert [(item.teacher_name, item.seed_url) for item in WHITELIST] == [
        ("安静拆主线", "https://mp.weixin.qq.com/s/6RCwiTm4z85BVSMqsFEJRA"),
        ("股痴流沙河", "https://mp.weixin.qq.com/s/uEuR9LOFufNF0LC1eOlpQw"),
        ("爱在冰川", "https://mp.weixin.qq.com/s/6205pCZ6Y3Num0gTzGdLjQ"),
    ]
    assert isinstance(WHITELIST, tuple)
    with pytest.raises((AttributeError, TypeError)):
        WHITELIST[0].teacher_name = "other"  # type: ignore[misc]


def test_normalize_wechat_url_keeps_identity_and_drops_tracking() -> None:
    short = normalize_wechat_url(
        "http://mp.weixin.qq.com/s/token-_A?scene=1&from=timeline#wechat_redirect"
    )
    legacy = normalize_wechat_url(
        "https://mp.weixin.qq.com/s?scene=1&sn=abc&idx=2&mid=123&__biz=MzA%3D&chksm=xyz"
    )

    assert short == "https://mp.weixin.qq.com/s/token-_A"
    assert legacy == (
        "https://mp.weixin.qq.com/s?__biz=MzA%3D&chksm=xyz&idx=2&mid=123&sn=abc"
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1783951200, "2026-07-13T22:00:00+08:00"),
        (1783951200000, "2026-07-13T22:00:00+08:00"),
        ("1783951200", "2026-07-13T22:00:00+08:00"),
    ],
)
def test_publish_time_accepts_seconds_and_milliseconds(value, expected: str) -> None:
    assert normalize_publish_time(value) == expected


@pytest.mark.parametrize("value", [None, "", "abc", True, 10**30])
def test_publish_time_rejects_missing_or_invalid_values(value) -> None:
    with pytest.raises(ValueError, match="publish_time"):
        normalize_publish_time(value)


def test_html_to_text_is_readable_and_canonical() -> None:
    raw = (
        "<style>.x{}</style><h1> 主线&nbsp;判断 </h1>"
        "<p>第一点<br>第二点</p><script>secret()</script><ul><li>项目 A</li></ul>"
    )

    text = html_to_text(raw)

    assert text == "主线 判断\n第一点\n第二点\n项目 A\n"


def test_normalize_article_prefers_nonempty_content_and_checks_identity() -> None:
    meta = WeRSSArticleMeta(
        article_id="art-1",
        mp_id="mp-1",
        title="测试文章",
        url="https://mp.weixin.qq.com/s/token?scene=1",
        publish_time=1783951200,
        has_content=True,
    )
    detail = {
        "id": "art-1",
        "mp_id": "mp-1",
        "content": "<p>正文 A</p>",
        "content_html": "<p>错误后备</p>",
    }

    article = normalize_article(
        teacher_name="安静拆主线",
        source_account_id="mp-1",
        metadata=meta,
        detail=detail,
        fetched_at=datetime(2026, 7, 13, 14, 20, tzinfo=timezone.utc),
    )

    assert article.raw_html == "<p>正文 A</p>"
    assert article.raw_content == "正文 A\n"
    assert article.content_sha256 == canonical_content_sha256("正文 A")
    assert article.source_url == "https://mp.weixin.qq.com/s/token"
    assert article.published_at == "2026-07-13T22:00:00+08:00"
    assert article.fetched_at == "2026-07-13T22:20:00+08:00"

    with pytest.raises(FeedError, match="article_identity_mismatch"):
        normalize_article(
            teacher_name="安静拆主线",
            source_account_id="mp-1",
            metadata=meta,
            detail={**detail, "mp_id": "mp-other"},
            fetched_at=datetime.now(timezone.utc),
        )


def test_normalize_article_uses_content_html_fallback_and_rejects_empty_body() -> None:
    meta = WeRSSArticleMeta(
        "art-1", "mp-1", "标题", "https://mp.weixin.qq.com/s/token", 1783951200, True
    )
    fallback = normalize_article(
        teacher_name="安静拆主线",
        source_account_id="mp-1",
        metadata=meta,
        detail={"id": "art-1", "mp_id": "mp-1", "content": " ", "content_html": "<p>后备</p>"},
        fetched_at=datetime.now(timezone.utc),
    )
    assert fallback.raw_content == "后备\n"

    with pytest.raises(FeedError, match="empty_article_content"):
        normalize_article(
            teacher_name="安静拆主线",
            source_account_id="mp-1",
            metadata=meta,
            detail={"id": "art-1", "mp_id": "mp-1", "content": "", "content_html": ""},
            fetched_at=datetime.now(timezone.utc),
        )


def test_client_disables_env_proxy_and_paginates_sources() -> None:
    page1 = {
        "list": [{"id": f"mp-{i}", "mp_name": f"源{i}", "status": 1} for i in range(100)],
        "page": {"limit": 100, "offset": 0, "total": 101},
        "total": 101,
    }
    page2 = {
        "list": [{"id": "mp-last", "mp_name": "安静拆主线", "status": 1}],
        "page": {"limit": 100, "offset": 100, "total": 101},
        "total": 101,
    }
    session = FakeSession([_ok(page1), _ok(page2)])
    client = WeRSSClient(
        base_url="http://127.0.0.1:8001",
        access_key="WK-test",
        secret_key="SK-test",
        session=session,
    )

    sources = client.list_sources()

    assert len(sources) == 101
    assert sources[-1].mp_name == "安静拆主线"
    assert session.trust_env is False
    assert [call[2]["params"]["offset"] for call in session.calls] == [0, 100]
    for _, url, kwargs in session.calls:
        assert url.startswith("http://127.0.0.1:8001/api/v1/wx/")
        assert kwargs["headers"]["Authorization"] == "AK-SK WK-test:SK-test"
        assert kwargs["allow_redirects"] is False
        assert isinstance(kwargs["timeout"], tuple)


def test_client_lists_all_articles_without_filtering_missing_content() -> None:
    session = FakeSession(
        [
            _ok(
                {
                    "list": [
                        {
                            "id": "a1",
                            "mp_id": "mp1",
                            "title": "有正文",
                            "url": "https://mp.weixin.qq.com/s/a1",
                            "publish_time": 1783951200,
                            "has_content": 1,
                        },
                        {
                            "id": "a2",
                            "mp_id": "mp1",
                            "title": "待正文",
                            "url": "https://mp.weixin.qq.com/s/a2",
                            "publish_time": 1783951200,
                            "has_content": 0,
                        },
                    ],
                    "total": 2,
                }
            )
        ]
    )
    client = WeRSSClient("http://localhost:8001", "WK-test", "SK-test", session=session)

    rows = client.list_articles("mp1")

    assert [row.has_content for row in rows] == [True, False]
    assert "has_content" not in session.calls[0][2]["params"]


def test_client_detail_and_refresh_protocol() -> None:
    session = FakeSession(
        [
            _ok({"id": "a1", "mp_id": "mp1", "content": "<p>x</p>"}),
            _ok({"time_span": 99, "list": [], "total": 0}),
            FakeResponse({"code": 40402, "message": "recent", "data": {"time_span": 1}}),
        ]
    )
    client = WeRSSClient("http://[::1]:8001", "WK-test", "SK-test", session=session)

    assert client.get_article_detail("a1")["id"] == "a1"
    started = client.request_update("mp1")
    recent = client.request_update("mp1")

    assert (started.status, started.verified) == ("refresh_unverified", False)
    assert (recent.status, recent.verified) == ("recent_or_inflight", False)
    assert session.calls[1][2]["params"] == {"start_page": 0, "end_page": 5}


@pytest.mark.parametrize(
    ("response", "status", "reason"),
    [
        (FakeResponse({"detail": "bad auth"}, status_code=401), "auth_expired", "http_401"),
        (FakeResponse({"detail": {"code": 50001, "message": "boom"}}, status_code=201), "source_failed", "upstream_50001"),
        (FakeResponse({"code": 50002, "message": "boom"}), "source_failed", "upstream_50002"),
        (FakeResponse({}, status_code=302), "source_failed", "http_302"),
        (FakeResponse(status_code=500), "source_failed", "http_500"),
        (FakeResponse(json_error=ValueError("WK-test SK-test Authorization")), "source_failed", "invalid_json"),
        (requests.Timeout("WK-test SK-test Authorization"), "source_failed", "timeout"),
    ],
)
def test_client_errors_are_classified_without_secret_text(response, status: str, reason: str) -> None:
    session = FakeSession([response])
    client = WeRSSClient("http://127.0.0.1:8001", "WK-test", "SK-test", session=session)

    with pytest.raises(FeedError) as caught:
        client.list_sources()

    assert (caught.value.status, caught.value.reason) == (status, reason)
    rendered = repr(caught.value) + str(caught.value)
    assert "WK-test" not in rendered
    assert "SK-test" not in rendered
    assert "Authorization" not in rendered


def test_pagination_empty_page_before_total_fails_fast() -> None:
    session = FakeSession([_ok({"list": [], "total": 2})])
    client = WeRSSClient("http://127.0.0.1:8001", "WK-test", "SK-test", session=session)

    with pytest.raises(FeedError, match="pagination_stalled"):
        client.list_articles("mp1")

    assert len(session.calls) == 1


@pytest.mark.parametrize(
    ("payload", "call", "reason"),
    [
        ({"code": True, "data": {"list": [], "total": 0}}, "sources", "invalid_response_code"),
        ({"code": 0, "data": {"list": [], "total": True}}, "sources", "invalid_pagination_total"),
        (
            {
                "code": 0,
                "data": {
                    "list": [{"id": "mp1", "mp_name": "安静拆主线", "status": "bad"}],
                    "total": 1,
                },
            },
            "sources",
            "invalid_source_item",
        ),
        (
            {
                "code": 0,
                "data": {
                    "list": [
                        {
                            "id": "a1",
                            "mp_id": "mp1",
                            "title": "标题",
                            "url": "https://mp.weixin.qq.com/s/a1",
                            "publish_time": 1783951200,
                            "has_content": "bad",
                        }
                    ],
                    "total": 1,
                },
            },
            "articles",
            "invalid_article_metadata",
        ),
    ],
)
def test_client_rejects_malformed_scalar_fields(payload, call: str, reason: str) -> None:
    client = WeRSSClient(
        "http://127.0.0.1:8001",
        "WK-test",
        "SK-test",
        session=FakeSession([FakeResponse(payload)]),
    )

    with pytest.raises(FeedError) as caught:
        client.list_sources() if call == "sources" else client.list_articles("mp1")

    assert caught.value.reason == reason


@pytest.mark.parametrize(
    "base_url",
    [
        "https://example.com:8001",
        "http://localhost.evil:8001",
        "http://user@localhost:8001",
        "http://localhost:8001/path",
        "http://localhost:8001?token=x",
        "file://localhost/tmp/socket",
    ],
)
def test_client_rejects_non_loopback_or_non_origin_base_url(base_url: str) -> None:
    with pytest.raises(FeedError) as caught:
        WeRSSClient(base_url, "WK-test", "SK-test", session=FakeSession([]))

    assert caught.value.status == "blocked"
    assert caught.value.reason == "invalid_base_url"
