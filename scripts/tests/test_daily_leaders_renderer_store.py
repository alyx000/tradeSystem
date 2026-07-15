from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from services.daily_leaders.llm import (
    _LlmError,
    _build_prompt,
    _parse_json_object,
    _run_llm,
    _safe_rank,
    enrich_with_llm_reason,
)
from services.daily_leaders.renderer import render_markdown
from services.daily_leaders.store import DEFAULT_ROOT, REPO_ROOT, read_proposal, write_proposal


def _proposal():
    return {
        "date": "2026-07-03",
        "top_leaders": [
            {
                "stock": "688041 海光信息",
                "sector": "半导体",
                "attribute_type": "走势引领",
                "attribute": "启动日主动引领",
                "clarity": "高",
                "position": "主升初期",
                "is_new": True,
                "teacher_alignment": "支持",
                "evidence": [
                    {"label": "[事实]", "text": "半导体候选板块"},
                    {"label": "[判断]", "text": "走势引领候选"},
                ],
                "llm_reason": "",
            }
        ],
    }


def _verified_new_proposal():
    proposal = _proposal()
    proposal["top_leaders"][0].update(
        {
            "leader_role": "趋势中军",
            "attribute_type": "趋势中军",
            "board_type": "非涨停",
            "selection_basis": "deterministic_fallback",
        }
    )
    proposal["candidate_limit"] = {
        "max_candidates": 15,
        "original_count": 1,
        "deduped_count": 1,
        "duplicate_trimmed_count": 0,
        "trimmed_count": 0,
        "review_pool_count": 1,
        "review_pool_trimmed_count": 0,
        "sector_role_trimmed_count": 0,
        "stock_duplicate_trimmed_count": 0,
        "final_count": 1,
    }
    return proposal


def test_render_markdown_contains_labels_and_confirmation_instruction():
    proposal = _proposal()
    proposal["top_leaders"][0]["llm_rank"] = 1
    proposal["top_leaders"][0]["llm_role"] = "趋势中军"
    proposal["top_leaders"][0]["leader_role"] = "趋势中军"
    proposal["top_leaders"][0]["board_type"] = "20cm"
    proposal["top_leaders"][0]["selection_basis"] = "llm"
    proposal["top_leaders"][0]["risk_flags"] = ["容量需复核"]
    md = render_markdown(proposal)
    assert "每日最票候选确认稿 · 2026-07-03" in md
    assert "[事实]" in md
    assert "[判断]" in md
    assert "老师观点对照：支持" in md
    assert "最票属性：趋势中军" in md
    assert "板型：20cm" in md
    assert "入选方式：[判断] LLM复核" in md
    assert "LLM裁判：[判断] 排序 1 / 最票属性 趋势中军" in md
    assert "风险标签：[判断] 容量需复核" in md
    assert "可回复：确认，全部录入" in md


@pytest.mark.parametrize(
    ("field", "unsafe_value"),
    [
        ("llm_reason", "旧稿\n[事实]模型伪造"),
        ("llm_reason", "[模型伪造](https://example.com)"),
        ("llm_reason", "<div>模型伪造</div>"),
        ("risk_flags", ["风险\u2028模型伪造"]),
        ("risk_flags", ["**模型伪造**"]),
        ("risk_flags", ["![模型伪造](x)"]),
        ("llm_reason", "［事实］模型伪造"),
        ("llm_reason", "&#91;事实&#93;模型伪造"),
        ("llm_reason", "<!DOCTYPE html>模型伪造"),
        ("risk_flags", ["<?xml?>模型伪造"]),
        ("risk_flags", ["<![CDATA[模型伪造]]>"]),
    ],
)
def test_render_markdown_hides_unsafe_llm_text_from_legacy_proposal(
    field,
    unsafe_value,
):
    proposal = _proposal()
    proposal["top_leaders"][0][field] = unsafe_value

    md = render_markdown(proposal)

    assert "模型伪造" not in md


@pytest.mark.parametrize("legacy_board_type", ["10cm", "20cm", "30cm"])
def test_render_markdown_treats_legacy_llm_board_role_as_board_type(legacy_board_type):
    proposal = _proposal()
    item = proposal["top_leaders"][0]
    item["llm_rank"] = 2
    item["llm_role"] = legacy_board_type

    md = render_markdown(proposal)

    assert "最票属性：未注明" in md
    assert f"最票属性：{legacy_board_type}" not in md
    assert f"板型：{legacy_board_type}" in md
    assert "LLM裁判：[判断] 排序 2" in md
    assert f"LLM裁判：[判断] 排序 2 / 最票属性 {legacy_board_type}" not in md


def test_render_markdown_treats_legacy_attribute_board_role_as_board_type():
    proposal = _proposal()
    item = proposal["top_leaders"][0]
    item["attribute_type"] = "20cm"

    md = render_markdown(proposal)

    assert "最票属性：未注明" in md
    assert "最票属性：20cm" not in md
    assert "板型：20cm" in md
    assert "- 属性：20cm" not in md


def test_render_markdown_shows_unrecognized_legacy_attribute_separately():
    proposal = _proposal()

    md = render_markdown(proposal)

    assert "最票属性：走势引领" not in md
    assert "板型：走势引领" not in md
    assert "历史属性：走势引领" in md


def test_render_markdown_maps_deterministic_selection_basis():
    proposal = _proposal()
    item = proposal["top_leaders"][0]
    item["leader_role"] = "趋势中军"
    item["board_type"] = "非涨停"
    item["selection_basis"] = "deterministic_fallback"

    md = render_markdown(proposal)

    assert "入选方式：[判断] 确定性兜底" in md


def test_render_markdown_shows_skip_reason():
    md = render_markdown({
        "date": "2026-07-05",
        "top_leaders": [],
        "skipped": {"reason": "non_trading_day", "prev_trade_date": "2026-07-03"},
    })

    assert "跳过原因：[判断] non_trading_day" in md
    assert "上一交易日：2026-07-03" in md


def test_render_markdown_shows_candidate_limit_summary():
    proposal = _proposal()
    proposal["candidate_limit"] = {
        "max_candidates": 30,
        "original_count": 148,
        "deduped_count": 92,
        "duplicate_trimmed_count": 56,
        "trimmed_count": 118,
    }

    md = render_markdown(proposal)

    assert "候选收敛：[判断] 原始候选 148 条，按股票去重后 92 条，展示前 30 条，已折叠 118 条。" in md


