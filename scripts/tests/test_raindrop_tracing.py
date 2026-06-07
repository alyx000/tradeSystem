from __future__ import annotations

import pytest


def test_tool_span_passes_exception_info_to_span_exit(monkeypatch):
    from utils import raindrop_tracing

    exit_args = []

    class FakeSpanContext:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb):
            exit_args.append((exc_type, exc, tb))
            return False

    class FakeRaindrop:
        def tool_span(self, name):
            return FakeSpanContext()

    monkeypatch.setattr(raindrop_tracing, "_ensure_init", lambda: True)
    monkeypatch.setattr(raindrop_tracing, "_raindrop", FakeRaindrop())

    with pytest.raises(ValueError, match="boom"):
        with raindrop_tracing.tool_span("x"):
            raise ValueError("boom")

    assert exit_args
    assert exit_args[0][0] is ValueError
    assert isinstance(exit_args[0][1], ValueError)
    assert exit_args[0][2] is not None


def test_tool_span_record_methods_are_best_effort(monkeypatch):
    from utils import raindrop_tracing

    class BadSpan:
        def record_input(self, data):
            raise RuntimeError("record input failed")

        def record_output(self, data):
            raise RuntimeError("record output failed")

        def set_properties(self, props):
            raise RuntimeError("props failed")

    class FakeSpanContext:
        def __enter__(self):
            return BadSpan()

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeRaindrop:
        def tool_span(self, name):
            return FakeSpanContext()

    monkeypatch.setattr(raindrop_tracing, "_ensure_init", lambda: True)
    monkeypatch.setattr(raindrop_tracing, "_raindrop", FakeRaindrop())

    with raindrop_tracing.tool_span("x") as span:
        span.record_input({"x": 1})
        span.record_output({"ok": True})
        span.set_properties({"p": 1})


def test_finish_passes_error_to_interaction(monkeypatch):
    from utils import raindrop_tracing

    calls = []

    class Interaction:
        def finish(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(raindrop_tracing, "_raindrop", None)
    monkeypatch.setattr(raindrop_tracing, "_initialized", False)

    raindrop_tracing.finish(Interaction(), output="ignored", error="push failed")

    assert calls == [{"output": "Error: push failed", "error": "push failed"}]
