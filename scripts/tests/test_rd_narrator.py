"""research_digest.narrator：无 LLM 原样；三级 fallback（M3）；不改事实（H6）。"""
from __future__ import annotations

from services.research_digest import narrator

US = [
    {"ticker": "NVDA", "firm": "Morgan Stanley", "action": "up", "to_grade": "Buy"},
    {"ticker": "AMD", "firm": "Goldman", "action": "down", "to_grade": "Hold"},
]


def test_no_runner_returns_unchanged():
    assert narrator.narrate([dict(x) for x in US], llm_runner=None) == US


def test_llm_backfills_theme_and_one_liner():
    def runner(prompt, payload):
        return {"items": [{"id": 0, "theme": "AI算力", "one_liner": "上调"},
                          {"id": 1, "theme": "半导体", "one_liner": "下调"}]}
    out = narrator.narrate([dict(x) for x in US], llm_runner=runner)
    assert out[0]["theme"] == "AI算力" and out[0]["one_liner"] == "上调"
    assert out[0]["ticker"] == "NVDA"  # 事实不变


def test_extra_returned_item_drops_all_narration():
    """L2：返回 3 条 > 输入 2 条（幻觉新增）→ 丢全部 narration。"""
    def runner(p, pl):
        return {"items": [{"id": 0, "theme": "x", "one_liner": "y"},
                          {"id": 1, "theme": "a", "one_liner": "b"},
                          {"id": 2, "theme": "幻觉", "one_liner": "编造"}]}
    out = narrator.narrate([dict(x) for x in US], llm_runner=runner)
    assert all("theme" not in it and "one_liner" not in it for it in out)


def test_duplicate_id_drops_all_narration():
    """M3 严格化：返回重复 id（[0,0,1] for 2 items）→ 整批不可信，全量降级。"""
    def runner(p, pl):
        return {"items": [{"id": 0, "theme": "x", "one_liner": "y"},
                          {"id": 0, "theme": "dup", "one_liner": "dup"},
                          {"id": 1, "theme": "a", "one_liner": "b"}]}
    out = narrator.narrate([dict(x) for x in US], llm_runner=runner)
    assert all("theme" not in it and "one_liner" not in it for it in out)


def test_missing_field_that_item_drops_both_others_kept():
    """M2：id0 缺 one_liner → 该条 theme/one_liner 都不写（退模板，不留半截）；id1 完整保留。"""
    def runner(p, pl):
        return {"items": [{"id": 0, "theme": "AI"}, {"id": 1, "theme": "半导", "one_liner": "降"}]}
    out = narrator.narrate([dict(x) for x in US], llm_runner=runner)
    assert "theme" not in out[0] and "one_liner" not in out[0]
    assert out[1]["theme"] == "半导" and out[1]["one_liner"] == "降"


def test_float_id_rejected_drops_all():
    """M1：float id（0.5）非纯 int → 畸形 → len 不符 → 全量降级。"""
    def runner(p, pl):
        return {"items": [{"id": 0.5, "theme": "x", "one_liner": "y"},
                          {"id": 1, "theme": "a", "one_liner": "b"}]}
    out = narrator.narrate([dict(x) for x in US], llm_runner=runner)
    assert all("theme" not in it for it in out)


def test_runner_exception_returns_unchanged():
    def runner(p, pl):
        raise RuntimeError("timeout")
    assert narrator.narrate([dict(x) for x in US], llm_runner=runner) == US


def test_malformed_result_returns_unchanged():
    assert narrator.narrate([dict(x) for x in US], llm_runner=lambda p, pl: "not a dict") == US


def test_llm_cannot_overwrite_facts():
    """LLM 自带 ticker 被忽略，只取 theme/one_liner（事实为主键）。"""
    def runner(p, pl):
        return {"items": [{"id": 0, "ticker": "FAKE", "theme": "x", "one_liner": "y"},
                          {"id": 1, "theme": "a", "one_liner": "b"}]}
    out = narrator.narrate([dict(x) for x in US], llm_runner=runner)
    assert out[0]["ticker"] == "NVDA"


