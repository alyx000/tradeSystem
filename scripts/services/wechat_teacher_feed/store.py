from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterator

from services.content_identity import canonical_content_sha256, canonicalize_raw_content

from .constants import SOURCE_PLATFORM
from .models import (
    ArticleObservation,
    CollectionOutcome,
    FeedError,
    PersistResult,
    WechatTeacherArticle,
)
from .normalize import normalize_wechat_url
from .schedule import PHASES


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ROOT = REPO_ROOT / "data" / "runs" / "wechat-teacher-feed"
INDEX_SCHEMA_VERSION = 1
TRANSACTION_SCHEMA_VERSION = 2
_ARTICLE_SCALAR_FIELDS = (
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
)
_COUNT_FIELDS = ("new", "retained", "candidate", "seen", "pending", "baseline")


class _InvalidDigestPayload(ValueError):
    pass


def _compact_json(payload: Any) -> str:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _payload_digest(payload: Any) -> str:
    try:
        encoded = _compact_json(payload).encode("utf-8")
    except UnicodeError as exc:
        raise _InvalidDigestPayload("payload contains invalid Unicode") from exc
    return hashlib.sha256(encoded).hexdigest()


def manifest_digest(manifest_without_digest: dict[str, Any]) -> str:
    payload = dict(manifest_without_digest)
    payload.pop("manifest_digest", None)
    return _payload_digest(payload)


def journal_digest(journal_without_digest: dict[str, Any]) -> str:
    """Detect accidental transaction corruption; this is not authentication."""
    payload = dict(journal_without_digest)
    payload.pop("journal_digest", None)
    return _payload_digest(payload)


