from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from services.wechat_teacher_feed.constants import WHITELIST
from services.wechat_teacher_feed.models import (
    FeedError,
    PhaseDecision,
    RefreshResult,
    WeRSSArticleMeta,
    WeRSSSource,
)
from services.wechat_teacher_feed.service import collect_phase
from services.wechat_teacher_feed.store import FeedStore


RUN_DATE = "2026-07-13"
PUBLISH_TIME = 1783951200


def _sources() -> list[WeRSSSource]:
    return [
        WeRSSSource(f"mp-{index}", source.teacher_name)
        for index, source in enumerate(WHITELIST, start=1)
    ]


def _metadata(mp_id: str, article_id: str, *, has_content: bool = True) -> WeRSSArticleMeta:
    return WeRSSArticleMeta(
        article_id=article_id,
        mp_id=mp_id,
        title=f"标题 {article_id}",
        url=f"https://mp.weixin.qq.com/s/{article_id}?scene=1",
        publish_time=PUBLISH_TIME,
        has_content=has_content,
    )


def _detail(meta: WeRSSArticleMeta, content: str = "<p>老师观点</p>") -> dict:
    return {
        "id": meta.article_id,
        "mp_id": meta.mp_id,
        "content": content,
        "sync_time": 9999999999,
    }


class FakeClient:
    def __init__(self) -> None:
        self.sources = _sources()
        self.source_error: FeedError | None = None
        self.refreshes: dict[str, RefreshResult | FeedError] = {
            source.mp_id: RefreshResult("verified_success", "verified_success", True)
            for source in self.sources
        }
        self.article_lists: dict[str, list[WeRSSArticleMeta] | FeedError] = {
            source.mp_id: [] for source in self.sources
        }
        self.details: dict[str, dict | FeedError] = {}
        self.events: list[tuple[str, object]] = []

    def list_sources(self) -> list[WeRSSSource]:
        self.events.append(("list_sources", None))
        if self.source_error is not None:
            raise self.source_error
        return list(self.sources)

    def request_update(self, mp_id: str, *, start_page: int, end_page: int) -> RefreshResult:
        self.events.append(("update", (mp_id, start_page, end_page)))
        result = self.refreshes[mp_id]
        if isinstance(result, FeedError):
            raise result
        return result

    def list_articles(self, mp_id: str) -> list[WeRSSArticleMeta]:
        self.events.append(("list_articles", mp_id))
        result = self.article_lists[mp_id]
        if isinstance(result, FeedError):
            raise result
        return list(result)

    def get_article_detail(self, article_id: str) -> dict:
        self.events.append(("detail", article_id))
        result = self.details[article_id]
        if isinstance(result, FeedError):
            raise result
        return dict(result)


def _decision(status: str = "run", reason: str = "scheduled") -> PhaseDecision:
    return PhaseDecision(status, RUN_DATE, "post-market", RUN_DATE, reason)


def _collect(
    tmp_path: Path,
    client: FakeClient,
    *,
    decision: PhaseDecision | None = None,
    dry_run: bool = False,
    cached_only: bool = False,
    events: list[tuple[str, object]] | None = None,
):
    event_log = events if events is not None else client.events

    def sleeper(seconds: float) -> None:
        event_log.append(("sleep", seconds))

    return collect_phase(
        client,
        FeedStore(tmp_path / "runs"),
        decision or _decision(),
        "codex_automation",
        dry_run=dry_run,
        cached_only=cached_only,
        sleeper=sleeper,
        refresh_grace_seconds=0.25,
        fetched_at=lambda: datetime(2026, 7, 13, 14, 30, tzinfo=timezone.utc),
    )