# ---- build_antigravity_runner 健壮性（Antigravity CLI，契约「任何失败降级」须真成立）----

def test_build_runner_non_int_timeout_does_not_crash(monkeypatch):
    """LLM_TIMEOUT_SECONDS 非整数:构造期(在 narrate try/except 之外)不得崩,回退 180s。"""
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "abc")
    runner = narrator.build_antigravity_runner()  # 此前会 ValueError 崩 CLI
    assert callable(runner)


def test_build_runner_permission_error_degrades_to_none(monkeypatch):
    """agy 不可执行(PermissionError,OSError 子类)→ runner 返 None 而非抛出。"""
    import subprocess

    def boom(*a, **k):
        raise PermissionError("not executable")
    monkeypatch.setattr(subprocess, "run", boom)
    runner = narrator.build_antigravity_runner()
    assert runner("prompt", [{"id": 0}]) is None


def test_build_runner_nonzero_returncode_degrades_to_none(monkeypatch):
    import subprocess

    class R:
        returncode = 1
        stdout = "whatever"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    runner = narrator.build_antigravity_runner()
    assert runner("prompt", [{"id": 0}]) is None


def test_build_runner_uses_antigravity_print_mode(monkeypatch):
    import subprocess

    seen = {}

    class R:
        returncode = 0
        stdout = '{"items":[]}'

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return R()

    monkeypatch.setenv("ANTIGRAVITY_BIN", "agy")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "77")
    monkeypatch.setattr(subprocess, "run", fake_run)

    runner = narrator.build_antigravity_runner()
    assert runner("prompt", []) == {"items": []}
    assert seen["cmd"][0] == "agy"
    assert "--prompt" in seen["cmd"]
    assert "--print-timeout" in seen["cmd"]
    assert "77s" in seen["cmd"]


def test_build_runner_passes_antigravity_model(monkeypatch):
    import subprocess

    seen = {}

    class R:
        returncode = 0
        stdout = '{"items":[]}'

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return R()

    monkeypatch.setenv("ANTIGRAVITY_BIN", "agy")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setattr(subprocess, "run", fake_run)

    runner = narrator.build_antigravity_runner()
    assert runner("prompt", []) == {"items": []}
    assert "--model" in seen["cmd"]
    assert "test-model" in seen["cmd"]


# ---- _parse_json 边界（阶段3 接线后补覆盖）----

def test_parse_json_empty_and_none():
    assert narrator._parse_json("") is None
    assert narrator._parse_json(None) is None


def test_parse_json_no_brace():
    assert narrator._parse_json("just text no json") is None


def test_parse_json_direct():
    assert narrator._parse_json('{"items":[]}') == {"items": []}


def test_parse_json_strips_markdown_fence():
    assert narrator._parse_json('```json\n{"items":[{"id":0}]}\n```') == {"items": [{"id": 0}]}


def test_parse_json_balanced_extraction_with_trailing_garbage():
    """前导/尾部垃圾:平衡括号提取首个 {...}。"""
    assert narrator._parse_json('noise {"a":1} more}}}') == {"a": 1}


def test_parse_json_unbalanced_returns_none():
    """截断未闭合(depth 永不归零)→ None,不抛。"""
    assert narrator._parse_json('{"a":{"b":1') is None


# ---- prompt 收紧（真实采集复核：LLM 曾凭空编降级理由，根因=prompt 未禁因果）----

def test_prompt_forbids_inventing_rationale():
    """one_liner 须显式限定『只复述板块归类+方向』且『严禁编造理由/因果』（A股/美股都生效）。"""
    for mkt in ("us", "cn"):
        p = narrator._build_prompt(mkt)
        assert "严禁编造" in p and "理由" in p and "因果" in p
        assert "板块归类" in p
