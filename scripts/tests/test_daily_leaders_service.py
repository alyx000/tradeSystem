from __future__ import annotations

import json
import sqlite3

import pytest

from db import queries as Q
from db.migrate import migrate
from services.daily_leaders import service


@pytest.fixture()
def conn(tmp_path):
    db_path = tmp_path / "trade.db"
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    migrate(c)
    yield c
    c.close()


def test_propose_fail_closed_when_active_history_unavailable(tmp_path, monkeypatch):
    def raise_history_error(conn):
        raise RuntimeError("leader_tracking unavailable")

    monkeypatch.setattr(service.Q, "get_active_leaders", raise_history_error)

    proposal = service.propose(
        object(),
        "2026-07-03",
        {
            "step5_leaders": {
                "top_leaders": [
                    {
                        "stock": "海光信息",
                        "sector": "半导体",
                        "attribute_type": "走势引领",
                    }
                ]
            },
            "teacher_notes": [],
            "market": {
                "stock_quotes": {
                    "data": [
                        {"code": "688041.SH", "name": "海光信息", "pct_chg": 10.01, "amount_yi": 42.0}
                    ]
                }
            },
        },
        no_llm=True,
        output_root=tmp_path,
    )

    assert proposal["date"] == "2026-07-03"
    assert proposal["top_leaders"][0]["stock"] == "海光信息"
    assert proposal["top_leaders"][0]["is_new"] is True
    assert proposal["paths"]["json"].endswith("2026-07-03.json")


def test_propose_does_not_read_trend_leader_pool(tmp_path, monkeypatch):
    captured = {}

    def fake_build_candidates(*, prefill, trend_pool, history, date, min_amount_yi=None):
        captured["trend_pool"] = trend_pool
        captured["min_amount_yi"] = min_amount_yi
        return {
            "date": date,
            "top_leaders": [
                {
                    "stock": "海光信息",
                    "sector": "半导体",
                    "attribute_type": "走势引领",
                    "evidence": [],
                }
            ],
        }

    monkeypatch.setattr(service, "build_candidates", fake_build_candidates)

    proposal = service.propose(
        object(),
        "2026-07-03",
        {
            "step5_leaders": {
                "top_leaders": [
                    {
                        "stock": "海光信息",
                        "sector": "半导体",
                        "attribute_type": "走势引领",
                    }
                ]
            },
            "teacher_notes": [],
        },
        no_llm=True,
        output_root=tmp_path,
    )

    assert captured["trend_pool"] == []
    assert captured["min_amount_yi"] == 20.0
    assert proposal["candidate_filters"] == {"min_amount_yi": 20.0}
    assert [item["stock"] for item in proposal["top_leaders"]] == ["海光信息"]


def test_propose_limits_confirmation_candidates_by_default(tmp_path, monkeypatch):
    raw_candidates = [
        {
            "stock": f"候选{i:02d}",
            "sector": f"板块{i:02d}",
            "attribute_type": "走势引领",
            "evidence": [],
            "_selection_score": 100 - i,
        }
        for i in range(35)
    ]

    def fake_build_candidates(*, prefill, trend_pool, history, date, min_amount_yi=None):
        return {"date": date, "top_leaders": raw_candidates}

    monkeypatch.setattr(service, "build_candidates", fake_build_candidates)

    proposal = service.propose(
        object(),
        "2026-07-03",
        {"step5_leaders": {"top_leaders": []}, "teacher_notes": []},
        no_llm=True,
        output_root=tmp_path,
    )

    assert len(proposal["top_leaders"]) == 15
    assert proposal["candidate_limit"]["max_candidates"] == 15
    assert proposal["candidate_limit"]["original_count"] == 35
    assert proposal["candidate_limit"]["review_pool_count"] == 30
    assert proposal["candidate_limit"]["review_pool_trimmed_count"] == 5
    assert proposal["candidate_limit"]["final_count"] == 15
    assert proposal["candidate_limit"]["trimmed_count"] == 20
    assert proposal["top_leaders"][-1]["stock"] == "候选14"


def test_propose_dedupes_confirmation_candidates_by_stock_after_ranking(tmp_path, monkeypatch):
    raw_candidates = [
        {
            "stock": "华大九天",
            "sector": "国家大基金持股",
            "attribute_type": "走势引领",
            "evidence": [],
            "llm_rank": 1,
        },
        {
            "stock": "概伦电子",
            "sector": "EDA概念",
            "attribute_type": "走势引领",
            "evidence": [],
            "llm_rank": 2,
        },
        {
            "stock": "华大九天",
            "sector": "EDA概念",
            "attribute_type": "走势引领",
            "evidence": [],
            "llm_rank": 3,
        },
    ]

    def fake_build_candidates(*, prefill, trend_pool, history, date, min_amount_yi=None):
        return {"date": date, "top_leaders": raw_candidates}

    def fake_enrich(proposal, *, enabled):
        return proposal

    monkeypatch.setattr(service, "build_candidates", fake_build_candidates)
    monkeypatch.setattr(service, "enrich_with_llm_reason", fake_enrich)

    proposal = service.propose(
        object(),
        "2026-07-03",
        {"step5_leaders": {"top_leaders": []}, "teacher_notes": []},
        no_llm=False,
        output_root=tmp_path,
    )

    assert [(item["stock"], item["sector"]) for item in proposal["top_leaders"]] == [
        ("华大九天", "国家大基金持股"),
        ("概伦电子", "EDA概念"),
    ]
    assert proposal["candidate_limit"]["duplicate_trimmed_count"] == 1


def test_repeated_stock_tokens_match_quote_and_dedupe_through_final_selection():
    prefill = {
        "step5_leaders": {
            "top_leaders": [
                {
                    "stock": "协创数据 协创数据 协创数据",
                    "sector": "旧概念",
                    "attribute_type": "旧属性",
                }
            ]
        },
        "teacher_notes": [],
        "market": {
            "stock_quotes": {
                "data": [
                    {
                        "code": "300857.SZ",
                        "name": "协创数据",
                        "pct_chg": 20.0,
                        "amount_yi": 30.0,
                        "sw_l2": "消费电子",
                        "sector_source": "tushare:index_member_all",
                    }
                ]
            },
            "top_volume_stocks": {
                "data": [{"name": "协创数据", "amount_yi": 30.0}]
            },
        },
    }

    raw = service.build_candidates(prefill=prefill, trend_pool=[], history=[])[
        "top_leaders"
    ]
    prepared = service.prepare_llm_review_pool(
        service.assign_fallback_roles(raw),
        limit=30,
    )
    final, _ = service.select_confirmation_candidates(
        prepared,
        max_candidates=15,
        llm_ok=False,
    )

    assert len(raw) == 1
    assert len(prepared) == 1
    assert len(final) == 1
    assert service._raw_stock_counts(raw) == (1, 0)
    assert final[0]["stock"] == "协创数据"
    assert final[0]["stock_code"] == "300857"
    assert service.stock_identity_key(final[0]) == "300857"


