from __future__ import annotations

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
