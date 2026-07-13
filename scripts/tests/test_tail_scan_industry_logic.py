"""tail-scan 个股产业逻辑与近期证据聚合（全 fake / 内存数据）。"""
from __future__ import annotations

import json
import sqlite3

import pytest

from providers.base import DataResult
from services.tail_scan import industry_logic
from services.tail_scan import renderer
from services.tail_scan import scorer


def _profile(
    code: str,
    status: str,
    *,
    source: str = "",
    business: str = "",
    scope: str = "",
    types=None,
    products=None,
):
    return {
        "ts_code": code,
        "profile_status": status,
        "introduction": "",
        "main_business": business,
        "business_scope": scope,
        "product_types": types or [],
        "product_names": products or [],
        "source": source,
        "error": "",
    }


class Registry:
    def __init__(self, primary=None, fallback=None, *, primary_source="tushare:stock_company"):
        self.primary = primary or {}
        self.fallback = fallback or {}
        self.primary_source = primary_source
        self.calls = []
        self.specific_calls = []

    def call(self, capability, codes):
        assert capability == "get_stock_business_profiles"
        self.calls.append((capability, codes))
        return DataResult(data=self.primary, source=self.primary_source)

    def call_specific(self, provider, capability, codes):
        assert provider == "akshare"
        assert capability == "get_stock_business_profiles"
        self.specific_calls.append((provider, capability, codes))
        return DataResult(data=self.fallback, source="akshare:stock_zyjs_ths")


@pytest.fixture
def conn():
    db = sqlite3.connect(":memory:")
    db.execute(
        "CREATE TABLE teacher_notes "
        "(id INTEGER, date TEXT, title TEXT, mentioned_stocks TEXT)"
    )
    db.execute(
        "CREATE TABLE industry_info "
        "(id INTEGER, date TEXT, sector_name TEXT, content TEXT, source TEXT)"
    )
    try:
        yield db
    finally:
        db.close()


def test_partial_tushare_result_only_falls_back_for_non_ok_codes():
    registry = Registry(
        primary={
            "688106.SH": _profile(
                "688106.SH", "ok", source="tushare:stock_company", business="气体研发"
            ),
            "605090.SH": _profile("605090.SH", "missing"),
        },
        fallback={
            "605090.SH": _profile(
                "605090.SH",
                "ok",
                source="akshare:stock_zyjs_ths",
                business="清洁能源综合服务",
                products=["LNG", "液氦"],
            )
        },
    )

    profiles = industry_logic._load_profiles(
        registry, ["605090", "688106.SH", "688106"]
    )

    assert registry.calls == [
        ("get_stock_business_profiles", ["605090.SH", "688106.SH"])
    ]
    assert registry.specific_calls == [
        ("akshare", "get_stock_business_profiles", ["605090.SH"])
    ]
    assert profiles["688106.SH"]["source"] == "tushare:stock_company"
    assert profiles["605090.SH"]["source"] == "akshare:stock_zyjs_ths"


def test_profile_calls_that_raise_degrade_to_per_stock_source_failed():
    class RaisingRegistry:
        def call(self, capability, codes):
            raise RuntimeError("registry bug")

    profiles = industry_logic._load_profiles(RaisingRegistry(), ["688106.SH"])
    assert profiles["688106.SH"]["profile_status"] == "source_failed"


def test_call_specific_exception_does_not_escape_batch():
    class RaisingFallbackRegistry(Registry):
        def call_specific(self, provider, capability, codes):
            raise RuntimeError("fallback bug")

    registry = RaisingFallbackRegistry(
        primary={"688106.SH": _profile("688106.SH", "source_failed")}
    )
    profiles = industry_logic._load_profiles(registry, ["688106.SH"])
    assert profiles["688106.SH"]["profile_status"] == "source_failed"


def test_registry_result_already_from_akshare_is_used_without_second_fallback():
    registry = Registry(
        primary={"688106.SH": _profile("688106.SH", "missing")},
        primary_source="akshare:stock_zyjs_ths",
    )
    profiles = industry_logic._load_profiles(registry, ["688106.SH"])
    assert profiles["688106.SH"]["profile_status"] == "missing"
    assert registry.specific_calls == []


