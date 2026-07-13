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
TRANSACTION_SCHEMA_VERSION = 1


def _compact_json(payload: Any) -> str:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def manifest_digest(manifest_without_digest: dict[str, Any]) -> str:
    payload = dict(manifest_without_digest)
    payload.pop("manifest_digest", None)
    return hashlib.sha256(_compact_json(payload).encode("utf-8")).hexdigest()


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
        return self.root / "index.json"

    def _phase_dir(self, run_date: str, phase: str) -> Path:
        _valid_date(run_date)
        if phase not in PHASES:
            raise ValueError(f"unsupported phase: {phase}")
        return self.root / run_date / phase

    def _journal_path(self, run_date: str, phase: str) -> Path:
        return self._phase_dir(run_date, phase) / "transaction.json"

    def _manifest_path(self, run_date: str, phase: str) -> Path:
        return self._phase_dir(run_date, phase) / "manifest.json"

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        lock_path = self.root / ".lock"
        with lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _empty_index() -> dict[str, Any]:
        return {"schema_version": INDEX_SCHEMA_VERSION, "sources": {}, "records": []}

    def _load_index(self) -> dict[str, Any]:
        if not self.index_path.exists():
            return self._empty_index()
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise FeedError("source_failed", "corrupt_feed_index") from exc
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != INDEX_SCHEMA_VERSION
            or not isinstance(payload.get("sources"), dict)
            or not isinstance(payload.get("records"), list)
        ):
            raise FeedError("source_failed", "corrupt_feed_index")
        return payload

    def load_snapshot(self) -> dict[str, Any]:
        return copy.deepcopy(self._load_index())

    def _atomic_write_text(self, path: Path, text: str, label: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            self._fault_hook(f"before_replace:{label}")
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
            if source.listed and source.teacher_name not in index["sources"]:
                index["sources"][source.teacher_name] = {
                    "first_snapshot_run_date": outcome.run_date,
                    "source_account_id": source.source_account_id,
                }

        for observation in outcome.observations:
            matches = self._matching_records(index, observation)
            record = matches[0] if matches else None
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
        return journal, manifest

    def _safe_journal_target(self, relative_path: str) -> Path:
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise FeedError("source_failed", "corrupt_transaction_journal")
        candidate = (self.root / relative).resolve()
        resolved_root = self.root.resolve()
        if candidate != resolved_root and resolved_root not in candidate.parents:
            raise FeedError("source_failed", "corrupt_transaction_journal")
        return candidate

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
        manifest = payload["manifest"]
        if manifest.get("manifest_digest") != manifest_digest(manifest):
            raise FeedError("source_failed", "corrupt_transaction_journal")
        candidates = manifest.get("candidate_articles")
        if not isinstance(candidates, list):
            raise FeedError("source_failed", "corrupt_transaction_journal")
        raw_content_hashes: dict[str, str] = {}
        for article in candidates:
            if not isinstance(article, dict):
                raise FeedError("source_failed", "corrupt_transaction_journal")
            path = article.get("raw_content_path")
            digest = article.get("content_sha256")
            if not isinstance(path, str) or not isinstance(digest, str):
                raise FeedError("source_failed", "corrupt_transaction_journal")
            raw_content_hashes[path] = digest
        for item in payload["files"]:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("relative_path"), str)
                or item.get("label")
                not in {"raw_html", "raw_content", "raw_json"}
                or not isinstance(item.get("text"), str)
            ):
                raise FeedError("source_failed", "corrupt_transaction_journal")
            self._safe_journal_target(item["relative_path"])
            if item["label"] == "raw_content":
                text = item["text"]
                expected = raw_content_hashes.get(item["relative_path"])
                if (
                    not text
                    or text != canonicalize_raw_content(text)
                    or expected is None
                    or canonical_content_sha256(text) != expected
                ):
                    raise FeedError("source_failed", "corrupt_transaction_journal")
        return payload

    def _read_journal(self, run_date: str, phase: str) -> dict[str, Any]:
        path = self._journal_path(run_date, phase)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise FeedError("source_failed", "corrupt_transaction_journal") from exc
        return self._validate_journal(payload, run_date, phase)

    def _delete_journal(self, journal_path: Path) -> None:
        self._fault_hook("before_delete:journal")
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
        if not self.root.exists():
            return []
        return sorted(self.root.glob("????-??-??/*/transaction.json"))

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
            _, manifest = self._build_transaction(outcome, source_index)
            manifest["commit_state"] = "dry_run"
            manifest["manifest_digest"] = manifest_digest(manifest)
            return self._result(manifest, None)

        with self._locked():
            self._recover_all_locked()
            journal, manifest = self._build_transaction(outcome, self._load_index())
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
        except (OSError, json.JSONDecodeError) as exc:
            raise FeedError("source_failed", "corrupt_phase_manifest") from exc
        if not isinstance(payload, dict) or payload.get("commit_state") != "complete":
            return None
        if payload.get("manifest_digest") != manifest_digest(payload):
            raise FeedError("source_failed", "corrupt_phase_manifest")
        return payload

    def pending_transactions(self) -> tuple[str, ...]:
        paths = []
        for path in self._pending_journal_paths():
            try:
                paths.append(str(path.relative_to(self.root)))
            except ValueError:
                continue
        return tuple(sorted(paths))
