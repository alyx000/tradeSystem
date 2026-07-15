from __future__ import annotations

import json

import pytest

from services.daily_leaders.candidates import (
    _board_attribute,
    build_candidates,
    teacher_alignment,
)


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


def test_build_candidates_ignores_trend_pool_source():
    prefill = {
        "step5_leaders": None,
        "teacher_notes": [],
        "cognitions_by_step": {},
        "market": {
            "stock_quotes": {
                "data": [
                    {"code": "688041.SH", "name": "海光信息", "pct_chg": 6.8, "amount_yi": 42.0}
                ]
            }
        },
    }
    trend_pool = [
        {
            "code": "688041",
            "name": "海光信息",
            "sw_l2": "半导体",
            "entered_date": "2026-07-03",
            "last_seen_date": "2026-07-03",
            "last_signal": {"entry_trigger": "涨停"},
        }
    ]

    result = build_candidates(prefill=prefill, trend_pool=trend_pool, history=[], date="2026-07-03")

    assert result["top_leaders"] == []


def test_active_history_candidate_added_when_not_in_prefill():
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


def test_codes_are_explicit_and_history_name_code_enriches_same_name_flow():
    prefill = {
        "step5_leaders": {
            "top_leaders": [
                {
                    "stock": "五粮液",
                    "code": "000858.SZ",
                    "sector": "白酒",
                    "attribute_type": "趋势中军",
                }
            ]
        },
        "teacher_notes": [],
        "market": {
            "top_volume_stocks": {
                "data": [
                    {"name": "五粮液", "amount_yi": 80.0},
                    {"name": "贵州茅台", "amount_yi": 100.0},
                ]
            },
            "concept_moneyflow_ths": {
                "data": [
                    {
                        "name": "高端白酒",
                        "lead_stock": "贵州茅台",
                        "net_amount_yi": 30.0,
                    }
                ]
            },
        },
    }
    history = [
        {
            "stock_code": "600519.SH",
            "stock_name": "贵州茅台",
            "sector": "饮料制造",
        }
    ]

    rows = build_candidates(
        prefill=prefill,
        trend_pool=[],
        history=history,
        min_amount_yi=20.0,
    )["top_leaders"]
    by_sector = {item["sector"]: item for item in rows}

    assert by_sector["白酒"]["code"] == "000858"
    assert by_sector["白酒"]["stock_code"] == "000858"
    assert by_sector["饮料制造"]["stock_code"] == "600519"
    assert by_sector["饮料制造"]["stock_name"] == "贵州茅台"
    assert by_sector["饮料制造"]["source_sector"] == "高端白酒"
    assert "高端白酒" in by_sector["饮料制造"]["source_sectors"]
    assert any(
        "同花顺概念资金流" in row["text"]
        for row in by_sector["饮料制造"]["evidence"]
    )


def test_distinct_trusted_codes_for_same_name_do_not_enrich_name_only_flow():
    prefill = {
        "step5_leaders": {
            "top_leaders": [
                {
                    "stock": "贵州茅台",
                    "code": "000858.SZ",
                    "sector": "白酒",
                    "attribute_type": "趋势中军",
                }
            ]
        },
        "teacher_notes": [],
        "market": {
            "top_volume_stocks": {
                "data": [{"name": "贵州茅台", "amount_yi": 100.0}]
            },
            "concept_moneyflow_ths": {
                "data": [
                    {
                        "name": "高端白酒",
                        "lead_stock": "贵州茅台",
                        "net_amount_yi": 30.0,
                    }
                ]
            },
        },
    }
    history = [
        {
            "stock_code": "600519.SH",
            "stock_name": "贵州茅台",
            "sector": "饮料制造",
        }
    ]

    rows = build_candidates(
        prefill=prefill,
        trend_pool=[],
        history=history,
        min_amount_yi=20.0,
    )["top_leaders"]
    flow = next(item for item in rows if item["sector"] == "未分类")

    assert "stock_code" not in flow
    assert "code" not in flow
    assert flow["source_sector"] == "高端白酒"