def test_propose_dedupes_history_code_display_and_name_only_flow_when_quotes_fail(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        service,
        "_active_history",
        lambda conn: [
            {
                "stock_code": "600519.SH",
                "stock_name": "贵州茅台",
                "sector": "饮料制造",
            }
        ],
    )

    class FailedQuoteRegistry:
        def call(self, name, *args):
            if name == "get_market_daily_quotes":
                raise RuntimeError("quote unavailable")
            if name == "get_stock_sw_industry_map":
                return type(
                    "Result",
                    (),
                    {"success": False, "data": None, "error": "map unavailable"},
                )()
            raise AssertionError(name)

    prepared: list[dict] = []
    original_prepare = service.prepare_llm_review_pool

    def capture_prepare(items, limit):
        result = original_prepare(items, limit=limit)
        prepared.extend(result)
        return result

    monkeypatch.setattr(service, "prepare_llm_review_pool", capture_prepare)

    proposal = service.propose(
        object(),
        "2026-07-03",
        {
            "step5_leaders": {"top_leaders": []},
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
        },
        no_llm=True,
        output_root=tmp_path,
        registry=FailedQuoteRegistry(),
    )

    assert len(prepared) == 1
    assert len(proposal["top_leaders"]) == 1
    assert proposal["top_leaders"][0]["stock_code"] == "600519"
    assert proposal["top_leaders"][0]["code"] == "600519"


@pytest.mark.parametrize(
    ("step5_items", "history"),
    [
        (
            [
                {
                    "stock": "贵州茅台",
                    "stock_code": "600519.SH",
                    "sector": "饮料制造",
                    "attribute_type": "趋势中军",
                }
            ],
            [],
        ),
        (
            [],
            [
                {
                    "stock_code": "600519",
                    "stock_name": "贵州茅台",
                    "sector": "饮料制造",
                },
                {
                    "stock_code": "600519.SH",
                    "stock_name": "贵州茅台",
                    "sector": "饮料制造",
                },
            ],
        ),
    ],
    ids=["prefill-unique-code", "history-bare-and-full-equivalent-code"],
)
def test_propose_dedupes_name_only_flow_from_all_trusted_equivalent_codes(
    tmp_path, monkeypatch, step5_items, history
):
    monkeypatch.setattr(service, "_active_history", lambda conn: history)

    class FailedQuoteRegistry:
        def call(self, name, *args):
            if name == "get_market_daily_quotes":
                raise RuntimeError("quote unavailable")
            if name == "get_stock_sw_industry_map":
                return type(
                    "Result",
                    (),
                    {"success": False, "data": None, "error": "map unavailable"},
                )()
            raise AssertionError(name)

    prepared: list[dict] = []
    original_prepare = service.prepare_llm_review_pool

    def capture_prepare(items, limit):
        result = original_prepare(items, limit=limit)
        prepared.extend(result)
        return result

    monkeypatch.setattr(service, "prepare_llm_review_pool", capture_prepare)

    proposal = service.propose(
        object(),
        "2026-07-03",
        {
            "step5_leaders": {"top_leaders": step5_items},
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
        },
        no_llm=True,
        output_root=tmp_path,
        registry=FailedQuoteRegistry(),
    )

    assert len(prepared) == 1
    assert len(proposal["top_leaders"]) == 1
    assert proposal["top_leaders"][0].get("stock_code") in {
        "600519",
        "600519.SH",
    }


def test_propose_allows_custom_candidate_limit(tmp_path, monkeypatch):
    raw_candidates = [
        {
            "stock": f"候选{i:02d}",
            "sector": f"板块{i:02d}",
            "attribute_type": "走势引领",
            "evidence": [],
            "_selection_score": 100 - i,
        }
        for i in range(8)
    ]

    def fake_build_candidates(*, prefill, trend_pool, history, date, min_amount_yi=None):
        return {"date": date, "top_leaders": raw_candidates}

    monkeypatch.setattr(service, "build_candidates", fake_build_candidates)

    proposal = service.propose(
        object(),
        "2026-07-03",
        {"step5_leaders": {"top_leaders": []}, "teacher_notes": []},
        no_llm=True,
        output_root=tmp_path,
        max_candidates=5,
    )

    assert [item["stock"] for item in proposal["top_leaders"]] == [
        "候选00",
        "候选01",
        "候选02",
        "候选03",
        "候选04",
    ]
    assert proposal["candidate_limit"]["trimmed_count"] == 3


def test_propose_skips_non_trading_day_without_candidates(tmp_path):
    proposal = service.propose(
        object(),
        "2026-07-05",
        {
            "is_trading_day": False,
            "prev_trade_date": "2026-07-03",
            "market": None,
            "step5_leaders": None,
            "teacher_notes": [],
        },
        no_llm=True,
        output_root=tmp_path,
    )

    assert proposal["top_leaders"] == []
    assert proposal["skipped"] == {
        "reason": "non_trading_day",
        "prev_trade_date": "2026-07-03",
    }
    assert proposal["paths"]["json"].endswith("2026-07-05.json")


def test_attach_market_quotes_adds_stock_quotes_without_mutating_source():
    prefill = {"market": {"concept_moneyflow_ths": {"data": []}}}

    class FakeRegistry:
        def call(self, name, *args):
            if name == "get_market_daily_quotes":
                assert args == ("2026-06-29",)
                return type(
                    "Result",
                    (),
                    {
                        "success": True,
                        "data": [
                            {
                                "ts_code": "688432.SH",
                                "pct_chg": 12.34,
                                "amount": 1880000.0,
                            }
                        ],
                        "error": "",
                    },
                )()
            if name == "get_stock_basic_list":
                assert args == ("2026-06-29",)
                return type(
                    "Result",
                    (),
                    {
                        "success": True,
                        "data": [{"ts_code": "688432.SH", "name": "有研硅"}],
                        "error": "",
                    },
                )()
            if name == "get_stock_sw_industry_map":
                assert args == ()
                return type(
                    "Result",
                    (),
                    {
                        "success": True,
                        "data": {"688432": {"name": "有研硅", "sw_l2": "半导体"}},
                        "source": "tushare:index_member_all",
                        "error": "",
                    },
                )()
            raise AssertionError(name)

    out = service.attach_market_quotes(prefill, "2026-06-29", FakeRegistry())

    assert "stock_quotes" not in prefill["market"]
    assert out["market"]["stock_quotes"]["data"] == [
        {
            "code": "688432.SH",
            "name": "有研硅",
            "pct_chg": 12.34,
            "amount_yi": 18.8,
            "sw_l2": "半导体",
            "sector_source": "tushare:index_member_all",
        }
    ]
    assert out["market"]["stock_industry_map"] == {
        "status": "success",
        "source": "tushare:index_member_all",
        "error": "",
    }


