from __future__ import annotations

import argparse
from dataclasses import dataclass

import pytest


@dataclass
class _Rec:
    title: str = "title"
    markdown: str = "markdown"


def test_recommend_finishes_trace_when_push_raises(monkeypatch):
    from cli import recommend

    finishes = []
    interaction = object()

    class Conn:
        def close(self):
            pass

    monkeypatch.setattr(recommend.tracing, "begin", lambda *a, **k: interaction)
    monkeypatch.setattr(recommend.tracing, "finish", lambda inter, **k: finishes.append((inter, k)))
    monkeypatch.setattr(recommend, "get_connection", lambda: Conn())
    monkeypatch.setattr(recommend, "run_recommend", lambda *a, **k: _Rec())

    def push(*args, **kwargs):
        raise RuntimeError("push failed")

    monkeypatch.setattr(recommend, "_push_to_dingtalk", push)

    with pytest.raises(RuntimeError, match="push failed"):
        recommend._run("daily", argparse.Namespace(lookback_days=3, top_k=5, dry_run=False))

    assert finishes == [(interaction, {"error": "push failed"})]