def test_invalid_history_stock_code_is_ignored_and_cannot_override_quote_code():
    prefill = {
        "step5_leaders": {
            "top_leaders": [
                {"stock": "海光信息", "sector": "旧概念", "attribute_type": "旧属性"}
            ]
        },
        "teacher_notes": [],
        "market": {
            "stock_quotes": {
                "data": [
                    {
                        "code": "688041.sh",
                        "name": "海光信息",
                        "pct_chg": 12.0,
                        "amount_yi": 80.0,
                        "sw_l2": "半导体",
                        "sector_source": "tushare:index_member_all",
                    }
                ]
            }
        },
    }
    history = [
        {"stock_code": "海光信息", "stock_name": "海光信息", "sector": "旧行业"}
    ]

    rows = build_candidates(prefill=prefill, trend_pool=[], history=history)["top_leaders"]

    assert len(rows) == 1
    assert rows[0]["stock_code"] == "688041"
    assert rows[0]["code"] == "688041"
    assert rows[0]["sector"] == "半导体"


def test_conflicting_legal_history_codes_do_not_infer_identity_for_name_only_flow():
    prefill = {
        "step5_leaders": None,
        "teacher_notes": [],
        "market": {
            "top_volume_stocks": {"data": [{"name": "同名样本", "amount_yi": 60.0}]},
            "concept_moneyflow_ths": {
                "data": [
                    {"name": "冲突概念", "lead_stock": "同名样本", "net_amount_yi": 30.0}
                ]
            },
        },
    }
    history = [
        {"stock_code": "600001.SH", "stock_name": "同名样本", "sector": "历史行业甲"},
        {"stock_code": "000001.sz", "stock_name": "同名样本", "sector": "历史行业乙"},
    ]

    rows = build_candidates(
        prefill=prefill,
        trend_pool=[],
        history=history,
        min_amount_yi=20.0,
    )["top_leaders"]
    flow = next(item for item in rows if item["source_sector"] == "冲突概念")

    assert flow["sector"] == "未分类"
    assert "stock_code" not in flow
    assert "code" not in flow


def test_distinct_legal_codes_with_same_name_and_sector_remain_distinct():
    history = [
        {"stock_code": "600001.SH", "stock_name": "同名样本", "sector": "同一行业"},
        {"stock_code": "000001.SZ", "stock_name": "同名样本", "sector": "同一行业"},
    ]

    rows = build_candidates(
        prefill={"step5_leaders": None, "teacher_notes": []},
        trend_pool=[],
        history=history,
    )["top_leaders"]

    assert {item["stock_code"] for item in rows} == {"600001", "000001"}


def test_equivalent_history_codes_enrich_cross_sector_candidates_with_one_identity():
    prefill = {
        "step5_leaders": None,
        "teacher_notes": [],
        "market": {
            "top_volume_stocks": {"data": [{"name": "贵州茅台", "amount_yi": 100.0}]},
            "concept_moneyflow_ths": {
                "data": [
                    {"name": "高端白酒", "lead_stock": "贵州茅台", "net_amount_yi": 30.0}
                ]
            },
        },
    }
    history = [
        {"stock_code": "600519", "stock_name": "贵州茅台", "sector": "饮料制造"},
        {"stock_code": "600519.SH", "stock_name": "贵州茅台", "sector": "饮料制造"},
    ]

    rows = build_candidates(
        prefill=prefill,
        trend_pool=[],
        history=history,
        min_amount_yi=20.0,
    )["top_leaders"]

    assert {item["sector"] for item in rows} == {"饮料制造"}
    assert {(item["stock_code"], item["code"]) for item in rows} == {
        ("600519", "600519")
    }


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

    assert [item["stock"] for item in result["top_leaders"]] == ["工业富联"]


def test_build_candidates_filters_st_stocks_from_non_trend_sources():
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
    assert by_stock["绿的谐波"]["sector"] == "未分类"
    assert by_stock["绿的谐波"]["source_sector"] == "同花顺新质50"
    assert "净流入 91.0 亿" in by_stock["绿的谐波"]["evidence"][0]["text"]
    assert "埃斯顿" in by_stock
    assert by_stock["埃斯顿"]["sector"] == "未分类"
    assert by_stock["埃斯顿"]["source_sector"] == "机器视觉"
    assert "榜单排名 2" in by_stock["埃斯顿"]["evidence"][0]["text"]
    assert "ST臻镭" not in by_stock


