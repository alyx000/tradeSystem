from __future__ import annotations

import math
import time
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from .constants import (
    DEFAULT_REFRESH_END_PAGE,
    DEFAULT_REFRESH_GRACE_SECONDS,
    MAX_REFRESH_END_PAGE,
    MAX_REFRESH_GRACE_SECONDS,
    WHITELIST,
)
from .models import (
    ArticleObservation,
    CollectionOutcome,
    FeedError,
    PersistResult,
    PhaseDecision,
    RefreshResult,
    SourceResult,
    WeRSSArticleMeta,
    WeRSSSource,
)
from .normalize import normalize_article, normalize_publish_time, normalize_wechat_url
from .store import FeedStore


_FAILURE_STATUSES = {"auth_expired", "source_failed", "source_missing", "content_missing"}


def _empty_outcome(
    decision: PhaseDecision,
    input_by: str,
    *,
    status: str,
    reason: str,
    exit_code: int,
    dry_run: bool,
    cached_only: bool,
) -> CollectionOutcome:
    return CollectionOutcome(
        run_date=decision.run_date,
        phase=decision.phase,
        target_trade_date=decision.target_trade_date,
        input_by=input_by,
        status=status,
        reason=reason,
        exit_code=exit_code,
        source_results=(),
        observations=(),
        cached_only=cached_only,
        dry_run=dry_run,
    )


def _deduplicate_metadata(rows: Iterable[WeRSSArticleMeta]) -> list[WeRSSArticleMeta]:
    by_id: dict[str, WeRSSArticleMeta] = {}
    order: list[str] = []
    for row in rows:
        existing = by_id.get(row.article_id)
        if existing is None:
            by_id[row.article_id] = row
            order.append(row.article_id)
            continue
        if not existing.has_content and row.has_content:
            by_id[row.article_id] = row
    return [by_id[article_id] for article_id in order]


def _base_observation(
    teacher_name: str,
    source_account_id: str,
    metadata: WeRSSArticleMeta,
) -> ArticleObservation:
    if metadata.mp_id != source_account_id:
        raise FeedError("source_failed", "article_source_mismatch")
    if not metadata.title.strip():
        raise FeedError("source_failed", "missing_article_title")
    try:
        source_url = normalize_wechat_url(metadata.url)
        published_at = normalize_publish_time(metadata.publish_time)
    except ValueError as exc:
        reason = "invalid_publish_time" if "publish_time" in str(exc) else "invalid_source_url"
        raise FeedError("source_failed", reason) from exc
    return ArticleObservation(
        teacher_name=teacher_name,
        source_account_id=source_account_id,
        source_article_id=metadata.article_id,
        source_url=source_url,
        title=metadata.title.strip(),
        published_at=published_at,
        state="unseen",
    )


def _error_priority(errors: list[FeedError]) -> FeedError | None:
    for status in ("auth_expired", "source_failed", "content_missing"):
        for error in errors:
            if error.status == status:
                return error
    return errors[0] if errors else None