def test_attach_market_quotes_supports_full_and_bare_sw_codes_and_limit_step():
    prefill = {
        "market": {
            "limit_step": {
                "data": [
                    {"ts_code": "688041.SH", "name": "海光信息", "nums": "3"},
                    {"name": "华大九天", "nums": 2},
                ]
            }
        }
    }

    class FakeRegistry:
        def __init__(self):
            self.calls = []

        def call(self, name, *args):
            self.calls.append((name, args))
            if name == "get_market_daily_quotes":
                return type("Result", (), {
                    "success": True,
                    "data": [
                        {"ts_code": "688041.SH", "pct_chg": 20.0, "amount": 8_800_000},
                        {"ts_code": "301269.SZ", "pct_chg": 12.0, "amount": 3_900_000},
                    ],
                    "source": "fake_quotes",
                    "error": "",
                })()
            if name == "get_stock_basic_list":
                return type("Result", (), {
                    "success": True,
                    "data": [
                        {"ts_code": "688041.SH", "name": "海光信息"},
                        {"ts_code": "301269.SZ", "name": "华大九天"},
                    ],
                    "source": "fake_basic",
                    "error": "",
                })()
            if name == "get_stock_sw_industry_map":
                return type("Result", (), {
                    "success": True,
                    "data": {
                        "688041": {"name": "海光信息", "sw_l2": "半导体"},
                        "301269.SZ": {"name": "华大九天", "sw_l2": "软件开发"},
                    },
                    "source": "tushare:index_member_all",
                    "error": "",
                })()
            raise AssertionError(name)

    registry = FakeRegistry()
    out = service.attach_market_quotes(prefill, "2026-07-14", registry)

    assert registry.calls.count(("get_stock_sw_industry_map", ())) == 1
    assert out["market"]["stock_quotes"]["data"] == [
        {
            "code": "688041.SH",
            "name": "海光信息",
            "pct_chg": 20.0,
            "amount_yi": 88.0,
            "sw_l2": "半导体",
            "sector_source": "tushare:index_member_all",
            "limit_height": 3,
        },
        {
            "code": "301269.SZ",
            "name": "华大九天",
            "pct_chg": 12.0,
            "amount_yi": 39.0,
            "sw_l2": "软件开发",
            "sector_source": "tushare:index_member_all",
        },
    ]


def test_attach_market_quotes_never_uses_name_height_when_quote_has_valid_code():
    prefill = {
        "market": {
            "limit_step": {
                "data": [
                    {"ts_code": "600001.SH", "name": "同名候选", "nums": 3}
                ]
            }
        }
    }

    class FakeRegistry:
        def call(self, name, *args):
            if name == "get_market_daily_quotes":
                return type(
                    "Result",
                    (),
                    {
                        "success": True,
                        "data": [
                            {
                                "ts_code": "000001.SZ",
                                "name": "同名候选",
                                "pct_chg": 10.0,
                                "amount": 3_000_000,
                            }
                        ],
                        "error": "",
                    },
                )()
            if name == "get_stock_basic_list":
                return type("Result", (), {"success": True, "data": [], "error": ""})()
            if name == "get_stock_sw_industry_map":
                return type(
                    "Result",
                    (),
                    {"success": True, "data": {}, "source": "fake", "error": ""},
                )()
            raise AssertionError(name)

    out = service.attach_market_quotes(prefill, "2026-07-14", FakeRegistry())

    quote = out["market"]["stock_quotes"]["data"][0]
    assert quote["code"] == "000001.SZ"
    assert "limit_height" not in quote


def test_attach_market_quotes_falls_back_to_unique_name_only_without_valid_quote_code():
    prefill = {
        "market": {
            "limit_step": {
                "data": [
                    {"ts_code": "600001.SH", "name": "唯一名称", "nums": 3}
                ]
            }
        }
    }

    class FakeRegistry:
        def call(self, name, *args):
            if name == "get_market_daily_quotes":
                return type(
                    "Result",
                    (),
                    {
                        "success": True,
                        "data": [
                            {
                                "name": "唯一名称",
                                "pct_chg": 10.0,
                                "amount": 3_000_000,
                            }
                        ],
                        "error": "",
                    },
                )()
            if name == "get_stock_basic_list":
                return type("Result", (), {"success": True, "data": [], "error": ""})()
            if name == "get_stock_sw_industry_map":
                return type(
                    "Result",
                    (),
                    {"success": True, "data": {}, "source": "fake", "error": ""},
                )()
            raise AssertionError(name)

    out = service.attach_market_quotes(prefill, "2026-07-14", FakeRegistry())

    assert out["market"]["stock_quotes"]["data"][0]["limit_height"] == 3


def test_attach_market_quotes_disables_name_fallback_for_conflicting_limit_codes():
    prefill = {
        "market": {
            "limit_step": {
                "data": [
                    {"ts_code": "600001.SH", "name": "冲突名称", "nums": 3},
                    {"ts_code": "000001.SZ", "name": "冲突名称"},
                ]
            }
        }
    }

    class FakeRegistry:
        def call(self, name, *args):
            if name == "get_market_daily_quotes":
                return type(
                    "Result",
                    (),
                    {
                        "success": True,
                        "data": [
                            {
                                "name": "冲突名称",
                                "pct_chg": 10.0,
                                "amount": 3_000_000,
                            }
                        ],
                        "error": "",
                    },
                )()
            if name == "get_stock_basic_list":
                return type("Result", (), {"success": True, "data": [], "error": ""})()
            if name == "get_stock_sw_industry_map":
                return type(
                    "Result",
                    (),
                    {"success": True, "data": {}, "source": "fake", "error": ""},
                )()
            raise AssertionError(name)

    out = service.attach_market_quotes(prefill, "2026-07-14", FakeRegistry())

    assert "limit_height" not in out["market"]["stock_quotes"]["data"][0]


@pytest.mark.parametrize(
    ("raw_height", "expected"),
    [
        (3, 3),
        (3.0, 3),
        ("3", 3),
        ("3.0", 3),
        (0, 0),
    ],
)
def test_limit_height_indexes_accepts_only_finite_nonnegative_integers(
    raw_height,
    expected,
):
    by_code, by_name = service._limit_height_indexes(
        {
            "limit_step": {
                "data": [
                    {
                        "ts_code": "688041.SH",
                        "name": "海光信息",
                        "nums": raw_height,
                    }
                ]
            }
        }
    )

    assert by_code == {"688041.SH": expected, "688041": expected}
    assert by_name == {"海光信息": expected}


@pytest.mark.parametrize(
    "raw_height",
    [2.5, "2.5", -1, "-1", float("nan"), float("inf"), float("-inf"), True, False],
)
def test_limit_height_indexes_ignores_invalid_heights(raw_height):
    by_code, by_name = service._limit_height_indexes(
        {
            "limit_step": {
                "data": [
                    {
                        "ts_code": "688041.SH",
                        "name": "海光信息",
                        "nums": raw_height,
                    }
                ]
            }
        }
    )

    assert by_code == {}
    assert by_name == {}


def test_attach_market_quotes_preserves_explicit_zero_height_and_omits_missing_height():
    prefill = {
        "market": {
            "limit_step": {
                "data": [{"ts_code": "600001.SH", "name": "首板候选", "nums": 0}]
            }
        }
    }

    class FakeRegistry:
        def call(self, name, *args):
            if name == "get_market_daily_quotes":
                return type(
                    "Result",
                    (),
                    {
                        "success": True,
                        "data": [
                            {"ts_code": "600001.SH", "pct_chg": 10.0, "amount": 3_000_000},
                            {"ts_code": "600002.SH", "pct_chg": 9.9, "amount": 3_000_000},
                        ],
                        "error": "",
                    },
                )()
            if name == "get_stock_basic_list":
                return type(
                    "Result",
                    (),
                    {
                        "success": True,
                        "data": [
                            {"ts_code": "600001.SH", "name": "首板候选"},
                            {"ts_code": "600002.SH", "name": "高度缺失候选"},
                        ],
                        "error": "",
                    },
                )()
            if name == "get_stock_sw_industry_map":
                return type(
                    "Result",
                    (),
                    {
                        "success": True,
                        "data": {
                            "600001.SH": {"name": "首板候选", "sw_l2": "汽车零部件"},
                            "600002.SH": {"name": "高度缺失候选", "sw_l2": "汽车零部件"},
                        },
                        "source": "tushare:index_member_all",
                        "error": "",
                    },
                )()
            raise AssertionError(name)

    out = service.attach_market_quotes(prefill, "2026-07-14", FakeRegistry())
    rows = {row["name"]: row for row in out["market"]["stock_quotes"]["data"]}

    assert rows["首板候选"]["limit_height"] == 0
    assert "limit_height" not in rows["高度缺失候选"]


