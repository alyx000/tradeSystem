from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

import pytest


SCRIPTS_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = SCRIPTS_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from services.content_identity import canonical_content_sha256
from services.wechat_teacher_feed.constants import WHITELIST
from services.wechat_teacher_feed.models import (
    ArticleObservation,
    CollectionOutcome,
    FeedError,
    SourceResult,
    WechatTeacherArticle,
    WeRSSSource,
)
from services.wechat_teacher_feed.store import FeedStore, manifest_digest


RUN_DATE = "2026-07-13"


def _cli_module():
    from cli import wechat_teacher_feed

    return wechat_teacher_feed


def _invoke(argv: list[str], capsys) -> tuple[int, dict]:
    from main import build_parser

    module = _cli_module()
    args = build_parser().parse_args(argv)
    code = module.handle_command({}, args)
    captured = capsys.readouterr()
    assert captured.err == ""
    return code, json.loads(captured.out)


def _create_minimal_db(path: Path, calendar: dict[str, int] | None = None) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE trade_calendar (
            date TEXT PRIMARY KEY,
            is_open INTEGER NOT NULL
        );
        CREATE TABLE teachers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        );
        CREATE TABLE teacher_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            title TEXT NOT NULL,
            source_platform TEXT,
            source_url TEXT,
            source_article_id TEXT,
            published_at TEXT,
            fetched_at TEXT,
            content_sha256 TEXT
        );
        PRAGMA user_version = 40;
        """
    )
    for day, is_open in (calendar or {}).items():
        conn.execute(
            "INSERT INTO trade_calendar(date, is_open) VALUES (?, ?)",
            (day, is_open),
        )
    conn.commit()
    conn.close()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _article(
    article_id: str,
    *,
    teacher_name: str = "安静拆主线",
    title: str | None = None,
    content: str | None = None,
    url: str | None = None,
) -> WechatTeacherArticle:
    raw_content = content or f"{article_id} 的老师观点\n"
    return WechatTeacherArticle(
        teacher_name=teacher_name,
        source_platform="wechat_mp",
        source_account_id=f"mp-{teacher_name}",
        source_article_id=article_id,
        source_url=url or f"https://mp.weixin.qq.com/s/{article_id}",
        title=title or f"标题 {article_id}",
        published_at="2026-07-13T20:18:00+08:00",
        fetched_at="2026-07-13T22:15:05+08:00",
        raw_content=raw_content,
        raw_html=f"<p>{raw_content.strip()}</p>",
        content_sha256=canonical_content_sha256(raw_content),
    )


def _observation(article: WechatTeacherArticle) -> ArticleObservation:
    return ArticleObservation(
        teacher_name=article.teacher_name,
        source_account_id=article.source_account_id,
        source_article_id=article.source_article_id,
        source_url=article.source_url,
        title=article.title,
        published_at=article.published_at,
        state="complete",
        article=article,
    )


def _outcome(
    *articles: WechatTeacherArticle,
    phase: str = "post-market",
    status: str = "success",
    reason: str = "verified",
) -> CollectionOutcome:
    source_results = tuple(
        SourceResult(
            teacher_name=source.teacher_name,
            source_account_id=f"mp-{source.teacher_name}",
            status="ok",
            reason="verified",
            refresh_status="verified_success",
            refresh_verified=True,
            metadata_count=len(articles),
            complete_count=len(articles),
            listed=True,
        )
        for source in WHITELIST
    )
    return CollectionOutcome(
        run_date=RUN_DATE,
        phase=phase,
        target_trade_date=RUN_DATE,
        input_by="codex_automation",
        status=status,
        reason=reason,
        exit_code=0 if status in {"success", "empty", "skip"} else 1,
        source_results=source_results,
        observations=tuple(_observation(article) for article in articles),
    )


class CachedOnlyClient:
    def __init__(self, *args, **kwargs) -> None:
        self.sources = [
            WeRSSSource(f"mp-{index}", source.teacher_name)
            for index, source in enumerate(WHITELIST, start=1)
        ]
        self.update_calls = 0

    def list_sources(self):
        return list(self.sources)

    def request_update(self, *args, **kwargs):
        self.update_calls += 1
        raise AssertionError("cached-only/dry-run must not call request_update")

    def list_articles(self, mp_id: str):
        return []

    def get_article_detail(self, article_id: str):
        raise AssertionError("empty cache must not request detail")


@pytest.mark.parametrize(
    "argv",
    [
        [
            "wechat-teacher-feed", "should-run", "--phase", "post-market",
            "--date", RUN_DATE, "--json",
        ],
        ["wechat-teacher-feed", "doctor", "--json"],
        [
            "wechat-teacher-feed", "collect", "--phase", "pre-trading-eve",
            "--date", "2026-07-12", "--input-by", "codex_automation", "--json",
        ],
        [
            "wechat-teacher-feed", "collect", "--phase", "post-market",
            "--date", RUN_DATE, "--input-by", "codex_automation", "--dry-run",
            "--force", "--cached-only", "--json",
        ],
        ["wechat-teacher-feed", "show", "--date", RUN_DATE, "--json"],
    ],
)
def test_parser_accepts_all_wechat_teacher_feed_forms(argv: list[str]) -> None:
    from main import build_parser

    args = build_parser().parse_args(argv)

    assert args.command == "wechat-teacher-feed"


def test_collect_requires_input_by_before_dependencies_are_opened() -> None:
    from main import build_parser

    parser = build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(
            [
                "wechat-teacher-feed", "collect", "--phase", "post-market",
                "--date", RUN_DATE, "--json",
            ]
        )
    assert exc_info.value.code == 2


def test_should_run_is_read_only_and_calendar_missing_is_blocked(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    db_path = tmp_path / "calendar.db"
    _create_minimal_db(db_path)
    before_hash = _sha256_file(db_path)
    monkeypatch.setenv("TRADE_DB_PATH", str(db_path))

    code, payload = _invoke(
        [
            "wechat-teacher-feed", "should-run", "--phase", "post-market",
            "--date", RUN_DATE, "--json",
        ],
        capsys,
    )

    assert code == 2
    assert payload["status"] == "blocked"
    assert payload["reason"] == "calendar_unavailable"
    assert _sha256_file(db_path) == before_hash


def test_collect_skip_returns_zero_without_constructing_client(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    db_path = tmp_path / "calendar.db"
    _create_minimal_db(db_path, {RUN_DATE: 0})
    root = tmp_path / "runs"
    module = _cli_module()
    monkeypatch.setenv("TRADE_DB_PATH", str(db_path))
    monkeypatch.setattr(module, "FeedStore", lambda: FeedStore(root))
    monkeypatch.setattr(
        module,
        "WeRSSClient",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("skip must not construct client")
        ),
    )

    code, payload = _invoke(
        [
            "wechat-teacher-feed", "collect", "--phase", "post-market",
            "--date", RUN_DATE, "--input-by", "codex_automation", "--json",
        ],
        capsys,
    )

    assert code == 0
    assert (payload["status"], payload["reason"]) == (
        "skip", "phase_not_scheduled"
    )
    assert FeedStore(root).read_manifest(RUN_DATE, "post-market")["status"] == "skip"


def test_force_cannot_bypass_calendar_blocked(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    db_path = tmp_path / "calendar.db"
    _create_minimal_db(db_path)
    root = tmp_path / "runs"
    module = _cli_module()
    monkeypatch.setenv("TRADE_DB_PATH", str(db_path))
    monkeypatch.setattr(module, "FeedStore", lambda: FeedStore(root))
    monkeypatch.setattr(
        module,
        "WeRSSClient",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("blocked must not construct client")
        ),
    )

    code, payload = _invoke(
        [
            "wechat-teacher-feed", "collect", "--phase", "post-market",
            "--date", RUN_DATE, "--input-by", "codex_automation", "--force",
            "--json",
        ],
        capsys,
    )

    assert code == 2
    assert (payload["status"], payload["reason"]) == (
        "blocked", "calendar_unavailable"
    )


def test_force_turns_explicit_skip_into_run(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    db_path = tmp_path / "calendar.db"
    _create_minimal_db(db_path, {RUN_DATE: 0})
    root = tmp_path / "runs"
    module = _cli_module()
    created: list[CachedOnlyClient] = []

    def factory(*args, **kwargs):
        client = CachedOnlyClient(*args, **kwargs)
        created.append(client)
        return client

    monkeypatch.setenv("TRADE_DB_PATH", str(db_path))
    monkeypatch.setenv("WERSS_ACCESS_KEY", "ak")
    monkeypatch.setenv("WERSS_SECRET_KEY", "sk")
    monkeypatch.setattr(module, "FeedStore", lambda: FeedStore(root))
    monkeypatch.setattr(module, "WeRSSClient", factory)

    code, payload = _invoke(
        [
            "wechat-teacher-feed", "collect", "--phase", "post-market",
            "--date", RUN_DATE, "--input-by", "codex_automation", "--force",
            "--cached-only", "--json",
        ],
        capsys,
    )

    assert code == 1
    assert payload["status"] == "partial"
    assert created and created[0].update_calls == 0


def test_collect_reuses_complete_manifest_without_constructing_client(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    db_path = tmp_path / "calendar.db"
    _create_minimal_db(db_path, {RUN_DATE: 1})
    root = tmp_path / "runs"
    store = FeedStore(root)
    existing = store.persist_phase(
        _outcome(status="partial", reason="refresh_unverified")
    )
    manifest_path = Path(existing.manifest_path or "")
    before_bytes = manifest_path.read_bytes()
    module = _cli_module()
    monkeypatch.setenv("TRADE_DB_PATH", str(db_path))
    monkeypatch.setattr(module, "FeedStore", lambda: FeedStore(root))
    monkeypatch.setattr(
        module,
        "WeRSSClient",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("complete manifest must short-circuit")
        ),
    )

    code, payload = _invoke(
        [
            "wechat-teacher-feed", "collect", "--phase", "post-market",
            "--date", RUN_DATE, "--input-by", "codex_automation", "--json",
        ],
        capsys,
    )

    assert code == 1
    assert payload["status"] == "partial"
    assert payload["manifest_digest"] == existing.manifest["manifest_digest"]
    assert manifest_path.read_bytes() == before_bytes


@pytest.mark.parametrize("flag", ["--dry-run", "--cached-only"])
def test_non_refresh_modes_never_call_update(
    flag: str, tmp_path: Path, monkeypatch, capsys
) -> None:
    db_path = tmp_path / "calendar.db"
    _create_minimal_db(db_path, {RUN_DATE: 1})
    root = tmp_path / "runs"
    module = _cli_module()
    created: list[CachedOnlyClient] = []

    def factory(*args, **kwargs):
        client = CachedOnlyClient(*args, **kwargs)
        created.append(client)
        return client

    monkeypatch.setenv("TRADE_DB_PATH", str(db_path))
    monkeypatch.setenv("WERSS_ACCESS_KEY", "test-access-key")
    monkeypatch.setenv("WERSS_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("WERSS_REFRESH_GRACE_SECONDS", "0")
    monkeypatch.setattr(module, "FeedStore", lambda: FeedStore(root))
    monkeypatch.setattr(module, "WeRSSClient", factory)

    code, payload = _invoke(
        [
            "wechat-teacher-feed", "collect", "--phase", "post-market",
            "--date", RUN_DATE, "--input-by", "codex_automation", flag,
            "--json",
        ],
        capsys,
    )

    assert code == 1
    assert payload["status"] == "partial"
    assert created[0].update_calls == 0
    if flag == "--dry-run":
        assert payload["manifest_path"] is None
        assert not root.exists()
    else:
        assert Path(payload["manifest_path"]).is_file()


@pytest.mark.parametrize("raw_value", ["inf", "-inf", "nan", "301", "1e309"])
def test_doctor_rejects_unsafe_refresh_grace_before_client(
    raw_value: str, monkeypatch, capsys
) -> None:
    module = _cli_module()
    monkeypatch.setenv("WERSS_ACCESS_KEY", "test-access-key")
    monkeypatch.setenv("WERSS_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("WERSS_REFRESH_GRACE_SECONDS", raw_value)
    monkeypatch.setattr(
        module,
        "WeRSSClient",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("invalid grace must block before client construction")
        ),
    )

    code, payload = _invoke(
        ["wechat-teacher-feed", "doctor", "--json"], capsys
    )

    assert code == 2
    assert payload["status"] == "blocked"
    assert payload["reason"] == "invalid_refresh_grace_seconds"


@pytest.mark.parametrize("raw_value", ["0", "-1", "21", "1.5", "nan", "bad"])
def test_doctor_rejects_unsafe_refresh_end_page_before_client(
    raw_value: str, monkeypatch, capsys
) -> None:
    module = _cli_module()
    monkeypatch.setenv("WERSS_ACCESS_KEY", "test-access-key")
    monkeypatch.setenv("WERSS_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("WERSS_REFRESH_END_PAGE", raw_value)
    monkeypatch.setattr(
        module,
        "WeRSSClient",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("invalid end page must block before client construction")
        ),
    )

    code, payload = _invoke(
        ["wechat-teacher-feed", "doctor", "--json"], capsys
    )

    assert code == 2
    assert payload["status"] == "blocked"
    assert payload["reason"] == "invalid_refresh_end_page"


def test_collect_rejects_unsafe_refresh_grace_before_client(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    db_path = tmp_path / "calendar.db"
    _create_minimal_db(db_path, {RUN_DATE: 1})
    root = tmp_path / "runs"
    module = _cli_module()
    monkeypatch.setenv("TRADE_DB_PATH", str(db_path))
    monkeypatch.setenv("WERSS_ACCESS_KEY", "test-access-key")
    monkeypatch.setenv("WERSS_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("WERSS_REFRESH_GRACE_SECONDS", "inf")
    monkeypatch.setattr(module, "FeedStore", lambda: FeedStore(root))
    monkeypatch.setattr(
        module,
        "WeRSSClient",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("invalid grace must block before client construction")
        ),
    )

    code, payload = _invoke(
        [
            "wechat-teacher-feed", "collect", "--phase", "post-market",
            "--date", RUN_DATE, "--input-by", "codex_automation",
            "--dry-run", "--json",
        ],
        capsys,
    )

    assert code == 2
    assert payload["status"] == "blocked"
    assert payload["reason"] == "invalid_refresh_grace_seconds"
    assert not root.exists()


def test_collect_rejects_unsafe_refresh_end_page_before_client(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    db_path = tmp_path / "calendar.db"
    _create_minimal_db(db_path, {RUN_DATE: 1})
    root = tmp_path / "runs"
    module = _cli_module()
    monkeypatch.setenv("TRADE_DB_PATH", str(db_path))
    monkeypatch.setenv("WERSS_ACCESS_KEY", "test-access-key")
    monkeypatch.setenv("WERSS_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("WERSS_REFRESH_END_PAGE", "21")
    monkeypatch.setattr(module, "FeedStore", lambda: FeedStore(root))
    monkeypatch.setattr(
        module,
        "WeRSSClient",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("invalid end page must block before client construction")
        ),
    )

    code, payload = _invoke(
        [
            "wechat-teacher-feed", "collect", "--phase", "post-market",
            "--date", RUN_DATE, "--input-by", "codex_automation",
            "--dry-run", "--json",
        ],
        capsys,
    )

    assert code == 2
    assert payload["status"] == "blocked"
    assert payload["reason"] == "invalid_refresh_end_page"
    assert not root.exists()


def test_cached_only_recovers_journal_without_starting_refresh(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    db_path = tmp_path / "calendar.db"
    _create_minimal_db(db_path, {RUN_DATE: 1})
    root = tmp_path / "runs"

    def fail_before_manifest(label: str) -> None:
        if label == "before_replace:manifest":
            raise RuntimeError("simulated crash")

    with pytest.raises(RuntimeError, match="simulated crash"):
        FeedStore(root, fault_hook=fail_before_manifest).persist_phase(
            _outcome(_article("recover-me"))
        )
    journal = root / RUN_DATE / "post-market" / "transaction.json"
    assert journal.is_file()
    module = _cli_module()
    created: list[CachedOnlyClient] = []

    def factory(*args, **kwargs):
        client = CachedOnlyClient(*args, **kwargs)
        created.append(client)
        return client

    monkeypatch.setenv("TRADE_DB_PATH", str(db_path))
    monkeypatch.setenv("WERSS_ACCESS_KEY", "ak")
    monkeypatch.setenv("WERSS_SECRET_KEY", "sk")
    monkeypatch.setattr(module, "FeedStore", lambda: FeedStore(root))
    monkeypatch.setattr(module, "WeRSSClient", factory)

    code, payload = _invoke(
        [
            "wechat-teacher-feed", "collect", "--phase", "post-market",
            "--date", RUN_DATE, "--input-by", "codex_automation",
            "--cached-only", "--json",
        ],
        capsys,
    )

    assert code == 1
    assert payload["candidate_count"] == 1
    assert payload["new_count"] == 0
    assert created[0].update_calls == 0
    assert not journal.exists()
    assert Path(payload["manifest_path"]).is_file()


def test_doctor_missing_credentials_and_remote_url_are_secret_safe(
    monkeypatch, capsys
) -> None:
    module = _cli_module()
    monkeypatch.delenv("WERSS_ACCESS_KEY", raising=False)
    monkeypatch.delenv("WERSS_SECRET_KEY", raising=False)
    monkeypatch.delenv("WERSS_BASE_URL", raising=False)

    code, payload = _invoke(
        ["wechat-teacher-feed", "doctor", "--json"], capsys
    )
    assert code == 2
    assert payload["missing_env"] == ["WERSS_ACCESS_KEY", "WERSS_SECRET_KEY"]
    assert "matched" not in payload
    assert "missing" not in payload
    assert "missing_sources" not in payload
    assert "ambiguous_sources" not in payload
    rendered = json.dumps(payload, ensure_ascii=False)
    assert "Authorization" not in rendered

    monkeypatch.setenv("WERSS_ACCESS_KEY", "distinct-access-secret")
    monkeypatch.setenv("WERSS_SECRET_KEY", "distinct-signing-secret")
    monkeypatch.setenv("WERSS_BASE_URL", "https://example.com")
    monkeypatch.setattr(
        module,
        "WeRSSClient",
        module._REAL_WERSS_CLIENT,
    )
    code, payload = _invoke(
        ["wechat-teacher-feed", "doctor", "--json"], capsys
    )
    assert code == 2
    assert payload["reason"] == "invalid_base_url"
    rendered = json.dumps(payload, ensure_ascii=False)
    assert "distinct-access-secret" not in rendered
    assert "distinct-signing-secret" not in rendered


def test_doctor_matches_exact_three_source_whitelist(
    monkeypatch, capsys
) -> None:
    module = _cli_module()

    class DoctorClient:
        def __init__(self, *args, **kwargs):
            pass

        def list_sources(self):
            return [
                WeRSSSource(f"mp-{index}", source.teacher_name)
                for index, source in enumerate(WHITELIST, start=1)
            ] + [WeRSSSource("mp-extra", "额外订阅")]

    monkeypatch.setenv("WERSS_ACCESS_KEY", "ak")
    monkeypatch.setenv("WERSS_SECRET_KEY", "sk")
    monkeypatch.delenv("WERSS_BASE_URL", raising=False)
    monkeypatch.setattr(module, "WeRSSClient", DoctorClient)

    code, payload = _invoke(
        ["wechat-teacher-feed", "doctor", "--json"], capsys
    )

    assert code == 0
    assert payload["status"] == "ok"
    assert payload["whitelist_total"] == 3
    assert payload["matched"] == 3
    assert payload["missing"] == 0


def test_doctor_never_reports_ready_for_invalid_source_identifier(
    monkeypatch, capsys
) -> None:
    module = _cli_module()

    class InvalidSourceClient:
        def __init__(self, *args, **kwargs):
            pass

        def list_sources(self):
            raise FeedError("source_failed", "invalid_upstream_identifier")

    monkeypatch.setenv("WERSS_ACCESS_KEY", "ak")
    monkeypatch.setenv("WERSS_SECRET_KEY", "sk")
    monkeypatch.setattr(module, "WeRSSClient", InvalidSourceClient)

    code, payload = _invoke(
        ["wechat-teacher-feed", "doctor", "--json"], capsys
    )

    assert code == 1
    assert payload["status"] == "source_failed"
    assert payload["reason"] == "invalid_upstream_identifier"


def test_show_uses_candidate_union_and_honors_phase_filter(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    db_path = tmp_path / "notes.db"
    _create_minimal_db(db_path)
    root = tmp_path / "runs"
    store = FeedStore(root)
    first = _article("first")
    second = _article("second")
    third = _article("third", teacher_name="股痴流沙河")
    store.persist_phase(_outcome(first, second))
    rerun = store.persist_phase(_outcome(second))
    store.persist_phase(_outcome(third, phase="pre-trading-eve"))
    assert rerun.manifest["new_article_ids"] == ["second"]
    assert rerun.manifest["candidate_article_ids"] == ["first", "second"]
    module = _cli_module()
    monkeypatch.setenv("TRADE_DB_PATH", str(db_path))
    monkeypatch.setattr(module, "FeedStore", lambda: FeedStore(root))

    code, payload = _invoke(
        ["wechat-teacher-feed", "show", "--date", RUN_DATE, "--json"],
        capsys,
    )
    assert code == 0
    assert {item["source_article_id"] for item in payload["candidates"]} == {
        "first", "second", "third"
    }
    assert payload["new_count"] == 3
    assert len(payload["manifests"]) == 2

    code, payload = _invoke(
        [
            "wechat-teacher-feed", "show", "--date", RUN_DATE,
            "--phase", "post-market", "--json",
        ],
        capsys,
    )
    assert code == 0
    assert {item["source_article_id"] for item in payload["candidates"]} == {
        "first", "second"
    }


def test_show_invalid_date_is_machine_readable_blocked(capsys) -> None:
    code, payload = _invoke(
        ["wechat-teacher-feed", "show", "--date", "nope", "--json"],
        capsys,
    )

    assert code == 2
    assert payload == {
        "run_date": "nope",
        "phase": None,
        "status": "blocked",
        "reason": "date must be valid YYYY-MM-DD",
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("teacher_name", "股痴流沙河"),
        ("source_account_id", "mp-conflict"),
        ("source_url", "https://mp.weixin.qq.com/s/other"),
        ("title", "冲突标题"),
    ],
)
def test_merge_candidate_rejects_strong_id_provenance_conflict(
    field: str, value: str
) -> None:
    module = _cli_module()
    existing = asdict(_article("same-id"))
    incoming = dict(existing)
    incoming[field] = value

    candidates = [existing]
    with pytest.raises(FeedError, match="ambiguous_article_provenance"):
        module._merge_candidate(candidates, incoming)

    assert candidates == [existing]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", "冲突标题"),
        ("date", "2026-07-14"),
        ("published_at", "2026-07-14T20:18:00+08:00"),
    ],
)
def test_merge_candidate_rejects_same_url_stable_metadata_conflict(
    field: str, value: str
) -> None:
    module = _cli_module()
    existing = asdict(_article("first-id"))
    incoming = asdict(
        _article("alias-id", url=existing["source_url"], content="first-id 的老师观点\n")
    )
    incoming[field] = value

    with pytest.raises(FeedError, match="ambiguous_article_provenance"):
        module._merge_candidate([existing], incoming)


def test_show_quarantines_both_sides_of_identity_conflict(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    db_path = tmp_path / "notes.db"
    _create_minimal_db(db_path)
    root = tmp_path / "runs"
    store = FeedStore(root)
    common_content = "同一份正文\n"
    first = _article("first-id", title="原始标题", content=common_content)
    second = _article("second-id", title="另一标题", content=common_content)
    store.persist_phase(_outcome(first))
    store.persist_phase(_outcome(second, phase="pre-trading-eve"))

    manifest_path = root / RUN_DATE / "pre-trading-eve" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for field in ("candidate_articles", "new_articles"):
        manifest[field][0]["source_article_id"] = first.source_article_id
    manifest["candidate_article_ids"] = [first.source_article_id]
    manifest["new_article_ids"] = [first.source_article_id]
    manifest["manifest_digest"] = manifest_digest(manifest)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    module = _cli_module()
    monkeypatch.setenv("TRADE_DB_PATH", str(db_path))
    monkeypatch.setattr(module, "FeedStore", lambda: FeedStore(root))

    code, payload = _invoke(
        ["wechat-teacher-feed", "show", "--date", RUN_DATE, "--json"],
        capsys,
    )

    assert code == 1
    assert payload["status"] == "source_failed"
    assert payload["reason"] == "ambiguous_article_provenance"
    assert payload["new_count"] == 0
    assert payload["candidates"] == []


def test_show_quarantines_retained_candidate_after_fatal_collect_conflict(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    db_path = tmp_path / "notes.db"
    _create_minimal_db(db_path)
    root = tmp_path / "runs"
    store = FeedStore(root)
    store.persist_phase(_outcome(_article("stable-before-conflict")))
    failed = store.persist_phase(
        _outcome(status="source_failed", reason="source_content_changed")
    )
    assert failed.manifest["candidate_article_ids"] == ["stable-before-conflict"]
    module = _cli_module()
    monkeypatch.setenv("TRADE_DB_PATH", str(db_path))
    monkeypatch.setattr(module, "FeedStore", lambda: FeedStore(root))

    code, payload = _invoke(
        ["wechat-teacher-feed", "show", "--date", RUN_DATE, "--json"],
        capsys,
    )

    assert code == 1
    assert payload["status"] == "source_failed"
    assert payload["reason"] == "source_content_changed"
    assert payload["new_count"] == 0
    assert payload["candidates"] == []


def test_show_quarantines_fatal_source_reason_hidden_by_manifest_reason(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    db_path = tmp_path / "notes.db"
    _create_minimal_db(db_path)
    root = tmp_path / "runs"
    store = FeedStore(root)
    store.persist_phase(_outcome(_article("stable-before-source-conflict")))
    failure = _outcome(status="partial", reason="cached_only_unverified")
    source_results = list(failure.source_results)
    source_results[0] = replace(
        source_results[0],
        status="partial",
        reason="cached_only_unverified",
        refresh_verified=False,
    )
    source_results[1] = replace(
        source_results[1],
        status="source_failed",
        reason="source_identity_changed",
        listed=False,
    )
    store.persist_phase(
        replace(failure, source_results=tuple(source_results), observations=())
    )
    module = _cli_module()
    monkeypatch.setenv("TRADE_DB_PATH", str(db_path))
    monkeypatch.setattr(module, "FeedStore", lambda: FeedStore(root))

    code, payload = _invoke(
        ["wechat-teacher-feed", "show", "--date", RUN_DATE, "--json"],
        capsys,
    )

    assert code == 1
    assert payload["status"] == "source_failed"
    assert payload["reason"] == "source_identity_changed"
    assert payload["candidates"] == []


def test_make_collect_does_not_expand_boolean_make_expression() -> None:
    completed = subprocess.run(
        [
            "make",
            "-n",
            "wechat-teacher-collect",
            "PHASE=post-market",
            "INPUT_BY=codex_automation",
            r"FORCE=$(shell printf MAKE_INJECT >&2)",
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "MAKE_INJECT" not in completed.stdout
    assert "MAKE_INJECT" not in completed.stderr


def test_show_excludes_all_three_teacher_note_identity_matches(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    db_path = tmp_path / "notes.db"
    _create_minimal_db(db_path)
    root = tmp_path / "runs"
    articles = [
        _article("new"),
        _article("id-match"),
        _article("url-match"),
        _article("fallback-match"),
    ]
    FeedStore(root).persist_phase(_outcome(*articles))
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO teachers(name) VALUES ('安静拆主线')")
    teacher_id = conn.execute(
        "SELECT id FROM teachers WHERE name='安静拆主线'"
    ).fetchone()[0]
    by_id, by_url, by_fallback = articles[1:]
    conn.execute(
        """
        INSERT INTO teacher_notes
            (teacher_id, date, title, source_platform, source_article_id,
             content_sha256)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            teacher_id, "2026-07-13", by_id.title, "wechat_mp",
            by_id.source_article_id, by_id.content_sha256,
        ),
    )
    conn.execute(
        """
        INSERT INTO teacher_notes
            (teacher_id, date, title, source_url, content_sha256)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            teacher_id, "2026-07-13", by_url.title, by_url.source_url,
            by_url.content_sha256,
        ),
    )
    conn.execute(
        """
        INSERT INTO teacher_notes
            (teacher_id, date, title, content_sha256)
        VALUES (?, ?, ?, ?)
        """,
        (
            teacher_id, "2026-07-13", by_fallback.title,
            by_fallback.content_sha256,
        ),
    )
    conn.commit()
    recorded_ids = {
        row[0] for row in conn.execute("SELECT id FROM teacher_notes")
    }
    conn.close()
    module = _cli_module()
    monkeypatch.setenv("TRADE_DB_PATH", str(db_path))
    monkeypatch.setattr(module, "FeedStore", lambda: FeedStore(root))

    code, payload = _invoke(
        ["wechat-teacher-feed", "show", "--date", RUN_DATE, "--json"],
        capsys,
    )

    assert code == 0
    assert [item["source_article_id"] for item in payload["candidates"]] == ["new"]
    assert payload["new_count"] == 1
    assert payload["recorded_count"] == 3
    assert {
        item["already_recorded_note_id"] for item in payload["recorded"]
    } == recorded_ids


@pytest.mark.parametrize("tamper", ["manifest", "raw_content", "invalid_utf8"])
def test_show_integrity_failures_are_not_confirmable(
    tamper: str, tmp_path: Path, monkeypatch, capsys
) -> None:
    db_path = tmp_path / "notes.db"
    _create_minimal_db(db_path)
    root = tmp_path / "runs"
    result = FeedStore(root).persist_phase(_outcome(_article("tampered")))
    manifest_path = Path(result.manifest_path or "")
    if tamper == "manifest":
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["reason"] = "tampered"
        manifest_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    elif tamper == "raw_content":
        article = result.manifest["candidate_articles"][0]
        (root / article["raw_content_path"]).write_text(
            "被篡改的原文\n", encoding="utf-8"
        )
    else:
        article = result.manifest["candidate_articles"][0]
        (root / article["raw_content_path"]).write_bytes(b"\xff")
    module = _cli_module()
    monkeypatch.setenv("TRADE_DB_PATH", str(db_path))
    monkeypatch.setattr(module, "FeedStore", lambda: FeedStore(root))

    code, payload = _invoke(
        ["wechat-teacher-feed", "show", "--date", RUN_DATE, "--json"],
        capsys,
    )

    assert code == 1
    assert payload["candidates"] == []
    assert payload["issues"]
    assert payload["status"] in {"partial", "source_failed"}


@pytest.mark.parametrize("linked_component", ["file", "parent"])
def test_show_rejects_symlinked_raw_archive_components(
    linked_component: str, tmp_path: Path, monkeypatch, capsys
) -> None:
    db_path = tmp_path / "notes.db"
    _create_minimal_db(db_path)
    root = tmp_path / "runs"
    result = FeedStore(root).persist_phase(_outcome(_article("linked-raw")))
    article = result.manifest["candidate_articles"][0]
    raw_path = root / article["raw_content_path"]
    if linked_component == "file":
        target = root / "same-content.md"
        target.write_bytes(raw_path.read_bytes())
        raw_path.unlink()
        raw_path.symlink_to(target)
    else:
        articles_dir = raw_path.parent
        target = articles_dir.with_name("articles-real")
        articles_dir.rename(target)
        articles_dir.symlink_to(target, target_is_directory=True)
    module = _cli_module()
    monkeypatch.setenv("TRADE_DB_PATH", str(db_path))
    monkeypatch.setattr(module, "FeedStore", lambda: FeedStore(root))

    code, payload = _invoke(
        ["wechat-teacher-feed", "show", "--date", RUN_DATE, "--json"],
        capsys,
    )

    assert code == 1
    assert payload["status"] == "source_failed"
    assert payload["candidates"] == []
    assert payload["issues"][0]["reason"] == "invalid_raw_content_path"


@pytest.mark.parametrize("linked_parent", ["date", "phase"])
def test_show_symlinked_store_parent_is_machine_readable_failure(
    linked_parent: str, tmp_path: Path, monkeypatch, capsys
) -> None:
    db_path = tmp_path / "notes.db"
    _create_minimal_db(db_path)
    root = tmp_path / "runs"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    marker = outside / "marker.txt"
    marker.write_text("unchanged", encoding="utf-8")
    if linked_parent == "date":
        (root / RUN_DATE).symlink_to(outside, target_is_directory=True)
    else:
        (root / RUN_DATE).mkdir()
        (root / RUN_DATE / "post-market").symlink_to(
            outside, target_is_directory=True
        )
    module = _cli_module()
    monkeypatch.setenv("TRADE_DB_PATH", str(db_path))
    monkeypatch.setattr(module, "FeedStore", lambda: FeedStore(root))

    code, payload = _invoke(
        ["wechat-teacher-feed", "show", "--date", RUN_DATE, "--json"],
        capsys,
    )

    assert code == 1
    assert payload["status"] == "source_failed"
    assert payload["candidates"] == []
    assert any(issue["reason"] == "unsafe_store_path" for issue in payload["issues"])
    assert marker.read_text(encoding="utf-8") == "unchanged"


def test_show_malformed_raw_path_is_machine_readable_failure(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    db_path = tmp_path / "notes.db"
    _create_minimal_db(db_path)
    root = tmp_path / "runs"
    result = FeedStore(root).persist_phase(_outcome(_article("malformed-path")))
    manifest_path = Path(result.manifest_path or "")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["candidate_articles"][0]["raw_content_path"] = "bad\x00path.md"
    manifest["manifest_digest"] = manifest_digest(manifest)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    module = _cli_module()
    monkeypatch.setenv("TRADE_DB_PATH", str(db_path))
    monkeypatch.setattr(module, "FeedStore", lambda: FeedStore(root))

    code, payload = _invoke(
        ["wechat-teacher-feed", "show", "--date", RUN_DATE, "--json"],
        capsys,
    )

    assert code == 1
    assert payload["status"] == "source_failed"
    assert payload["candidates"] == []
    assert payload["issues"][0]["reason"] == "invalid_raw_content_path"


def test_show_exposes_pending_journal_without_recovering_it(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    db_path = tmp_path / "notes.db"
    _create_minimal_db(db_path)
    root = tmp_path / "runs"
    FeedStore(root).persist_phase(_outcome(_article("stable")))

    def fail_before_manifest(label: str) -> None:
        if label == "before_replace:manifest":
            raise RuntimeError("simulated crash")

    with pytest.raises(RuntimeError, match="simulated crash"):
        FeedStore(root, fault_hook=fail_before_manifest).persist_phase(
            _outcome(_article("new-after-crash"))
        )
    journal = root / RUN_DATE / "post-market" / "transaction.json"
    before = journal.read_bytes()
    module = _cli_module()
    monkeypatch.setenv("TRADE_DB_PATH", str(db_path))
    monkeypatch.setattr(module, "FeedStore", lambda: FeedStore(root))

    code, payload = _invoke(
        ["wechat-teacher-feed", "show", "--date", RUN_DATE, "--json"],
        capsys,
    )

    assert code == 1
    assert payload["status"] == "partial"
    assert payload["reason"] == "transaction_pending"
    assert payload["pending_transactions"] == [
        f"{RUN_DATE}/post-market/transaction.json"
    ]
    assert journal.read_bytes() == before


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("skip", 0), ("empty", 0), ("success", 0), ("ok", 0),
        ("partial", 1), ("source_failed", 1), ("source_missing", 1),
        ("content_missing", 1), ("auth_expired", 1), ("blocked", 2),
        ("unknown", 1),
    ],
)
def test_status_maps_to_exit_code(status: str, expected: int) -> None:
    assert _cli_module().status_exit_code(status) == expected


def test_main_propagates_wechat_teacher_feed_exit_code(monkeypatch) -> None:
    import main

    module = _cli_module()
    monkeypatch.setattr(main, "load_config", lambda: {})
    monkeypatch.setattr(module, "handle_command", lambda config, args: 1)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "main.py", "wechat-teacher-feed", "should-run", "--phase",
            "post-market", "--date", RUN_DATE, "--json",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main.main()
    assert exc_info.value.code == 1