def test_collects_only_exact_whitelist_in_sequential_refresh_order(tmp_path: Path) -> None:
    client = FakeClient()
    client.sources.insert(1, WeRSSSource("mp-extra", "额外订阅"))
    for source in _sources():
        meta = _metadata(source.mp_id, f"article-{source.mp_id}")
        client.article_lists[source.mp_id] = [meta]
        client.details[meta.article_id] = _detail(meta)

    result = _collect(tmp_path, client)

    assert result.manifest["status"] == "success"
    assert result.manifest["exit_code"] == 0
    assert [row["teacher_name"] for row in result.manifest["source_results"]] == [
        source.teacher_name for source in WHITELIST
    ]
    assert not any("extra" in str(event) for event in client.events)
    assert [kind for kind, _ in client.events] == [
        "list_sources",
        "update",
        "sleep",
        "list_articles",
        "detail",
        "update",
        "sleep",
        "list_articles",
        "detail",
        "update",
        "sleep",
        "list_articles",
        "detail",
    ]
    assert [value for kind, value in client.events if kind == "update"] == [
        ("mp-1", 0, 5),
        ("mp-2", 0, 5),
        ("mp-3", 0, 5),
    ]
    first = result.manifest["new_articles"][0]
    assert first["date"] == RUN_DATE
    assert first["published_at"] == "2026-07-13T22:00:00+08:00"
    assert first["source_url"].startswith("https://mp.weixin.qq.com/s/article-")
    assert len(first["content_sha256"]) == 64


def test_missing_and_ambiguous_sources_are_explicit_partial_results(tmp_path: Path) -> None:
    client = FakeClient()
    client.sources = [
        _sources()[0],
        _sources()[1],
        replace(_sources()[1], mp_id="mp-duplicate"),
        WeRSSSource("mp-extra", "额外订阅"),
    ]

    result = _collect(tmp_path, client)

    assert result.manifest["status"] == "partial"
    assert result.manifest["exit_code"] == 1
    by_name = {
        row["teacher_name"]: (row["status"], row["reason"])
        for row in result.manifest["source_results"]
    }
    assert by_name == {
        "安静拆主线": ("ok", "verified_success"),
        "股痴流沙河": ("source_failed", "ambiguous_source"),
        "爱在冰川": ("source_missing", "source_missing"),
    }


def test_pinned_source_account_rejects_same_name_replacement(tmp_path: Path) -> None:
    initial = FakeClient()
    first_result = _collect(tmp_path, initial)
    assert first_result.manifest["status"] == "empty"
    store = FeedStore(tmp_path / "runs")
    before = store.load_snapshot()
    assert before["sources"]["安静拆主线"]["source_account_id"] == "mp-1"

    replacement = FakeClient()
    replacement.sources[0] = WeRSSSource("mp-replacement", "安静拆主线")
    result = _collect(tmp_path, replacement)

    by_name = {
        row["teacher_name"]: row
        for row in result.manifest["source_results"]
    }
    assert result.manifest["status"] == "partial"
    assert by_name["安静拆主线"]["status"] == "source_failed"
    assert by_name["安静拆主线"]["reason"] == "source_identity_changed"
    assert not any(
        value == "mp-replacement"
        or isinstance(value, tuple) and value[0] == "mp-replacement"
        for _, value in replacement.events
    )
    assert store.load_snapshot() == before


@pytest.mark.parametrize("grace", [float("inf"), float("nan"), 301.0])
def test_service_rejects_unsafe_refresh_grace_before_client(
    grace: float, tmp_path: Path
) -> None:
    client = FakeClient()

    with pytest.raises(ValueError, match="refresh_grace_seconds"):
        collect_phase(
            client,
            FeedStore(tmp_path / "runs"),
            _decision(),
            "codex_automation",
            refresh_grace_seconds=grace,
        )

    assert client.events == []


@pytest.mark.parametrize("end_page", [True, 0, 21])
def test_service_rejects_unsafe_refresh_end_page_before_client(
    end_page, tmp_path: Path
) -> None:
    client = FakeClient()

    with pytest.raises(ValueError, match="refresh_end_page"):
        collect_phase(
            client,
            FeedStore(tmp_path / "runs"),
            _decision(),
            "codex_automation",
            refresh_end_page=end_page,
        )

    assert client.events == []