def test_attach_market_quotes_converts_all_thousand_yuan_amounts_and_filters_at_twenty_yi():
    class FakeRegistry:
        def call(self, name, *args):
            if name == "get_market_daily_quotes":
                return type(
                    "Result",
                    (),
                    {
                        "success": True,
                        "data": [
                            {
                                "ts_code": "600001.SH",
                                "name": "五亿样本",
                                "pct_chg": 10.0,
                                "amount": 500_000,
                            },
                            {
                                "ts_code": "600002.SH",
                                "name": "二十亿样本",
                                "pct_chg": 10.0,
                                "amount": 2_000_000,
                            },
                            {
                                "ts_code": "600003.SH",
                                "name": "非法金额样本",
                                "pct_chg": 10.0,
                                "amount": "invalid",
                            },
                            {
                                "ts_code": "600004.SH",
                                "name": "非有限金额样本",
                                "pct_chg": 10.0,
                                "amount": float("inf"),
                            },
                        ],
                        "source": "fake_quotes",
                        "error": "",
                    },
                )()
            if name == "get_stock_basic_list":
                return type("Result", (), {"success": True, "data": [], "error": ""})()
            if name == "get_stock_sw_industry_map":
                return type(
                    "Result",
                    (),
                    {
                        "success": True,
                        "data": {
                            "600001.SH": {"name": "五亿样本", "sw_l2": "银行"},
                            "600002.SH": {"name": "二十亿样本", "sw_l2": "银行"},
                            "600003.SH": {"name": "非法金额样本", "sw_l2": "银行"},
                            "600004.SH": {"name": "非有限金额样本", "sw_l2": "银行"},
                        },
                        "source": "tushare:index_member_all",
                        "error": "",
                    },
                )()
            raise AssertionError(name)

    prefill = service.attach_market_quotes(
        {"step5_leaders": None, "teacher_notes": [], "market": {}},
        "2026-07-14",
        FakeRegistry(),
    )

    quote_by_name = {
        item["name"]: item for item in prefill["market"]["stock_quotes"]["data"]
    }
    assert quote_by_name["五亿样本"]["amount_yi"] == 5.0
    assert quote_by_name["二十亿样本"]["amount_yi"] == 20.0
    assert quote_by_name["非法金额样本"]["amount_yi"] is None
    assert quote_by_name["非有限金额样本"]["amount_yi"] is None

    proposal = service.build_candidates(
        prefill=prefill,
        trend_pool=[],
        history=[],
        min_amount_yi=20.0,
    )
    assert [item["stock"] for item in proposal["top_leaders"]] == ["二十亿样本"]


@pytest.mark.parametrize(
    ("pct_chg", "expected_board_type"),
    [(30.0, "30cm"), (5.0, "非涨停")],
)
def test_attach_then_build_classifies_bj_four_prefix_board_type(
    pct_chg,
    expected_board_type,
):
    class FakeRegistry:
        def call(self, name, *args):
            if name == "get_market_daily_quotes":
                return type(
                    "Result",
                    (),
                    {
                        "success": True,
                        "data": [
                            {
                                "ts_code": "430047.BJ",
                                "name": "诺思兰德",
                                "pct_chg": pct_chg,
                                "amount": 3_000_000,
                            }
                        ],
                        "error": "",
                    },
                )()
            if name == "get_stock_basic_list":
                return type("Result", (), {"success": True, "data": [], "error": ""})()
            if name == "get_stock_sw_industry_map":
                return type(
                    "Result",
                    (),
                    {
                        "success": True,
                        "data": {
                            "430047.BJ": {
                                "name": "诺思兰德",
                                "sw_l2": "生物制品",
                            }
                        },
                        "source": "tushare:index_member_all",
                        "error": "",
                    },
                )()
            raise AssertionError(name)

    prefill = service.attach_market_quotes(
        {
            "step5_leaders": {
                "top_leaders": [
                    {
                        "stock": "430047.BJ 诺思兰德",
                        "sector": "旧概念",
                        "attribute_type": "旧属性",
                    }
                ]
            },
            "teacher_notes": [],
            "market": {},
        },
        "2026-07-14",
        FakeRegistry(),
    )

    proposal = service.build_candidates(
        prefill=prefill,
        trend_pool=[],
        history=[],
    )

    assert prefill["market"]["stock_quotes"]["data"][0]["code"] == "430047.BJ"
    assert proposal["top_leaders"][0]["stock_code"] == "430047"
    assert proposal["top_leaders"][0]["board_type"] == expected_board_type


def test_propose_timeout_keeps_fractional_amount_tiebreaker(tmp_path, monkeypatch):
    class FakeRegistry:
        def call(self, name, *args):
            if name == "get_market_daily_quotes":
                return type(
                    "Result",
                    (),
                    {
                        "success": True,
                        "data": [
                            {
                                "ts_code": "600001.SH",
                                "name": "A低额",
                                "pct_chg": 10.0,
                                "amount": 3_010_000,
                            },
                            {
                                "ts_code": "600002.SH",
                                "name": "Z高额",
                                "pct_chg": 10.0,
                                "amount": 3_080_000,
                            },
                        ],
                        "error": "",
                    },
                )()
            if name == "get_stock_basic_list":
                return type("Result", (), {"success": True, "data": [], "error": ""})()
            if name == "get_stock_sw_industry_map":
                return type(
                    "Result",
                    (),
                    {
                        "success": True,
                        "data": {
                            "600001.SH": {"name": "A低额", "sw_l2": "汽车零部件"},
                            "600002.SH": {"name": "Z高额", "sw_l2": "汽车零部件"},
                        },
                        "source": "tushare:index_member_all",
                        "error": "",
                    },
                )()
            raise AssertionError(name)

    monkeypatch.setattr(
        service,
        "enrich_with_llm_reason",
        lambda proposal, *, enabled: {
            **proposal,
            "llm_status": {"ok": False, "reason": "timeout"},
        },
    )

    proposal = service.propose(
        object(),
        "2026-07-14",
        {
            "step5_leaders": None,
            "teacher_notes": [],
            "market": {
                "limit_step": {
                    "data": [
                        {"ts_code": "600001.SH", "name": "A低额", "nums": 2},
                        {"ts_code": "600002.SH", "name": "Z高额", "nums": 2},
                    ]
                }
            },
        },
        output_root=tmp_path,
        registry=FakeRegistry(),
    )

    assert proposal["llm_status"] == {"ok": False, "reason": "timeout"}
    assert [item["stock"] for item in proposal["top_leaders"]] == ["Z高额"]
    assert proposal["top_leaders"][0]["amount_yi"] == 30.8
    assert proposal["top_leaders"][0]["leader_role"] == "连板核心"