def test_build_candidates_prioritizes_intraday_strength_over_flow_amount():
    prefill = {
        "step5_leaders": None,
        "teacher_notes": [],
        "cognitions_by_step": {},
        "market": {
            "stock_quotes": {
                "data": [
                    {
                        "code": "301269.SZ",
                        "name": "华大九天",
                        "pct_chg": 14.05,
                        "amount_yi": 39.0,
                    },
                    {
                        "code": "688206.SH",
                        "name": "概伦电子",
                        "pct_chg": 20.01,
                        "amount_yi": 11.52,
                    },
                ]
            },
            "concept_moneyflow_dc": {
                "data": [
                    {
                        "name": "EDA概念",
                        "buy_sm_amount_stock": "华大九天",
                        "net_amount_yi": 1.92,
                        "pct_change": 2.88,
                        "rank": 7,
                    }
                ]
            },
        },
    }

    result = build_candidates(prefill=prefill, trend_pool=[], history=[])

    stocks = [item["stock"] for item in result["top_leaders"][:2]]
    by_stock = {item["stock"]: item for item in result["top_leaders"]}
    assert stocks == ["概伦电子", "华大九天"]
    assert by_stock["概伦电子"]["board_type"] == "20cm"
    assert "个股涨幅 20.01%" in by_stock["概伦电子"]["evidence"][0]["text"]
    assert "成交额 11.52 亿" in by_stock["概伦电子"]["evidence"][0]["text"]


def test_quote_strength_normalizes_mislabelled_amount_yi():
    prefill = {
        "step5_leaders": None,
        "teacher_notes": [],
        "market": {
            "stock_quotes": {
                "data": [
                    {
                        "code": "688039.SH",
                        "name": "当虹科技",
                        "pct_chg": 20.0,
                        "amount_yi": 335890.03,
                    }
                ]
            }
        },
    }

    result = build_candidates(prefill=prefill, trend_pool=[], history=[])

    item = result["top_leaders"][0]
    assert item["attribute"] == "日内涨幅 20.0% / 成交额 3.36 亿"
    assert "成交额 3.36 亿" in item["evidence"][0]["text"]


def test_quote_strength_uses_amount_after_limit_strength_bucket():
    prefill = {
        "step5_leaders": None,
        "teacher_notes": [],
        "market": {
            "stock_quotes": {
                "data": [
                    {"code": "688206.SH", "name": "概伦电子", "pct_chg": 20.0098, "amount_yi": 11.52},
                    {"code": "300502.SZ", "name": "中石科技", "pct_chg": 19.9968, "amount_yi": 33.57},
                    {"code": "688039.SH", "name": "当虹科技", "pct_chg": 20.0, "amount_yi": 3.36},
                ]
            }
        },
    }

    result = build_candidates(prefill=prefill, trend_pool=[], history=[])

    assert [item["stock"] for item in result["top_leaders"][:3]] == [
        "中石科技",
        "概伦电子",
        "当虹科技",
    ]


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

    item = next(item for item in result["top_leaders"] if item["stock"] == "有研硅")
    evidence = "；".join(e["text"] for e in item["evidence"])
    assert item["teacher_alignment"] == "支持"
    assert "老师明确支持主线预填票" in evidence
    assert "个股涨幅 12.34%" in evidence
    assert "成交额 18.8 亿" in evidence
    assert "板块资金净流入 36.0 亿" in evidence