def test_all_missing_is_source_missing_and_mixed_failures_are_source_failed(tmp_path: Path) -> None:
    missing = FakeClient()
    missing.sources = [WeRSSSource("extra", "额外订阅")]
    missing_result = _collect(tmp_path / "missing", missing)
    assert (missing_result.manifest["status"], missing_result.manifest["exit_code"]) == (
        "source_missing",
        1,
    )

    mixed = FakeClient()
    mixed.article_lists["mp-1"] = FeedError("auth_expired", "http_401")
    mixed.article_lists["mp-2"] = FeedError("source_failed", "timeout")
    mixed.sources = mixed.sources[:2]
    mixed_result = _collect(tmp_path / "mixed", mixed)
    assert mixed_result.manifest["status"] == "source_failed"
    assert mixed_result.manifest["reason"] == "mixed_source_failures"


def test_has_content_false_never_fetches_detail_and_all_pending_is_content_missing(
    tmp_path: Path,
) -> None:
    client = FakeClient()
    for source in client.sources:
        client.article_lists[source.mp_id] = [
            _metadata(source.mp_id, f"pending-{source.mp_id}", has_content=False)
        ]

    result = _collect(tmp_path, client)

    assert result.manifest["status"] == "content_missing"
    assert result.manifest["counts"]["pending"] == 3
    assert not any(kind == "detail" for kind, _ in client.events)


def test_empty_detail_is_pending_and_duplicate_metadata_fetches_detail_once(tmp_path: Path) -> None:
    client = FakeClient()
    meta = _metadata("mp-1", "duplicate")
    client.article_lists["mp-1"] = [meta, meta]
    client.details[meta.article_id] = _detail(meta, "")

    result = _collect(tmp_path, client)

    assert result.manifest["status"] == "partial"
    assert result.manifest["pending_article_ids"] == ["duplicate"]
    assert [event for event in client.events if event == ("detail", "duplicate")] == [
        ("detail", "duplicate")
    ]


@pytest.mark.parametrize(
    ("failure", "expected_status", "expected_reason"),
    [
        (FeedError("auth_expired", "http_401"), "auth_expired", "http_401"),
        (FeedError("source_failed", "timeout"), "source_failed", "timeout"),
        (
            {"id": "wrong-id", "mp_id": "mp-1", "content": "<p>正文</p>"},
            "source_failed",
            "article_identity_mismatch",
        ),
    ],
)
def test_non_content_detail_failures_do_not_create_pending_index_records(
    tmp_path: Path,
    failure,
    expected_status: str,
    expected_reason: str,
) -> None:
    client = FakeClient()
    meta = _metadata("mp-1", "failed-detail")
    client.article_lists["mp-1"] = [meta]
    client.details[meta.article_id] = failure

    result = _collect(tmp_path, client)

    assert result.manifest["status"] == "partial"
    first_source = result.manifest["source_results"][0]
    assert (first_source["status"], first_source["reason"]) == (
        expected_status,
        expected_reason,
    )
    assert result.pending_article_ids == ()
    assert result.manifest["counts"]["pending"] == 0
    index = json.loads((tmp_path / "runs" / "index.json").read_text(encoding="utf-8"))
    assert index["records"] == []


@pytest.mark.parametrize("first_kind", ["metadata", "empty_detail"])
def test_pending_content_recovers_to_archived_on_later_run(
    tmp_path: Path, first_kind: str
) -> None:
    first_client = FakeClient()
    second_client = FakeClient()
    for source in first_client.sources:
        article_id = f"recover-{source.mp_id}"
        first_meta = _metadata(
            source.mp_id, article_id, has_content=first_kind != "metadata"
        )
        first_client.article_lists[source.mp_id] = [first_meta]
        if first_kind == "empty_detail":
            first_client.details[article_id] = _detail(first_meta, "")

        second_meta = _metadata(source.mp_id, article_id)
        second_client.article_lists[source.mp_id] = [second_meta]
        second_client.details[article_id] = _detail(second_meta, "<p>补齐正文</p>")

    first = _collect(tmp_path, first_client)
    second = _collect(tmp_path, second_client)

    assert first.manifest["counts"]["pending"] == 3
    assert second.manifest["status"] == "success"
    assert len(second.new_article_ids) == 3
    assert second.pending_article_ids == ()


