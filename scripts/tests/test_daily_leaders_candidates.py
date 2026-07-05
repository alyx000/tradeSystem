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


def test_build_candidates_filters_st_stocks_from_all_sources():
    prefill = {
        "step5_leaders": {
            "top_leaders": [
                {"stock": "ST长方", "sector": "光学光电子", "attribute_type": "走势引领"},
                {"stock": "海光信息", "sector": "半导体", "attribute_type": "走势引领"},
            ]
        },
        "teacher_notes": [],
        "cognitions_by_step": {},
    }
    trend_pool = [
        {"code": "300301", "name": "ST长方", "sw_l2": "光学光电子"},
        {"code": "688041", "name": "海光信息", "sw_l2": "半导体"},
    ]
    history = [
        {"stock_code": "300301", "stock_name": "ST长方", "sector": "光学光电子"},
        {"stock_code": "601138", "stock_name": "工业富联", "sector": "算力"},
    ]

    result = build_candidates(prefill=prefill, trend_pool=trend_pool, history=history)

    stocks = [item["stock"] for item in result["top_leaders"]]
    assert "ST长方" not in " ".join(stocks)
    assert stocks == ["海光信息", "601138 工业富联"]


def test_build_candidates_adds_market_flow_leaders_to_llm_pool():
    prefill = {
        "step5_leaders": None,
        "teacher_notes": [],
        "cognitions_by_step": {},
        "market": {
            "concept_moneyflow_ths": {
                "data": [
                    {
                        "name": "同花顺新质50",
                        "lead_stock": "绿的谐波",
                        "net_amount_yi": 91.0,
                        "pct_change_stock": 18.15,
                    },
                    {
                        "name": "ST板块",
                        "lead_stock": "ST臻镭",
                        "net_amount_yi": 120.0,
                        "pct_change_stock": 5.0,
                    },
                ]
            },
            "concept_moneyflow_dc": {
                "data": [
                    {
                        "name": "机器视觉",
                        "buy_sm_amount_stock": "埃斯顿",
                        "net_amount_yi": 19.08,
                        "pct_change": 2.44,
                        "rank": 2,
                    }
                ]
            },
        },
    }

    result = build_candidates(prefill=prefill, trend_pool=[], history=[])
    by_stock = {item["stock"]: item for item in result["top_leaders"]}

    assert "绿的谐波" in by_stock
    assert by_stock["绿的谐波"]["sector"] == "同花顺新质50"
    assert "净流入 91.0 亿" in by_stock["绿的谐波"]["evidence"][0]["text"]
    assert "埃斯顿" in by_stock
    assert by_stock["埃斯顿"]["sector"] == "机器视觉"
    assert "榜单排名 2" in by_stock["埃斯顿"]["evidence"][0]["text"]
    assert "ST臻镭" not in by_stock


def test_teacher_supported_prefill_candidate_gets_market_strength_evidence_for_llm():
    prefill = {
        "step5_leaders": {
            "top_leaders": [
                {"stock": "有研硅", "sector": "中芯国际概念", "attribute_type": "走势引领"}
            ]
        },
        "teacher_notes": [
            {
                "id": 244,
                "date": "2026-06-29",
                "teacher_name": "小鲍",
                "title": "芯片主线与长鑫存储鱼尾风险",
                "sectors": '["中芯国际概念", "半导体"]',
                "core_view": "硬科技仍是绝对主线，芯片强于科技硬件。",
            }
        ],
        "market": {
            "stock_quotes": {
                "data": [
                    {
                        "name": "有研硅",
                        "code": "688432",
                        "pct_chg": 12.34,
                        "amount_yi": 18.8,
                    }
                ]
            },
            "concept_moneyflow_ths": {
                "data": [
                    {
                        "name": "中芯国际概念",
                        "lead_stock": "有研硅",
                        "net_amount_yi": 36.0,
                        "pct_change_stock": 12.34,
                    }
                ]
            },
        },
    }

    result = build_candidates(prefill=prefill, trend_pool=[], history=[])

    item = result["top_leaders"][0]
    evidence = "；".join(e["text"] for e in item["evidence"])
    assert item["teacher_alignment"] == "支持"
    assert "老师明确支持主线预填票" in evidence
    assert "个股涨幅 12.34%" in evidence
    assert "成交额 18.8 亿" in evidence
    assert "板块资金净流入 36.0 亿" in evidence


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