def _collect_source(
    client: Any,
    store: FeedStore,
    source: WeRSSSource,
    teacher_name: str,
    *,
    run_date: str,
    phase: str,
    snapshot: dict[str, Any],
    dry_run: bool,
    cached_only: bool,
    sleeper: Callable[[float], None],
    refresh_grace_seconds: float,
    refresh_end_page: int,
    fetched_at: Callable[[], datetime],
) -> tuple[SourceResult, list[ArticleObservation]]:
    errors: list[FeedError] = []
    if dry_run:
        refresh = RefreshResult("refresh_unverified", "dry_run_unverified", False)
    elif cached_only:
        refresh = RefreshResult("refresh_unverified", "cached_only_unverified", False)
    else:
        try:
            refresh = client.request_update(
                source.mp_id, start_page=0, end_page=refresh_end_page
            )
            sleeper(refresh_grace_seconds)
        except FeedError as exc:
            errors.append(exc)
            refresh = RefreshResult(exc.status, exc.reason, False)

    try:
        raw_metadata = client.list_articles(source.mp_id)
    except FeedError as exc:
        errors.append(exc)
        primary = _error_priority(errors) or exc
        return (
            SourceResult(
                teacher_name=teacher_name,
                source_account_id=source.mp_id,
                status=primary.status,
                reason=primary.reason,
                refresh_status=refresh.status,
                refresh_verified=refresh.verified,
                issue_count=len(errors),
                listed=False,
            ),
            [],
        )

    rows = _deduplicate_metadata(raw_metadata)
    observations: list[ArticleObservation] = []
    complete_count = 0
    pending_count = 0
    metadata_pending_count = 0

    for metadata in rows:
        try:
            base = _base_observation(teacher_name, source.mp_id, metadata)
            state = store.classify_observation(
                base, run_date=run_date, phase=phase, snapshot=snapshot
            )
        except FeedError as exc:
            errors.append(exc)
            continue

        if state in {"seen", "same_phase_candidate", "baseline_seen"}:
            observations.append(replace(base, state=state))
            if state != "baseline_seen":
                complete_count += 1
            continue

        if not metadata.has_content:
            observations.append(
                replace(base, state="pending_content", reason="has_content_false")
            )
            pending_count += 1
            metadata_pending_count += 1
            continue

        try:
            detail = client.get_article_detail(metadata.article_id)
            article = normalize_article(
                teacher_name=teacher_name,
                source_account_id=source.mp_id,
                metadata=metadata,
                detail=detail,
                fetched_at=fetched_at(),
            )
            complete = ArticleObservation(
                teacher_name=teacher_name,
                source_account_id=source.mp_id,
                source_article_id=article.source_article_id,
                source_url=article.source_url,
                title=article.title,
                published_at=article.published_at,
                state="complete",
                article=article,
            )
            final_state = store.classify_observation(
                complete, run_date=run_date, phase=phase, snapshot=snapshot
            )
            if final_state in {"seen", "same_phase_candidate", "baseline_seen"}:
                complete = replace(complete, state=final_state)
            observations.append(complete)
            complete_count += 1
        except FeedError as exc:
            errors.append(exc)
            if exc.status == "content_missing":
                observations.append(
                    replace(base, state="pending_content", reason=exc.reason)
                )
                pending_count += 1

    primary = _error_priority(errors)
    if primary is not None:
        if complete_count:
            status = "partial"
        else:
            status = primary.status if primary.status in _FAILURE_STATUSES else "source_failed"
        reason = primary.reason
    elif pending_count:
        status = "partial" if complete_count else "content_missing"
        reason = "content_missing"
    elif not refresh.verified:
        status = "partial"
        reason = refresh.reason
    else:
        status = "ok"
        reason = refresh.reason

    return (
        SourceResult(
            teacher_name=teacher_name,
            source_account_id=source.mp_id,
            status=status,
            reason=reason,
            refresh_status=refresh.status,
            refresh_verified=refresh.verified,
            metadata_count=len(rows),
            complete_count=complete_count,
            pending_count=pending_count,
            issue_count=len(errors) + metadata_pending_count,
            listed=True,
        ),
        observations,
    )


def _has_new_candidate(observation: ArticleObservation) -> bool:
    if observation.state == "same_phase_candidate":
        return True
    return observation.article is not None and observation.state not in {
        "seen",
        "baseline_seen",
    }


def _aggregate_status(
    source_results: tuple[SourceResult, ...],
    observations: tuple[ArticleObservation, ...],
) -> tuple[str, str, int]:
    statuses = [result.status for result in source_results]
    if source_results and all(status == "auth_expired" for status in statuses):
        return "auth_expired", "all_sources_auth_expired", 1
    if source_results and all(status == "source_missing" for status in statuses):
        return "source_missing", "all_sources_missing", 1
    if source_results and all(status == "content_missing" for status in statuses):
        return "content_missing", "all_sources_content_missing", 1
    if source_results and all(status == "source_failed" for status in statuses):
        reasons = {result.reason for result in source_results}
        reason = next(iter(reasons)) if len(reasons) == 1 else "all_sources_failed"
        return "source_failed", reason, 1

    clean = len(source_results) == len(WHITELIST) and all(
        result.status == "ok" and result.refresh_verified for result in source_results
    )
    if clean:
        if any(_has_new_candidate(observation) for observation in observations):
            return "success", "all_sources_verified_with_new_articles", 0
        return "empty", "all_sources_verified_no_new_articles", 0

    refresh_only = len(source_results) == len(WHITELIST) and all(
        result.listed
        and result.status == "partial"
        and not result.refresh_verified
        and result.refresh_status in {"refresh_unverified", "recent_or_inflight"}
        and result.pending_count == 0
        and result.issue_count == 0
        for result in source_results
    )
    usable = any(
        (
            result.listed
            and result.status == "ok"
            and result.refresh_verified
        )
        or result.complete_count > 0
        for result in source_results
    )
    if refresh_only:
        first = source_results[0]
        reason = (
            first.reason
            if first.reason in {"cached_only_unverified", "dry_run_unverified"}
            else first.refresh_status
        )
        return "partial", reason, 1
    if usable:
        for result in source_results:
            if result.status != "ok" or not result.refresh_verified:
                reason = (
                    result.reason
                    if result.reason in {"cached_only_unverified", "dry_run_unverified"}
                    else result.refresh_status
                    if not result.refresh_verified
                    and result.refresh_status
                    in {"refresh_unverified", "recent_or_inflight"}
                    else result.reason
                )
                return "partial", reason, 1
        return "partial", "incomplete_source_set", 1
    return "source_failed", "mixed_source_failures", 1