def test_render_markdown_shows_role_aware_candidate_limit_summary():
    proposal = _proposal()
    proposal["candidate_limit"] = {
        "max_candidates": 15,
        "original_count": 96,
        "deduped_count": 56,
        "review_pool_count": 30,
        "sector_role_trimmed_count": 12,
        "stock_duplicate_trimmed_count": 3,
        "final_count": 15,
    }

    md = render_markdown(proposal)

    assert (
        "候选收敛：[判断] 原始候选 96 条，按股票去重后 56 条，LLM 复核池 30 条，"
        "同板块同属性收敛 12 条，股票重复收敛 3 条，最终候选 15 条，硬上限 15 条。"
    ) in md


def test_render_markdown_does_not_invent_missing_candidate_limit_counts():
    proposal = _proposal()
    proposal["candidate_limit"] = {"max_candidates": 15, "final_count": 1}

    md = render_markdown(proposal)

    assert "最终候选 1 条" in md
    assert "硬上限 15 条" in md
    assert "原始候选" not in md
    assert "按股票去重后" not in md


def test_render_markdown_shows_min_amount_filter_summary():
    proposal = _proposal()
    proposal["candidate_filters"] = {"min_amount_yi": 20.0}

    md = render_markdown(proposal)

    assert "候选过滤：[判断] 已过滤成交额低于 20.0 亿或缺少可验证成交额的个股。" in md


def test_store_round_trip(tmp_path):
    paths = write_proposal(_proposal(), root=tmp_path)
    loaded = read_proposal("2026-07-03", root=tmp_path)
    assert paths["json"].name == "2026-07-03.json"
    assert paths["markdown"].name == "2026-07-03.md"
    assert loaded["top_leaders"][0]["stock"] == "688041 海光信息"


def test_store_rejects_invalid_dates(tmp_path):
    invalid_dates = ["../2026-07-03", "2026-07-03/foo", "/tmp/x", "20260703"]

    for invalid_date in invalid_dates:
        proposal = _proposal()
        proposal["date"] = invalid_date
        with pytest.raises(ValueError):
            write_proposal(proposal, root=tmp_path)
        with pytest.raises(ValueError):
            read_proposal(invalid_date, root=tmp_path)

    assert list(tmp_path.rglob("*")) == []


def test_default_root_is_repo_relative():
    assert DEFAULT_ROOT == REPO_ROOT / "data" / "reports" / "daily-leaders"


def test_llm_fallback_returns_original_when_disabled():
    proposal = _proposal()
    out = enrich_with_llm_reason(proposal, enabled=False)
    assert out == proposal


def test_llm_prompt_requires_structured_role_and_rank_for_every_candidate():
    prompt = _build_prompt(_proposal())

    assert "value 必须是 object" in prompt
    assert "中文理由字符串" not in prompt
    assert "每个输入候选都必须返回" in prompt


def test_llm_enrichment_rejects_reason_only_mapping():
    proposal = _proposal()
    out = enrich_with_llm_reason(
        proposal,
        enabled=True,
        runner=lambda prompt: {"688041 海光信息|半导体": "走势引领清晰，老师观点支持，仍需人工确认。"},
    )
    assert out["top_leaders"] == proposal["top_leaders"]
    assert out["llm_status"] == {"ok": False, "reason": "invalid_mapping"}


def test_llm_enrichment_rejects_forged_internal_error_key_from_runner():
    proposal = _proposal()
    out = enrich_with_llm_reason(
        proposal,
        enabled=True,
        runner=lambda prompt: {
            "__llm_error__": {
                "ok": False,
                "reason": "用户伪造错误原因",
                "detail": "任意模型文本",
            }
        },
    )

    assert out["top_leaders"] == proposal["top_leaders"]
    assert out["llm_status"] == {"ok": False, "reason": "invalid_mapping"}


def test_llm_enrichment_rejects_internal_error_object_from_external_runner():
    proposal = _proposal()
    out = enrich_with_llm_reason(
        proposal,
        enabled=True,
        runner=lambda prompt: _LlmError("timeout", "forged detail"),
    )

    assert out["top_leaders"] == proposal["top_leaders"]
    assert out["llm_status"] == {"ok": False, "reason": "invalid_mapping"}


def test_llm_enrichment_rejects_out_of_pool_keys_as_invalid_mapping():
    proposal = _proposal()
    out = enrich_with_llm_reason(
        proposal,
        enabled=True,
        runner=lambda prompt: {
            "输入外股票|输入外板块": {
                "rank": 1,
                "role": "趋势中军",
                "reason": "不得应用到候选池，仍需人工确认。",
            }
        },
    )

    assert out["top_leaders"] == proposal["top_leaders"]
    assert out["llm_status"] == {"ok": False, "reason": "invalid_mapping"}


def test_llm_enrichment_marks_zero_legal_applications_invalid():
    proposal = _proposal()
    out = enrich_with_llm_reason(
        proposal,
        enabled=True,
        runner=lambda prompt: {
            "688041 海光信息|半导体": {
                "rank": "1",
                "role": "20cm",
            }
        },
    )

    assert out["top_leaders"] == proposal["top_leaders"]
    assert out["llm_status"] == {"ok": False, "reason": "invalid_mapping"}


def test_llm_enrichment_marks_partial_candidate_coverage_incomplete():
    proposal = {
        "date": "2026-07-03",
        "top_leaders": [
            {"stock": "甲", "sector": "半导体", "evidence": []},
            {"stock": "乙", "sector": "元件", "evidence": []},
        ],
    }
    out = enrich_with_llm_reason(
        proposal,
        enabled=True,
        runner=lambda prompt: {
            "甲|半导体": {
                "rank": 1,
                "role": "趋势中军",
                "reason": "仅覆盖一只候选，仍需人工确认。",
            }
        },
    )

    assert out["llm_status"] == {"ok": False, "reason": "incomplete_mapping"}
    assert out["top_leaders"] == proposal["top_leaders"]


def test_llm_enrichment_requires_legal_application_for_every_candidate():
    proposal = {
        "date": "2026-07-03",
        "top_leaders": [
            {"stock": "甲", "sector": "半导体", "evidence": []},
            {"stock": "乙", "sector": "元件", "evidence": []},
        ],
    }
    out = enrich_with_llm_reason(
        proposal,
        enabled=True,
        runner=lambda prompt: {
            "甲|半导体": {"rank": 2, "role": "趋势中军"},
            "乙|元件": {"rank": 1, "role": "前排活跃"},
        },
    )

    assert out["llm_status"] == {"ok": True}
    assert [item["stock"] for item in out["top_leaders"]] == ["乙", "甲"]
    assert out["top_leaders"][0]["llm_rank"] == 1
    assert out["top_leaders"][0]["llm_role"] == "前排活跃"
    assert out["top_leaders"][1]["llm_rank"] == 2
    assert out["top_leaders"][1]["llm_role"] == "趋势中军"