def test_build_candidates_filters_below_min_amount_for_confirmation_pool():
    prefill = {
        "step5_leaders": {
            "top_leaders": [
                {"stock": "容量达标", "sector": "半导体", "attribute_type": "趋势中军"},
                {"stock": "低额预填", "sector": "半导体", "attribute_type": "趋势中军"},
            ]
        },
        "teacher_notes": [],
        "market": {
            "stock_quotes": {
                "data": [
                    {"code": "688001.SH", "name": "容量达标", "pct_chg": 10.01, "amount_yi": 20.0},
                    {"code": "688002.SH", "name": "低额预填", "pct_chg": 10.01, "amount_yi": 19.99},
                    {"code": "688003.SH", "name": "低额涨停", "pct_chg": 20.0, "amount_yi": 11.52},
                    {"code": "688004.SH", "name": "高额涨停", "pct_chg": 20.0, "amount_yi": 33.57},
                    {"code": "688005.SH", "name": "低额资金", "pct_chg": 8.0, "amount_yi": 18.8},
                    {"code": "688006.SH", "name": "高额资金", "pct_chg": 8.0, "amount_yi": 22.0},
                ]
            },
            "concept_moneyflow_ths": {
                "data": [
                    {"name": "芯片", "lead_stock": "低额资金", "net_amount_yi": 90.0, "pct_change_stock": 8.0},
                    {"name": "芯片", "lead_stock": "高额资金", "net_amount_yi": 80.0, "pct_change_stock": 8.0},
                ]
            },
        },
    }
    history = [
        {"stock_name": "低额历史", "sector": "算力"},
        {"stock_name": "高额历史", "sector": "算力"},
    ]
    prefill["market"]["stock_quotes"]["data"].extend(
        [
            {"code": "601001.SH", "name": "低额历史", "pct_chg": 3.0, "amount_yi": 12.0},
            {"code": "601002.SH", "name": "高额历史", "pct_chg": 3.0, "amount_yi": 25.0},
        ]
    )

    result = build_candidates(prefill=prefill, trend_pool=[], history=history, min_amount_yi=20.0)

    assert [item["stock"] for item in result["top_leaders"]] == [
        "高额涨停",
        "容量达标",
        "高额资金",
        "高额历史",
    ]


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


def test_quote_strength_uses_sw_l2_sector_and_separate_board_type():
    prefill = {
        "step5_leaders": None,
        "teacher_notes": [],
        "market": {
            "stock_quotes": {
                "data": [
                    {
                        "code": "688041.SH",
                        "name": "海光信息",
                        "pct_chg": 20.0,
                        "amount_yi": 88.0,
                        "sw_l2": "半导体",
                        "sector_source": "tushare:index_member_all",
                        "limit_height": 3,
                    }
                ]
            }
        },
    }

    result = build_candidates(prefill=prefill, trend_pool=[], history=[])

    item = result["top_leaders"][0]
    assert item["sector"] == "半导体"
    assert item["sector_source"] == "tushare:index_member_all"
    assert item["source_sector"] == "日内强势"
    assert item["board_type"] == "20cm"
    assert item["amount_yi"] == 88.0
    assert item["pct_chg"] == 20.0
    assert item["limit_height"] == 3
    assert isinstance(item["_selection_score"], float)


def test_quote_strength_uses_unclassified_instead_of_intraday_as_sector():
    prefill = {
        "step5_leaders": None,
        "teacher_notes": [],
        "market": {
            "stock_quotes": {
                "data": [
                    {
                        "code": "600001.SH",
                        "name": "主板样本",
                        "pct_chg": 10.0,
                        "amount_yi": 30.0,
                    }
                ]
            }
        },
    }

    item = build_candidates(prefill=prefill, trend_pool=[], history=[])["top_leaders"][0]

    assert item["sector"] == "未分类"
    assert item["source_sector"] == "日内强势"
    assert item["board_type"] == "10cm"


@pytest.mark.parametrize(
    ("code", "pct_chg", "expected"),
    [
        ("830799", 30.0, "30cm"),
        ("430047", 30.0, "30cm"),
        ("920001", 30.0, "30cm"),
        ("600001.BJ", 30.0, "30cm"),
        ("900901.SH", 10.0, "10cm"),
        ("900901", 10.0, "10cm"),
        ("430047", 9.69, "非涨停"),
        ("920001.BJ", 9.69, "非涨停"),
        ("830799", 9.69, "非涨停"),
    ],
)
def test_board_attribute_uses_canonical_price_limit_board_mapping(
    code: str,
    pct_chg: float,
    expected: str,
):
    assert _board_attribute(code, pct_chg) == expected