@pytest.mark.parametrize(
    ("refresh", "reason"),
    [
        (RefreshResult("refresh_unverified", "background_refresh_unverified"), "refresh_unverified"),
        (RefreshResult("recent_or_inflight", "recent_or_inflight"), "recent_or_inflight"),
    ],
)
def test_unverified_refresh_can_archive_candidates_but_is_partial(
    tmp_path: Path, refresh: RefreshResult, reason: str
) -> None:
    client = FakeClient()
    for source in client.sources:
        client.refreshes[source.mp_id] = refresh
        meta = _metadata(source.mp_id, f"new-{source.mp_id}")
        client.article_lists[source.mp_id] = [meta]
        client.details[meta.article_id] = _detail(meta)

    result = _collect(tmp_path, client)

    assert result.manifest["status"] == "partial"
    assert result.manifest["reason"] == reason
    assert len(result.new_article_ids) == 3


def test_cached_only_and_dry_run_skip_update_and_never_claim_verified_empty(
    tmp_path: Path,
) -> None:
    cached_client = FakeClient()
    cached = _collect(tmp_path / "cached", cached_client, cached_only=True)
    assert cached.manifest["status"] == "partial"
    assert cached.manifest["reason"] == "cached_only_unverified"
    assert not any(kind in {"update", "sleep"} for kind, _ in cached_client.events)

    dry_client = FakeClient()
    dry_root = tmp_path / "dry"
    dry = _collect(dry_root, dry_client, dry_run=True)
    assert dry.manifest["status"] == "partial"
    assert dry.manifest["commit_state"] == "dry_run"
    assert not any(kind in {"update", "sleep"} for kind, _ in dry_client.events)
    assert not (dry_root / "runs").exists()


def test_one_auth_failure_plus_two_verified_sources_is_partial(tmp_path: Path) -> None:
    client = FakeClient()
    client.article_lists["mp-1"] = FeedError("auth_expired", "http_401")

    result = _collect(tmp_path, client)

    assert result.manifest["status"] == "partial"
    assert result.manifest["exit_code"] == 1
    assert result.manifest["source_results"][0]["status"] == "auth_expired"


def test_all_source_auth_failures_and_global_auth_failure_are_auth_expired(
    tmp_path: Path,
) -> None:
    client = FakeClient()
    for source in client.sources:
        client.article_lists[source.mp_id] = FeedError("auth_expired", "http_403")
    all_source = _collect(tmp_path / "per-source", client)
    assert all_source.manifest["status"] == "auth_expired"

    global_client = FakeClient()
    global_client.source_error = FeedError("auth_expired", "http_401")
    global_result = _collect(tmp_path / "global", global_client)
    assert global_result.manifest["status"] == "auth_expired"
    assert global_result.manifest["source_results"] == []


def test_all_same_source_failures_are_not_reported_as_mixed(tmp_path: Path) -> None:
    client = FakeClient()
    for source in client.sources:
        client.article_lists[source.mp_id] = FeedError("source_failed", "timeout")

    result = _collect(tmp_path, client)

    assert result.manifest["status"] == "source_failed"
    assert result.manifest["reason"] == "timeout"


def test_only_verified_all_source_completion_can_be_empty(tmp_path: Path) -> None:
    client = FakeClient()
    result = _collect(tmp_path, client)

    assert result.manifest["status"] == "empty"
    assert result.manifest["reason"] == "all_sources_verified_no_new_articles"
    assert result.manifest["exit_code"] == 0