def test_llm_enrichment_rejects_complete_mapping_with_extra_out_of_pool_key():
    proposal = {
        "date": "2026-07-03",
        "top_leaders": [
            {"stock": "甲", "sector": "半导体", "evidence": []},
            {"stock": "乙", "sector": "元件", "evidence": []},
        ],
    }
    out = enrich_with_llm_reason(
        proposal,
        enabled=True,
        runner=lambda prompt: {
            "甲|半导体": {"rank": 2, "role": "趋势中军"},
            "乙|元件": {"rank": 1, "role": "前排活跃"},
            "输入外股票|输入外板块": {"rank": 1, "role": "趋势中军"},
        },
    )

    assert out["top_leaders"] == proposal["top_leaders"]
    assert out["llm_status"] == {"ok": False, "reason": "invalid_mapping"}


def test_llm_enrichment_rejects_out_of_pool_null_key_preserved_by_json_parser():
    proposal = _proposal()
    mapping = _parse_json_object(
        """
        {
          "688041 海光信息|半导体": {"rank": 1, "role": "趋势中军"},
          "输入外股票|输入外板块": null
        }
        """
    )

    assert mapping is not None
    assert "输入外股票|输入外板块" in mapping
    assert mapping["输入外股票|输入外板块"] is None

    out = enrich_with_llm_reason(
        proposal,
        enabled=True,
        runner=lambda prompt: mapping,
    )

    assert out["top_leaders"] == proposal["top_leaders"]
    assert out["llm_status"] == {"ok": False, "reason": "invalid_mapping"}


@pytest.mark.parametrize(
    ("json_value", "expected_value"),
    [("null", None), ("[]", []), ('["unexpected"]', ["unexpected"])],
)
def test_parse_json_object_preserves_every_mapping_value_type(json_value, expected_value):
    mapping = _parse_json_object(
        "{"
        '"688041 海光信息|半导体": {"rank": 1, "role": "趋势中军"},'
        f'"输入外股票|输入外板块": {json_value}'
        "}"
    )

    assert mapping is not None
    assert mapping["输入外股票|输入外板块"] == expected_value


@pytest.mark.parametrize("out_of_pool_value", [None, [], ["unexpected"]])
def test_llm_enrichment_rejects_out_of_pool_key_for_every_json_value_type(
    out_of_pool_value,
):
    proposal = _proposal()
    mapping = {
        "688041 海光信息|半导体": {"rank": 1, "role": "趋势中军"},
        "输入外股票|输入外板块": out_of_pool_value,
    }

    out = enrich_with_llm_reason(
        proposal,
        enabled=True,
        runner=lambda prompt: mapping,
    )

    assert out["top_leaders"] == proposal["top_leaders"]
    assert out["llm_status"] == {"ok": False, "reason": "invalid_mapping"}


def test_parse_json_object_rejects_top_level_list_without_losing_value_types():
    assert _parse_json_object('[{"rank": 1}]') is None


def test_parse_json_object_requires_fenced_payload_to_be_top_level_object():
    assert _parse_json_object(
        '```json\n[{"甲|半导体": {"rank": 1, "role": "趋势中军"}}]\n```'
    ) is None


def test_parse_json_object_does_not_extract_object_from_prose_wrapped_list():
    assert _parse_json_object(
        '结果如下：[{"甲|半导体": {"rank": 1, "role": "趋势中军"}}]，请复核。'
    ) is None


def test_parse_json_object_rejects_malformed_prose_list_before_inner_object():
    with pytest.raises(json.JSONDecodeError):
        _parse_json_object(
            '结果如下：[invalid {"甲|半导体": {"rank": 1, "role": "趋势中军"}}]'
        )


def test_parse_json_object_rejects_non_json_text_inside_fence():
    with pytest.raises(json.JSONDecodeError):
        _parse_json_object(
            '```json\n结果如下：{"甲|半导体": {"rank": 1, "role": "趋势中军"}}\n```'
        )


def test_parse_json_object_accepts_fenced_top_level_object():
    mapping = _parse_json_object(
        '```json\n{"甲|半导体": {"rank": 1, "role": "趋势中军"}}\n```'
    )

    assert mapping == {"甲|半导体": {"rank": 1, "role": "趋势中军"}}


@pytest.mark.parametrize("missing_field", ["stock", "sector"])
def test_llm_enrichment_rejects_candidate_missing_identity_field(missing_field):
    invalid_candidate = {"stock": "乙", "sector": "元件", "evidence": []}
    invalid_candidate[missing_field] = ""
    proposal = {
        "date": "2026-07-03",
        "top_leaders": [
            {"stock": "甲", "sector": "半导体", "evidence": []},
            invalid_candidate,
        ],
    }

    out = enrich_with_llm_reason(
        proposal,
        enabled=True,
        runner=lambda prompt: {
            "甲|半导体": {"rank": 1, "role": "趋势中军"},
        },
    )

    assert out["top_leaders"] == proposal["top_leaders"]
    assert out["llm_status"] == {"ok": False, "reason": "invalid_mapping"}


def test_llm_enrichment_rejects_duplicate_candidate_keys_before_application():
    proposal = {
        "date": "2026-07-03",
        "top_leaders": [
            {"stock": "甲", "sector": "半导体", "evidence": [{"text": "来源一"}]},
            {"stock": "甲", "sector": "半导体", "evidence": [{"text": "来源二"}]},
        ],
    }

    out = enrich_with_llm_reason(
        proposal,
        enabled=True,
        runner=lambda prompt: {
            "甲|半导体": {"rank": 1, "role": "趋势中军"},
        },
    )

    assert out["top_leaders"] == proposal["top_leaders"]
    assert out["llm_status"] == {"ok": False, "reason": "invalid_mapping"}


def test_llm_judgement_reorders_and_labels_candidates():
    proposal = {
        "date": "2026-07-03",
        "top_leaders": [
            {"stock": "拓普集团", "sector": "同花顺出海50", "evidence": []},
            {"stock": "绿的谐波", "sector": "同花顺新质50", "evidence": []},
        ],
    }

    out = enrich_with_llm_reason(
        proposal,
        enabled=True,
        runner=lambda prompt: {
            "拓普集团|同花顺出海50": {
                "rank": 2,
                "role": "备选",
                "reason": "涨停但板块角色不如机器人分支清晰，仍需人工确认。",
                "risk_flags": ["题材归属偏宽"],
            },
            "绿的谐波|同花顺新质50": {
                "rank": 1,
                "role": "弹性前排",
                "reason": "新质生产力与机器人分支共振，领涨幅度更强，仍需人工确认。",
                "risk_flags": ["容量需复核"],
            },
        },
    )

    assert [item["stock"] for item in out["top_leaders"]] == ["绿的谐波", "拓普集团"]
    assert out["top_leaders"][0]["llm_rank"] == 1
    assert out["top_leaders"][0]["llm_role"] == "弹性前排"
    assert out["top_leaders"][0]["risk_flags"] == ["容量需复核"]
    assert out["top_leaders"][1]["llm_rank"] == 2