def test_market_flow_preserves_source_sector_but_groups_by_sw_l2():
    prefill = {
        "step5_leaders": None,
        "teacher_notes": [],
        "market": {
            "stock_quotes": {
                "data": [
                    {
                        "code": "688017.SH",
                        "name": "绿的谐波",
                        "pct_chg": 8.0,
                        "amount_yi": 31.5,
                        "sw_l2": "机器人",
                        "sector_source": "tushare:index_member_all",
                    }
                ]
            },
            "concept_moneyflow_ths": {
                "data": [
                    {
                        "name": "同花顺新质50",
                        "lead_stock": "绿的谐波",
                        "net_amount_yi": 91.0,
                        "pct_change_stock": 8.0,
                    }
                ]
            },
        },
    }

    item = build_candidates(prefill=prefill, trend_pool=[], history=[])["top_leaders"][0]

    assert item["stock"] == "绿的谐波"
    assert item["sector"] == "机器人"
    assert item["sector_source"] == "tushare:index_member_all"
    assert item["source_sector"] == "同花顺新质50"
    assert item["board_type"] == "非涨停"
    assert item["amount_yi"] == 31.5
    assert item["pct_chg"] == 8.0


def test_market_flow_without_sw_match_is_unclassified_and_keeps_source_only():
    prefill = {
        "step5_leaders": None,
        "teacher_notes": [],
        "market": {
            "stock_quotes": {
                "data": [
                    {
                        "code": "600001.SH",
                        "name": "未映射领涨",
                        "pct_chg": 8.0,
                        "amount_yi": 30.0,
                        "sw_l2": "",
                        "sector_source": "",
                    }
                ]
            },
            "concept_moneyflow_ths": {
                "data": [
                    {
                        "name": "原始热门概念",
                        "lead_stock": "未映射领涨",
                        "net_amount_yi": 20.0,
                    }
                ]
            },
        },
    }

    item = build_candidates(prefill=prefill, trend_pool=[], history=[])["top_leaders"][0]

    assert item["sector"] == "未分类"
    assert item["source_sector"] == "原始热门概念"
    assert item.get("sector_source", "") == ""


def test_stock_quotes_source_rows_are_indexed_once_for_many_candidates():
    class CountingRows(list):
        def __init__(self, rows):
            super().__init__(rows)
            self.iteration_count = 0

        def __iter__(self):
            self.iteration_count += 1
            return super().__iter__()

    quote_rows = CountingRows(
        [
            {
                "code": f"600{i:03d}.SH",
                "name": f"样本{i:03d}",
                "pct_chg": 8.0,
                "amount_yi": 30.0,
                "sw_l2": "软件开发",
                "sector_source": "tushare:index_member_all",
            }
            for i in range(100)
        ]
    )
    prefill = {
        "step5_leaders": {
            "top_leaders": [
                {"stock": f"样本{i:03d}", "sector": "旧概念", "attribute_type": "旧属性"}
                for i in range(50)
            ]
        },
        "teacher_notes": [],
        "market": {
            "stock_quotes": {"data": quote_rows},
            "concept_moneyflow_ths": {
                "data": [
                    {
                        "name": f"概念{i:03d}",
                        "lead_stock": f"样本{i:03d}",
                        "net_amount_yi": 20.0,
                    }
                    for i in range(50, 100)
                ]
            },
        },
    }

    rows = build_candidates(prefill=prefill, trend_pool=[], history=[])["top_leaders"]

    assert len(rows) == 100
    assert quote_rows.iteration_count == 1


