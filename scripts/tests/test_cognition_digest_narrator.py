from __future__ import annotations
from services.cognition_digest import narrator
from services.cognition_digest.scorer import ScoredCognition


def _sc(cid="c1", *, category="signal", heat=3, consensus=2, is_new=True):
    return ScoredCognition(
        cognition_id=cid, title=f"认知{cid}", category=category, sub_category=None,
        pattern="范式", confidence=0.6, heat=heat, consensus=consensus,
        is_new=is_new, score=1.0,
    )


def test_no_llm_falls_back_to_template():
    out = narrator.generate_suggestions([_sc()], no_llm=True, llm_runner=None)
    assert out["_llm_used"] is False
    assert out["system_suggestions"]  # 模板非空
    assert out["direction_suggestions"]


def test_llm_clean_output_accepted():
    def runner(prompt, payload):
        return {"system_suggestions": ["加强认知验证机制"],
                "direction_suggestions": ["聚焦高共识方向"]}
    out = narrator.generate_suggestions([_sc()], no_llm=False, llm_runner=runner)
    assert out["_llm_used"] is True
    assert "加强认知验证机制" in out["system_suggestions"]


def test_llm_redline_bullet_dropped():
    def runner(prompt, payload):
        return {"system_suggestions": ["建议买入算力龙头", "完善复盘节奏"],
                "direction_suggestions": ["设置止损位 12 元"]}
    out = narrator.generate_suggestions([_sc()], no_llm=False, llm_runner=runner)
    # "买入" / "止损位" 命中红线被丢；system 保留 1 条，direction 全空 → 模板兜底
    assert "建议买入算力龙头" not in out["system_suggestions"]
    assert "完善复盘节奏" in out["system_suggestions"]
    assert all("止损位" not in b for b in out["direction_suggestions"])
    assert out["direction_suggestions"]  # 空段走模板兜底


def test_llm_exception_falls_back():
    def runner(prompt, payload):
        raise RuntimeError("boom")
    out = narrator.generate_suggestions([_sc()], no_llm=False, llm_runner=runner)
    assert out["_llm_used"] is False
    assert out["system_suggestions"]  # 模板兜底


def test_llm_bad_type_falls_back():
    out = narrator.generate_suggestions([_sc()], no_llm=False, llm_runner=lambda p, d: None)
    assert out["_llm_used"] is False


def test_llm_missing_key_falls_back():
    # 缺键 / 非 list → L1 结构不符，整段模板兜底（不部分采纳，codex 中项回归）
    def runner(prompt, payload):
        return {"system_suggestions": "不是列表"}  # 缺 direction_suggestions 且类型错
    out = narrator.generate_suggestions([_sc()], no_llm=False, llm_runner=runner)
    assert out["_llm_used"] is False
    assert out["system_suggestions"]  # 模板兜底非空


def test_llm_non_string_bullets_dropped():
    # list 内含非字符串元素 → 逐条丢弃，不渲染 "None"/dict 串（codex 中项回归）
    def runner(prompt, payload):
        return {"system_suggestions": [None, "完善复盘节奏", {"x": 1}],
                "direction_suggestions": [123]}
    out = narrator.generate_suggestions([_sc()], no_llm=False, llm_runner=runner)
    assert out["system_suggestions"] == ["完善复盘节奏"]  # 非字符串全丢
    assert "None" not in "".join(out["system_suggestions"])
    assert out["direction_suggestions"]  # direction 全非字符串 → 清空 → 模板兜底