def _json_text(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _valid_date(value: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("date must be valid YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise ValueError("date must be valid YYYY-MM-DD")
    return value


class FeedStore:
    def __init__(
        self,
        root: str | Path | None = None,
        *,
        fault_hook: Callable[[str], None] | None = None,
    ) -> None:
        self.root = Path(root) if root is not None else DEFAULT_ROOT
        self._fault_hook = fault_hook or (lambda _: None)

    @property
    def index_path(self) -> Path:
        return self._safe_store_path("index.json")

    @staticmethod
    def _unsafe_store_path() -> FeedError:
        return FeedError("source_failed", "unsafe_store_path")

    @staticmethod
    def _assert_contained(candidate: Path, resolved_root: Path) -> None:
        try:
            candidate.resolve(strict=False).relative_to(resolved_root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise FeedStore._unsafe_store_path() from exc

    def _resolved_root(self, *, create: bool = False) -> Path:
        if self.root.is_symlink():
            raise self._unsafe_store_path()
        if create:
            try:
                self.root.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise self._unsafe_store_path() from exc
        if self.root.is_symlink() or (
            self.root.exists() and not self.root.is_dir()
        ):
            raise self._unsafe_store_path()
        try:
            return self.root.resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise self._unsafe_store_path() from exc

    def _safe_store_path(
        self, relative_path: str | Path, *, create_parents: bool = False
    ) -> Path:
        """Return a path contained in root without traversing store symlinks."""
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise self._unsafe_store_path()
        resolved_root = self._resolved_root(create=create_parents)
        current = self.root
        parts = relative.parts
        parent_count = max(0, len(parts) - 1)
        for position, part in enumerate(parts):
            current = current / part
            if current.is_symlink():
                raise self._unsafe_store_path()
            self._assert_contained(current, resolved_root)
            is_parent = position < parent_count
            if create_parents and is_parent and not current.exists():
                try:
                    current.mkdir()
                except OSError as exc:
                    raise self._unsafe_store_path() from exc
                if current.is_symlink():
                    raise self._unsafe_store_path()
                self._assert_contained(current, resolved_root)
            if is_parent and current.exists() and not current.is_dir():
                raise self._unsafe_store_path()
        return current

    def _relative_store_path(self, path: Path) -> Path:
        try:
            return path.relative_to(self.root)
        except ValueError as exc:
            raise self._unsafe_store_path() from exc

    def _phase_dir(self, run_date: str, phase: str) -> Path:
        _valid_date(run_date)
        if phase not in PHASES:
            raise ValueError(f"unsupported phase: {phase}")
        return self._safe_store_path(Path(run_date) / phase)

    def _journal_path(self, run_date: str, phase: str) -> Path:
        self._phase_dir(run_date, phase)
        return self._safe_store_path(Path(run_date) / phase / "transaction.json")

    def _manifest_path(self, run_date: str, phase: str) -> Path:
        self._phase_dir(run_date, phase)
        return self._safe_store_path(Path(run_date) / phase / "manifest.json")

    @contextmanager
    def _locked(self) -> Iterator[None]:
        lock_path = self._safe_store_path(".lock", create_parents=True)
        with lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _empty_index() -> dict[str, Any]:
        return {"schema_version": INDEX_SCHEMA_VERSION, "sources": {}, "records": []}

    @classmethod
    def _validate_index_payload(cls, payload: Any) -> dict[str, Any]:
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != INDEX_SCHEMA_VERSION
            or not isinstance(payload.get("sources"), dict)
            or not isinstance(payload.get("records"), list)
        ):
            raise FeedError("source_failed", "corrupt_feed_index")
        for teacher_name, source in payload["sources"].items():
            if (
                not isinstance(teacher_name, str)
                or not teacher_name.strip()
                or not isinstance(source, dict)
                or not isinstance(source.get("source_account_id"), str)
                or not source["source_account_id"].strip()
            ):
                raise FeedError("source_failed", "corrupt_feed_index")
            try:
                _valid_date(source.get("first_snapshot_run_date"))
            except ValueError as exc:
                raise FeedError("source_failed", "corrupt_feed_index") from exc
        for record in payload["records"]:
            if not isinstance(record, dict):
                raise FeedError("source_failed", "corrupt_feed_index")
            scalar_fields = (
                "record_key",
                "teacher_name",
                "source_account_id",
                "title",
                "published_at",
                "state",
            )
            if any(not isinstance(record.get(field), str) for field in scalar_fields):
                raise FeedError("source_failed", "corrupt_feed_index")
            article_ids = record.get("source_article_ids")
            source_urls = record.get("source_urls")
            if (
                not isinstance(article_ids, list)
                or not article_ids
                or any(not isinstance(item, str) or not item for item in article_ids)
                or len(set(article_ids)) != len(article_ids)
                or not isinstance(source_urls, list)
                or not source_urls
                or any(not isinstance(item, str) or not item for item in source_urls)
                or len(set(source_urls)) != len(source_urls)
            ):
                raise FeedError("source_failed", "corrupt_feed_index")
            try:
                normalized_urls = [normalize_wechat_url(value) for value in source_urls]
            except ValueError as exc:
                raise FeedError("source_failed", "corrupt_feed_index") from exc
            if normalized_urls != source_urls:
                raise FeedError("source_failed", "corrupt_feed_index")
            source_pin = payload["sources"].get(record["teacher_name"])
            if (
                not isinstance(source_pin, dict)
                or source_pin.get("source_account_id")
                != record["source_account_id"]
            ):
                raise FeedError("source_failed", "corrupt_feed_index")
            if record["state"] not in {"archived", "pending_content", "baseline_seen"}:
                raise FeedError("source_failed", "corrupt_feed_index")
            if record["state"] != "archived":
                continue
            article = cls._validate_manifest_article(
                record.get("article"), "corrupt_feed_index"
            )
            try:
                _valid_date(record.get("first_seen_run_date"))
            except ValueError as exc:
                raise FeedError("source_failed", "corrupt_feed_index") from exc
            content_sha256 = record.get("content_sha256")
            if (
                record.get("first_seen_phase") not in PHASES
                or content_sha256 != article["content_sha256"]
                or article["teacher_name"] != record["teacher_name"]
                or article["source_account_id"] != record["source_account_id"]
                or article["source_article_id"] not in article_ids
                or normalize_wechat_url(article["source_url"]) not in source_urls
                or article["title"] != record["title"]
                or article["published_at"] != record["published_at"]
                or article["date"] != record["published_at"][:10]
            ):
                raise FeedError("source_failed", "corrupt_feed_index")
        return payload

    def _load_index(self) -> dict[str, Any]:
        path = self.index_path
        if not path.exists():
            return self._empty_index()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise FeedError("source_failed", "corrupt_feed_index") from exc
        return self._validate_index_payload(payload)

    def load_snapshot(self) -> dict[str, Any]:
        return copy.deepcopy(self._load_index())

    def _atomic_write_text(self, path: Path, text: str, label: str) -> None:
        relative = self._relative_store_path(path)
        path = self._safe_store_path(relative, create_parents=True)
        fd, temp_name = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            self._fault_hook(f"before_replace:{label}")
            path = self._safe_store_path(relative)
            os.replace(str(temp_path), str(path))
            self._fsync_dir(path.parent)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    @staticmethod
    def _fsync_dir(directory: Path) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        try:
            fd = os.open(str(directory), flags)
        except OSError:
            return
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _atomic_write_json(self, path: Path, payload: Any, label: str) -> None:
        self._atomic_write_text(path, _json_text(payload), label)

    @staticmethod
    def _record_key(article_id: str) -> str:
        raw = f"wechat_mp\0{article_id}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _archive_stem(article: WechatTeacherArticle) -> str:
        raw = f"{article.source_platform}\0{article.source_article_id}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:32]

    @staticmethod
    def _fallback_key(observation: ArticleObservation) -> str | None:
        if observation.article is None:
            return None
        article = observation.article
        return "\0".join(
            [
                article.teacher_name,
                article.publication_date,
                article.title,
                article.content_sha256,
            ]
        )

    def _matching_records(
        self, index: dict[str, Any], observation: ArticleObservation
    ) -> list[dict[str, Any]]:
        article_id = observation.source_article_id
        try:
            source_url = normalize_wechat_url(observation.source_url)
        except ValueError as exc:
            raise FeedError("source_failed", "invalid_source_url") from exc
        fallback = self._fallback_key(observation)
        matches = []
        for record in index["records"]:
            if not isinstance(record, dict):
                raise FeedError("source_failed", "corrupt_feed_index")
            by_id = article_id in (record.get("source_article_ids") or [])
            by_url = source_url in (record.get("source_urls") or [])
            by_fallback = bool(fallback and fallback == record.get("fallback_key"))
            if by_id or by_url or by_fallback:
                matches.append(record)
        if len(matches) > 1:
            raise FeedError("source_failed", "ambiguous_article_identity")
        return matches

    @staticmethod
    def _add_aliases(record: dict[str, Any], observation: ArticleObservation) -> None:
        ids = record.setdefault("source_article_ids", [])
        if observation.source_article_id not in ids:
            ids.append(observation.source_article_id)
            ids.sort()
        urls = record.setdefault("source_urls", [])
        source_url = normalize_wechat_url(observation.source_url)
        if source_url not in urls:
            urls.append(source_url)
            urls.sort()

    @staticmethod
    def _ambiguous_provenance() -> FeedError:
        return FeedError("source_failed", "ambiguous_article_provenance")

    @classmethod
    def _provenance_url(cls, value: Any) -> str:
        try:
            return normalize_wechat_url(value)
        except (TypeError, ValueError) as exc:
            raise cls._ambiguous_provenance() from exc

    @classmethod
    def _validate_observation_article_provenance(
        cls, observation: ArticleObservation
    ) -> None:
        article = observation.article
        if article is None:
            return
        if (
            article.source_platform != SOURCE_PLATFORM
            or article.teacher_name != observation.teacher_name
            or article.source_account_id != observation.source_account_id
            or article.source_article_id != observation.source_article_id
            or cls._provenance_url(article.source_url)
            != cls._provenance_url(observation.source_url)
            or article.title != observation.title
            or article.published_at != observation.published_at
            or article.publication_date != observation.published_at[:10]
        ):
            raise cls._ambiguous_provenance()

    @classmethod
    def _validate_record_provenance(
        cls, record: dict[str, Any], observation: ArticleObservation
    ) -> None:
        record_ids = record.get("source_article_ids")
        record_urls = record.get("source_urls")
        if not isinstance(record_ids, list) or not isinstance(record_urls, list):
            raise cls._ambiguous_provenance()
        normalized_urls = [cls._provenance_url(value) for value in record_urls]
        incoming_url = cls._provenance_url(observation.source_url)
        if (
            record.get("teacher_name") != observation.teacher_name
            or record.get("source_account_id") != observation.source_account_id
        ):
            raise cls._ambiguous_provenance()

        id_match = observation.source_article_id in record_ids
        url_match = incoming_url in normalized_urls
        if not id_match and not url_match:
            return

        record_published_at = record.get("published_at")
        incoming_date = observation.published_at[:10]
        if (
            record.get("title") != observation.title
            or record_published_at != observation.published_at
            or not isinstance(record_published_at, str)
            or record_published_at[:10] != incoming_date
            or (id_match and incoming_url not in normalized_urls)
        ):
            raise cls._ambiguous_provenance()

        stored_article = record.get("article")
        if stored_article is None:
            return
        if not isinstance(stored_article, dict):
            raise cls._ambiguous_provenance()
        stored_url = cls._provenance_url(stored_article.get("source_url"))
        if (
            stored_article.get("teacher_name") != record.get("teacher_name")
            or stored_article.get("source_account_id")
            != record.get("source_account_id")
            or stored_article.get("source_article_id") not in record_ids
            or stored_url not in normalized_urls
            or stored_article.get("title") != record.get("title")
            or stored_article.get("published_at") != record_published_at
            or stored_article.get("date") != record_published_at[:10]
            or stored_article.get("date") != incoming_date
        ):
            raise cls._ambiguous_provenance()

    def classify_observation(
        self,
        observation: ArticleObservation,
        *,
        run_date: str,
        phase: str,
        snapshot: dict[str, Any] | None = None,
    ) -> str:
        """Read-only hint used by the service; persist_phase revalidates under lock."""
        index = snapshot if snapshot is not None else self._load_index()
        matches = self._matching_records(index, observation)
        if matches:
            record = matches[0]
            if record.get("state") == "archived":
                if (
                    record.get("first_seen_run_date") == run_date
                    and record.get("first_seen_phase") == phase
                ):
                    return "same_phase_candidate"
                return "seen"
            return str(record.get("state") or "pending_content")
        if (
            observation.teacher_name not in index.get("sources", {})
            and observation.published_at[:10] < run_date
        ):
            return "baseline_seen"
        return "unseen"

    @staticmethod
    def _article_metadata(
        article: WechatTeacherArticle, paths: dict[str, str]
    ) -> dict[str, Any]:
        return {
            "teacher_name": article.teacher_name,
            "source_platform": article.source_platform,
            "source_account_id": article.source_account_id,
            "source_article_id": article.source_article_id,
            "source_url": article.source_url,
            "title": article.title,
            "date": article.publication_date,
            "published_at": article.published_at,
            "fetched_at": article.fetched_at,
            "content_sha256": article.content_sha256,
            **paths,
        }

    def _raw_files(
        self, article: WechatTeacherArticle, run_date: str, phase: str
    ) -> tuple[dict[str, str], list[dict[str, str]]]:
        raw_content = canonicalize_raw_content(article.raw_content)
        if not raw_content:
            raise FeedError("content_missing", "empty_article_content")
        if canonical_content_sha256(raw_content) != article.content_sha256:
            raise FeedError("source_failed", "content_hash_mismatch")
        stem = self._archive_stem(article)
        prefix = Path(run_date) / phase / "articles" / stem
        paths = {
            "raw_html_path": str(prefix.with_suffix(".html")),
            "raw_content_path": str(prefix.with_suffix(".md")),
            "article_json_path": str(prefix.with_suffix(".json")),
        }
        metadata = self._article_metadata(article, paths)
        return paths, [
            {"relative_path": paths["raw_html_path"], "label": "raw_html", "text": article.raw_html},
            {
                "relative_path": paths["raw_content_path"],
                "label": "raw_content",
                "text": raw_content,
            },
            {"relative_path": paths["article_json_path"], "label": "raw_json", "text": _json_text(metadata)},
        ]

    def _new_record(
        self, observation: ArticleObservation, state: str
    ) -> dict[str, Any]:
        record = {
            "record_key": self._record_key(observation.source_article_id),
            "teacher_name": observation.teacher_name,
            "source_account_id": observation.source_account_id,
            "source_article_ids": [observation.source_article_id],
            "source_urls": [normalize_wechat_url(observation.source_url)],
            "title": observation.title,
            "published_at": observation.published_at,
            "state": state,
        }
        fallback = self._fallback_key(observation)
        if fallback:
            record["fallback_key"] = fallback
        return record

    def _archive_record(
        self,
        record: dict[str, Any],
        article: WechatTeacherArticle,
        run_date: str,
        phase: str,
        paths: dict[str, str],
    ) -> None:
        record.update(
            {
                "state": "archived",
                "teacher_name": article.teacher_name,
                "source_account_id": article.source_account_id,
                "title": article.title,
                "published_at": article.published_at,
                "content_sha256": article.content_sha256,
                "fallback_key": "\0".join(
                    [
                        article.teacher_name,
                        article.publication_date,
                        article.title,
                        article.content_sha256,
                    ]
                ),
                "first_seen_run_date": run_date,
                "first_seen_phase": phase,
                "article": self._article_metadata(article, paths),
            }
        )

    def _build_transaction(
        self, outcome: CollectionOutcome, source_index: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        index = copy.deepcopy(source_index)
        records = index["records"]
        files: list[dict[str, str]] = []
        baseline_ids: list[str] = []
        pending_ids: list[str] = []
        seen_ids: list[str] = []
        current_candidate_keys: set[str] = set()
        originally_bootstrapped = set(index["sources"])

        for source in outcome.source_results:
            if not source.listed:
                continue
            source_account_id = str(source.source_account_id or "").strip()
            existing_source = index["sources"].get(source.teacher_name)
            if existing_source is not None:
                if existing_source.get("source_account_id") != source_account_id:
                    raise FeedError("source_failed", "source_identity_changed")
                continue
            if not source_account_id:
                raise FeedError("source_failed", "source_identity_changed")
            index["sources"][source.teacher_name] = {
                "first_snapshot_run_date": outcome.run_date,
                "source_account_id": source_account_id,
            }

        for observation in outcome.observations:
            self._validate_observation_article_provenance(observation)
            matches = self._matching_records(index, observation)
            record = matches[0] if matches else None
            if record is not None:
                self._validate_record_provenance(record, observation)
            source_pin = index["sources"].get(observation.teacher_name)
            if (
                not isinstance(source_pin, dict)
                or source_pin.get("source_account_id")
                != observation.source_account_id
            ):
                raise FeedError("source_failed", "source_identity_changed")
            if record is not None:
                self._add_aliases(record, observation)
                if record.get("state") == "archived":
                    existing_hash = record.get("content_sha256")
                    incoming_hash = (
                        observation.article.content_sha256 if observation.article else None
                    )
                    if incoming_hash and existing_hash and incoming_hash != existing_hash:
                        raise FeedError("source_failed", "source_content_changed")
                    if (
                        record.get("first_seen_run_date") == outcome.run_date
                        and record.get("first_seen_phase") == outcome.phase
                    ):
                        if observation.state in {"complete", "same_phase_candidate"}:
                            current_candidate_keys.add(
                                str(record.get("record_key") or "")
                            )
                        continue
                    seen_ids.append(observation.source_article_id)
                    continue
                if record.get("state") == "baseline_seen":
                    baseline_ids.append(observation.source_article_id)
                    continue
            else:
                should_baseline = (
                    observation.state == "baseline_seen"
                    or (
                        observation.teacher_name not in originally_bootstrapped
                        and observation.published_at[:10] < outcome.run_date
                    )
                )
                if should_baseline:
                    record = self._new_record(observation, "baseline_seen")
                    records.append(record)
                    baseline_ids.append(observation.source_article_id)
                    continue
                state = "pending_content" if observation.article is None else "archived"
                record = self._new_record(observation, state)
                records.append(record)

            if observation.article is None:
                record["state"] = "pending_content"
                record["pending_reason"] = observation.reason or "content_missing"
                pending_ids.append(observation.source_article_id)
                continue

            paths, raw_files = self._raw_files(
                observation.article, outcome.run_date, outcome.phase
            )
            files.extend(raw_files)
            self._archive_record(
                record, observation.article, outcome.run_date, outcome.phase, paths
            )
            current_candidate_keys.add(str(record.get("record_key") or ""))

        records.sort(key=lambda item: str(item.get("record_key") or ""))
        phase_records = [
            record
            for record in records
            if record.get("state") == "archived"
            and record.get("first_seen_run_date") == outcome.run_date
            and record.get("first_seen_phase") == outcome.phase
            and isinstance(record.get("article"), dict)
        ]

        def article_sort_key(record: dict[str, Any]) -> tuple[str, str, str]:
            article = record["article"]
            return (
                str(article.get("published_at") or ""),
                str(article.get("teacher_name") or ""),
                str(article.get("source_article_id") or ""),
            )

        phase_records.sort(key=article_sort_key)
        new_articles = [
            copy.deepcopy(record["article"])
            for record in phase_records
            if str(record.get("record_key") or "") in current_candidate_keys
        ]
        retained_articles = [
            copy.deepcopy(record["article"])
            for record in phase_records
            if str(record.get("record_key") or "") not in current_candidate_keys
        ]
        candidate_articles = [copy.deepcopy(record["article"]) for record in phase_records]
        manifest: dict[str, Any] = {
            "schema_version": 1,
            "commit_state": "complete",
            "run_date": outcome.run_date,
            "phase": outcome.phase,
            "target_trade_date": outcome.target_trade_date,
            "input_by": outcome.input_by,
            "status": outcome.status,
            "reason": outcome.reason,
            "exit_code": outcome.exit_code,
            "cached_only": outcome.cached_only,
            "source_results": [asdict(item) for item in outcome.source_results],
            "new_articles": new_articles,
            "new_article_ids": [item["source_article_id"] for item in new_articles],
            "retained_articles": retained_articles,
            "retained_article_ids": [
                item["source_article_id"] for item in retained_articles
            ],
            "candidate_articles": candidate_articles,
            "candidate_article_ids": [
                item["source_article_id"] for item in candidate_articles
            ],
            "seen_article_ids": sorted(set(seen_ids)),
            "pending_article_ids": sorted(set(pending_ids)),
            "baseline_article_ids": sorted(set(baseline_ids)),
        }
        manifest["counts"] = {
            "new": len(manifest["new_article_ids"]),
            "retained": len(manifest["retained_article_ids"]),
            "candidate": len(manifest["candidate_article_ids"]),
            "seen": len(manifest["seen_article_ids"]),
            "pending": len(manifest["pending_article_ids"]),
            "baseline": len(manifest["baseline_article_ids"]),
        }
        manifest["manifest_digest"] = manifest_digest(manifest)
        journal = {
            "schema_version": TRANSACTION_SCHEMA_VERSION,
            "run_date": outcome.run_date,
            "phase": outcome.phase,
            "files": files,
            "index": index,
            "manifest": manifest,
        }
        journal["journal_digest"] = journal_digest(journal)
        return journal, manifest

    @staticmethod
    def _manifest_error(reason: str) -> FeedError:
        return FeedError("source_failed", reason)

    @classmethod
    def _validate_manifest_article(cls, article: Any, reason: str) -> dict[str, Any]:
        if not isinstance(article, dict) or any(
            not isinstance(article.get(field), str)
            for field in _ARTICLE_SCALAR_FIELDS
        ):
            raise cls._manifest_error(reason)
        if article["source_platform"] != SOURCE_PLATFORM:
            raise cls._manifest_error(reason)
        try:
            _valid_date(article["date"])
            normalize_wechat_url(article["source_url"])
        except ValueError as exc:
            raise cls._manifest_error(reason) from exc
        digest = article["content_sha256"]
        if (
            article["published_at"][:10] != article["date"]
            or len(digest) != 64
            or digest.lower() != digest
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise cls._manifest_error(reason)
        return article

    @classmethod
    def _validate_manifest_structure(
        cls, payload: dict[str, Any], run_date: str, phase: str, reason: str
    ) -> None:
        if (
            payload.get("schema_version") != 1
            or payload.get("commit_state") != "complete"
            or payload.get("run_date") != run_date
            or payload.get("phase") != phase
        ):
            raise cls._manifest_error(reason)
        counts = payload.get("counts")
        if not isinstance(counts, dict) or any(
            isinstance(counts.get(field), bool)
            or not isinstance(counts.get(field), int)
            or counts[field] < 0
            for field in _COUNT_FIELDS
        ):
            raise cls._manifest_error(reason)
        article_fields = (
            ("new_articles", "new_article_ids", "new"),
            ("retained_articles", "retained_article_ids", "retained"),
            ("candidate_articles", "candidate_article_ids", "candidate"),
        )
        for articles_field, ids_field, count_field in article_fields:
            articles = payload.get(articles_field)
            ids = payload.get(ids_field)
            if not isinstance(articles, list) or not isinstance(ids, list):
                raise cls._manifest_error(reason)
            validated = [
                cls._validate_manifest_article(article, reason)
                for article in articles
            ]
            if (
                any(not isinstance(item, str) for item in ids)
                or counts[count_field] != len(validated)
                or len(ids) != len(validated)
            ):
                raise cls._manifest_error(reason)
        source_results = payload.get("source_results")
        if not isinstance(source_results, list) or any(
            not isinstance(item, dict) for item in source_results
        ):
            raise cls._manifest_error(reason)
        for ids_field, count_field in (
            ("seen_article_ids", "seen"),
            ("pending_article_ids", "pending"),
            ("baseline_article_ids", "baseline"),
        ):
            ids = payload.get(ids_field)
            if (
                not isinstance(ids, list)
                or any(not isinstance(item, str) for item in ids)
                or counts[count_field] != len(ids)
            ):
                raise cls._manifest_error(reason)

    def _safe_journal_target(self, relative_path: str) -> Path:
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise FeedError("source_failed", "corrupt_transaction_journal")
        return self._safe_store_path(relative)

    def _validate_journal(self, payload: Any, run_date: str, phase: str) -> dict[str, Any]:
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != TRANSACTION_SCHEMA_VERSION
            or payload.get("run_date") != run_date
            or payload.get("phase") != phase
            or not isinstance(payload.get("files"), list)
            or not isinstance(payload.get("index"), dict)
            or not isinstance(payload.get("manifest"), dict)
        ):
            raise FeedError("source_failed", "corrupt_transaction_journal")
        try:
            expected_journal_digest = journal_digest(payload)
        except _InvalidDigestPayload as exc:
            raise FeedError(
                "source_failed", "corrupt_transaction_journal"
            ) from exc
        if payload.get("journal_digest") != expected_journal_digest:
            raise FeedError("source_failed", "corrupt_transaction_journal")
        try:
            self._validate_index_payload(payload["index"])
        except FeedError as exc:
            raise FeedError(
                "source_failed", "corrupt_transaction_journal"
            ) from exc
        manifest = payload["manifest"]
        try:
            expected_manifest_digest = manifest_digest(manifest)
        except _InvalidDigestPayload as exc:
            raise FeedError(
                "source_failed", "corrupt_transaction_journal"
            ) from exc
        if manifest.get("manifest_digest") != expected_manifest_digest:
            raise FeedError("source_failed", "corrupt_transaction_journal")
        self._validate_manifest_structure(
            manifest, run_date, phase, "corrupt_transaction_journal"
        )
        candidates = manifest.get("candidate_articles")
        raw_content_hashes: dict[str, str] = {}
        expected_files: dict[str, str] = {}
        candidate_file_sets: list[set[str]] = []
        path_specs = (
            ("raw_html_path", "raw_html", ".html"),
            ("raw_content_path", "raw_content", ".md"),
            ("article_json_path", "raw_json", ".json"),
        )
        for article in candidates:
            candidate_paths: set[str] = set()
            for field, label, suffix in path_specs:
                path = article[field]
                relative = Path(path)
                if (
                    relative.is_absolute()
                    or ".." in relative.parts
                    or len(relative.parts) != 4
                    or relative.parts[:3] != (run_date, phase, "articles")
                    or relative.suffix != suffix
                    or path in expected_files
                ):
                    raise FeedError(
                        "source_failed", "corrupt_transaction_journal"
                    )
                self._safe_journal_target(path)
                expected_files[path] = label
                candidate_paths.add(path)
            raw_content_hashes[article["raw_content_path"]] = article[
                "content_sha256"
            ]
            candidate_file_sets.append(candidate_paths)
        actual_paths: set[str] = set()
        for item in payload["files"]:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("relative_path"), str)
                or item.get("label")
                not in {"raw_html", "raw_content", "raw_json"}
                or not isinstance(item.get("text"), str)
            ):
                raise FeedError("source_failed", "corrupt_transaction_journal")
            relative_path = item["relative_path"]
            if (
                relative_path in actual_paths
                or expected_files.get(relative_path) != item["label"]
            ):
                raise FeedError("source_failed", "corrupt_transaction_journal")
            self._safe_journal_target(relative_path)
            actual_paths.add(relative_path)
            if item["label"] == "raw_content":
                text = item["text"]
                expected = raw_content_hashes.get(relative_path)
                if (
                    not text
                    or text != canonicalize_raw_content(text)
                    or expected is None
                    or canonical_content_sha256(text) != expected
                ):
                    raise FeedError("source_failed", "corrupt_transaction_journal")
        for candidate_paths in candidate_file_sets:
            present = candidate_paths & actual_paths
            if present and present != candidate_paths:
                raise FeedError("source_failed", "corrupt_transaction_journal")
            if not present and not all(
                self._safe_journal_target(path).is_file()
                for path in candidate_paths
            ):
                raise FeedError("source_failed", "corrupt_transaction_journal")
        return payload

    def _read_journal(self, run_date: str, phase: str) -> dict[str, Any]:
        path = self._journal_path(run_date, phase)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise FeedError("source_failed", "corrupt_transaction_journal") from exc
        return self._validate_journal(payload, run_date, phase)

    def _delete_journal(self, journal_path: Path) -> None:
        self._fault_hook("before_delete:journal")
        journal_path = self._safe_store_path(
            self._relative_store_path(journal_path)
        )
        journal_path.unlink()
        self._fsync_dir(journal_path.parent)

    def _replay_locked(self, run_date: str, phase: str) -> dict[str, Any] | None:
        journal_path = self._journal_path(run_date, phase)
        if not journal_path.exists():
            return None
        journal = self._read_journal(run_date, phase)
        for item in journal["files"]:
            self._atomic_write_text(
                self._safe_journal_target(item["relative_path"]),
                item["text"],
                item["label"],
            )
        self._atomic_write_json(self.index_path, journal["index"], "index")
        self._atomic_write_json(
            self._manifest_path(run_date, phase), journal["manifest"], "manifest"
        )
        self._delete_journal(journal_path)
        return journal["manifest"]

    def _pending_journal_paths(self) -> list[Path]:
        self._resolved_root()
        if not self.root.exists():
            return []
        paths: list[Path] = []
        try:
            date_entries = sorted(self.root.iterdir())
        except OSError as exc:
            raise self._unsafe_store_path() from exc
        for date_entry in date_entries:
            try:
                run_date = _valid_date(date_entry.name)
            except ValueError:
                continue
            safe_date = self._safe_store_path(run_date)
            if not safe_date.is_dir():
                continue
            try:
                phase_entries = sorted(safe_date.iterdir())
            except OSError as exc:
                raise self._unsafe_store_path() from exc
            for phase_entry in phase_entries:
                safe_phase = self._safe_store_path(Path(run_date) / phase_entry.name)
                if not safe_phase.is_dir():
                    continue
                journal_path = self._safe_store_path(
                    Path(run_date) / phase_entry.name / "transaction.json"
                )
                if journal_path.exists():
                    paths.append(journal_path)
        return sorted(paths)

    def _recover_all_locked(self) -> dict[tuple[str, str], dict[str, Any]]:
        paths = self._pending_journal_paths()
        if len(paths) > 1:
            # Two full-index journals cannot be replayed safely in sequence: a newer
            # journal may have been created from an index that did not yet contain
            # the older transaction. Preserve both for manual reconciliation.
            raise FeedError("source_failed", "multiple_pending_transactions")
        recovered: dict[tuple[str, str], dict[str, Any]] = {}
        for path in paths:
            try:
                run_date, phase, _ = path.relative_to(self.root).parts
            except (ValueError, TypeError):
                raise FeedError("source_failed", "corrupt_transaction_journal") from None
            manifest = self._replay_locked(run_date, phase)
            if manifest is not None:
                recovered[(run_date, phase)] = manifest
        return recovered

    def recover_phase(self, run_date: str, phase: str) -> dict[str, Any] | None:
        self._phase_dir(run_date, phase)
        with self._locked():
            return self._recover_all_locked().get((run_date, phase))

    def persist_phase(self, outcome: CollectionOutcome) -> PersistResult:
        _valid_date(outcome.run_date)
        if outcome.phase not in PHASES:
            raise ValueError(f"unsupported phase: {outcome.phase}")
        if not str(outcome.input_by or "").strip():
            raise ValueError("input_by is required")
        if outcome.dry_run:
            source_index = self._load_index() if self.index_path.exists() else self._empty_index()
            try:
                _, manifest = self._build_transaction(outcome, source_index)
                manifest["commit_state"] = "dry_run"
                manifest["manifest_digest"] = manifest_digest(manifest)
            except _InvalidDigestPayload as exc:
                raise FeedError("source_failed", "invalid_unicode_payload") from exc
            return self._result(manifest, None)

        with self._locked():
            self._recover_all_locked()
            try:
                journal, manifest = self._build_transaction(
                    outcome, self._load_index()
                )
            except _InvalidDigestPayload as exc:
                raise FeedError("source_failed", "invalid_unicode_payload") from exc
            journal_path = self._journal_path(outcome.run_date, outcome.phase)
            self._atomic_write_json(journal_path, journal, "journal")
            for item in journal["files"]:
                self._atomic_write_text(
                    self._safe_journal_target(item["relative_path"]),
                    item["text"],
                    item["label"],
                )
            self._atomic_write_json(self.index_path, journal["index"], "index")
            manifest_path = self._manifest_path(outcome.run_date, outcome.phase)
            self._atomic_write_json(manifest_path, manifest, "manifest")
            self._delete_journal(journal_path)
            return self._result(manifest, str(manifest_path))

    @staticmethod
    def _result(manifest: dict[str, Any], path: str | None) -> PersistResult:
        return PersistResult(
            manifest=copy.deepcopy(manifest),
            manifest_path=path,
            new_article_ids=tuple(manifest.get("new_article_ids") or []),
            retained_article_ids=tuple(manifest.get("retained_article_ids") or []),
            candidate_article_ids=tuple(manifest.get("candidate_article_ids") or []),
            seen_article_ids=tuple(manifest.get("seen_article_ids") or []),
            pending_article_ids=tuple(manifest.get("pending_article_ids") or []),
            baseline_article_ids=tuple(manifest.get("baseline_article_ids") or []),
        )

    def read_manifest(self, run_date: str, phase: str) -> dict[str, Any] | None:
        path = self._manifest_path(run_date, phase)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise FeedError("source_failed", "corrupt_phase_manifest") from exc
        if not isinstance(payload, dict) or payload.get("commit_state") != "complete":
            return None
        try:
            expected_digest = manifest_digest(payload)
        except _InvalidDigestPayload as exc:
            raise FeedError("source_failed", "corrupt_phase_manifest") from exc
        if payload.get("manifest_digest") != expected_digest:
            raise FeedError("source_failed", "corrupt_phase_manifest")
        self._validate_manifest_structure(
            payload, run_date, phase, "corrupt_phase_manifest"
        )
        return payload

    def pending_transactions(self) -> tuple[str, ...]:
        paths = []
        for path in self._pending_journal_paths():
            try:
                paths.append(str(path.relative_to(self.root)))
            except ValueError:
                continue
        return tuple(sorted(paths))
