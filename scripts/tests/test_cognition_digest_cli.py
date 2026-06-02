from __future__ import annotations
import argparse
import pytest
import services.cognition_digest as cd_pkg
from services.cognition_digest import RenderedCognitionDigest
from cli import cognition_digest


def test_date_validator_rejects_bad_format():
    with pytest.raises(argparse.ArgumentTypeError):
        cognition_digest._iso_date("2026/06/02")
    assert cognition_digest._iso_date("2026-06-02") == "2026-06-02"


def _args(**kw):
    base = dict(cognition_digest_window="weekly", date=None, dry_run=False, no_llm=True)
    base.update(kw)
    return argparse.Namespace(**base)


def test_empty_window_skips_push(monkeypatch):
    empty = RenderedCognitionDigest("t", "m", [], {"instances": 0}, {})
    monkeypatch.setattr(cd_pkg, "run_window_digest", lambda *a, **k: empty)
    pushed = []
    monkeypatch.setattr(cognition_digest, "_push_to_dingtalk", lambda *a, **k: pushed.append(1))
    cognition_digest.handle_command({}, _args())
    assert pushed == []


def test_nonempty_window_pushes(monkeypatch):
    full = RenderedCognitionDigest("t", "m", [object()], {"instances": 3}, {})
    monkeypatch.setattr(cd_pkg, "run_window_digest", lambda *a, **k: full)
    pushed = []
    monkeypatch.setattr(cognition_digest, "_push_to_dingtalk", lambda *a, **k: pushed.append(1))
    cognition_digest.handle_command({}, _args())
    assert pushed == [1]