@pytest.mark.parametrize(
    "raw_amount",
    [None, "", "invalid", float("nan"), float("inf"), float("-inf")],
)
def test_amount_to_yi_rejects_invalid_or_non_finite_values(raw_amount):
    assert service._amount_to_yi(raw_amount) is None


@pytest.mark.parametrize("pct_chg", [float("nan"), float("inf"), float("-inf")])
def test_propose_ignores_nonfinite_quote_pct_without_crashing(tmp_path, pct_chg):
    class FakeRegistry:
        def call(self, name, *args):
            if name == "get_market_daily_quotes":
                return type(
                    "Result",
                    (),
                    {
                        "success": True,
                        "data": [
                            {
                                "ts_code": "688888.SH",
                                "name": "异常涨幅样本",
                                "pct_chg": pct_chg,
                                "amount": 2_500_000,
                            }
                        ],
                        "source": "fake_quotes",
                        "error": "",
                    },
                )()
            if name == "get_stock_basic_list":
                return type("Result", (), {"success": True, "data": [], "error": ""})()
            if name == "get_stock_sw_industry_map":
                return type(
                    "Result",
                    (),
                    {
                        "success": True,
                        "data": {
                            "688888.SH": {"name": "异常涨幅样本", "sw_l2": "半导体"}
                        },
                        "source": "tushare:index_member_all",
                        "error": "",
                    },
                )()
            raise AssertionError(name)

    proposal = service.propose(
        object(),
        "2026-07-14",
        {"step5_leaders": None, "teacher_notes": [], "market": {}},
        no_llm=True,
        output_root=tmp_path,
        registry=FakeRegistry(),
    )

    assert proposal["top_leaders"] == []
    assert proposal["llm_status"] == {"ok": False, "reason": "disabled"}


def test_attach_market_quotes_degrades_when_sw_map_fails():
    class FakeRegistry:
        def call(self, name, *args):
            if name == "get_market_daily_quotes":
                return type("Result", (), {
                    "success": True,
                    "data": [{"ts_code": "600001.SH", "name": "普通股", "pct_chg": 10.0, "amount": 3_000_000}],
                    "source": "fake_quotes",
                    "error": "",
                })()
            if name == "get_stock_basic_list":
                return type("Result", (), {"success": True, "data": [], "source": "fake", "error": ""})()
            if name == "get_stock_sw_industry_map":
                return type("Result", (), {
                    "success": False,
                    "data": None,
                    "source": "tushare",
                    "error": "index_member_all_failed: boom",
                })()
            raise AssertionError(name)

    out = service.attach_market_quotes({"market": {}}, "2026-07-14", FakeRegistry())

    assert out["market"]["stock_quotes"]["data"][0]["sw_l2"] == ""
    assert out["market"]["stock_quotes"]["data"][0]["sector_source"] == ""
    assert out["market"]["stock_industry_map"] == {
        "status": "source_failed",
        "source": "tushare",
        "error": "index_member_all_failed: boom",
    }


@pytest.mark.parametrize("quote_mode", ["exception", "failed_result"])
def test_attach_market_quotes_still_fetches_sw_map_once_when_quotes_fail(quote_mode):
    class FakeRegistry:
        def __init__(self):
            self.calls = []

        def call(self, name, *args):
            self.calls.append((name, args))
            if name == "get_market_daily_quotes":
                if quote_mode == "exception":
                    raise RuntimeError("quote source boom")
                return type(
                    "Result",
                    (),
                    {
                        "success": False,
                        "data": None,
                        "source": "fake_quotes",
                        "error": "quote_fetch_failed",
                    },
                )()
            if name == "get_stock_sw_industry_map":
                return type(
                    "Result",
                    (),
                    {
                        "success": True,
                        "data": {"688041.SH": {"name": "海光信息", "sw_l2": "半导体"}},
                        "source": "tushare:index_member_all",
                        "error": "",
                    },
                )()
            raise AssertionError(name)

    registry = FakeRegistry()
    out = service.attach_market_quotes({"market": {}}, "2026-07-14", registry)

    assert registry.calls.count(("get_stock_sw_industry_map", ())) == 1
    assert out["market"]["stock_quotes"]["data"] == []
    expected_error = "quote source boom" if quote_mode == "exception" else "quote_fetch_failed"
    assert out["market"]["stock_quotes"]["error"] == expected_error
    assert out["market"]["stock_industry_map"] == {
        "status": "success",
        "source": "tushare:index_member_all",
        "error": "",
    }


@pytest.mark.parametrize("max_candidates", [0, 16])
def test_propose_rejects_limits_outside_service_hard_cap(tmp_path, max_candidates):
    with pytest.raises(ValueError, match="max_candidates"):
        service.propose(
            object(),
            "2026-07-14",
            {"step5_leaders": {"top_leaders": []}, "teacher_notes": []},
            no_llm=True,
            output_root=tmp_path,
            max_candidates=max_candidates,
        )


@pytest.mark.parametrize("max_candidates", [True, 15.0, "15"])
def test_propose_requires_max_candidates_to_be_a_strict_int(tmp_path, max_candidates):
    with pytest.raises(ValueError, match="max_candidates"):
        service.propose(
            object(),
            "2026-07-14",
            {"step5_leaders": {"top_leaders": []}, "teacher_notes": []},
            no_llm=True,
            output_root=tmp_path,
            max_candidates=max_candidates,
        )


@pytest.mark.parametrize("llm_mode", ["success", "timeout", "disabled"])
def test_propose_limits_llm_review_pool_and_always_applies_final_constraints(
    tmp_path,
    monkeypatch,
    llm_mode,
):
    raw_candidates = [
        {
            "stock": f"候选{i:02d}",
            "sector": f"板块{i // 2:02d}",
            "board_type": "非涨停",
            "amount_yi": 100 - i,
            "limit_height": 0,
            "_selection_score": float(100 - i),
            "evidence": [],
        }
        for i in range(50)
    ]
    monkeypatch.setattr(
        service,
        "build_candidates",
        lambda **kwargs: {"date": kwargs["date"], "top_leaders": raw_candidates},
    )
    observed = {}

    def fake_enrich(proposal, *, enabled):
        observed["enabled"] = enabled
        observed["review_count"] = len(proposal["top_leaders"])
        if llm_mode == "timeout":
            return {**proposal, "llm_status": {"ok": False, "reason": "timeout"}}
        assert enabled is True
        enriched = {**proposal, "top_leaders": [dict(item) for item in proposal["top_leaders"]]}
        for rank, item in enumerate(enriched["top_leaders"], start=1):
            item["llm_role"] = "前排活跃"
            item["llm_rank"] = rank
        enriched["llm_status"] = {"ok": True}
        return enriched

    monkeypatch.setattr(service, "enrich_with_llm_reason", fake_enrich)

    proposal = service.propose(
        object(),
        "2026-07-14",
        {"step5_leaders": {"top_leaders": []}, "teacher_notes": []},
        no_llm=llm_mode == "disabled",
        output_root=tmp_path,
    )

    if llm_mode == "disabled":
        assert observed == {}
        assert proposal["llm_status"] == {"ok": False, "reason": "disabled"}
    else:
        assert observed == {"enabled": True, "review_count": 30}
        if llm_mode == "timeout":
            assert proposal["llm_status"]["reason"] == "timeout"
        else:
            assert proposal["llm_status"]["ok"] is True
    assert len(proposal["top_leaders"]) <= 15
    assert len({(item["sector"], item["leader_role"]) for item in proposal["top_leaders"]}) == len(proposal["top_leaders"])
    assert len({item["stock"] for item in proposal["top_leaders"]}) == len(proposal["top_leaders"])
    assert proposal["candidate_limit"]["original_count"] == 50
    assert proposal["candidate_limit"]["review_pool_count"] == 30
    assert proposal["candidate_limit"]["review_pool_trimmed_count"] == 20
    assert proposal["candidate_limit"]["final_count"] == len(proposal["top_leaders"])
    assert {
        "original_count",
        "deduped_count",
        "duplicate_trimmed_count",
        "trimmed_count",
        "review_pool_count",
        "review_pool_trimmed_count",
        "sector_role_trimmed_count",
        "stock_duplicate_trimmed_count",
        "final_count",
    } <= proposal["candidate_limit"].keys()


