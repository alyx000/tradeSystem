from __future__ import annotations

from typing import Any, Callable
from urllib.parse import urlsplit

import requests

from .constants import DEFAULT_CONNECT_TIMEOUT, DEFAULT_READ_TIMEOUT, DEFAULT_REFRESH_END_PAGE
from .models import FeedError, RefreshResult, WeRSSArticleMeta, WeRSSSource


class WeRSSClient:
    def __init__(
        self,
        base_url: str,
        access_key: str,
        secret_key: str,
        *,
        session=None,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        read_timeout: float = DEFAULT_READ_TIMEOUT,
    ) -> None:
        self._base_url = self._validate_base_url(base_url)
        if not str(access_key or "").strip() or not str(secret_key or "").strip():
            raise FeedError("blocked", "missing_credentials")
        self._access_key = str(access_key).strip()
        self._secret_key = str(secret_key).strip()
        self._session = session if session is not None else requests.Session()
        self._session.trust_env = False
        self._timeout = (float(connect_timeout), float(read_timeout))

    @staticmethod
    def _validate_base_url(value: str) -> str:
        try:
            parsed = urlsplit(str(value or "").strip())
            _ = parsed.port
        except ValueError as exc:
            raise FeedError("blocked", "invalid_base_url") from exc
        if (
            parsed.scheme not in {"http", "https"}
            or (parsed.hostname or "").lower() not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise FeedError("blocked", "invalid_base_url")
        return str(value).strip().rstrip("/")

    def _request_envelope(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        accepted_codes: set[int] | None = None,
    ) -> tuple[int, Any]:
        headers = {"Authorization": f"AK-SK {self._access_key}:{self._secret_key}"}
        try:
            response = self._session.request(
                "GET",
                self._base_url + path,
                params=params or {},
                headers=headers,
                timeout=self._timeout,
                allow_redirects=False,
            )
        except requests.Timeout as exc:
            raise FeedError("source_failed", "timeout") from None
        except Exception as exc:
            raise FeedError("source_failed", "request_failed") from None
        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code in {401, 403}:
            raise FeedError("auth_expired", f"http_{status_code}")
        if status_code != 200:
            if status_code == 201:
                try:
                    body = response.json()
                except Exception:
                    raise FeedError("source_failed", "http_201") from None
                detail = body.get("detail") if isinstance(body, dict) else None
                code = detail.get("code") if isinstance(detail, dict) else None
                reason = f"upstream_{code}" if isinstance(code, int) else "http_201"
                raise FeedError("source_failed", reason)
            raise FeedError("source_failed", f"http_{status_code}")
        try:
            body = response.json()
        except Exception:
            raise FeedError("source_failed", "invalid_json") from None
        if not isinstance(body, dict):
            raise FeedError("source_failed", "invalid_json_shape")
        code = body.get("code")
        if isinstance(code, bool) or not isinstance(code, int):
            raise FeedError("source_failed", "invalid_response_code")
        if code != 0 and (accepted_codes is None or code not in accepted_codes):
            raise FeedError("source_failed", f"upstream_{code}")
        return code, body.get("data")

    def _request_json(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        _, data = self._request_envelope(path, params=params)
        return data

    @staticmethod
    def _total(data: dict[str, Any]) -> int:
        direct = data.get("total")
        page = data.get("page")
        nested = page.get("total") if isinstance(page, dict) else None
        value = direct if direct is not None else nested
        if (
            isinstance(direct, int)
            and not isinstance(direct, bool)
            and isinstance(nested, int)
            and not isinstance(nested, bool)
            and direct != nested
        ):
            raise FeedError("source_failed", "pagination_total_mismatch")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise FeedError("source_failed", "invalid_pagination_total")
        return value

    def _paginate(
        self,
        path: str,
        *,
        params: dict[str, Any],
        mapper: Callable[[dict[str, Any]], Any],
    ) -> list[Any]:
        offset = 0
        out: list[Any] = []
        identities: set[str] = set()
        while True:
            page_params = {**params, "limit": 100, "offset": offset}
            data = self._request_json(path, params=page_params)
            if not isinstance(data, dict) or not isinstance(data.get("list"), list):
                raise FeedError("source_failed", "invalid_list_payload")
            total = self._total(data)
            rows = data["list"]
            if not rows and offset < total:
                raise FeedError("source_failed", "pagination_stalled")
            for raw in rows:
                if not isinstance(raw, dict):
                    raise FeedError("source_failed", "invalid_list_item")
                item = mapper(raw)
                identity = str(
                    getattr(item, "article_id", None) or getattr(item, "mp_id", "")
                )
                if identity in identities:
                    raise FeedError("source_failed", "pagination_repeated_items")
                identities.add(identity)
                out.append(item)
            offset += len(rows)
            if offset >= total:
                return out

    def list_sources(self) -> list[WeRSSSource]:
        def map_source(raw: dict[str, Any]) -> WeRSSSource:
            mp_id = str(raw.get("id") or raw.get("mp_id") or "")
            mp_name = str(raw.get("mp_name") or "")
            if not mp_id or not mp_name:
                raise FeedError("source_failed", "invalid_source_item")
            try:
                status = int(raw.get("status", 1))
            except (TypeError, ValueError):
                raise FeedError("source_failed", "invalid_source_item") from None
            if status not in {0, 1}:
                raise FeedError("source_failed", "invalid_source_item")
            return WeRSSSource(mp_id=mp_id, mp_name=mp_name, status=status)

        return self._paginate("/api/v1/wx/mps", params={"status": 1}, mapper=map_source)

    def list_articles(self, mp_id: str) -> list[WeRSSArticleMeta]:
        def map_article(raw: dict[str, Any]) -> WeRSSArticleMeta:
            article_id = str(raw.get("id") or "")
            source_id = str(raw.get("mp_id") or "")
            if not article_id or not source_id:
                raise FeedError("source_failed", "invalid_article_metadata")
            try:
                has_content = int(raw.get("has_content", 0) or 0)
            except (TypeError, ValueError):
                raise FeedError("source_failed", "invalid_article_metadata") from None
            if has_content not in {0, 1}:
                raise FeedError("source_failed", "invalid_article_metadata")
            return WeRSSArticleMeta(
                article_id=article_id,
                mp_id=source_id,
                title=str(raw.get("title") or "").strip(),
                url=str(raw.get("url") or "").strip(),
                publish_time=raw.get("publish_time"),
                has_content=bool(has_content),
                raw=dict(raw),
            )

        return self._paginate(
            "/api/v1/wx/articles", params={"mp_id": mp_id}, mapper=map_article
        )

    def get_article_detail(self, article_id: str) -> dict[str, Any]:
        data = self._request_json(
            f"/api/v1/wx/articles/{article_id}", params={"content": "true"}
        )
        if not isinstance(data, dict):
            raise FeedError("source_failed", "invalid_article_detail")
        return data

    def request_update(
        self,
        mp_id: str,
        *,
        start_page: int = 0,
        end_page: int = DEFAULT_REFRESH_END_PAGE,
    ) -> RefreshResult:
        code, _ = self._request_envelope(
            f"/api/v1/wx/mps/update/{mp_id}",
            params={"start_page": start_page, "end_page": end_page},
            accepted_codes={40402},
        )
        if code == 40402:
            return RefreshResult("recent_or_inflight", "recent_or_inflight", verified=False)
        return RefreshResult("refresh_unverified", "background_refresh_unverified", verified=False)