def test_same_phase_rerun_keeps_candidate_without_refetching_detail(tmp_path: Path) -> None:
    first_client = FakeClient()
    second_client = FakeClient()
    for source in first_client.sources:
        meta = _metadata(source.mp_id, f"rerun-{source.mp_id}")
        first_client.article_lists[source.mp_id] = [meta]
        first_client.details[meta.article_id] = _detail(meta)
        second_client.article_lists[source.mp_id] = [meta]

    first = _collect(tmp_path, first_client)
    second = _collect(tmp_path, second_client)

    assert first.new_article_ids == second.new_article_ids
    assert second.candidate_article_ids == first.candidate_article_ids
    assert second.manifest["status"] == "success"
    assert not any(kind == "detail" for kind, _ in second_client.events)


def test_prior_candidates_are_retained_but_not_counted_new_on_non_observing_reruns(
    tmp_path: Path,
) -> None:
    first_client = FakeClient()
    for source in first_client.sources:
        meta = _metadata(source.mp_id, f"retained-{source.mp_id}")
        first_client.article_lists[source.mp_id] = [meta]
        first_client.details[meta.article_id] = _detail(meta)
    first = _collect(tmp_path, first_client)

    auth_client = FakeClient()
    auth_client.source_error = FeedError("auth_expired", "http_401")
    auth = _collect(tmp_path, auth_client)
    blocked = _collect(
        tmp_path,
        FakeClient(),
        decision=_decision("blocked", "calendar_unavailable"),
    )
    verified_empty = _collect(tmp_path, FakeClient())

    expected = first.candidate_article_ids
    assert len(expected) == 3
    for result, status in [
        (auth, "auth_expired"),
        (blocked, "blocked"),
        (verified_empty, "empty"),
    ]:
        assert result.manifest["status"] == status
        assert result.new_article_ids == ()
        assert result.manifest["counts"]["new"] == 0
        assert result.retained_article_ids == expected
        assert result.candidate_article_ids == expected


def test_mixed_failures_need_verified_source_or_usable_fulltext_for_partial(
    tmp_path: Path,
) -> None:
    no_usable = FakeClient()
    no_usable.sources = no_usable.sources[:2]
    no_usable.refreshes["mp-1"] = RefreshResult(
        "refresh_unverified", "background_refresh_unverified", False
    )
    no_usable.article_lists["mp-2"] = FeedError("auth_expired", "http_401")
    failed = _collect(tmp_path / "no-usable", no_usable)
    assert (failed.manifest["status"], failed.manifest["reason"]) == (
        "source_failed",
        "mixed_source_failures",
    )

    fulltext = FakeClient()
    fulltext.sources = fulltext.sources[:2]
    fulltext.refreshes["mp-1"] = RefreshResult(
        "refresh_unverified", "background_refresh_unverified", False
    )
    meta = _metadata("mp-1", "usable-fulltext")
    fulltext.article_lists["mp-1"] = [meta]
    fulltext.details[meta.article_id] = _detail(meta)
    fulltext.article_lists["mp-2"] = FeedError("auth_expired", "http_401")
    partial_with_text = _collect(tmp_path / "fulltext", fulltext)
    assert partial_with_text.manifest["status"] == "partial"

    verified = FakeClient()
    verified.sources = verified.sources[:2]
    verified.article_lists["mp-2"] = FeedError("auth_expired", "http_401")
    partial_with_verified_source = _collect(tmp_path / "verified", verified)
    assert partial_with_verified_source.manifest["status"] == "partial"

    all_unverified = FakeClient()
    for source in all_unverified.sources:
        all_unverified.refreshes[source.mp_id] = RefreshResult(
            "refresh_unverified", "background_refresh_unverified", False
        )
    empty_cache = _collect(tmp_path / "all-unverified", all_unverified)
    assert (empty_cache.manifest["status"], empty_cache.manifest["reason"]) == (
        "partial",
        "refresh_unverified",
    )


def test_blocked_decision_short_circuits_client_and_has_exit_code_two(tmp_path: Path) -> None:
    client = FakeClient()
    result = _collect(
        tmp_path,
        client,
        decision=_decision("blocked", "calendar_unavailable"),
    )

    assert result.manifest["status"] == "blocked"
    assert result.manifest["exit_code"] == 2
    assert client.events == []