def test_llm_enrichment_rejects_redline_risk_flag():
    proposal = _proposal()
    out = enrich_with_llm_reason(
        proposal,
        enabled=True,
        runner=lambda prompt: {
            "688041 海光信息|半导体": {
                "rank": 1,
                "role": "前排活跃",
                "reason": "板块前排角色清晰，仍需人工确认。",
                "risk_flags": ["建议买入后观察容量"],
            }
        },
    )

    assert out["top_leaders"] == proposal["top_leaders"]
    assert out["llm_status"] == {"ok": False, "reason": "invalid_mapping"}


@pytest.mark.parametrize(
    "invalid_risk_flags",
    [None, "容量需复核", {"flag": "容量需复核"}, 1, True],
)
def test_llm_enrichment_rejects_present_non_list_risk_flags(invalid_risk_flags):
    proposal = _proposal()
    out = enrich_with_llm_reason(
        proposal,
        enabled=True,
        runner=lambda prompt: {
            "688041 海光信息|半导体": {
                "rank": 1,
                "role": "前排活跃",
                "risk_flags": invalid_risk_flags,
            }
        },
    )

    assert out["top_leaders"] == proposal["top_leaders"]
    assert out["llm_status"] == {"ok": False, "reason": "invalid_mapping"}


@pytest.mark.parametrize(
    "invalid_risk_flags",
    [
        [None],
        [1],
        [""],
        ["过长风险标签超过二十四个中文字符必须整批拒绝而不能静默截断"],
        ["容量需复核"] * 6,
    ],
)
def test_llm_enrichment_rejects_invalid_risk_flag_items(invalid_risk_flags):
    proposal = _proposal()
    out = enrich_with_llm_reason(
        proposal,
        enabled=True,
        runner=lambda prompt: {
            "688041 海光信息|半导体": {
                "rank": 1,
                "role": "前排活跃",
                "risk_flags": invalid_risk_flags,
            }
        },
    )

    assert out["top_leaders"] == proposal["top_leaders"]
    assert out["llm_status"] == {"ok": False, "reason": "invalid_mapping"}


@pytest.mark.parametrize("invalid_reason", [None, {}, [], 1, True, "理由" * 161])
def test_llm_enrichment_rejects_invalid_reason_schema(invalid_reason):
    proposal = _proposal()
    out = enrich_with_llm_reason(
        proposal,
        enabled=True,
        runner=lambda prompt: {
            "688041 海光信息|半导体": {
                "rank": 1,
                "role": "前排活跃",
                "reason": invalid_reason,
            }
        },
    )

    assert out["top_leaders"] == proposal["top_leaders"]
    assert out["llm_status"] == {"ok": False, "reason": "invalid_mapping"}


def test_llm_enrichment_rejects_unknown_structured_value_field():
    proposal = _proposal()
    out = enrich_with_llm_reason(
        proposal,
        enabled=True,
        runner=lambda prompt: {
            "688041 海光信息|半导体": {
                "rank": 1,
                "role": "前排活跃",
                "unexpected": "模型自创字段",
            }
        },
    )

    assert out["top_leaders"] == proposal["top_leaders"]
    assert out["llm_status"] == {"ok": False, "reason": "invalid_mapping"}


@pytest.mark.parametrize(
    ("field", "unsafe_value"),
    [
        ("reason", "合法首行\n> [事实] 模型伪造"),
        ("reason", "合法首行\r[老师观点] 模型伪造"),
        ("reason", "合法文本\t模型伪造"),
        ("reason", "[事实] 模型伪造"),
        ("reason", "> 模型伪造"),
        ("risk_flags", ["容量\n[老师观点]模型伪造"]),
        ("risk_flags", ["容量\r[研报观点]模型伪造"]),
        ("risk_flags", ["容量\x1b模型伪造"]),
        ("risk_flags", ["[来源陈述]模型伪造"]),
        ("risk_flags", ["- 模型伪造"]),
    ],
)
def test_llm_enrichment_rejects_unsafe_multiline_or_markdown_text(
    field,
    unsafe_value,
):
    proposal = _proposal()
    judgement = {
        "rank": 1,
        "role": "前排活跃",
        field: unsafe_value,
    }

    out = enrich_with_llm_reason(
        proposal,
        enabled=True,
        runner=lambda prompt: {"688041 海光信息|半导体": judgement},
    )

    assert out["top_leaders"] == proposal["top_leaders"]
    assert out["llm_status"] == {"ok": False, "reason": "invalid_mapping"}
    assert "模型伪造" not in render_markdown(out)


@pytest.mark.parametrize(
    ("field", "unsafe_value"),
    [
        ("reason", "文本\u2028模型伪造"),
        ("reason", "文本\u2029模型伪造"),
        ("reason", "[模型伪造](https://example.com)"),
        ("reason", "![模型伪造](https://example.com/x.png)"),
        ("reason", "`模型伪造`"),
        ("reason", "**模型伪造**"),
        ("reason", "<span>模型伪造</span>"),
        ("reason", "---"),
        ("risk_flags", ["文本\u2028模型伪造"]),
        ("risk_flags", ["`模型伪造`"]),
        ("risk_flags", ["_模型伪造_"]),
        ("risk_flags", ["<b>模型伪造</b>"]),
        ("reason", "［事实］模型伪造"),
        ("reason", "＊＊模型伪造＊＊"),
        ("reason", "&#91;事实&#93;模型伪造"),
        ("reason", "<!DOCTYPE html>模型伪造"),
        ("reason", "<?xml version='1.0'?>模型伪造"),
        ("risk_flags", ["<![CDATA[模型伪造]]>"]),
        ("risk_flags", ["<!--模型伪造-->"]),
    ],
)
def test_llm_enrichment_rejects_extended_unsafe_plain_text(field, unsafe_value):
    proposal = _proposal()
    judgement = {
        "rank": 1,
        "role": "前排活跃",
        field: unsafe_value,
    }

    out = enrich_with_llm_reason(
        proposal,
        enabled=True,
        runner=lambda prompt: {"688041 海光信息|半导体": judgement},
    )

    assert out["top_leaders"] == proposal["top_leaders"]
    assert out["llm_status"] == {"ok": False, "reason": "invalid_mapping"}