def test_prefill_and_history_candidates_gain_matching_quote_facts():
    prefill = {
        "step5_leaders": {
            "top_leaders": [
                {"stock": "海光信息", "sector": "芯片", "attribute_type": "旧属性"}
            ]
        },
        "teacher_notes": [],
        "market": {
            "stock_quotes": {
                "data": [
                    {
                        "code": "688041.SH",
                        "name": "海光信息",
                        "pct_chg": 12.0,
                        "amount_yi": 80.0,
                        "sw_l2": "半导体",
                        "sector_source": "tushare:index_member_all",
                        "limit_height": 0,
                    },
                    {
                        "code": "600519.SH",
                        "name": "贵州茅台",
                        "pct_chg": 2.0,
                        "amount_yi": 55.0,
                        "sw_l2": "白酒",
                        "sector_source": "tushare:index_member_all",
                    },
                ]
            }
        },
    }
    history = [{"stock_code": "600519.SH", "stock_name": "贵州茅台", "sector": "饮料制造"}]

    rows = build_candidates(prefill=prefill, trend_pool=[], history=history)["top_leaders"]
    by_name = {item["stock"].split()[-1]: item for item in rows}

    assert by_name["海光信息"]["sector"] == "半导体"
    assert by_name["海光信息"]["sector_source"] == "tushare:index_member_all"
    assert by_name["海光信息"]["board_type"] == "20cm"
    assert by_name["海光信息"]["amount_yi"] == 80.0
    assert by_name["贵州茅台"]["sector"] == "白酒"
    assert by_name["贵州茅台"]["sector_source"] == "tushare:index_member_all"
    assert by_name["贵州茅台"]["board_type"] == "非涨停"
    assert by_name["贵州茅台"]["amount_yi"] == 55.0


def test_prefill_history_and_flow_share_matched_sw_l2_grouping_sector():
    prefill = {
        "step5_leaders": {
            "top_leaders": [
                {"stock": "预填芯片", "sector": "旧预填概念", "attribute_type": "旧属性"}
            ]
        },
        "teacher_notes": [],
        "market": {
            "stock_quotes": {
                "data": [
                    {
                        "code": "688001.SH",
                        "name": "预填芯片",
                        "pct_chg": 8.0,
                        "amount_yi": 30.0,
                        "sw_l2": "半导体",
                        "sector_source": "tushare:index_member_all",
                    },
                    {
                        "code": "688002.SH",
                        "name": "历史芯片",
                        "pct_chg": 7.0,
                        "amount_yi": 25.0,
                        "sw_l2": "半导体",
                        "sector_source": "tushare:index_member_all",
                    },
                    {
                        "code": "688003.SH",
                        "name": "资金芯片",
                        "pct_chg": 6.0,
                        "amount_yi": 22.0,
                        "sw_l2": "半导体",
                        "sector_source": "tushare:index_member_all",
                    },
                ]
            },
            "concept_moneyflow_ths": {
                "data": [
                    {
                        "name": "先进封装概念",
                        "lead_stock": "资金芯片",
                        "net_amount_yi": 20.0,
                        "pct_change_stock": 6.0,
                    }
                ]
            },
        },
    }
    history = [
        {"stock_code": "688002.SH", "stock_name": "历史芯片", "sector": "旧历史行业"}
    ]

    rows = build_candidates(prefill=prefill, trend_pool=[], history=history)["top_leaders"]
    by_name = {item["stock"].split()[-1]: item for item in rows}

    assert set(by_name) == {"预填芯片", "历史芯片", "资金芯片"}
    assert {item["sector"] for item in by_name.values()} == {"半导体"}
    assert {item["sector_source"] for item in by_name.values()} == {
        "tushare:index_member_all"
    }
    assert by_name["资金芯片"]["source_sector"] == "先进封装概念"


def test_prefill_and_history_without_matching_quote_do_not_fake_market_facts():
    prefill = {
        "step5_leaders": {
            "top_leaders": [
                {"stock": "无行情预填", "sector": "半导体", "attribute_type": "旧属性"}
            ]
        },
        "teacher_notes": [],
        "market": {
            "stock_quotes": {
                "data": [
                    {
                        "code": "600000.SH",
                        "name": "其他股票",
                        "pct_chg": 10.0,
                        "amount_yi": 30.0,
                    }
                ]
            }
        },
    }
    history = [
        {
            "stock_code": "688999.SH",
            "stock_name": "无行情历史",
            "sector": "软件开发",
        }
    ]

    rows = build_candidates(prefill=prefill, trend_pool=[], history=history)["top_leaders"]
    by_name = {item["stock"].split()[-1]: item for item in rows}

    for stock, sector in (("无行情预填", "半导体"), ("无行情历史", "软件开发")):
        item = by_name[stock]
        assert item["sector"] == sector
        assert "board_type" not in item
        assert "limit_height" not in item
        assert "amount_yi" not in item
        assert "pct_chg" not in item


