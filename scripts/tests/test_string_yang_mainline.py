"""串阳首阴：主线融合判断。"""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace

from db.migrate import migrate
from services.string_yang import constants as C
from services.volume_concentration import repo as vc_repo


class _Registry:
    def __init__(self) -> None:
        self.calls = []

    def call(self, name: str, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        if name == "get_ths_member":
            return SimpleNamespace(success=True, data=[
                {"con_code": "600010.SH", "index_name": "CPO"},
                {"con_code": "600011.SH", "index_name": "CPO"},
                {"con_code": "600012.SH", "index_name": "机器人"},
            ])
        if name == "get_concept_moneyflow_ths":
            return SimpleNamespace(success=True, data=[
                {"name": "CPO", "net_amount": 9_000_000_000},
                {"name": "机器人", "net_amount": 4_000_000_000},
                {"name": "未知概念", "net_amount": 3_000_000_000},
            ])
        raise AssertionError(f"unexpected provider call: {name}")


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    migrate(conn)
    vc_repo.save_concentration(conn, {
        "date": "2026-06-09",
        "top_n": 20,
        "total_amount_billion": 1000,
        "sector_summary": [
            {"industry": "半导体", "amount_billion": 150, "amount_share": 0.15},
            {"industry": "银行", "amount_billion": 80, "amount_share": 0.08},
        ],
        "stocks": [],
        "source": {"provider": "pytest"},
    })
    conn.execute(
        """INSERT INTO teacher_notes
           (date, title, source_type, input_by, core_view, sectors, raw_content)
           VALUES (?, ?, 'text', 'pytest', ?, ?, ?)""",
        (
            "2026-06-09",
            "老师盘后",
            "今天主线在算力链，CPO 分支比泛科技更集中。",
            '["算力","CPO"]',
            "CPO 成交额集中，机器人只能看轮动。",
        ),
    )
    return conn


def test_judge_mainline_uses_llm_but_only_accepts_provided_sectors_and_concepts() -> None:
    from services.string_yang import mainline

    def runner(prompt, payload):
        assert "老师观点" in prompt
        assert payload["volume_sectors"][0]["industry"] == "半导体"
        assert payload["teacher_notes"][0]["title"] == "老师盘后"
        return {
            "main_l2": ["半导体", "不存在行业"],
            "main_concepts": ["CPO", "不存在概念"],
            "watch_only": ["机器人"],
            "evidence": ["半导体成交额居前", "老师强调 CPO 分支"],
            "confidence": 0.82,
        }

    judgment = mainline.judge_mainline(
        _conn(),
        _Registry(),
        "2026-06-09",
        top_k=2,
        top_concepts=3,
        teacher_lookback_days=7,
        use_llm=True,
        llm_runner=runner,
    )

    assert judgment.main_sectors == ["半导体"]
    assert judgment.main_concepts == ["CPO"]
    assert judgment.watch_only == ["机器人"]
    assert judgment.status == "llm"
    assert judgment.confidence == 0.82
    assert "600010" in judgment.stock_concept_map


def test_judge_mainline_falls_back_to_volume_sectors_when_llm_returns_no_valid_mainline() -> None:
    from services.string_yang import mainline

    judgment = mainline.judge_mainline(
        _conn(),
        _Registry(),
        "2026-06-09",
        top_k=1,
        top_concepts=3,
        teacher_lookback_days=7,
        use_llm=True,
        llm_runner=lambda _prompt, _payload: {"main_l2": ["不存在行业"], "main_concepts": []},
    )

    assert judgment.status == "llm_fallback"
    assert judgment.main_sectors == ["半导体"]
    assert judgment.main_concepts == []
    assert "llm_mainline_empty" in judgment.source_errors


def test_judge_mainline_prefetches_only_ranked_concept_window(monkeypatch) -> None:
    from services.string_yang import mainline

    monkeypatch.setattr(C, "CONCEPT_PREFETCH_MIN", 2)
    monkeypatch.setattr(C, "CONCEPT_PREFETCH_MULTIPLIER", 1)
    registry = _Registry()

    mainline.judge_mainline(
        _conn(),
        registry,
        "2026-06-09",
        top_k=1,
        top_concepts=1,
        teacher_lookback_days=7,
        use_llm=True,
        llm_runner=lambda _prompt, _payload: {"main_l2": ["半导体"], "main_concepts": ["CPO"]},
    )

    ths_calls = [call for call in registry.calls if call[0] == "get_ths_member"]
    assert ths_calls == [
        ("get_ths_member", ("2026-06-09",), {"concept_names": ["CPO", "机器人"]})
    ]


def test_judge_mainline_call_failure_labeled_and_retried_once() -> None:
    """runner 静默失败(返 None) → 标 llm_call_failed(≠llm_mainline_empty)且非超时重试 1 次。"""
    from services.string_yang import mainline

    calls = {"n": 0}

    def runner(_prompt, _payload):
        calls["n"] += 1
        runner.last_diagnostics = {"reason": "empty_stdout"}
        return None

    runner.last_diagnostics = None
    judgment = mainline.judge_mainline(
        _conn(), _Registry(), "2026-06-09",
        top_k=1, top_concepts=3, teacher_lookback_days=7,
        use_llm=True, llm_runner=runner,
    )

    assert calls["n"] == 2  # 首次失败 + 非超时重试 1 次
    assert judgment.status == "llm_fallback"
    assert "llm_call_failed" in judgment.source_errors
    assert "llm_mainline_empty" not in judgment.source_errors
    assert judgment.main_sectors == ["半导体"]  # 降级成交额 Top-K 仍生效


def test_judge_mainline_timeout_failure_not_retried() -> None:
    """超时失败不重试（重试大概率再超时,拖长盘后任务链）。"""
    from services.string_yang import mainline

    calls = {"n": 0}

    def runner(_prompt, _payload):
        calls["n"] += 1
        runner.last_diagnostics = {"reason": "timeout"}
        return None

    runner.last_diagnostics = None
    judgment = mainline.judge_mainline(
        _conn(), _Registry(), "2026-06-09",
        top_k=1, top_concepts=3, teacher_lookback_days=7,
        use_llm=True, llm_runner=runner,
    )

    assert calls["n"] == 1
    assert "llm_call_failed" in judgment.source_errors


def test_teacher_notes_payload_slimmed() -> None:
    """teacher_notes 喂 LLM 的 payload 受常量约束：条数 ≤ TEACHER_NOTES_LIMIT、单段 ≤ TEACHER_SNIPPET_CHARS。"""
    from services.string_yang import mainline

    conn = _conn()
    for i in range(C.TEACHER_NOTES_LIMIT + 5):
        conn.execute(
            """INSERT INTO teacher_notes
               (date, title, source_type, input_by, core_view, sectors, raw_content)
               VALUES (?, ?, 'text', 'pytest', ?, '[]', ?)""",
            ("2026-06-09", f"观点{i}", "长" * (C.TEACHER_SNIPPET_CHARS + 80), "尾" * 500),
        )
    conn.commit()

    captured = {}

    def runner(_prompt, payload):
        captured["notes"] = payload["teacher_notes"]
        return {"main_l2": ["半导体"], "main_concepts": []}

    mainline.judge_mainline(
        conn, _Registry(), "2026-06-09",
        top_k=1, top_concepts=3, teacher_lookback_days=7,
        use_llm=True, llm_runner=runner,
    )

    notes = captured["notes"]
    assert len(notes) <= C.TEACHER_NOTES_LIMIT
    assert all(len(s) <= C.TEACHER_SNIPPET_CHARS for n in notes for s in n["snippets"])