def test_llm_enrichment_cleans_legal_risk_flags_list():
    proposal = _proposal()
    out = enrich_with_llm_reason(
        proposal,
        enabled=True,
        runner=lambda prompt: {
            "688041 海光信息|半导体": {
                "rank": 1,
                "role": "前排活跃",
                "risk_flags": ["  容量需复核  ", "题材归属需复核"],
            }
        },
    )

    assert out["llm_status"] == {"ok": True}
    assert out["top_leaders"][0]["risk_flags"] == ["容量需复核", "题材归属需复核"]


def test_llm_enrichment_marks_batch_incomplete_when_one_risk_flag_hits_redline():
    proposal = {
        "date": "2026-07-03",
        "top_leaders": [
            {"stock": "甲", "sector": "半导体", "evidence": []},
            {"stock": "乙", "sector": "元件", "evidence": []},
        ],
    }
    out = enrich_with_llm_reason(
        proposal,
        enabled=True,
        runner=lambda prompt: {
            "甲|半导体": {
                "rank": 1,
                "role": "前排活跃",
                "risk_flags": ["容量需复核"],
            },
            "乙|元件": {
                "rank": 2,
                "role": "趋势中军",
                "risk_flags": ["目标价仍需确认"],
            },
        },
    )

    assert out["top_leaders"] == proposal["top_leaders"]
    assert out["llm_status"] == {"ok": False, "reason": "incomplete_mapping"}


def test_llm_prompt_uses_semantic_roles_not_board_types():
    proposal = _proposal()

    def runner(prompt):
        assert "趋势中军|连板核心|前排活跃|弹性前排|备选|剔除" in prompt
        assert '"role": "趋势中军|小票弹性（连板）|20cm' not in prompt
        assert "10cm/20cm/30cm 只作为 board_type 事实" in prompt
        assert "同一板块、同一 role 只选最好一个" in prompt
        assert "总结果不是程序最终约束" in prompt
        assert "排序优先级依次为：涨停/涨幅强度、成交额、板块主线审美" in prompt
        assert "概念资金流和老师观点只作为辅助证据" in prompt
        assert "走势引领|容量中军|分支核心" not in prompt
        return {
            "688041 海光信息|半导体": {
                "rank": 1,
                "role": "前排活跃",
                "reason": "板块前排角色清晰，仍需人工确认。",
            }
        }

    out = enrich_with_llm_reason(proposal, enabled=True, runner=runner)

    assert out["top_leaders"][0]["llm_rank"] == 1
    assert out["top_leaders"][0]["llm_role"] == "前排活跃"


def test_llm_judgement_rejects_board_type_as_old_role():
    proposal = _proposal()
    out = enrich_with_llm_reason(
        proposal,
        enabled=True,
        runner=lambda prompt: {
            "688041 海光信息|半导体": {
                "rank": 1,
                "role": "20cm",
                "reason": "板型不能作为角色，仍需人工确认。",
            }
        },
    )

    assert out["top_leaders"] == proposal["top_leaders"]
    assert out["llm_status"] == {"ok": False, "reason": "invalid_mapping"}


@pytest.mark.parametrize("value", [1, 2, 99])
def test_safe_rank_accepts_only_positive_integers(value):
    assert _safe_rank(value) == value


@pytest.mark.parametrize("value", [True, False, 1.0, 1.5, "1", "01", 0, -1, None])
def test_safe_rank_rejects_non_integer_or_non_positive_values(value):
    assert _safe_rank(value) is None


def test_llm_enrichment_drops_redline_reason():
    proposal = _proposal()
    out = enrich_with_llm_reason(
        proposal,
        enabled=True,
        runner=lambda prompt: {
            "688041 海光信息|半导体": {
                "rank": 1,
                "role": "前排活跃",
                "reason": "建议买入，目标价上看，仍需人工确认。",
            }
        },
    )

    assert out["top_leaders"] == proposal["top_leaders"]
    assert out["llm_status"] == {"ok": False, "reason": "invalid_mapping"}


def test_llm_fail_closed_when_runner_raises():
    proposal = _proposal()

    def raising_runner(prompt):
        raise RuntimeError("llm unavailable")

    out = enrich_with_llm_reason(proposal, enabled=True, runner=raising_runner)

    assert out["top_leaders"] == proposal["top_leaders"]
    assert out["llm_status"] == {"ok": False, "reason": "exception"}


def test_llm_runner_timeout_is_classified_with_short_detail():
    proposal = _proposal()

    def timeout_runner(prompt):
        raise subprocess.TimeoutExpired(cmd=["agy"], timeout=9)

    out = enrich_with_llm_reason(proposal, enabled=True, runner=timeout_runner)

    assert out["top_leaders"] == proposal["top_leaders"]
    assert out["llm_status"] == {
        "ok": False,
        "reason": "timeout",
        "detail": "timeout_seconds=9",
    }


def test_run_llm_timeout_returns_diagnostic_with_log_path(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTIGRAVITY_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(
        "utils.llm_cli.resolve_config",
        lambda **kwargs: SimpleNamespace(timeout_seconds=17),
    )
    monkeypatch.setattr(
        "utils.llm_cli.build_prompt_command",
        lambda config, prompt, log_file: ["agy", "--log-file", log_file],
    )

    def timeout_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", timeout_run)

    out = _run_llm("prompt")

    error = out
    assert error.reason == "timeout"
    assert "timeout_seconds=17" in error.detail
    assert f"log_file={tmp_path}/daily-leaders-" in error.detail


def test_run_llm_invalid_json_is_classified(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTIGRAVITY_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(
        "utils.llm_cli.resolve_config",
        lambda **kwargs: SimpleNamespace(timeout_seconds=17),
    )
    monkeypatch.setattr(
        "utils.llm_cli.build_prompt_command",
        lambda config, prompt, log_file: ["agy", "--log-file", log_file],
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout="not JSON", stderr=""
        ),
    )

    out = _run_llm("prompt")

    error = out
    assert error.reason == "invalid_json"
    assert f"log_file={tmp_path}/daily-leaders-" in error.detail


def test_run_llm_nonzero_exit_is_classified(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTIGRAVITY_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(
        "utils.llm_cli.resolve_config",
        lambda **kwargs: SimpleNamespace(timeout_seconds=17),
    )
    monkeypatch.setattr(
        "utils.llm_cli.build_prompt_command",
        lambda config, prompt, log_file: ["agy", "--log-file", log_file],
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=3, stdout="", stderr="failed"
        ),
    )

    out = _run_llm("prompt")

    error = out
    assert error.reason == "nonzero_exit"
    assert "returncode=3" in error.detail
    assert f"log_file={tmp_path}/daily-leaders-" in error.detail


@pytest.mark.parametrize(
    ("stderr", "log_text", "expected_reason"),
    [
        ("", "not logged into Antigravity", "auth_required"),
        ("", "RESOURCE_EXHAUSTED", "quota_exhausted"),
        ("", "", "empty_output"),
    ],
)
def test_run_llm_empty_output_is_classified(
    monkeypatch,
    tmp_path,
    stderr,
    log_text,
    expected_reason,
):
    monkeypatch.setenv("ANTIGRAVITY_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(
        "utils.llm_cli.resolve_config",
        lambda **kwargs: SimpleNamespace(timeout_seconds=17),
    )
    monkeypatch.setattr(
        "utils.llm_cli.build_prompt_command",
        lambda config, prompt, log_file: ["agy", "--log-file", log_file],
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout="", stderr=stderr
        ),
    )
    monkeypatch.setattr("pathlib.Path.read_text", lambda *args, **kwargs: log_text)

    out = _run_llm("prompt")

    error = out
    assert error.reason == expected_reason
    assert f"log_file={tmp_path}/daily-leaders-" in error.detail