@pytest.mark.parametrize(
    ("primary_status", "fallback_status", "expected"),
    [
        ("source_failed", "source_failed", "source_failed"),
        ("missing", "missing", "missing"),
        ("source_failed", "missing", "missing"),
        ("missing", "source_failed", "missing"),
    ],
)
def test_both_business_sources_failed_and_both_missing_are_distinct(
    primary_status, fallback_status, expected
):
    registry = Registry(
        primary={"688106.SH": _profile("688106.SH", primary_status)},
        fallback={"688106.SH": _profile("688106.SH", fallback_status)},
    )
    profiles = industry_logic._load_profiles(registry, ["688106.SH"])
    assert profiles["688106.SH"]["profile_status"] == expected


def test_industry_position_only_reuses_supplied_business_and_products():
    assert industry_logic._industry_position(
        "电子化学品", "气体研发和销售", ["超纯氨"]
    ) == "电子化学品产业链企业，核心产品包括超纯氨"
    cases = [
        ("电子化学品", "气体研发和销售", [], "电子化学品领域企业，主营气体研发和销售"),
        ("电子化学品", "", [], "电子化学品相关企业"),
        ("", "气体研发和销售", [], "主营气体研发和销售"),
        ("", "", [], ""),
    ]
    for sw_l2, business, products, expected in cases:
        text = industry_logic._industry_position(sw_l2, business, products)
        assert text == expected
        for invented in ("上游", "国产替代", "涨价", "市占率"):
            assert invented not in text


def test_code_normalizes_bse_shanghai_and_shenzhen():
    assert industry_logic._code("430047") == "430047.BJ"
    assert industry_logic._code("920001") == "920001.BJ"
    assert industry_logic._code("688106") == "688106.SH"
    assert industry_logic._code("000001") == "000001.SZ"


def test_teacher_note_matches_exact_normalized_code(conn):
    conn.execute(
        "INSERT INTO teacher_notes(id,date,title,mentioned_stocks) VALUES(1,?,?,?)",
        (
            "2026-07-12",
            "冰点修复期控仓试错与硬科技轮动",
            json.dumps(
                [{"code": "605090", "name": "九丰能源", "reason": "液氦链条跟踪"}],
                ensure_ascii=False,
            ),
        ),
    )
    evidence, ok = industry_logic._read_teacher_evidence(
        conn, "2026-06-13", "2026-07-13", {"605090.SH": "九丰能源"}
    )
    assert ok is True
    assert evidence["605090.SH"][0]["label"] == "老师观点·个股"
    assert evidence["605090.SH"][0]["text"] == "液氦链条跟踪"


