from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from services.content_identity import canonical_content_sha256, canonicalize_raw_content
from services.wechat_teacher_feed.models import (
    ArticleObservation,
    CollectionOutcome,
    FeedError,
    SourceResult,
    WechatTeacherArticle,
)
from services.wechat_teacher_feed.store import FeedStore, manifest_digest


def _article(
    article_id: str = "article/../../one",
    *,
    url: str = "https://mp.weixin.qq.com/s/token-one",
    title: str = "主线复盘",
    content: str = "观点一\n",
    published_at: str = "2026-07-13T20:18:00+08:00",
) -> WechatTeacherArticle:
    return WechatTeacherArticle(
        teacher_name="安静拆主线",
        source_platform="wechat_mp",
        source_account_id="mp-quiet",
        source_article_id=article_id,
        source_url=url,
        title=title,
        published_at=published_at,
        fetched_at="2026-07-13T22:15:05+08:00",
        raw_content=content,
        raw_html=f"<p>{content.strip()}</p>",
        content_sha256=canonical_content_sha256(content),
    )


def _observation(article: WechatTeacherArticle, state: str = "complete") -> ArticleObservation:
    return ArticleObservation(
        teacher_name=article.teacher_name,
        source_account_id=article.source_account_id,
        source_article_id=article.source_article_id,
        source_url=article.source_url,
        title=article.title,
        published_at=article.published_at,
        state=state,
        article=article if state == "complete" else None,
        reason="missing_body" if state == "pending_content" else "",
    )


def _conflicting_observation(
    article: WechatTeacherArticle, conflict: str
) -> ArticleObservation:
    replacements = {
        "teacher_name": {"teacher_name": "冒名老师"},
        "source_account_id": {"source_account_id": "mp-impostor"},
        "title": {"title": "冲突标题"},
        "publication_date": {"published_at": "2026-07-14T20:18:00+08:00"},
        "published_at": {"published_at": "2026-07-13T21:18:00+08:00"},
        "normalized_url": {"source_url": "https://mp.weixin.qq.com/s/conflict"},
    }
    return _observation(replace(article, **replacements[conflict]))


def _observation_with_embedded_article_conflict(
    article: WechatTeacherArticle, conflict: str
) -> ArticleObservation:
    replacements = {
        "teacher_name": {"teacher_name": "内嵌冒名老师"},
        "source_account_id": {"source_account_id": "mp-embedded-impostor"},
        "source_platform": {"source_platform": "not-wechat"},
        "source_article_id": {"source_article_id": "embedded-conflict-id"},
        "normalized_url": {
            "source_url": "https://mp.weixin.qq.com/s/embedded-conflict"
        },
        "title": {"title": "内嵌冲突标题"},
        "published_at": {"published_at": "2026-07-13T21:18:00+08:00"},
    }
    return replace(
        _observation(article), article=replace(article, **replacements[conflict])
    )


def _source(*, status: str = "ok", listed: bool = True) -> SourceResult:
    return SourceResult(
        teacher_name="安静拆主线",
        source_account_id="mp-quiet",
        status=status,
        reason="listed",
        refresh_status="verified_success",
        refresh_verified=True,
        metadata_count=1,
        complete_count=1 if status == "ok" else 0,
        listed=listed,
    )


def _outcome(
    *observations: ArticleObservation,
    phase: str = "post-market",
    status: str = "success",
    reason: str = "all_sources_verified",
    dry_run: bool = False,
    cached_only: bool = False,
    source_results: tuple[SourceResult, ...] | None = None,
) -> CollectionOutcome:
    return CollectionOutcome(
        run_date="2026-07-13",
        phase=phase,
        target_trade_date="2026-07-13",
        input_by="codex_automation",
        status=status,
        reason=reason,
        exit_code=0 if status in {"success", "empty", "skip"} else 1,
        source_results=source_results or (_source(),),
        observations=tuple(observations),
        cached_only=cached_only,
        dry_run=dry_run,
    )


def _read_index(root: Path) -> dict:
    return json.loads((root / "index.json").read_text(encoding="utf-8"))