@pytest.mark.parametrize(
    ("stderr", "log_text", "expected_reason"),
    [
        ("not logged into Antigravity", "", "auth_required"),
        ("", "RESOURCE_EXHAUSTED", "quota_exhausted"),
    ],
)
def test_run_llm_nonzero_exit_prioritizes_auth_and_quota_classification(
    monkeypatch,
    tmp_path,
    stderr,
    log_text,
    expected_reason,
):
    monkeypatch.setenv("ANTIGRAVITY_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(
        "utils.llm_cli.resolve_config",
        lambda **kwargs: SimpleNamespace(timeout_seconds=17),
    )
    monkeypatch.setattr(
        "utils.llm_cli.build_prompt_command",
        lambda config, prompt, log_file: ["agy", "--log-file", log_file],
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=7, stdout="", stderr=stderr
        ),
    )
    monkeypatch.setattr("pathlib.Path.read_text", lambda *args, **kwargs: log_text)

    out = _run_llm("prompt")

    error = out
    assert error.reason == expected_reason
    assert "returncode=7" in error.detail
    assert f"log_file={tmp_path}/daily-leaders-" in error.detail


@pytest.mark.parametrize(
    ("stderr", "log_text", "expected_reason"),
    [
        ("not logged into Antigravity", "", "auth_required"),
        ("", "RESOURCE_EXHAUSTED", "quota_exhausted"),
    ],
)
def test_run_llm_success_exit_prioritizes_auth_and_quota_over_non_json_stdout(
    monkeypatch,
    tmp_path,
    stderr,
    log_text,
    expected_reason,
):
    monkeypatch.setenv("ANTIGRAVITY_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(
        "utils.llm_cli.resolve_config",
        lambda **kwargs: SimpleNamespace(timeout_seconds=17),
    )
    monkeypatch.setattr(
        "utils.llm_cli.build_prompt_command",
        lambda config, prompt, log_file: ["agy", "--log-file", log_file],
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout="Antigravity error text", stderr=stderr
        ),
    )
    monkeypatch.setattr("pathlib.Path.read_text", lambda *args, **kwargs: log_text)

    error = _run_llm("prompt")

    assert error.reason == expected_reason
    assert f"log_file={tmp_path}/daily-leaders-" in error.detail


@pytest.mark.parametrize(
    ("returncode", "stdout", "stderr", "log_text", "expected_reason"),
    [
        (0, "", "HTTP 429 Too Many Requests", "", "quota_exhausted"),
        (7, "HTTP429 Too Many Requests", "", "", "quota_exhausted"),
        (0, "", "HTTP/1.1 401 Unauthorized", "", "auth_required"),
        (7, "HTTP401 Unauthorized", "", "", "auth_required"),
        (7, "HTTP Error 401: Unauthorized", "", "", "auth_required"),
        (0, "", "", "code: 401 Unauthorized", "auth_required"),
        (0, "", "", "status_code=401 Unauthorized", "auth_required"),
        (0, "RESOURCE_EXHAUSTED", "", "", "quota_exhausted"),
        (7, "", "quota exceeded", "", "quota_exhausted"),
        (0, "", "", "RESOURCE_EXHAUSTED", "quota_exhausted"),
        (0, "not authenticated", "", "", "auth_required"),
        (7, "", "Unauthenticated", "", "auth_required"),
        (0, "", "", "not logged into Antigravity", "auth_required"),
    ],
)
def test_run_llm_uses_shared_failure_classification_across_process_channels(
    monkeypatch,
    tmp_path,
    returncode,
    stdout,
    stderr,
    log_text,
    expected_reason,
):
    monkeypatch.setenv("ANTIGRAVITY_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(
        "utils.llm_cli.resolve_config",
        lambda **kwargs: SimpleNamespace(timeout_seconds=17),
    )
    monkeypatch.setattr(
        "utils.llm_cli.build_prompt_command",
        lambda config, prompt, log_file: ["agy", "--log-file", log_file],
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        ),
    )
    monkeypatch.setattr("pathlib.Path.read_text", lambda *args, **kwargs: log_text)

    error = _run_llm("prompt")

    assert error.reason == expected_reason
    assert f"log_file={tmp_path}/daily-leaders-" in error.detail


@pytest.mark.parametrize(
    ("returncode", "stdout", "expected_reason"),
    [
        (0, "", "empty_output"),
        (7, "", "nonzero_exit"),
        (7, "ordinary runtime failure", "nonzero_exit"),
        (0, "ordinary record 401 failed", "invalid_json"),
        (7, "ordinary record 401 failed", "nonzero_exit"),
    ],
)
def test_run_llm_keeps_generic_empty_and_nonzero_fallbacks(
    monkeypatch,
    tmp_path,
    returncode,
    stdout,
    expected_reason,
):
    monkeypatch.setenv("ANTIGRAVITY_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(
        "utils.llm_cli.resolve_config",
        lambda **kwargs: SimpleNamespace(timeout_seconds=17),
    )
    monkeypatch.setattr(
        "utils.llm_cli.build_prompt_command",
        lambda config, prompt, log_file: ["agy", "--log-file", log_file],
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=returncode,
            stdout=stdout,
            stderr="ordinary failure",
        ),
    )
    monkeypatch.setattr("pathlib.Path.read_text", lambda *args, **kwargs: "")

    error = _run_llm("prompt")

    assert error.reason == expected_reason