def test_confirm_writes_step5_and_syncs_leader_tracking(conn, tmp_path):
    Q.upsert_daily_review(conn, "2026-07-03", {
        "step8_plan": {
            "factor_decision": {
                "score_run_id": "old-factor-run",
                "status": "accepted",
                "primary_factor": "sector_rhythm",
                "supporting_factors": ["leader_signal"],
                "input_by": "web",
            },
            "key_factor": "sector_rhythm",
            "secondary_factors": ["leader_signal"],
        },
    })
    conn.commit()
    leaders_file = tmp_path / "leaders.json"
    leaders_file.write_text(
        json.dumps(
            {
                "date": "2026-07-03",
                "top_leaders": [
                    {
                        "stock": "  海光信息  ",
                        "sector": "  半导体  ",
                        "attribute_type": "走势引领",
                        "llm_role": "前排活跃",
                        "leader_role": "趋势中军",
                        "attribute": "启动日主动引领",
                        "clarity": "高",
                        "position": "主升初期",
                        "is_new": True,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = service.confirm(conn, "2026-07-03", "codex", leaders_file=leaders_file)

    review = Q.get_daily_review(conn, "2026-07-03")
    step5 = json.loads(review["step5_leaders"])
    step8 = json.loads(review["step8_plan"])
    leaders = Q.get_active_leaders(conn)
    assert result["ok"] is True
    assert result["synced_leader_tracking"] == 1
    assert step5["notes"] == "daily-leaders confirmed by codex"
    assert step5["top_leaders"] == [
        {
            "stock": "海光信息",
            "sector": "半导体",
            "attribute_type": "趋势中军",
            "attribute": "启动日主动引领",
            "clarity": "高",
            "position": "主升初期",
            "is_new": True,
        }
    ]
    assert step8["factor_decision"] is None
    assert step8["key_factor"] == ""
    assert step8["secondary_factors"] == []
    assert leaders[0]["stock_code"] == "海光信息"
    assert leaders[0]["sector"] == "半导体"


@pytest.mark.parametrize(
    ("candidate_roles", "expected"),
    [
        ({"leader_role": "连板核心", "llm_role": "前排活跃", "attribute_type": "旧属性"}, "连板核心"),
        ({"leader_role": "备选", "llm_role": "弹性前排", "attribute_type": "旧属性"}, "弹性前排"),
        ({"leader_role": "剔除", "llm_role": "备选", "attribute_type": "10cm"}, "10cm"),
        ({"leader_role": "备选", "llm_role": "剔除", "attribute_type": "备选"}, None),
    ],
)
def test_confirm_prefers_new_legal_roles_and_never_persists_status_roles(candidate_roles, expected):
    source = {
        "date": "2026-07-14",
        "top_leaders": [
            {
                "stock": "海光信息",
                "sector": "半导体",
                **candidate_roles,
            }
        ],
    }

    result = service._confirmed_step5_leaders(source, "2026-07-14")

    assert result[0]["attribute_type"] == expected
    assert result[0]["attribute_type"] not in {"备选", "剔除"}


@pytest.mark.parametrize(
    "candidate",
    [
        {"stock": "", "sector": "半导体"},
        {"stock": "   ", "sector": "半导体"},
        {"stock": 688041, "sector": "半导体"},
        {"stock": "海光信息", "sector": ""},
        {"stock": "海光信息", "sector": "   "},
        {"stock": "海光信息", "sector": ["半导体"]},
        "bad-row",
    ],
)
def test_confirm_invalid_candidate_raises_without_writing(conn, tmp_path, candidate):
    leaders_file = tmp_path / "leaders.json"
    leaders_file.write_text(
        json.dumps({"date": "2026-07-03", "top_leaders": [candidate]}, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        service.confirm(conn, "2026-07-03", "codex", leaders_file=leaders_file)

    assert Q.get_daily_review(conn, "2026-07-03") is None
    assert Q.get_active_leaders(conn) == []


def test_confirm_rejects_date_mismatch_without_writing(conn, tmp_path):
    leaders_file = tmp_path / "leaders.json"
    leaders_file.write_text(
        json.dumps(
            {
                "date": "2026-07-02",
                "top_leaders": [{"stock": "海光信息", "sector": "半导体"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        service.confirm(conn, "2026-07-03", "codex", leaders_file=leaders_file)

    assert Q.get_daily_review(conn, "2026-07-03") is None
    assert Q.get_active_leaders(conn) == []


def test_confirm_rejects_invalid_cli_date_without_writing(conn, tmp_path):
    leaders_file = tmp_path / "leaders.json"
    leaders_file.write_text(
        json.dumps(
            {
                "date": "2026-99-99",
                "top_leaders": [{"stock": "海光信息", "sector": "半导体"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        service.confirm(conn, "2026-99-99", "codex", leaders_file=leaders_file)

    assert Q.get_daily_review(conn, "2026-99-99") is None
    assert Q.get_active_leaders(conn) == []


def test_confirm_rejects_invalid_source_date_without_writing(conn, tmp_path):
    leaders_file = tmp_path / "leaders.json"
    leaders_file.write_text(
        json.dumps(
            {
                "date": "2026-99-99",
                "top_leaders": [{"stock": "海光信息", "sector": "半导体"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        service.confirm(conn, "2026-07-03", "codex", leaders_file=leaders_file)

    assert Q.get_daily_review(conn, "2026-07-03") is None
    assert Q.get_active_leaders(conn) == []


def test_confirm_requires_top_leaders_list_without_writing(conn, tmp_path):
    leaders_file = tmp_path / "leaders.json"
    leaders_file.write_text(
        json.dumps({"date": "2026-07-03", "top_leaders": {"stock": "海光信息"}}, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        service.confirm(conn, "2026-07-03", "codex", leaders_file=leaders_file)

    assert Q.get_daily_review(conn, "2026-07-03") is None
    assert Q.get_active_leaders(conn) == []


def _assert_confirm_rejects_leaders_without_writing(conn, tmp_path, leaders):
    leaders_file = tmp_path / "leaders.json"
    leaders_file.write_text(
        json.dumps(
            {"date": "2026-07-03", "top_leaders": leaders},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        service.confirm(conn, "2026-07-03", "codex", leaders_file=leaders_file)

    assert Q.get_daily_review(conn, "2026-07-03") is None
    assert Q.get_active_leaders(conn) == []


def test_confirm_rejects_more_than_15_leaders_without_writing(conn, tmp_path):
    leaders = [
        {
            "stock": f"候选{i:02d}",
            "stock_code": f"600{i:03d}.SH",
            "sector": f"板块{i:02d}",
            "leader_role": "前排活跃",
        }
        for i in range(16)
    ]

    _assert_confirm_rejects_leaders_without_writing(conn, tmp_path, leaders)


def test_confirm_rejects_normalized_stock_identity_duplicate_without_writing(
    conn, tmp_path
):
    leaders = [
        {
            "stock": "贵州茅台",
            "stock_code": "600519.SH",
            "sector": "白酒",
            "leader_role": "趋势中军",
        },
        {
            "stock": "茅台",
            "code": "600519",
            "sector": "食品饮料",
            "leader_role": "前排活跃",
        },
    ]

    _assert_confirm_rejects_leaders_without_writing(conn, tmp_path, leaders)


def test_confirm_rejects_same_display_identity_when_only_one_row_has_code(
    conn, tmp_path
):
    leaders = [
        {
            "stock": "贵州茅台",
            "stock_code": "600519.SH",
            "sector": "白酒",
            "leader_role": "趋势中军",
        },
        {
            "stock": "贵州茅台",
            "sector": "食品饮料",
            "leader_role": "前排活跃",
        },
    ]

    _assert_confirm_rejects_leaders_without_writing(conn, tmp_path, leaders)


def test_confirm_rejects_embedded_code_and_name_only_duplicate_with_unicode_whitespace(
    conn, tmp_path
):
    leaders = [
        {
            "stock": "600519\u3000贵州\u00a0茅台",
            "sector": "白酒",
            "leader_role": "趋势中军",
        },
        {
            "stock": "贵州茅台",
            "sector": "食品饮料",
            "leader_role": "前排活跃",
        },
    ]

    _assert_confirm_rejects_leaders_without_writing(conn, tmp_path, leaders)


@pytest.mark.parametrize("compact_stock", ["600519贵州茅台", "600519.SH贵州茅台"])
def test_confirm_rejects_compact_code_name_and_name_only_duplicate(
    conn, tmp_path, compact_stock
):
    leaders = [
        {
            "stock": compact_stock,
            "stock_code": "600519.SH",
            "sector": "白酒",
            "leader_role": "趋势中军",
        },
        {
            "stock": "贵州茅台",
            "sector": "食品饮料",
            "leader_role": "前排活跃",
        },
    ]

    _assert_confirm_rejects_leaders_without_writing(conn, tmp_path, leaders)


@pytest.mark.parametrize("explicit_code", ["600001", "600002"])
@pytest.mark.parametrize(
    "malformed_stock",
    [
        "600001.BAD同名股票",
        "600001 .BAD同名股票",
        "600001\u3000.BAD同名股票",
        "600001\u00a0.BAD同名股票",
    ],
)
def test_confirm_rejects_malformed_display_code_suffix(
    conn, tmp_path, explicit_code, malformed_stock
):
    leaders = [
        {
            "stock": malformed_stock,
            "stock_code": explicit_code,
            "sector": "软件开发",
            "leader_role": "趋势中军",
        },
        {
            "stock": "同名股票",
            "sector": "通信设备",
            "leader_role": "前排活跃",
        },
    ]

    _assert_confirm_rejects_leaders_without_writing(conn, tmp_path, leaders)


def test_confirm_allows_same_display_name_for_distinct_canonical_codes():
    source = {
        "date": "2026-07-14",
        "top_leaders": [
            {
                "stock": "同名股票",
                "stock_code": "600001.SH",
                "sector": "软件开发",
                "leader_role": "趋势中军",
            },
            {
                "stock": "同名股票",
                "stock_code": "600002.SH",
                "sector": "通信设备",
                "leader_role": "前排活跃",
            },
        ],
    }

    leaders = service._confirmed_step5_leaders(source, "2026-07-14")

    assert [leader["stock_code"] for leader in leaders] == ["600001", "600002"]


@pytest.mark.parametrize(
    "candidate",
    [
        {
            "stock": "贵州茅台",
            "stock_code": "600519.BAD",
            "sector": "白酒",
            "leader_role": "趋势中军",
        },
        {
            "stock": "贵州茅台",
            "stock_code": "600519.SH",
            "code": "000001.SZ",
            "sector": "白酒",
            "leader_role": "趋势中军",
        },
        {
            "stock": "000001 平安银行",
            "stock_code": "600519.SH",
            "sector": "银行",
            "leader_role": "趋势中军",
        },
    ],
)
def test_confirm_rejects_invalid_or_conflicting_stock_codes_without_writing(
    conn, tmp_path, candidate
):
    _assert_confirm_rejects_leaders_without_writing(conn, tmp_path, [candidate])


@pytest.mark.parametrize("role_field", ["leader_role", "attribute_type"])
def test_confirm_rejects_duplicate_sector_role_without_writing(
    conn, tmp_path, role_field
):
    leaders = [
        {
            "stock": "海光信息",
            "stock_code": "688041.SH",
            "sector": "半导体",
            role_field: "趋势中军",
        },
        {
            "stock": "寒武纪",
            "stock_code": "688256.SH",
            "sector": "半导体",
            role_field: "趋势中军",
        },
    ]

    _assert_confirm_rejects_leaders_without_writing(conn, tmp_path, leaders)


@pytest.mark.parametrize(
    "variant_sector",
    [
        "软件  开发",
        "软件\t开发",
        "软件\u00a0开发",
        "软件\u2003开发",
        "软件\u3000开发",
    ],
    ids=["ascii-spaces", "tab", "no-break-space", "em-space", "ideographic-space"],
)
def test_confirm_rejects_normalized_sector_role_duplicate_before_transaction(
    conn, tmp_path, variant_sector
):
    leaders_file = tmp_path / "leaders.json"
    leaders_file.write_text(
        json.dumps(
            {
                "date": "2026-07-14",
                "top_leaders": [
                    {
                        "stock": "第一票",
                        "stock_code": "600001.SH",
                        "sector": "软件 开发",
                        "leader_role": "前排活跃",
                    },
                    {
                        "stock": "第二票",
                        "stock_code": "600002.SH",
                        "sector": variant_sector,
                        "leader_role": "前排活跃",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    statements: list[str] = []
    conn.set_trace_callback(statements.append)
    caught: Exception | None = None
    try:
        service.confirm(conn, "2026-07-14", "codex", leaders_file=leaders_file)
    except Exception as exc:  # The aggregate assertion below reports every side effect.
        caught = exc
    finally:
        conn.set_trace_callback(None)

    transaction_statements = [
        statement
        for statement in statements
        if statement.lstrip().upper().startswith(("BEGIN", "COMMIT", "ROLLBACK"))
    ]
    observed = {
        "value_error": isinstance(caught, ValueError),
        "duplicate_error": "duplicates sector and leader role" in str(caught or ""),
        "transaction_statements": transaction_statements,
        "daily_review_written": Q.get_daily_review(conn, "2026-07-14") is not None,
        "leader_tracking_count": len(Q.get_active_leaders(conn)),
    }

    assert observed == {
        "value_error": True,
        "duplicate_error": True,
        "transaction_statements": [],
        "daily_review_written": False,
        "leader_tracking_count": 0,
    }


def test_propose_and_confirm_share_collapsed_sector_value_and_persist_it(
    conn, tmp_path, monkeypatch
):
    monkeypatch.setattr(service, "_active_history", lambda conn: [])
    monkeypatch.setattr(
        service,
        "build_candidates",
        lambda **kwargs: {
            "date": kwargs["date"],
            "top_leaders": [
                {
                    "stock": "海光信息",
                    "stock_code": "688041.SH",
                    "sector": "  软件\u3000开发\t ",
                    "board_type": "10cm",
                    "amount_yi": 100.0,
                    "_selection_score": 100.0,
                    "evidence": [],
                }
            ],
        },
    )

    proposal = service.propose(
        conn,
        "2026-07-14",
        {"teacher_notes": []},
        no_llm=True,
        output_root=tmp_path,
    )
    leaders_file = tmp_path / "confirm-leaders.json"
    leaders_file.write_text(
        json.dumps(
            {
                "date": "2026-07-14",
                "top_leaders": [
                    {
                        **proposal["top_leaders"][0],
                        "sector": "软件\u00a0\u2003开发",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    confirmed = service.confirm(
        conn,
        "2026-07-14",
        "codex",
        leaders_file=leaders_file,
    )
    review = Q.get_daily_review(conn, "2026-07-14")
    stored_step5 = json.loads(review["step5_leaders"])
    active_leaders = Q.get_active_leaders(conn)

    assert [
        proposal["top_leaders"][0]["sector"],
        confirmed["step5_leaders"][0]["sector"],
        stored_step5["top_leaders"][0]["sector"],
        active_leaders[0]["sector"],
    ] == ["软件 开发"] * 4


def test_confirm_rolls_back_legacy_identity_merge_when_tracking_sync_fails(
    conn, tmp_path, monkeypatch
):
    Q.upsert_leader_tracking(
        conn,
        stock_code="600519 贵州茅台",
        stock_name="600519 贵州茅台",
        sector="白酒",
        attribute_type="趋势中军",
        seen_date="2026-07-13",
    )
    conn.commit()
    leaders_file = tmp_path / "leaders.json"
    leaders_file.write_text(
        json.dumps(
            {
                "date": "2026-07-14",
                "top_leaders": [
                    {
                        "stock": "贵州茅台",
                        "stock_code": "600519",
                        "sector": "白酒",
                        "leader_role": "趋势中军",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def fail_tracking_sync(*args, **kwargs):
        raise RuntimeError("sync failed")

    monkeypatch.setattr(Q, "upsert_leader_tracking", fail_tracking_sync)

    with pytest.raises(RuntimeError, match="sync failed"):
        service.confirm(conn, "2026-07-14", "codex", leaders_file=leaders_file)

    rows = conn.execute("SELECT * FROM leader_tracking ORDER BY id").fetchall()
    assert Q.get_daily_review(conn, "2026-07-14") is None
    assert len(rows) == 1
    assert rows[0]["stock_code"] == "600519 贵州茅台"
    assert rows[0]["first_seen_date"] == "2026-07-13"
    assert rows[0]["last_seen_date"] == "2026-07-13"


def test_confirm_rolls_back_deleted_duplicate_rows_when_tracking_sync_fails(
    conn, tmp_path, monkeypatch
):
    Q.upsert_leader_tracking(
        conn,
        stock_code="600519 贵州茅台",
        stock_name="600519 贵州茅台",
        sector="白酒",
        attribute_type="趋势中军",
        seen_date="2026-07-12",
    )
    Q.upsert_leader_tracking(
        conn,
        stock_code="600519",
        stock_name="贵州茅台",
        sector="白酒",
        attribute_type="趋势中军",
        seen_date="2026-07-13",
    )
    conn.commit()
    leaders_file = tmp_path / "leaders.json"
    leaders_file.write_text(
        json.dumps(
            {
                "date": "2026-07-14",
                "top_leaders": [
                    {
                        "stock": "贵州茅台",
                        "stock_code": "600519",
                        "sector": "白酒",
                        "leader_role": "趋势中军",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        Q,
        "upsert_leader_tracking",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("sync failed")),
    )

    with pytest.raises(RuntimeError, match="sync failed"):
        service.confirm(conn, "2026-07-14", "codex", leaders_file=leaders_file)

    rows = conn.execute(
        "SELECT * FROM leader_tracking ORDER BY stock_code"
    ).fetchall()
    assert Q.get_daily_review(conn, "2026-07-14") is None
    assert [row["stock_code"] for row in rows] == ["600519", "600519 贵州茅台"]
    assert [row["first_seen_date"] for row in rows] == ["2026-07-13", "2026-07-12"]


def test_invalid_limit_step_code_cannot_attach_height_to_valid_quote():
    class Registry:
        def call(self, name, *args):
            if name == "get_market_daily_quotes":
                return type(
                    "Result",
                    (),
                    {
                        "success": True,
                        "data": [
                            {
                                "ts_code": "600001.SH",
                                "name": "合法样本",
                                "pct_chg": 10.0,
                                "amount": 3_000_000,
                            }
                        ],
                    },
                )()
            if name == "get_stock_basic_list":
                return type("Result", (), {"success": True, "data": []})()
            if name == "get_stock_sw_industry_map":
                return type(
                    "Result",
                    (),
                    {"success": True, "data": {}, "source": "test:sw"},
                )()
            raise AssertionError(name)

    enriched = service.attach_market_quotes(
        {"market": {"limit_step": {"data": [{"ts_code": "600001.BAD", "nums": 5}]}}},
        "2026-07-14",
        Registry(),
    )

    quote = enriched["market"]["stock_quotes"]["data"][0]
    assert quote["code"] == "600001.SH"
    assert "limit_height" not in quote


def test_propose_reports_both_review_pool_and_final_sector_role_trims(
    tmp_path, monkeypatch
):
    items = [
        {
            "stock": f"同板块样本{i}",
            "stock_code": f"60000{i}",
            "sector": "软件开发",
            "board_type": "10cm",
            "amount_yi": amount,
            "_selection_score": score,
            "evidence": [],
            "is_new": True,
        }
        for i, (amount, score) in enumerate(
            [(100.0, 400.0), (80.0, 300.0), (60.0, 200.0), (40.0, 100.0)],
            start=1,
        )
    ]
    monkeypatch.setattr(
        service,
        "build_candidates",
        lambda **kwargs: {"date": "2026-07-14", "top_leaders": items},
    )
    monkeypatch.setattr(service, "_active_history", lambda conn: [])

    proposal = service.propose(
        object(),
        "2026-07-14",
        {"teacher_notes": []},
        no_llm=True,
        output_root=tmp_path,
        registry=None,
    )

    assert proposal["candidate_limit"] == {
        "max_candidates": 15,
        "original_count": 4,
        "deduped_count": 4,
        "duplicate_trimmed_count": 0,
        "trimmed_count": 2,
        "review_pool_count": 3,
        "review_pool_trimmed_count": 1,
        "sector_role_trimmed_count": 1,
        "stock_duplicate_trimmed_count": 0,
        "final_count": 2,
    }