def _journal_digest(payload: dict) -> str:
    unsigned = dict(payload)
    unsigned.pop("journal_digest", None)
    encoded = json.dumps(
        unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def test_same_phase_rerun_keeps_candidate_but_cross_phase_is_seen(tmp_path: Path) -> None:
    store = FeedStore(tmp_path / "runs")
    article = _article()

    first = store.persist_phase(_outcome(_observation(article)))
    rerun = store.persist_phase(_outcome(_observation(article)))
    other_phase = store.persist_phase(
        _outcome(_observation(article), phase="pre-trading-eve")
    )

    assert first.new_article_ids == (article.source_article_id,)
    assert rerun.new_article_ids == (article.source_article_id,)
    assert rerun.seen_article_ids == ()
    assert other_phase.new_article_ids == ()
    assert other_phase.seen_article_ids == (article.source_article_id,)
    assert len(list((tmp_path / "runs").rglob("*.html"))) == 1


def test_pending_gets_first_seen_only_when_body_is_archived(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    store = FeedStore(root)
    article = _article()
    pending = replace(_observation(article, "pending_content"), article=None)

    first = store.persist_phase(_outcome(pending))
    pending_record = _read_index(root)["records"][0]
    recovered = store.persist_phase(
        _outcome(_observation(article), phase="pre-trading-eve")
    )
    archived_record = _read_index(root)["records"][0]

    assert first.pending_article_ids == (article.source_article_id,)
    assert pending_record["state"] == "pending_content"
    assert "first_seen_run_date" not in pending_record
    assert recovered.new_article_ids == (article.source_article_id,)
    assert archived_record["state"] == "archived"
    assert archived_record["first_seen_phase"] == "pre-trading-eve"


def test_fallback_identity_deduplicates_changed_id_and_url(tmp_path: Path) -> None:
    store = FeedStore(tmp_path / "runs")
    first = _article(article_id="old-id", url="https://mp.weixin.qq.com/s/old")
    alias = _article(article_id="new-id", url="https://mp.weixin.qq.com/s/new")

    store.persist_phase(_outcome(_observation(first)))
    second = store.persist_phase(
        _outcome(_observation(alias), phase="pre-trading-eve")
    )

    assert second.new_article_ids == ()
    assert second.seen_article_ids == ("new-id",)
    assert len(_read_index(tmp_path / "runs")["records"]) == 1


def test_conflicting_id_and_url_aliases_fail_closed(tmp_path: Path) -> None:
    store = FeedStore(tmp_path / "runs")
    one = _article(article_id="id-one", url="https://mp.weixin.qq.com/s/url-one", title="一")
    two = _article(article_id="id-two", url="https://mp.weixin.qq.com/s/url-two", title="二")
    store.persist_phase(_outcome(_observation(one)))
    store.persist_phase(_outcome(_observation(two)))
    conflict = _article(
        article_id="id-one",
        url="https://mp.weixin.qq.com/s/url-two",
        title="三",
        content="不同\n",
    )

    with pytest.raises(FeedError, match="ambiguous_article_identity"):
        store.persist_phase(_outcome(_observation(conflict), phase="pre-trading-eve"))


@pytest.mark.parametrize("existing_state", ["archived", "pending_content"])
@pytest.mark.parametrize(
    "conflict",
    [
        "teacher_name",
        "source_account_id",
        "title",
        "publication_date",
        "published_at",
        "normalized_url",
    ],
)
def test_matched_record_rejects_provenance_conflict_without_changing_index(
    tmp_path: Path, existing_state: str, conflict: str
) -> None:
    root = tmp_path / "runs"
    store = FeedStore(root)
    article = _article()
    initial = _observation(article)
    if existing_state == "pending_content":
        initial = replace(initial, state="pending_content", article=None)
    store.persist_phase(_outcome(initial))
    index_before = (root / "index.json").read_bytes()

    with pytest.raises(FeedError) as exc_info:
        store.persist_phase(
            _outcome(
                _conflicting_observation(article, conflict),
                phase="pre-trading-eve",
            )
        )

    assert exc_info.value.status == "source_failed"
    assert exc_info.value.reason == "ambiguous_article_provenance"
    assert (root / "index.json").read_bytes() == index_before


@pytest.mark.parametrize("existing_state", ["archived", "pending_content"])
@pytest.mark.parametrize(
    "conflict",
    [
        "teacher_name",
        "source_account_id",
        "source_platform",
        "source_article_id",
        "normalized_url",
        "title",
        "published_at",
    ],
)
def test_matched_record_rejects_observation_article_provenance_mismatch(
    tmp_path: Path, existing_state: str, conflict: str
) -> None:
    root = tmp_path / "runs"
    store = FeedStore(root)
    article = _article()
    initial = _observation(article)
    if existing_state == "pending_content":
        initial = replace(initial, state="pending_content", article=None)
    store.persist_phase(_outcome(initial))
    index_before = (root / "index.json").read_bytes()

    with pytest.raises(FeedError) as exc_info:
        store.persist_phase(
            _outcome(
                _observation_with_embedded_article_conflict(article, conflict),
                phase="pre-trading-eve",
            )
        )

    assert exc_info.value.status == "source_failed"
    assert exc_info.value.reason == "ambiguous_article_provenance"
    assert (root / "index.json").read_bytes() == index_before


def test_unmatched_observation_article_mismatch_is_rejected_before_new_record(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runs"
    store = FeedStore(root)

    with pytest.raises(FeedError) as exc_info:
        store.persist_phase(
            _outcome(
                _observation_with_embedded_article_conflict(
                    _article(), "source_article_id"
                )
            )
        )

    assert exc_info.value.status == "source_failed"
    assert exc_info.value.reason == "ambiguous_article_provenance"
    assert not (root / "index.json").exists()


@pytest.mark.parametrize("conflict_origin", ["source_result", "observation", "both"])
def test_source_account_pin_is_rechecked_inside_persist_lock(
    tmp_path: Path, conflict_origin: str
) -> None:
    root = tmp_path / "runs"
    store = FeedStore(root)
    source_a = replace(_source(), source_account_id="mp-A")
    article_a = replace(
        _article(article_id="article-A", url="https://mp.weixin.qq.com/s/article-A"),
        source_account_id="mp-A",
    )
    store.persist_phase(
        _outcome(_observation(article_a), source_results=(source_a,))
    )
    index_before = (root / "index.json").read_bytes()
    manifest_before = store.read_manifest("2026-07-13", "post-market")

    source_b = replace(_source(), source_account_id="mp-B")
    article_b = replace(
        _article(
            article_id="article-B",
            url="https://mp.weixin.qq.com/s/article-B",
            title="并发旧快照 B",
            content="B 观点\n",
        ),
        source_account_id="mp-B",
    )
    source_result = source_b if conflict_origin in {"source_result", "both"} else source_a
    observation = (
        _observation(article_b)
        if conflict_origin in {"observation", "both"}
        else _observation(article_a)
    )

    with pytest.raises(FeedError) as exc_info:
        store.persist_phase(
            _outcome(
                observation,
                phase="pre-trading-eve",
                source_results=(source_result,),
            )
        )

    assert exc_info.value.status == "source_failed"
    assert exc_info.value.reason == "source_identity_changed"
    assert (root / "index.json").read_bytes() == index_before
    assert store.read_manifest("2026-07-13", "post-market") == manifest_before
    assert store.read_manifest("2026-07-13", "pre-trading-eve") is None


@pytest.mark.parametrize(
    "source_entry",
    [
        "not-a-dict",
        {},
        {"source_account_id": "", "first_snapshot_run_date": "2026-07-13"},
        {"source_account_id": "mp-A", "first_snapshot_run_date": "not-a-date"},
    ],
)
def test_load_index_rejects_malformed_source_pin(
    tmp_path: Path, source_entry: object
) -> None:
    root = tmp_path / "runs"
    root.mkdir()
    (root / "index.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sources": {"安静拆主线": source_entry},
                "records": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(FeedError) as exc_info:
        FeedStore(root).load_snapshot()

    assert exc_info.value.status == "source_failed"
    assert exc_info.value.reason == "corrupt_feed_index"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_article_ids", 1),
        ("source_article_ids", "article-id"),
        ("source_urls", 1),
        ("source_urls", "https://mp.weixin.qq.com/s/token-one"),
        ("article", "not-a-dict"),
    ],
)
def test_load_index_rejects_malformed_record_aliases_and_article(
    tmp_path: Path, field: str, value: object
) -> None:
    root = tmp_path / "runs"
    store = FeedStore(root)
    store.persist_phase(_outcome(_observation(_article())))
    payload = _read_index(root)
    payload["records"][0][field] = value
    (root / "index.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )

    with pytest.raises(FeedError) as exc_info:
        store.load_snapshot()

    assert exc_info.value.status == "source_failed"
    assert exc_info.value.reason == "corrupt_feed_index"


def test_matched_record_accepts_equivalent_normalized_url(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    store = FeedStore(root)
    article = _article()
    store.persist_phase(_outcome(_observation(article)))
    equivalent = replace(
        article,
        source_url="https://mp.weixin.qq.com/s/token-one?utm_source=test#fragment",
    )

    result = store.persist_phase(
        _outcome(_observation(equivalent), phase="pre-trading-eve")
    )

    assert result.seen_article_ids == (article.source_article_id,)


def test_bootstrap_skips_history_but_late_unseen_history_is_new(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    store = FeedStore(root)
    old = _article(
        article_id="old",
        url="https://mp.weixin.qq.com/s/old",
        published_at="2026-07-12T20:00:00+08:00",
    )
    today = _article(article_id="today", url="https://mp.weixin.qq.com/s/today")
    baseline = _observation(old, "baseline_seen")

    first = store.persist_phase(_outcome(baseline, _observation(today)))
    late = _article(
        article_id="late-old",
        url="https://mp.weixin.qq.com/s/late-old",
        title="迟到旧文",
        content="迟到\n",
        published_at="2026-07-11T20:00:00+08:00",
    )
    second = store.persist_phase(
        _outcome(_observation(late), phase="pre-trading-eve")
    )

    assert first.baseline_article_ids == ("old",)
    assert first.new_article_ids == ("today",)
    assert second.new_article_ids == ("late-old",)
    assert not any("old" in path.name for path in root.rglob("*.html"))


def test_persist_writes_safe_raw_files_and_recomputable_manifest_digest(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    article = _article(content=" \r\n观点一\r\n\r\n ")
    result = FeedStore(root).persist_phase(_outcome(_observation(article)))
    manifest_path = Path(result.manifest_path or "")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    digest = manifest.pop("manifest_digest")

    assert manifest["commit_state"] == "complete"
    assert digest == manifest_digest(manifest)
    assert result.manifest["manifest_digest"] == digest
    article_meta = result.manifest["new_articles"][0]
    for key in ("raw_html_path", "raw_content_path", "article_json_path"):
        path = root / article_meta[key]
        assert path.is_file()
        assert ".." not in path.name
        assert "article" not in path.name
    raw_content = (root / article_meta["raw_content_path"]).read_text(encoding="utf-8")
    assert raw_content == canonicalize_raw_content(article.raw_content)
    assert canonical_content_sha256(raw_content) == article_meta["content_sha256"]


def test_manifest_separates_current_new_from_retained_candidate_union(tmp_path: Path) -> None:
    store = FeedStore(tmp_path / "runs")
    first = _article(article_id="first", url="https://mp.weixin.qq.com/s/first")
    second = _article(
        article_id="second",
        url="https://mp.weixin.qq.com/s/second",
        title="第二篇",
    )
    store.persist_phase(_outcome(_observation(first), _observation(second)))

    rerun = store.persist_phase(_outcome(_observation(second)))
    no_observation = store.persist_phase(_outcome(status="empty", reason="verified_empty"))

    assert rerun.new_article_ids == ("second",)
    assert rerun.retained_article_ids == ("first",)
    assert rerun.candidate_article_ids == ("first", "second")
    assert rerun.manifest["counts"] == {
        "new": 1,
        "retained": 1,
        "candidate": 2,
        "seen": 0,
        "pending": 0,
        "baseline": 0,
    }
    assert no_observation.new_article_ids == ()
    assert no_observation.retained_article_ids == ("first", "second")
    assert no_observation.candidate_article_ids == ("first", "second")
    assert no_observation.manifest["counts"]["new"] == 0


def test_store_persists_service_status_without_reinterpreting_it(tmp_path: Path) -> None:
    result = FeedStore(tmp_path / "runs").persist_phase(
        _outcome(status="partial", reason="refresh_unverified")
    )

    assert result.manifest["status"] == "partial"
    assert result.manifest["reason"] == "refresh_unverified"


def test_dry_run_creates_no_files_or_directories(tmp_path: Path) -> None:
    root = tmp_path / "does-not-exist"
    result = FeedStore(root).persist_phase(
        _outcome(_observation(_article()), dry_run=True, status="partial")
    )

    assert result.manifest_path is None
    assert result.manifest["commit_state"] == "dry_run"
    assert not root.exists()


@pytest.mark.parametrize(
    "failure_step",
    [
        "before_replace:journal",
        "before_replace:raw_html",
        "before_replace:raw_content",
        "before_replace:raw_json",
        "before_replace:index",
        "before_replace:manifest",
        "before_delete:journal",
    ],
)
def test_each_transaction_boundary_is_recoverable(tmp_path: Path, failure_step: str) -> None:
    root = tmp_path / "runs"
    article = _article(article_id="recover-me", url="https://mp.weixin.qq.com/s/recover")

    def fail(step: str) -> None:
        if step == failure_step:
            raise OSError("injected crash")

    broken = FeedStore(root, fault_hook=fail)
    with pytest.raises(OSError, match="injected crash"):
        broken.persist_phase(_outcome(_observation(article)))

    clean = FeedStore(root)
    if failure_step == "before_replace:journal":
        clean.persist_phase(_outcome(_observation(article)))
    else:
        assert clean.pending_transactions() == (
            "2026-07-13/post-market/transaction.json",
        )
        clean.recover_phase("2026-07-13", "post-market")

    manifest = clean.read_manifest("2026-07-13", "post-market")
    assert manifest is not None
    assert manifest["commit_state"] == "complete"
    assert [item["source_article_id"] for item in manifest["new_articles"]] == [
        "recover-me"
    ]
    assert clean.pending_transactions() == ()


def test_failed_rerun_preserves_old_complete_manifest_until_recovery(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    first = _article(article_id="first", url="https://mp.weixin.qq.com/s/first")
    second = _article(article_id="second", url="https://mp.weixin.qq.com/s/second", title="二")
    clean = FeedStore(root)
    clean.persist_phase(_outcome(_observation(first)))

    def fail(step: str) -> None:
        if step == "before_replace:manifest":
            raise OSError("crash")

    with pytest.raises(OSError):
        FeedStore(root, fault_hook=fail).persist_phase(_outcome(_observation(second)))

    stale = clean.read_manifest("2026-07-13", "post-market")
    assert [item["source_article_id"] for item in stale["new_articles"]] == ["first"]
    assert clean.pending_transactions()

    clean.recover_phase("2026-07-13", "post-market")
    recovered = clean.read_manifest("2026-07-13", "post-market")
    assert recovered["new_article_ids"] == ["second"]
    assert recovered["retained_article_ids"] == ["first"]
    assert recovered["candidate_article_ids"] == ["first", "second"]


def test_new_phase_recovers_other_phase_journal_before_committing(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    first = _article(article_id="phase-a", url="https://mp.weixin.qq.com/s/phase-a")
    second = _article(
        article_id="phase-b",
        url="https://mp.weixin.qq.com/s/phase-b",
        title="第二阶段",
    )

    def fail(step: str) -> None:
        if step == "before_replace:index":
            raise OSError("phase A crash")

    with pytest.raises(OSError, match="phase A crash"):
        FeedStore(root, fault_hook=fail).persist_phase(_outcome(_observation(first)))

    clean = FeedStore(root)
    clean.persist_phase(
        _outcome(_observation(second), phase="pre-trading-eve")
    )

    assert clean.pending_transactions() == ()
    assert {
        record["article"]["source_article_id"]
        for record in _read_index(root)["records"]
        if record["state"] == "archived"
    } == {"phase-a", "phase-b"}
    assert clean.read_manifest("2026-07-13", "post-market")["new_article_ids"] == [
        "phase-a"
    ]
    assert clean.read_manifest("2026-07-13", "pre-trading-eve")["new_article_ids"] == [
        "phase-b"
    ]


def test_corrupt_transaction_is_reported_and_not_deleted(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    journal = root / "2026-07-13" / "post-market" / "transaction.json"
    journal.parent.mkdir(parents=True)
    journal.write_text("not json", encoding="utf-8")

    with pytest.raises(FeedError, match="corrupt_transaction_journal"):
        FeedStore(root).recover_phase("2026-07-13", "post-market")

    assert journal.exists()


def test_non_utf8_index_is_reported_as_corrupt_feed_index(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    root.mkdir()
    (root / "index.json").write_bytes(b"\xff")

    with pytest.raises(FeedError) as exc_info:
        FeedStore(root).load_snapshot()

    assert exc_info.value.status == "source_failed"
    assert exc_info.value.reason == "corrupt_feed_index"


def test_non_utf8_journal_is_reported_and_preserved(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    journal = root / "2026-07-13" / "post-market" / "transaction.json"
    journal.parent.mkdir(parents=True)
    journal.write_bytes(b"\xff")

    with pytest.raises(FeedError) as exc_info:
        FeedStore(root).recover_phase("2026-07-13", "post-market")

    assert exc_info.value.status == "source_failed"
    assert exc_info.value.reason == "corrupt_transaction_journal"
    assert journal.read_bytes() == b"\xff"


def test_non_utf8_manifest_is_reported_as_corrupt_phase_manifest(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runs"
    manifest = root / "2026-07-13" / "post-market" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_bytes(b"\xff")

    with pytest.raises(FeedError) as exc_info:
        FeedStore(root).read_manifest("2026-07-13", "post-market")

    assert exc_info.value.status == "source_failed"
    assert exc_info.value.reason == "corrupt_phase_manifest"


def test_surrogate_manifest_is_reported_as_corrupt_phase_manifest(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runs"
    store = FeedStore(root)
    result = store.persist_phase(_outcome(_observation(_article())))
    manifest_path = Path(result.manifest_path or "")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["reason"] = "\ud800"
    manifest_path.write_text(json.dumps(payload, ensure_ascii=True), encoding="ascii")

    with pytest.raises(FeedError) as exc_info:
        store.read_manifest("2026-07-13", "post-market")

    assert exc_info.value.status == "source_failed"
    assert exc_info.value.reason == "corrupt_phase_manifest"


def test_surrogate_journal_is_reported_and_preserved(tmp_path: Path) -> None:
    root = tmp_path / "runs"

    def fail(step: str) -> None:
        if step == "before_replace:raw_content":
            raise OSError("leave journal")

    with pytest.raises(OSError, match="leave journal"):
        FeedStore(root, fault_hook=fail).persist_phase(
            _outcome(_observation(_article()))
        )
    journal_path = root / "2026-07-13" / "post-market" / "transaction.json"
    payload = json.loads(journal_path.read_text(encoding="utf-8"))
    payload["manifest"]["reason"] = "\ud800"
    journal_path.write_text(json.dumps(payload, ensure_ascii=True), encoding="ascii")

    with pytest.raises(FeedError) as exc_info:
        FeedStore(root).recover_phase("2026-07-13", "post-market")

    assert exc_info.value.status == "source_failed"
    assert exc_info.value.reason == "corrupt_transaction_journal"
    assert journal_path.exists()


def test_tampered_raw_content_journal_is_rejected_without_replay(tmp_path: Path) -> None:
    root = tmp_path / "runs"

    def fail(step: str) -> None:
        if step == "before_replace:raw_content":
            raise OSError("crash before raw content")

    with pytest.raises(OSError):
        FeedStore(root, fault_hook=fail).persist_phase(
            _outcome(_observation(_article()))
        )
    journal = root / "2026-07-13" / "post-market" / "transaction.json"
    payload = json.loads(journal.read_text(encoding="utf-8"))
    raw_item = next(item for item in payload["files"] if item["label"] == "raw_content")
    raw_item["text"] = "tampered\n"
    journal.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(FeedError, match="corrupt_transaction_journal"):
        FeedStore(root).recover_phase("2026-07-13", "post-market")

    assert journal.exists()


def test_tampered_journal_index_digest_is_rejected_without_overwriting_index(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runs"
    first = _article(article_id="first", url="https://mp.weixin.qq.com/s/first")
    second = _article(article_id="second", url="https://mp.weixin.qq.com/s/second")
    clean = FeedStore(root)
    clean.persist_phase(_outcome(_observation(first)))
    index_before = (root / "index.json").read_bytes()

    def fail(step: str) -> None:
        if step == "before_replace:index":
            raise OSError("crash before index")

    with pytest.raises(OSError, match="crash before index"):
        FeedStore(root, fault_hook=fail).persist_phase(
            _outcome(_observation(second), phase="pre-trading-eve")
        )
    journal = root / "2026-07-13" / "pre-trading-eve" / "transaction.json"
    payload = json.loads(journal.read_text(encoding="utf-8"))
    payload["index"]["sources"]["安静拆主线"]["source_account_id"] = "mp-poison"
    journal.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(FeedError, match="corrupt_transaction_journal"):
        clean.recover_phase("2026-07-13", "pre-trading-eve")

    assert (root / "index.json").read_bytes() == index_before
    assert journal.exists()


def test_journal_rejects_invalid_index_structure_with_recomputed_digest(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runs"
    first = _article(article_id="first", url="https://mp.weixin.qq.com/s/first")
    second = _article(article_id="second", url="https://mp.weixin.qq.com/s/second")
    clean = FeedStore(root)
    clean.persist_phase(_outcome(_observation(first)))
    index_before = (root / "index.json").read_bytes()

    def fail(step: str) -> None:
        if step == "before_replace:index":
            raise OSError("crash before index")

    with pytest.raises(OSError, match="crash before index"):
        FeedStore(root, fault_hook=fail).persist_phase(
            _outcome(_observation(second), phase="pre-trading-eve")
        )
    journal = root / "2026-07-13" / "pre-trading-eve" / "transaction.json"
    payload = json.loads(journal.read_text(encoding="utf-8"))
    payload["index"]["records"][0]["source_article_ids"] = 1
    payload["journal_digest"] = _journal_digest(payload)
    journal.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(FeedError) as exc_info:
        clean.recover_phase("2026-07-13", "pre-trading-eve")

    assert exc_info.value.status == "source_failed"
    assert exc_info.value.reason == "corrupt_transaction_journal"
    assert (root / "index.json").read_bytes() == index_before
    assert journal.exists()


@pytest.mark.parametrize(
    "target_relative",
    [
        ".lock",
        "2026-07-13/pre-trading-eve/manifest.json",
    ],
)
def test_journal_file_target_cannot_escape_its_candidate_archive_paths(
    tmp_path: Path, target_relative: str
) -> None:
    root = tmp_path / "runs"

    def fail(step: str) -> None:
        if step == "before_replace:raw_html":
            raise OSError("crash before raw html")

    with pytest.raises(OSError, match="crash before raw html"):
        FeedStore(root, fault_hook=fail).persist_phase(
            _outcome(_observation(_article()))
        )
    journal = root / "2026-07-13" / "post-market" / "transaction.json"
    payload = json.loads(journal.read_text(encoding="utf-8"))
    raw_html = next(item for item in payload["files"] if item["label"] == "raw_html")
    raw_html["relative_path"] = target_relative
    payload["journal_digest"] = _journal_digest(payload)
    journal.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    target = root / target_relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("sentinel", encoding="utf-8")

    with pytest.raises(FeedError, match="corrupt_transaction_journal"):
        FeedStore(root).recover_phase("2026-07-13", "post-market")

    assert target.read_text(encoding="utf-8") == "sentinel"
    assert journal.exists()


@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate", "wrong_label"])
def test_journal_candidate_file_set_must_be_exact_and_unique(
    tmp_path: Path, mutation: str
) -> None:
    root = tmp_path / "runs"

    def fail(step: str) -> None:
        if step == "before_replace:raw_html":
            raise OSError("crash before raw html")

    with pytest.raises(OSError, match="crash before raw html"):
        FeedStore(root, fault_hook=fail).persist_phase(
            _outcome(_observation(_article()))
        )
    journal = root / "2026-07-13" / "post-market" / "transaction.json"
    payload = json.loads(journal.read_text(encoding="utf-8"))
    if mutation == "missing":
        payload["files"].pop()
    elif mutation == "extra":
        payload["files"].append(
            {
                "relative_path": "2026-07-13/post-market/articles/extra.html",
                "label": "raw_html",
                "text": "extra",
            }
        )
    elif mutation == "duplicate":
        payload["files"].append(dict(payload["files"][0]))
    else:
        payload["files"][0]["label"] = "raw_json"
    payload["journal_digest"] = _journal_digest(payload)
    journal.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(FeedError, match="corrupt_transaction_journal"):
        FeedStore(root).recover_phase("2026-07-13", "post-market")

    assert journal.exists()


@pytest.mark.parametrize("linked_parent", ["date", "phase"])
def test_persist_rejects_symlinked_phase_parent_without_writing_outside(
    tmp_path: Path, linked_parent: str
) -> None:
    root = tmp_path / "runs"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    if linked_parent == "date":
        (root / "2026-07-13").symlink_to(outside, target_is_directory=True)
    else:
        (root / "2026-07-13").mkdir()
        (root / "2026-07-13" / "post-market").symlink_to(
            outside, target_is_directory=True
        )

    with pytest.raises(FeedError) as exc_info:
        FeedStore(root).persist_phase(_outcome(_observation(_article())))

    assert exc_info.value.status == "source_failed"
    assert exc_info.value.reason == "unsafe_store_path"
    assert not (outside / "post-market" / "transaction.json").exists()
    assert not (outside / "post-market" / "manifest.json").exists()
    assert not (outside / "transaction.json").exists()
    assert not (outside / "manifest.json").exists()


def test_manifest_read_rejects_date_symlink_without_following_outside(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runs"
    outside = tmp_path / "outside"
    root.mkdir()
    (outside / "post-market").mkdir(parents=True)
    outside_manifest = outside / "post-market" / "manifest.json"
    outside_manifest.write_text("not json", encoding="utf-8")
    (root / "2026-07-13").symlink_to(outside, target_is_directory=True)

    with pytest.raises(FeedError) as exc_info:
        FeedStore(root).read_manifest("2026-07-13", "post-market")

    assert exc_info.value.status == "source_failed"
    assert exc_info.value.reason == "unsafe_store_path"
    assert outside_manifest.read_text(encoding="utf-8") == "not json"


@pytest.mark.parametrize(
    ("destination_date", "destination_phase"),
    [
        ("2026-07-14", "post-market"),
        ("2026-07-13", "pre-trading-eve"),
    ],
)
def test_read_manifest_rejects_valid_manifest_copied_to_another_phase_path(
    tmp_path: Path, destination_date: str, destination_phase: str
) -> None:
    root = tmp_path / "runs"
    store = FeedStore(root)
    result = store.persist_phase(_outcome(_observation(_article())))
    source = Path(result.manifest_path or "")
    destination = root / destination_date / destination_phase / "manifest.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(source.read_bytes())

    with pytest.raises(FeedError) as exc_info:
        store.read_manifest(destination_date, destination_phase)

    assert exc_info.value.status == "source_failed"
    assert exc_info.value.reason == "corrupt_phase_manifest"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 2),
        ("run_date", "2026-07-14"),
        ("phase", "pre-trading-eve"),
    ],
)
def test_read_manifest_validates_schema_and_requested_phase_identity(
    tmp_path: Path, field: str, value: object
) -> None:
    root = tmp_path / "runs"
    store = FeedStore(root)
    result = store.persist_phase(_outcome(_observation(_article())))
    manifest_path = Path(result.manifest_path or "")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload[field] = value
    payload["manifest_digest"] = manifest_digest(payload)
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )

    with pytest.raises(FeedError) as exc_info:
        store.read_manifest("2026-07-13", "post-market")

    assert exc_info.value.status == "source_failed"
    assert exc_info.value.reason == "corrupt_phase_manifest"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("counts", []),
        ("counts", {"new": "not-an-int"}),
        ("candidate_articles", "not-a-list"),
        ("candidate_articles", ["not-a-dict"]),
        ("new_articles", "not-a-list"),
        ("retained_articles", "not-a-list"),
        ("source_results", "not-a-list"),
        ("source_results", ["not-a-dict"]),
    ],
)
def test_read_manifest_rejects_unsafe_collection_shapes(
    tmp_path: Path, field: str, value: object
) -> None:
    root = tmp_path / "runs"
    store = FeedStore(root)
    result = store.persist_phase(_outcome(_observation(_article())))
    manifest_path = Path(result.manifest_path or "")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload[field] = value
    payload["manifest_digest"] = manifest_digest(payload)
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(FeedError) as exc_info:
        store.read_manifest("2026-07-13", "post-market")

    assert exc_info.value.status == "source_failed"
    assert exc_info.value.reason == "corrupt_phase_manifest"


@pytest.mark.parametrize(
    "field",
    [
        "teacher_name",
        "source_platform",
        "source_account_id",
        "source_article_id",
        "source_url",
        "title",
        "date",
        "published_at",
        "fetched_at",
        "content_sha256",
        "raw_html_path",
        "raw_content_path",
        "article_json_path",
    ],
)
def test_read_manifest_rejects_non_scalar_candidate_metadata(
    tmp_path: Path, field: str
) -> None:
    root = tmp_path / "runs"
    store = FeedStore(root)
    result = store.persist_phase(_outcome(_observation(_article())))
    manifest_path = Path(result.manifest_path or "")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["candidate_articles"][0][field] = ["not-a-scalar"]
    payload["manifest_digest"] = manifest_digest(payload)
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(FeedError) as exc_info:
        store.read_manifest("2026-07-13", "post-market")

    assert exc_info.value.status == "source_failed"
    assert exc_info.value.reason == "corrupt_phase_manifest"


def test_journal_read_rejects_date_symlink_without_following_outside(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runs"
    outside = tmp_path / "outside"
    root.mkdir()
    (outside / "post-market").mkdir(parents=True)
    outside_journal = outside / "post-market" / "transaction.json"
    outside_journal.write_text("not json", encoding="utf-8")
    (root / "2026-07-13").symlink_to(outside, target_is_directory=True)

    with pytest.raises(FeedError) as exc_info:
        FeedStore(root).recover_phase("2026-07-13", "post-market")

    assert exc_info.value.status == "source_failed"
    assert exc_info.value.reason == "unsafe_store_path"
    assert outside_journal.read_text(encoding="utf-8") == "not json"