def test_run_llm_diagnostic_detail_does_not_copy_process_or_log_text(
    monkeypatch,
    tmp_path,
):
    marker = "sensitive-diagnostic-payload"
    monkeypatch.setenv("ANTIGRAVITY_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(
        "utils.llm_cli.resolve_config",
        lambda **kwargs: SimpleNamespace(timeout_seconds=17),
    )
    monkeypatch.setattr(
        "utils.llm_cli.build_prompt_command",
        lambda config, prompt, log_file: ["agy", "--log-file", log_file],
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=7,
            stdout=f"RESOURCE_EXHAUSTED {marker}",
            stderr=marker,
        ),
    )
    monkeypatch.setattr(
        "pathlib.Path.read_text",
        lambda *args, **kwargs: f"quota exceeded {marker}",
    )

    error = _run_llm("prompt")

    assert error.reason == "quota_exhausted"
    assert marker not in error.detail


def test_run_llm_auth_status_detail_does_not_copy_process_or_log_text(
    monkeypatch,
    tmp_path,
):
    marker = "sensitive-auth-diagnostic-payload"
    monkeypatch.setenv("ANTIGRAVITY_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(
        "utils.llm_cli.resolve_config",
        lambda **kwargs: SimpleNamespace(timeout_seconds=17),
    )
    monkeypatch.setattr(
        "utils.llm_cli.build_prompt_command",
        lambda config, prompt, log_file: ["agy", "--log-file", log_file],
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=7,
            stdout=f"HTTP/1.1 401 Unauthorized {marker}",
            stderr=marker,
        ),
    )
    monkeypatch.setattr(
        "pathlib.Path.read_text",
        lambda *args, **kwargs: f"code 401 {marker}",
    )

    error = _run_llm("prompt")

    assert error.reason == "auth_required"
    assert marker not in error.detail


def test_run_llm_valid_json_with_clean_log_is_unchanged(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTIGRAVITY_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(
        "utils.llm_cli.resolve_config",
        lambda **kwargs: SimpleNamespace(timeout_seconds=17),
    )
    monkeypatch.setattr(
        "utils.llm_cli.build_prompt_command",
        lambda config, prompt, log_file: ["agy", "--log-file", log_file],
    )
    mapping = {
        "甲|半导体": {
            "rank": 1,
            "role": "前排活跃",
            "reason": "合法结果，仍需人工确认。",
        }
    }
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout='{"甲|半导体":{"rank":1,"role":"前排活跃","reason":"合法结果，仍需人工确认。"}}', stderr=""
        ),
    )
    monkeypatch.setattr("pathlib.Path.read_text", lambda *args, **kwargs: "")

    assert _run_llm("prompt") == mapping


def test_run_llm_valid_json_wins_after_auth_recovery_log(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTIGRAVITY_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(
        "utils.llm_cli.resolve_config",
        lambda **kwargs: SimpleNamespace(timeout_seconds=17),
    )
    monkeypatch.setattr(
        "utils.llm_cli.build_prompt_command",
        lambda config, prompt, log_file: ["agy", "--log-file", log_file],
    )
    mapping = {
        "甲|半导体": {
            "rank": 1,
            "role": "前排活跃",
            "reason": "合法结果，仍需人工确认。",
        }
    }
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout='{"甲|半导体":{"rank":1,"role":"前排活跃","reason":"合法结果，仍需人工确认。"}}',
            stderr="",
        ),
    )
    monkeypatch.setattr(
        "pathlib.Path.read_text",
        lambda *args, **kwargs: (
            "not logged into Antigravity\nAuthentication completed successfully"
        ),
    )

    assert _run_llm("prompt") == mapping


@pytest.mark.parametrize(
    "stdout",
    [
        "[]",
        "```json\n[{}]\n```",
        "结果如下：[{}]，请复核。",
    ],
)
def test_run_llm_valid_non_object_json_ignores_stale_auth_log(
    monkeypatch,
    tmp_path,
    stdout,
):
    monkeypatch.setenv("ANTIGRAVITY_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(
        "utils.llm_cli.resolve_config",
        lambda **kwargs: SimpleNamespace(timeout_seconds=17),
    )
    monkeypatch.setattr(
        "utils.llm_cli.build_prompt_command",
        lambda config, prompt, log_file: ["agy", "--log-file", log_file],
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout=stdout, stderr=""
        ),
    )
    log_reads = []

    def stale_auth_log(*args, **kwargs):
        log_reads.append(True)
        return "not logged into Antigravity"

    monkeypatch.setattr("pathlib.Path.read_text", stale_auth_log)

    error = _run_llm("prompt")

    assert error.reason == "invalid_json"
    assert log_reads == []


def test_llm_fail_closed_marks_empty_mapping_and_renderer_warning():
    proposal = _proposal()
    out = enrich_with_llm_reason(proposal, enabled=True, runner=lambda prompt: None)

    assert out["top_leaders"] == proposal["top_leaders"]
    assert out["llm_status"] == {"ok": False, "reason": "empty_mapping"}
    md = render_markdown(out)
    assert "旧候选稿未验证新收敛规则，需人工复核" in md
    assert "已按确定性板块/属性规则兜底收敛" not in md


def test_render_markdown_claims_fallback_only_for_verified_new_proposal():
    proposal = _verified_new_proposal()
    proposal["llm_status"] = {"ok": False, "reason": "timeout"}

    md = render_markdown(proposal)

    assert (
        "LLM 复核未完成：[判断] timeout；已按确定性板块/属性规则兜底收敛，仍需人工确认。"
        in md
    )


def test_render_markdown_does_not_normalize_invalid_leader_container_to_empty():
    proposal = _verified_new_proposal()
    proposal["top_leaders"] = None
    proposal["llm_status"] = {"ok": False, "reason": "timeout"}
    proposal["candidate_limit"].update(
        {
            "original_count": 0,
            "deduped_count": 0,
            "duplicate_trimmed_count": 0,
            "trimmed_count": 0,
            "review_pool_count": 0,
            "review_pool_trimmed_count": 0,
            "sector_role_trimmed_count": 0,
            "stock_duplicate_trimmed_count": 0,
            "final_count": 0,
        }
    )

    md = render_markdown(proposal)

    assert "旧候选稿未验证新收敛规则，需人工复核" in md
    assert "已按确定性板块/属性规则兜底收敛" not in md