def test_matching_quote_only_adds_board_and_limit_facts_when_source_fields_exist():
    prefill = {
        "step5_leaders": {
            "top_leaders": [
                {"stock": "缺字段样本", "sector": "旧概念", "attribute_type": "旧属性"}
            ]
        },
        "teacher_notes": [],
        "market": {
            "stock_quotes": {
                "data": [
                    {
                        "code": "688888.SH",
                        "name": "缺字段样本",
                        "amount_yi": 25.0,
                        "sw_l2": "半导体",
                        "sector_source": "tushare:index_member_all",
                    }
                ]
            }
        },
    }

    item = build_candidates(prefill=prefill, trend_pool=[], history=[])["top_leaders"][0]

    assert item["sector"] == "半导体"
    assert item["amount_yi"] == 25.0
    assert "pct_chg" not in item
    assert "board_type" not in item
    assert "limit_height" not in item


@pytest.mark.parametrize("amount_yi", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_quote_amount_is_treated_as_missing(amount_yi):
    prefill = {
        "step5_leaders": None,
        "teacher_notes": [],
        "market": {
            "stock_quotes": {
                "data": [
                    {
                        "code": "600001.SH",
                        "name": "金额异常样本",
                        "pct_chg": 10.0,
                        "amount_yi": amount_yi,
                    }
                ]
            }
        },
    }

    result = build_candidates(prefill=prefill, trend_pool=[], history=[])

    item = result["top_leaders"][0]
    assert item["amount_yi"] is None
    json.dumps(result, ensure_ascii=False, allow_nan=False)
    filtered = build_candidates(
        prefill=prefill,
        trend_pool=[],
        history=[],
        min_amount_yi=20.0,
    )
    assert filtered["top_leaders"] == []


@pytest.mark.parametrize("pct_chg", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_quote_pct_is_treated_as_missing(pct_chg):
    prefill = {
        "step5_leaders": None,
        "teacher_notes": [],
        "market": {
            "stock_quotes": {
                "data": [
                    {
                        "code": "600001.SH",
                        "name": "涨幅异常样本",
                        "pct_chg": pct_chg,
                        "amount_yi": 30.0,
                    }
                ]
            }
        },
    }

    result = build_candidates(prefill=prefill, trend_pool=[], history=[])

    assert result["top_leaders"] == []
    json.dumps(result, ensure_ascii=False, allow_nan=False)


@pytest.mark.parametrize("limit_height", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_limit_height_is_not_emitted(limit_height):
    prefill = {
        "step5_leaders": {
            "top_leaders": [
                {"stock": "高度异常样本", "sector": "旧概念", "attribute_type": "旧属性"}
            ]
        },
        "teacher_notes": [],
        "market": {
            "stock_quotes": {
                "data": [
                    {
                        "code": "600001.SH",
                        "name": "高度异常样本",
                        "pct_chg": 10.0,
                        "amount_yi": 30.0,
                        "limit_height": limit_height,
                    }
                ]
            }
        },
    }

    item = build_candidates(prefill=prefill, trend_pool=[], history=[])["top_leaders"][0]

    assert "limit_height" not in item


def test_explicit_zero_limit_height_is_preserved_but_missing_height_is_not_emitted():
    prefill = {
        "step5_leaders": {
            "top_leaders": [
                {"stock": "显式零高度", "sector": "旧概念", "attribute_type": "旧属性"},
                {"stock": "缺失高度", "sector": "旧概念", "attribute_type": "旧属性"},
            ]
        },
        "teacher_notes": [],
        "market": {
            "stock_quotes": {
                "data": [
                    {
                        "code": "600001.SH",
                        "name": "显式零高度",
                        "pct_chg": 10.0,
                        "amount_yi": 30.0,
                        "limit_height": 0,
                    },
                    {
                        "code": "600002.SH",
                        "name": "缺失高度",
                        "pct_chg": 10.0,
                        "amount_yi": 30.0,
                    },
                ]
            }
        },
    }

    rows = build_candidates(prefill=prefill, trend_pool=[], history=[])["top_leaders"]
    by_name = {item["stock"]: item for item in rows}

    assert by_name["显式零高度"]["limit_height"] == 0
    assert "limit_height" not in by_name["缺失高度"]


@pytest.mark.parametrize("pct_chg", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_matched_quote_pct_is_not_added_to_prefill_candidate(pct_chg):
    prefill = {
        "step5_leaders": {
            "top_leaders": [
                {"stock": "预填异常涨幅", "sector": "旧概念", "attribute_type": "旧属性"}
            ]
        },
        "teacher_notes": [],
        "market": {
            "stock_quotes": {
                "data": [
                    {
                        "code": "688888.SH",
                        "name": "预填异常涨幅",
                        "pct_chg": pct_chg,
                        "amount_yi": 25.0,
                        "sw_l2": "半导体",
                        "sector_source": "tushare:index_member_all",
                    }
                ]
            }
        },
    }

    item = build_candidates(prefill=prefill, trend_pool=[], history=[])["top_leaders"][0]

    assert item["sector"] == "半导体"
    assert item["amount_yi"] == 25.0
    assert "pct_chg" not in item
    assert "board_type" not in item


@pytest.mark.parametrize("pct_chg", [True, False])
def test_boolean_matched_quote_pct_is_treated_as_missing(pct_chg):
    prefill = {
        "step5_leaders": {
            "top_leaders": [
                {"stock": "布尔涨幅", "sector": "旧概念", "attribute_type": "旧属性"}
            ]
        },
        "teacher_notes": [],
        "market": {
            "stock_quotes": {
                "data": [
                    {
                        "code": "688889.SH",
                        "name": "布尔涨幅",
                        "pct_chg": pct_chg,
                        "amount_yi": 25.0,
                        "sw_l2": "半导体",
                        "sector_source": "tushare:index_member_all",
                    }
                ]
            }
        },
    }

    item = build_candidates(prefill=prefill, trend_pool=[], history=[])["top_leaders"][0]

    assert item["sector"] == "半导体"
    assert item["amount_yi"] == 25.0
    assert "pct_chg" not in item
    assert "board_type" not in item


@pytest.mark.parametrize("code", ["", "BAD", "600001.BAD"])
def test_board_attribute_does_not_guess_limit_board_for_invalid_code(code):
    assert _board_attribute(code, 10.0) is None


def test_quote_strength_with_invalid_code_omits_board_type():
    result = build_candidates(
        prefill={
            "step5_leaders": None,
            "teacher_notes": [],
            "market": {
                "stock_quotes": {
                    "data": [
                        {
                            "code": "BAD",
                            "name": "未知板型",
                            "pct_chg": 10.0,
                            "amount_yi": 30.0,
                            "sw_l2": "软件开发",
                        }
                    ]
                }
            },
        },
        trend_pool=[],
        history=[],
    )

    item = result["top_leaders"][0]
    assert item["stock"] == "未知板型"
    assert "board_type" not in item


def test_intraday_strength_merges_matching_concept_flow_evidence():
    result = build_candidates(
        prefill={
            "step5_leaders": None,
            "teacher_notes": [],
            "market": {
                "stock_quotes": {
                    "data": [
                        {
                            "code": "300001.SZ",
                            "name": "合流科技",
                            "pct_chg": 20.0,
                            "amount_yi": 45.0,
                            "sw_l2": "软件开发",
                            "sector_source": "tushare:index_member_all",
                        }
                    ]
                },
                "concept_moneyflow_ths": {
                    "data": [
                        {
                            "name": "AI 应用",
                            "lead_stock": "合流科技",
                            "net_amount_yi": 25.0,
                            "pct_change_stock": 20.0,
                            "rank": 1,
                        }
                    ]
                },
            },
        },
        trend_pool=[],
        history=[],
    )

    assert len(result["top_leaders"]) == 1
    item = result["top_leaders"][0]
    assert item["stock_code"] == "300001"
    assert item["sector"] == "软件开发"
    assert item["source_sector"] == "AI 应用"
    assert set(item["source_sectors"]) == {"日内强势", "AI 应用"}
    evidence_text = "\n".join(row["text"] for row in item["evidence"])
    assert "日内涨幅强度候选" in evidence_text
    assert "同花顺概念资金流" in evidence_text