def test_teacher_code_and_huibo_name_require_exact_match(conn, tmp_path):
    conn.execute(
        "INSERT INTO teacher_notes(id,date,title,mentioned_stocks) VALUES(1,?,?,?)",
        (
            "2026-07-12",
            "近似代码",
            json.dumps([{"code": "605091", "reason": "不应命中"}], ensure_ascii=False),
        ),
    )
    teacher, teacher_ok = industry_logic._read_teacher_evidence(
        conn, "2026-06-13", "2026-07-13", {"605090.SH": "九丰能源"}
    )
    (tmp_path / "2026-07-12.json").write_text(
        json.dumps(
            {
                "reader_results": [
                    {
                        "reader": {
                            "mentioned_stocks": [
                                {"name": "九丰", "viewpoint": "不应命中", "source": "正文"}
                            ]
                        }
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    huibo, huibo_ok = industry_logic._read_huibo_evidence(
        tmp_path, "2026-06-13", "2026-07-13", {"605090.SH": "九丰能源"}
    )
    assert teacher_ok is True and huibo_ok is True
    assert teacher.get("605090.SH", []) == []
    assert huibo.get("605090.SH", []) == []


def test_teacher_bad_json_shapes_are_skipped_and_empty_text_uses_fallback(conn):
    conn.executemany(
        "INSERT INTO teacher_notes(id,date,title,mentioned_stocks) VALUES(?,?,?,?)",
        [
            (1, "2026-07-10", "坏JSON", "{"),
            (2, "2026-07-11", "非数组", json.dumps({"code": "605090"})),
            (3, "2026-07-12", "直接提及", json.dumps(["x", {"code": "605090"}])),
        ],
    )
    evidence, ok = industry_logic._read_teacher_evidence(
        conn, "2026-06-13", "2026-07-13", {"605090.SH": "九丰能源"}
    )
    assert ok is True
    assert evidence["605090.SH"][0]["text"] == "老师笔记直接提及九丰能源"


def test_huibo_empty_viewpoint_keeps_relation_without_expansion(tmp_path):
    payload = {
        "reader_results": [
            {
                "title": "华丰科技深度",
                "huibo_list_time": "2026-07-10",
                "reader": {
                    "pdf_report_date": "2026-07-08",
                    "mentioned_stocks": [
                        {
                            "name": "四川长虹",
                            "viewpoint": "",
                            "source": "控股股东关系 / 5 / 股权结构",
                        }
                    ],
                },
            }
        ]
    }
    (tmp_path / "2026-07-12.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    evidence, ok = industry_logic._read_huibo_evidence(
        tmp_path, "2026-06-13", "2026-07-13", {"600839.SH": "四川长虹"}
    )
    item = evidence["600839.SH"][0]
    assert ok is True
    assert item["kind"] == "huibo_relation"
    assert item["label"] == "来源陈述·个股关联"
    assert item["text"] == "控股股东关系 / 5 / 股权结构"
    assert "受益" not in item["text"]
    assert item["date"] == "2026-07-08"


def test_huibo_subjective_relation_source_is_kept_verbatim_as_source_statement(tmp_path):
    payload = {
        "reader_results": [
            {
                "title": "产业关系梳理",
                "huibo_list_time": "2026-07-10",
                "reader": {
                    "pdf_report_date": "2026-07-08",
                    "mentioned_stocks": [
                        {
                            "name": "四川长虹",
                            "viewpoint": "",
                            "source": "有望受益于控股股东协同",
                        }
                    ],
                },
            }
        ]
    }
    (tmp_path / "2026-07-10.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )

    evidence, ok = industry_logic._read_huibo_evidence(
        tmp_path, "2026-06-13", "2026-07-13", {"600839.SH": "四川长虹"}
    )

    assert ok is True
    assert evidence["600839.SH"] == [
        {
            "kind": "huibo_relation",
            "label": "来源陈述·个股关联",
            "date": "2026-07-08",
            "source": "产业关系梳理",
            "text": "有望受益于控股股东协同",
        }
    ]


def test_huibo_rejects_future_and_undated_evidence(tmp_path):
    payload = {
        "reader_results": [
            {
                "title": "未来研报",
                "huibo_list_time": "2026-07-14",
                "reader": {
                    "mentioned_stocks": [
                        {"name": "金宏气体", "viewpoint": "未来证据", "source": "正文"}
                    ]
                },
            },
            {
                "title": "无日期研报",
                "huibo_list_time": "",
                "reader": {
                    "mentioned_stocks": [
                        {"name": "金宏气体", "viewpoint": "无日期证据", "source": "正文"}
                    ]
                },
            },
        ]
    }
    (tmp_path / "not-a-date.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    evidence, ok = industry_logic._read_huibo_evidence(
        tmp_path, "2026-06-13", "2026-07-13", {"688106.SH": "金宏气体"}
    )
    assert ok is True
    assert evidence.get("688106.SH", []) == []


def test_huibo_date_falls_back_to_list_time_then_filename(tmp_path):
    (tmp_path / "2026-07-11.json").write_text(
        json.dumps(
            {
                "reader_results": [
                    {
                        "huibo_list_time": "2026-07-10",
                        "reader": {
                            "mentioned_stocks": [
                                {"name": "金宏气体", "viewpoint": "列表日期", "source": "A"}
                            ]
                        },
                    },
                    {
                        "huibo_list_time": "",
                        "reader": {
                            "mentioned_stocks": [
                                {"name": "金宏气体", "viewpoint": "文件日期", "source": "B"}
                            ]
                        },
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    evidence, ok = industry_logic._read_huibo_evidence(
        tmp_path, "2026-06-13", "2026-07-13", {"688106.SH": "金宏气体"}
    )
    assert ok is True
    assert [(item["text"], item["date"]) for item in evidence["688106.SH"]] == [
        ("列表日期", "2026-07-10"),
        ("文件日期", "2026-07-11"),
    ]


@pytest.mark.parametrize("invalid_list_time", ["待定", "2026/07/14"])
def test_huibo_nonempty_invalid_list_time_rejects_row_without_filename_fallback(
    tmp_path, invalid_list_time
):
    path = tmp_path / "2026-07-12.json"
    path.write_text(
        json.dumps(
            {
                "reader_results": [
                    {
                        "huibo_list_time": invalid_list_time,
                        "reader": {
                            "mentioned_stocks": [
                                {
                                    "name": "金宏气体",
                                    "viewpoint": "非法上架日期不得回退文件名",
                                    "source": "正文",
                                }
                            ]
                        },
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    evidence, ok = industry_logic._read_huibo_evidence(
        tmp_path, "2026-06-13", "2026-07-13", {"688106.SH": "金宏气体"}
    )

    assert industry_logic._huibo_availability_date(
        {"huibo_list_time": invalid_list_time}, path
    ) == ""
    assert ok is True
    assert evidence.get("688106.SH", []) == []


def test_huibo_requires_historical_availability_independent_of_pdf_date(tmp_path):
    (tmp_path / "2026-07-12.json").write_text(
        json.dumps(
            {
                "reader_results": [
                    {
                        "title": "旧PDF未来上架",
                        "huibo_list_time": "2026-07-14",
                        "reader": {
                            "pdf_report_date": "2026-07-08",
                            "mentioned_stocks": [
                                {"name": "金宏气体", "viewpoint": "未来上架不得泄漏", "source": "正文"}
                            ],
                        },
                    },
                    {
                        "title": "旧PDF已上架",
                        "huibo_list_time": "2026-07-13",
                        "reader": {
                            "pdf_report_date": "2026-07-08",
                            "mentioned_stocks": [
                                {"name": "金宏气体", "viewpoint": "当日已可用", "source": "正文"}
                            ],
                        },
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "2026-07-14.json").write_text(
        json.dumps(
            {
                "reader_results": [
                    {
                        "title": "旧PDF未来文件",
                        "huibo_list_time": "",
                        "reader": {
                            "pdf_report_date": "2026-07-08",
                            "mentioned_stocks": [
                                {"name": "金宏气体", "viewpoint": "未来文件不得泄漏", "source": "正文"}
                            ],
                        },
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    evidence, ok = industry_logic._read_huibo_evidence(
        tmp_path, "2026-06-13", "2026-07-13", {"688106.SH": "金宏气体"}
    )

    assert ok is True
    assert [(item["text"], item["date"]) for item in evidence["688106.SH"]] == [
        ("当日已可用", "2026-07-08")
    ]


def test_huibo_viewpoint_keeps_subjective_wording_and_uses_viewpoint_label(tmp_path):
    (tmp_path / "2026-07-12.json").write_text(
        json.dumps(
            {
                "reader_results": [
                    {
                        "title": "个股研报",
                        "huibo_list_time": "2026-07-12",
                        "reader": {
                            "pdf_report_date": "2026-07-10",
                            "mentioned_stocks": [
                                {
                                    "name": "金宏气体",
                                    "viewpoint": "目标价可能上调",
                                    "source": "正文观点",
                                }
                            ],
                        },
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    evidence, ok = industry_logic._read_huibo_evidence(
        tmp_path, "2026-06-13", "2026-07-13", {"688106.SH": "金宏气体"}
    )

    assert ok is True
    assert evidence["688106.SH"][0]["kind"] == "huibo_stock"
    assert evidence["688106.SH"][0]["label"] == "研报观点·个股催化"
    assert evidence["688106.SH"][0]["text"] == "目标价可能上调"


def test_corrupt_huibo_file_isolated_and_all_corrupt_is_failed(tmp_path):
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    (tmp_path / "2026-07-11.json").write_text(
        json.dumps({"reader_results": []}), encoding="utf-8"
    )
    evidence, ok = industry_logic._read_huibo_evidence(
        tmp_path, "2026-06-13", "2026-07-13", {"688106.SH": "金宏气体"}
    )
    assert evidence == {} and ok is True

    (tmp_path / "2026-07-11.json").unlink()
    evidence, ok = industry_logic._read_huibo_evidence(
        tmp_path, "2026-06-13", "2026-07-13", {"688106.SH": "金宏气体"}
    )
    assert evidence == {} and ok is False


def test_huibo_schema_invalid_json_is_failed_but_valid_empty_schema_is_ok(tmp_path):
    (tmp_path / "list.json").write_text("[]", encoding="utf-8")
    (tmp_path / "missing-reader-results.json").write_text("{}", encoding="utf-8")
    (tmp_path / "wrong-reader-results.json").write_text(
        json.dumps({"reader_results": {}}), encoding="utf-8"
    )

    evidence, ok = industry_logic._read_huibo_evidence(
        tmp_path, "2026-06-13", "2026-07-13", {"688106.SH": "金宏气体"}
    )

    assert evidence == {}
    assert ok is False

    (tmp_path / "valid-empty.json").write_text(
        json.dumps({"reader_results": []}), encoding="utf-8"
    )
    evidence, ok = industry_logic._read_huibo_evidence(
        tmp_path, "2026-06-13", "2026-07-13", {"688106.SH": "金宏气体"}
    )
    assert evidence == {}
    assert ok is True


def test_industry_match_rejects_broad_stopword_but_accepts_specific_token(conn):
    conn.executemany(
        "INSERT INTO industry_info(id,date,sector_name,content,source) VALUES(?,?,?,?,?)",
        [
            (1, "2026-07-12", "材料", "宽泛词不应命中", "测试源"),
            (2, "2026-07-11", "电子特气/工业气体", "电子特气需求提升", "行业笔记"),
        ],
    )
    evidence, ok = industry_logic._read_industry_evidence(
        conn,
        "2026-06-13",
        "2026-07-13",
        {
            "688106.SH": {
                "sw_l2": "电子化学品",
                "business_summary": "电子特气研发生产",
                "product_names": ["超纯氨"],
                "concept_names": ["设备"],
            }
        },
    )
    assert ok is True
    assert [item["text"] for item in evidence["688106.SH"]] == ["电子特气需求提升"]
    assert evidence["688106.SH"][0]["label"] == "来源陈述·行业催化"


def test_industry_explicit_fact_keeps_fact_label(conn):
    conn.execute(
        "INSERT INTO industry_info(id,date,sector_name,content,source) VALUES(?,?,?,?,?)",
        (1, "2026-07-12", "电子特气", "[事实]电子特气需求提升[判断]价格可能上涨", "行业笔记"),
    )
    evidence, ok = industry_logic._read_industry_evidence(
        conn,
        "2026-06-13",
        "2026-07-13",
        {
            "688106.SH": {
                "sw_l2": "电子化学品",
                "business_summary": "电子特气研发生产",
                "product_names": [],
                "concept_names": [],
            }
        },
    )
    assert ok is True
    assert evidence["688106.SH"] == [
        {
            "kind": "industry",
            "label": "事实·行业催化",
            "date": "2026-07-12",
            "source": "行业笔记",
            "text": "电子特气需求提升",
        }
    ]


def test_full_current_concept_map_can_match_industry_evidence_without_hot_hit_or_business(
    conn, tmp_path
):
    conn.execute(
        "INSERT INTO industry_info(id,date,sector_name,content,source) VALUES(?,?,?,?,?)",
        (1, "2026-07-12", "页岩气", "[事实]页岩气行业产量更新", "行业笔记"),
    )
    registry = Registry(primary={"605090.SH": _profile("605090.SH", "missing")})

    result = industry_logic.build_industry_logic_map(
        conn,
        registry,
        [{"code": "605090.SH", "name": "九丰能源"}],
        scan_date="2026-07-13",
        industry_map={},
        concept_map={"605090": ["页岩气"]},
        huibo_dir=tmp_path,
    )["605090.SH"]

    assert result["catalyst_status"] == "sector"
    assert result["catalyst_evidence"] == [{
        "kind": "industry",
        "label": "事实·行业催化",
        "date": "2026-07-12",
        "source": "行业笔记",
        "text": "页岩气行业产量更新",
    }]
    assert "concept_names" not in result


@pytest.mark.parametrize(
    ("sector_token", "haystack"),
    [
        ("电子化学品", "电子"),
        ("锂电池", "电池"),
        ("医药商业", "医药"),
        ("人形机器人", "机器人"),
    ],
)
def test_reverse_industry_match_rejects_short_generic_haystack(sector_token, haystack):
    assert industry_logic._token_matches(sector_token, [haystack]) is False


def test_reverse_industry_match_accepts_specific_four_character_haystack():
    assert industry_logic._token_matches("高端电子特气", ["电子特气"]) is True
    assert industry_logic._token_matches("电子特气", ["高端电子特气"]) is True


def test_short_sector_tokens_do_not_expand_into_long_business_description():
    tokens = industry_logic._sector_tokens("金融/券商")

    assert tokens == ["金融", "券商"]
    assert industry_logic._tokens_match(tokens, ["为金融机构提供软件"]) is False


@pytest.mark.parametrize(
    ("sector_token", "product"),
    [
        ("电池", "动力电池管理系统"),
        ("医药", "生物医药制品"),
        ("机器人", "工业机器人本体"),
    ],
)
def test_short_sector_token_does_not_expand_into_long_product_name(
    sector_token, product
):
    assert industry_logic._token_matches(sector_token, [product]) is False


def test_short_sector_token_still_matches_exact_value():
    assert industry_logic._token_matches("电池", ["电池"]) is True


def test_industry_content_never_relabels_explicit_judgment_as_fact():
    mixed = "[事实]电子特气需求提升。[判断]价格可能继续上涨。"
    assert industry_logic._fact_only_industry_text(mixed) == "电子特气需求提升。"
    assert industry_logic._fact_only_industry_text("[判断]行业景气向上。") == ""
    assert industry_logic._fact_only_industry_text("未标注客观内容") == "未标注客观内容"
    assert industry_logic._fact_only_industry_text("[600000.SH]股票代码说明") == "[600000.SH]股票代码说明"


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("[事实]A[待核验]B[传闻]C", "A"),
        ("[事实]A[风险]B[边界]C[来源陈述]D", "A"),
    ],
)
def test_industry_fact_segment_stops_at_all_controlled_semantic_tags(content, expected):
    assert industry_logic._fact_only_industry_text(content) == expected


def test_lookback_is_closed_interval_and_exact_beats_sector(conn, tmp_path):
    conn.execute(
        "INSERT INTO teacher_notes(id,date,title,mentioned_stocks) VALUES(1,?,?,?)",
        (
            "2026-06-13",
            "窗口起点老师笔记",
            json.dumps(
                [{"code": "688106.SH", "name": "金宏气体", "reason": "起点直接证据"}],
                ensure_ascii=False,
            ),
        ),
    )
    conn.executemany(
        "INSERT INTO industry_info(id,date,sector_name,content,source) VALUES(?,?,?,?,?)",
        [
            (1, "2026-06-12", "电子特气", "窗口外证据", "行业笔记"),
            (2, "2026-07-13", "电子特气", "扫描日行业证据", "行业笔记"),
        ],
    )
    registry = Registry(
        primary={
            "688106.SH": _profile(
                "688106.SH",
                "ok",
                source="tushare:stock_company",
                business="电子特气研发",
            )
        }
    )
    result = industry_logic.build_industry_logic_map(
        conn,
        registry,
        [{"code": "688106.SH", "name": "金宏气体"}],
        scan_date="2026-07-13",
        industry_map={"688106.SH": {"sw_l2": "电子化学品"}},
        concept_map={"688106": ["电子特气"]},
        huibo_dir=tmp_path,
    )
    evidence = result["688106.SH"]["catalyst_evidence"]
    assert [item["text"] for item in evidence] == ["起点直接证据", "扫描日行业证据"]
    assert evidence[0]["kind"] in industry_logic.DIRECT_KINDS
    assert all(item["text"] != "窗口外证据" for item in evidence)
    assert result["688106.SH"]["catalyst_status"] == "exact"
    assert "concept_names" not in result["688106.SH"]


def test_no_evidence_and_all_sources_failed_are_distinct(conn, tmp_path):
    registry = Registry(primary={"688106.SH": _profile("688106.SH", "missing")})
    none_result = industry_logic.build_industry_logic_map(
        conn,
        registry,
        [{"code": "688106.SH", "name": "金宏气体"}],
        scan_date="2026-07-13",
        industry_map={},
        concept_map={},
        huibo_dir=tmp_path,
    )
    assert none_result["688106.SH"]["catalyst_status"] == "none"

    broken_conn = sqlite3.connect(":memory:")
    try:
        failed_result = industry_logic.build_industry_logic_map(
            broken_conn,
            registry,
            [{"code": "688106.SH", "name": "金宏气体"}],
            scan_date="2026-07-13",
            industry_map={},
            concept_map={},
            huibo_dir=tmp_path / "missing-directory",
        )
    finally:
        broken_conn.close()
    assert failed_result["688106.SH"]["catalyst_status"] == "source_failed"


def test_evidence_is_single_line_and_length_bounded():
    item = industry_logic._evidence(
        "huibo_stock",
        "研报观点·个股催化",
        "2026-07-12",
        "慧博\n研报" * 20,
        "产业催化\n" * 80,
    )
    assert item is not None
    assert "\n" not in item["source"] and len(item["source"]) <= 60
    assert "\n" not in item["text"] and len(item["text"]) <= 120
    assert industry_logic._evidence("huibo_stock", "研报观点·个股催化", "", "源", "文") is None
    assert industry_logic._evidence("huibo_stock", "研报观点·个股催化", "2026-07-0x", "源", "文") is None
    assert industry_logic._evidence("huibo_stock", "研报观点·个股催化", "2026-07-12", "源", "") is None


def test_teacher_priority_precedes_newer_huibo_and_dedupes_stably():
    items = [
        {
            "kind": "huibo_stock",
            "label": "研报观点·个股催化",
            "date": "2026-07-13",
            "source": "慧博B",
            "text": "新慧博B",
        },
        {
            "kind": "teacher_stock",
            "label": "老师观点·个股",
            "date": "2026-07-01",
            "source": "老师笔记",
            "text": "较早老师观点",
        },
        {
            "kind": "huibo_stock",
            "label": "研报观点·个股催化",
            "date": "2026-07-12",
            "source": "慧博A",
            "text": "新慧博A",
        },
        {
            "kind": "teacher_stock",
            "label": "老师观点·个股",
            "date": "2026-07-01",
            "source": "老师笔记",
            "text": "较早老师观点",
        },
    ]
    selected = industry_logic._select_evidence(items)
    assert [(item["kind"], item["text"]) for item in selected] == [
        ("teacher_stock", "较早老师观点"),
        ("huibo_stock", "新慧博B"),
    ]


def test_business_fields_are_hidden_unless_profile_is_ok(conn, tmp_path):
    registry = Registry(
        primary={
            "688106.SH": _profile(
                "688106.SH", "missing", source="should-hide", business="should-hide", products=["hide"]
            )
        },
        fallback={"688106.SH": _profile("688106.SH", "missing")},
    )
    result = industry_logic.build_industry_logic_map(
        conn,
        registry,
        [{"code": "688106", "name": "金宏气体"}],
        scan_date="2026-07-13",
        industry_map={"688106.SH": {"sw_l2": "电子化学品"}},
        concept_map={},
        huibo_dir=tmp_path,
    )["688106.SH"]
    assert result["business_summary"] == ""
    assert result["product_names"] == []
    assert result["business_source"] == ""
    assert result["industry_position"] == "电子化学品相关企业"


def test_product_values_are_deduped_bounded_and_business_uses_introduction(conn, tmp_path):
    row = _profile(
        "688106.SH",
        "ok",
        source="tushare:stock_company",
        products=["A" * 50, "A" * 50, "B", "C", "D", "E"],
    )
    row["introduction"] = " 公司\n简介 "
    row["business_scope"] = "经营范围后备文本"
    result = industry_logic.build_industry_logic_map(
        conn,
        Registry(primary={"688106.SH": row}),
        [{"code": "688106.SH", "name": "金宏气体"}],
        scan_date="2026-07-13",
        industry_map={},
        concept_map={},
        huibo_dir=tmp_path,
    )["688106.SH"]
    assert result["business_summary"] == "公司 简介"
    assert result["product_names"] == ["A" * 39 + "…", "B", "C", "D"]


def test_scope_only_profile_populates_summary_position_and_industry_evidence(
    conn, tmp_path
):
    conn.execute(
        "INSERT INTO industry_info(id,date,sector_name,content,source) VALUES(?,?,?,?,?)",
        (1, "2026-07-12", "氢能源设备", "[事实]行业订单增加", "行业笔记"),
    )
    row = _profile(
        "688106.SH",
        "ok",
        source="tushare:stock_company",
        scope="氢能源设备制造与销售",
    )

    result = industry_logic.build_industry_logic_map(
        conn,
        Registry(primary={"688106.SH": row}),
        [{"code": "688106.SH", "name": "金宏气体"}],
        scan_date="2026-07-13",
        industry_map={"688106.SH": {"sw_l2": "专用设备"}},
        concept_map={},
        huibo_dir=tmp_path,
    )["688106.SH"]

    assert result["business_summary"] == "氢能源设备制造与销售"
    assert result["industry_position"] == "专用设备领域企业，主营氢能源设备制造与销售"
    assert result["industry_position"] != "专用设备相关企业"
    assert [item["text"] for item in result["catalyst_evidence"]] == ["行业订单增加"]


def test_product_types_only_profile_flows_through_scorer_and_renderer(conn, tmp_path):
    row = _profile(
        "300750.SZ",
        "ok",
        source="akshare:stock_zyjs_ths",
        types=[" 储能系统 ", "储能系统", "变流器"],
    )

    aggregated = industry_logic.build_industry_logic_map(
        conn,
        Registry(
            primary={"300750.SZ": row},
            primary_source="akshare:stock_zyjs_ths",
        ),
        [{"code": "300750.SZ", "name": "宁德时代"}],
        scan_date="2026-07-13",
        industry_map={"300750.SZ": {"sw_l2": "电池"}},
        concept_map={},
        huibo_dir=tmp_path,
    )["300750.SZ"]

    assert aggregated["business_summary"] == ""
    assert aggregated["product_names"] == ["储能系统", "变流器"]
    assert aggregated["industry_position"] == "电池产业链企业，核心产品包括储能系统、变流器"

    normalized = scorer._normalize_logic_row(aggregated, "电池", "2026-07-13")
    assert normalized["business_status"] == "ok"
    lines = "".join(renderer._render_industry_logic(normalized))
    assert "核心产品：储能系统、变流器" in lines
    assert "暂无可展示主营摘要" not in lines


def test_product_names_precede_types_then_merge_stably_with_one_shared_limit(
    conn, tmp_path
):
    row = _profile(
        "300750.SZ",
        "ok",
        source="akshare:stock_zyjs_ths",
        products=[" 储能柜 ", "变流器", "储能柜"],
        types=["电池", "变流器", "氢能\n设备", "光伏", "其他"],
    )

    result = industry_logic.build_industry_logic_map(
        conn,
        Registry(
            primary={"300750.SZ": row},
            primary_source="akshare:stock_zyjs_ths",
        ),
        [{"code": "300750.SZ", "name": "宁德时代"}],
        scan_date="2026-07-13",
        industry_map={},
        concept_map={},
        huibo_dir=tmp_path,
    )["300750.SZ"]

    assert result["product_names"] == ["储能柜", "变流器", "电池", "氢能 设备"]


def test_main_business_keeps_priority_over_introduction_and_scope(conn, tmp_path):
    row = _profile(
        "688106.SH",
        "ok",
        source="tushare:stock_company",
        business="主营优先文本",
        scope="经营范围文本",
    )
    row["introduction"] = "公司简介文本"

    result = industry_logic.build_industry_logic_map(
        conn,
        Registry(primary={"688106.SH": row}),
        [{"code": "688106.SH", "name": "金宏气体"}],
        scan_date="2026-07-13",
        industry_map={},
        concept_map={},
        huibo_dir=tmp_path,
    )["688106.SH"]

    assert result["business_summary"] == "主营优先文本"
    assert result["industry_position"] == "主营主营优先文本"


def test_empty_candidates_short_circuits_without_registry_db_or_filesystem_calls():
    class Bomb:
        def __getattr__(self, name):
            raise AssertionError(name)

    assert industry_logic.build_industry_logic_map(
        Bomb(),
        Bomb(),
        [],
        scan_date="not-a-date",
        industry_map={},
        concept_map={},
        huibo_dir=Bomb(),
    ) == {}


def test_reader_sql_success_zero_rows_and_empty_huibo_dir_are_ok(conn, tmp_path):
    teacher, teacher_ok = industry_logic._read_teacher_evidence(
        conn, "2026-06-13", "2026-07-13", {"688106.SH": "金宏气体"}
    )
    industry, industry_ok = industry_logic._read_industry_evidence(
        conn,
        "2026-06-13",
        "2026-07-13",
        {"688106.SH": {"sw_l2": "", "business_summary": "", "product_names": [], "concept_names": []}},
    )
    huibo, huibo_ok = industry_logic._read_huibo_evidence(
        tmp_path, "2026-06-13", "2026-07-13", {"688106.SH": "金宏气体"}
    )
    assert teacher == {} and teacher_ok is True
    assert industry == {} and industry_ok is True
    assert huibo == {} and huibo_ok is True