def test_render_markdown_claims_fallback_when_verified_board_type_is_missing():
    proposal = _verified_new_proposal()
    proposal["llm_status"] = {"ok": False, "reason": "timeout"}
    proposal["top_leaders"][0].pop("board_type")

    md = render_markdown(proposal)

    assert "已按确定性板块/属性规则兜底收敛" in md


def test_render_markdown_rejects_verified_metadata_with_illegal_board_type():
    proposal = _verified_new_proposal()
    proposal["llm_status"] = {"ok": False, "reason": "timeout"}
    proposal["top_leaders"][0]["board_type"] = "40cm"

    md = render_markdown(proposal)

    assert "旧候选稿未验证新收敛规则，需人工复核" in md
    assert "已按确定性板块/属性规则兜底收敛" not in md


def test_render_markdown_rejects_verified_metadata_with_duplicate_stock():
    proposal = _verified_new_proposal()
    proposal["llm_status"] = {"ok": False, "reason": "timeout"}
    proposal["top_leaders"].append(
        {
            **proposal["top_leaders"][0],
            "stock": "688041 海光信息 688041 海光信息",
            "sector": "软件开发",
            "leader_role": "前排活跃",
        }
    )
    proposal["candidate_limit"].update(
        {
            "original_count": 2,
            "deduped_count": 2,
            "review_pool_count": 2,
            "final_count": 2,
        }
    )

    md = render_markdown(proposal)

    assert "旧候选稿未验证新收敛规则，需人工复核" in md
    assert "已按确定性板块/属性规则兜底收敛" not in md


def test_render_markdown_rejects_verified_metadata_with_duplicate_sector_role():
    proposal = _verified_new_proposal()
    proposal["llm_status"] = {"ok": False, "reason": "timeout"}
    proposal["top_leaders"].append(
        {
            **proposal["top_leaders"][0],
            "stock": "000001 平安银行",
        }
    )
    proposal["candidate_limit"].update(
        {
            "original_count": 2,
            "deduped_count": 2,
            "review_pool_count": 2,
            "final_count": 2,
        }
    )

    md = render_markdown(proposal)

    assert "旧候选稿未验证新收敛规则，需人工复核" in md
    assert "已按确定性板块/属性规则兜底收敛" not in md


def test_render_markdown_keeps_old_30_candidate_draft_warning_neutral():
    proposal = _proposal()
    proposal["llm_status"] = {"ok": False, "reason": "timeout"}
    proposal["candidate_limit"] = {
        "max_candidates": 30,
        "original_count": 30,
        "trimmed_count": 0,
    }

    md = render_markdown(proposal)

    assert "旧候选稿未验证新收敛规则，需人工复核" in md
    assert "已按确定性板块/属性规则兜底收敛" not in md


@pytest.mark.parametrize(
    "mutate",
    [
        lambda proposal: proposal["candidate_limit"].update({"final_count": 16}),
        lambda proposal: proposal["top_leaders"][0].update({"leader_role": "20cm"}),
        lambda proposal: proposal["top_leaders"][0].update({"selection_basis": "llm"}),
        lambda proposal: proposal["top_leaders"][0].update({"selection_basis": "legacy"}),
    ],
)
def test_render_markdown_does_not_claim_fallback_for_unverified_new_metadata(mutate):
    proposal = _verified_new_proposal()
    proposal["llm_status"] = {"ok": False, "reason": "timeout"}
    mutate(proposal)

    md = render_markdown(proposal)

    assert "旧候选稿未验证新收敛规则，需人工复核" in md
    assert "已按确定性板块/属性规则兜底收敛" not in md


@pytest.mark.parametrize(
    "mutate",
    [
        lambda proposal: proposal["candidate_limit"].pop("review_pool_count"),
        lambda proposal: proposal["candidate_limit"].update(
            {"review_pool_trimmed_count": "0"}
        ),
        lambda proposal: proposal["candidate_limit"].update(
            {"stock_duplicate_trimmed_count": True}
        ),
    ],
)
def test_render_markdown_requires_complete_typed_candidate_limit_metadata(mutate):
    proposal = _verified_new_proposal()
    proposal["llm_status"] = {"ok": False, "reason": "timeout"}
    mutate(proposal)

    md = render_markdown(proposal)

    assert "旧候选稿未验证新收敛规则，需人工复核" in md
    assert "已按确定性板块/属性规则兜底收敛" not in md


@pytest.mark.parametrize(
    "mutate",
    [
        lambda proposal: proposal["candidate_limit"].update({"original_count": 0}),
        lambda proposal: proposal["candidate_limit"].update({"deduped_count": 0}),
        lambda proposal: proposal["candidate_limit"].update({"review_pool_count": 0}),
        lambda proposal: proposal["candidate_limit"].update(
            {"duplicate_trimmed_count": 1}
        ),
        lambda proposal: proposal["candidate_limit"].update(
            {"review_pool_trimmed_count": 1}
        ),
        lambda proposal: proposal["candidate_limit"].update({"trimmed_count": 1}),
        lambda proposal: proposal["top_leaders"][0].update(
            {"attribute_type": "前排活跃"}
        ),
        lambda proposal: proposal["top_leaders"][0].update({"is_new": 1}),
        lambda proposal: proposal["llm_status"].update({"ok": 0}),
        lambda proposal: proposal["llm_status"].update({"ok": None}),
    ],
)
def test_render_markdown_requires_consistent_fallback_metadata(mutate):
    proposal = _verified_new_proposal()
    proposal["llm_status"] = {"ok": False, "reason": "timeout"}
    mutate(proposal)

    md = render_markdown(proposal)

    assert "旧候选稿未验证新收敛规则，需人工复核" in md
    assert "已按确定性板块/属性规则兜底收敛" not in md


def test_render_markdown_sanitizes_unknown_llm_status_reason():
    proposal = _proposal()
    proposal["llm_status"] = {"ok": False, "reason": "任意模型文本<script>"}

    md = render_markdown(proposal)

    assert "任意模型文本" not in md
    assert "unknown_error" in md


def test_render_markdown_distinguishes_disabled_llm():
    proposal = _proposal()
    proposal["llm_status"] = {"ok": False, "reason": "disabled"}

    md = render_markdown(proposal)

    assert "LLM 复核已主动跳过" in md
    assert "旧候选稿未验证新收敛规则，需人工复核" in md
    assert "确定性板块/属性规则兜底收敛" not in md
    assert "LLM 复核未完成" not in md


def test_render_markdown_verified_disabled_proposal_describes_fallback():
    proposal = _verified_new_proposal()
    proposal["llm_status"] = {"ok": False, "reason": "disabled"}

    md = render_markdown(proposal)

    assert "LLM 复核已主动跳过" in md
    assert "确定性板块/属性规则兜底收敛" in md
