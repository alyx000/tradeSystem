from __future__ import annotations

import json

from services.daily_leaders.candidates import build_candidates, teacher_alignment


def test_build_candidates_merges_prefill_and_marks_new_leader():
    prefill = {
        "step5_leaders": {
            "top_leaders": [
                {
                    "stock": "海光信息",
                    "sector": "半导体",
                    "attribute_type": "走势引领",
                    "is_prefilled": True,
                }
            ]
        },
        "teacher_notes": [],
        "cognitions_by_step": {"step5_leaders": []},
    }
    history = [{"stock_name": "工业富联", "sector": "算力", "attribute_type": "容量最大"}]
    trend_pool = []

    result = build_candidates(prefill=prefill, trend_pool=trend_pool, history=history)

    assert result["date"] == ""
    assert result["top_leaders"][0]["stock"] == "海光信息"
    assert result["top_leaders"][0]["sector"] == "半导体"
    assert result["top_leaders"][0]["is_new"] is True
    assert result["top_leaders"][0]["teacher_alignment"] == "未提及"
    assert any(e["label"] == "[判断]" for e in result["top_leaders"][0]["evidence"])


def test_trend_pool_candidate_added_when_not_in_prefill():
    prefill = {"step5_leaders": None, "teacher_notes": [], "cognitions_by_step": {}}
    trend_pool = [
        {
            "code": "688041",
            "name": "海光信息",
            "sw_l2": "半导体",
            "entered_date": "2026-07-03",
            "last_signal": {"entry_trigger": "涨停"},
        }
    ]

    result = build_candidates(prefill=prefill, trend_pool=trend_pool, history=[])

    item = result["top_leaders"][0]
    assert item["stock"] == "688041 海光信息"
    assert item["sector"] == "半导体"
    assert item["attribute_type"] == "走势引领"
    assert item["clarity"] == "中"


def test_active_history_candidate_added_when_not_in_prefill_or_trend_pool():
    prefill = {"step5_leaders": None, "teacher_notes": [], "cognitions_by_step": {}}
    history = [
        {
            "stock_code": "601138",
            "stock_name": "工业富联",
            "sector": "算力",
            "attribute_type": "容量最大",
            "attribute": "连续两日最票",
            "clarity": "高",
        }
    ]

    result = build_candidates(prefill=prefill, trend_pool=[], history=history)

    item = result["top_leaders"][0]
    assert item["stock"] == "601138 工业富联"
    assert item["sector"] == "算力"
    assert item["attribute_type"] == "容量最大"
    assert item["is_new"] is False
    assert item["evidence"] == [{"label": "[判断]", "text": "来自历史最票跟踪，需用户确认是否仍属当日最票"}]


def test_history_candidate_deduplicates_prefill_with_different_stock_display():
    prefill = {
        "step5_leaders": {
            "top_leaders": [
                {"stock": "工业富联", "sector": "算力", "attribute_type": "容量最大"}
            ]
        },
        "teacher_notes": [],
        "cognitions_by_step": {},
    }
    history = [{"stock_code": "601138", "stock_name": "工业富联", "sector": "算力"}]

    result = build_candidates(prefill=prefill, trend_pool=[], history=history)

    assert [item["stock"] for item in result["top_leaders"]] == ["工业富联"]
    assert result["top_leaders"][0]["is_new"] is False


def test_teacher_alignment_support_conflict_and_unmentioned():
    notes = [
        {"teacher_name": "鞠磊", "sectors": '["半导体"]', "core_view": "半导体主线继续观察海光信息"},
        {"teacher_name": "小鲍", "sectors": '["机器人"]', "core_view": "机器人退潮，龙头承接走弱"},
    ]

    assert teacher_alignment("海光信息", "半导体", notes)["status"] == "支持"
    assert teacher_alignment("机器人A", "机器人", notes)["status"] == "冲突"
    assert teacher_alignment("其它股", "券商", notes)["status"] == "未提及"


def test_build_candidates_skips_blank_stock_or_sector():
    prefill = {
        "step5_leaders": {
            "top_leaders": [
                {"stock": " ", "sector": "半导体", "attribute_type": "走势引领"},
                {"stock": "海光信息", "sector": "", "attribute_type": "走势引领"},
                {"stock": "工业富联", "sector": "算力", "attribute_type": "容量最大"},
            ]
        },
        "teacher_notes": [],
        "cognitions_by_step": {},
    }
    trend_pool = [
        {"code": "", "name": " ", "sw_l2": "机器人"},
        {"code": "688041", "name": "", "sw_l2": ""},
    ]

    result = build_candidates(prefill=prefill, trend_pool=trend_pool, history=[])

    assert [item["stock"] for item in result["top_leaders"]] == ["工业富联", "688041"]
    assert result["top_leaders"][1]["sector"] == "未分类"


def test_candidate_payload_is_json_serializable_and_lightweight():
    prefill = {
        "step5_leaders": {
            "top_leaders": [
                {"stock": "海光信息", "sector": "半导体", "attribute_type": "走势引领"}
            ]
        },
        "teacher_notes": [
            {
                "id": 12,
                "date": "2026-07-05",
                "teacher_name": "鞠磊",
                "title": "半导体观察",
                "sectors": '["半导体"]',
                "core_view": "半导体主线继续观察海光信息",
                "raw_content": "不应进入候选 payload 的长篇原文",
            }
        ],
        "cognitions_by_step": {},
    }

    result = build_candidates(prefill=prefill, trend_pool=[], history=[])
    dumped = json.dumps(result, ensure_ascii=False)

    assert "raw_content" not in dumped
    assert "不应进入候选 payload" not in dumped
    assert result["top_leaders"][0]["teacher_note_ref"] == {
        "id": 12,
        "date": "2026-07-05",
        "teacher_name": "鞠磊",
        "title": "半导体观察",
        "snippet": "半导体主线继续观察海光信息",
    }


def test_teacher_alignment_conflict_words_must_be_near_matched_term():
    notes = [
        {
            "teacher_name": "鞠磊",
            "sectors": '["半导体", "机器人"]',
            "core_view": "半导体继续观察海光信息，机器人退潮",
        }
    ]

    assert teacher_alignment("海光信息", "半导体", notes)["status"] == "支持"
    assert teacher_alignment("机器人A", "机器人", notes)["status"] == "冲突"