def collect_phase(
    client: Any,
    store: FeedStore,
    decision: PhaseDecision,
    input_by: str,
    *,
    dry_run: bool = False,
    cached_only: bool = False,
    sleeper: Callable[[float], None] = time.sleep,
    refresh_grace_seconds: float = DEFAULT_REFRESH_GRACE_SECONDS,
    refresh_end_page: int = DEFAULT_REFRESH_END_PAGE,
    fetched_at: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> PersistResult:
    """Collect one gated phase and persist the service-owned business outcome."""
    if not str(input_by or "").strip():
        raise ValueError("input_by is required")
    if decision.status == "blocked":
        return store.persist_phase(
            _empty_outcome(
                decision,
                input_by,
                status="blocked",
                reason=decision.reason,
                exit_code=2,
                dry_run=dry_run,
                cached_only=cached_only,
            )
        )
    if decision.status == "skip":
        return store.persist_phase(
            _empty_outcome(
                decision,
                input_by,
                status="skip",
                reason=decision.reason,
                exit_code=0,
                dry_run=dry_run,
                cached_only=cached_only,
            )
        )
    if decision.status != "run":
        raise ValueError(f"unsupported decision status: {decision.status}")
    if (
        not isinstance(refresh_grace_seconds, (int, float))
        or isinstance(refresh_grace_seconds, bool)
        or not math.isfinite(refresh_grace_seconds)
        or refresh_grace_seconds < 0
        or refresh_grace_seconds > MAX_REFRESH_GRACE_SECONDS
    ):
        raise ValueError("refresh_grace_seconds must be between 0 and 300")
    if (
        not isinstance(refresh_end_page, int)
        or isinstance(refresh_end_page, bool)
        or refresh_end_page < 1
        or refresh_end_page > MAX_REFRESH_END_PAGE
    ):
        raise ValueError("refresh_end_page must be between 1 and 20")

    if not dry_run:
        store.recover_phase(decision.run_date, decision.phase)
    snapshot = store.load_snapshot()
    try:
        enabled_sources = [source for source in client.list_sources() if source.status == 1]
    except FeedError as exc:
        status = exc.status if exc.status in {"blocked", "auth_expired", "source_failed"} else "source_failed"
        exit_code = 2 if status == "blocked" else 1
        return store.persist_phase(
            _empty_outcome(
                decision,
                input_by,
                status=status,
                reason=exc.reason,
                exit_code=exit_code,
                dry_run=dry_run,
                cached_only=cached_only,
            )
        )

    source_results: list[SourceResult] = []
    observations: list[ArticleObservation] = []
    for whitelist_source in WHITELIST:
        matches = [
            source
            for source in enabled_sources
            if source.mp_name == whitelist_source.teacher_name
        ]
        if not matches:
            source_results.append(
                SourceResult(
                    teacher_name=whitelist_source.teacher_name,
                    source_account_id=None,
                    status="source_missing",
                    reason="source_missing",
                )
            )
            continue
        if len(matches) > 1:
            source_results.append(
                SourceResult(
                    teacher_name=whitelist_source.teacher_name,
                    source_account_id=None,
                    status="source_failed",
                    reason="ambiguous_source",
                )
            )
            continue
        pinned_source = (snapshot.get("sources") or {}).get(
            whitelist_source.teacher_name
        )
        pinned_account_id = (
            str(pinned_source.get("source_account_id") or "")
            if isinstance(pinned_source, dict)
            else ""
        )
        if pinned_account_id and matches[0].mp_id != pinned_account_id:
            source_results.append(
                SourceResult(
                    teacher_name=whitelist_source.teacher_name,
                    source_account_id=matches[0].mp_id,
                    status="source_failed",
                    reason="source_identity_changed",
                    listed=False,
                )
            )
            continue
        result, source_observations = _collect_source(
            client,
            store,
            matches[0],
            whitelist_source.teacher_name,
            run_date=decision.run_date,
            phase=decision.phase,
            snapshot=snapshot,
            dry_run=dry_run,
            cached_only=cached_only,
            sleeper=sleeper,
            refresh_grace_seconds=refresh_grace_seconds,
            refresh_end_page=refresh_end_page,
            fetched_at=fetched_at,
        )
        source_results.append(result)
        observations.extend(source_observations)

    source_tuple = tuple(source_results)
    observation_tuple = tuple(observations)
    status, reason, exit_code = _aggregate_status(source_tuple, observation_tuple)
    return store.persist_phase(
        CollectionOutcome(
            run_date=decision.run_date,
            phase=decision.phase,
            target_trade_date=decision.target_trade_date,
            input_by=input_by,
            status=status,
            reason=reason,
            exit_code=exit_code,
            source_results=source_tuple,
            observations=observation_tuple,
            cached_only=cached_only,
            dry_run=dry_run,
        )
    )
