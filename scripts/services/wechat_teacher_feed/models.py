from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TeacherSource:
    teacher_name: str
    seed_url: str


@dataclass(frozen=True)
class PhaseDecision:
    status: str
    run_date: str
    phase: str
    target_trade_date: str
    reason: str


@dataclass(frozen=True)
class WechatTeacherArticle:
    teacher_name: str
    source_platform: str
    source_account_id: str
    source_article_id: str
    source_url: str
    title: str
    published_at: str
    fetched_at: str
    raw_content: str
    raw_html: str
    content_sha256: str

    @property
    def publication_date(self) -> str:
        return self.published_at[:10]


@dataclass(frozen=True)
class WeRSSSource:
    mp_id: str
    mp_name: str
    status: int = 1


@dataclass(frozen=True)
class WeRSSArticleMeta:
    article_id: str
    mp_id: str
    title: str
    url: str
    publish_time: Any
    has_content: bool
    raw: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)


@dataclass(frozen=True)
class RefreshResult:
    status: str
    reason: str
    verified: bool = False


@dataclass(frozen=True)
class ArticleObservation:
    teacher_name: str
    source_account_id: str
    source_article_id: str
    source_url: str
    title: str
    published_at: str
    state: str
    article: WechatTeacherArticle | None = None
    reason: str = ""


@dataclass(frozen=True)
class SourceResult:
    teacher_name: str
    source_account_id: str | None
    status: str
    reason: str
    refresh_status: str = "not_attempted"
    refresh_verified: bool = False
    metadata_count: int = 0
    complete_count: int = 0
    pending_count: int = 0
    issue_count: int = 0
    listed: bool = False


@dataclass(frozen=True)
class CollectionOutcome:
    run_date: str
    phase: str
    target_trade_date: str
    input_by: str
    status: str
    reason: str
    exit_code: int
    source_results: tuple[SourceResult, ...]
    observations: tuple[ArticleObservation, ...]
    cached_only: bool = False
    dry_run: bool = False


@dataclass(frozen=True)
class PersistResult:
    manifest: dict[str, Any]
    manifest_path: str | None
    new_article_ids: tuple[str, ...]
    retained_article_ids: tuple[str, ...]
    candidate_article_ids: tuple[str, ...]
    seen_article_ids: tuple[str, ...]
    pending_article_ids: tuple[str, ...]
    baseline_article_ids: tuple[str, ...]


class FeedError(RuntimeError):
    """Safe machine-readable error that never includes request credentials."""

    def __init__(self, status: str, reason: str):
        self.status = status
        self.reason = reason
        super().__init__(f"{status}:{reason}")
