"""tail-scan 两层概念上下文契约（全 fake，无真实网络）。"""
from __future__ import annotations

import math

import pytest

from providers.base import DataResult
from services import concept_tags
from services.tail_scan import concept_context
from services.tail_scan import constants as C


def _concept(name: str, member_count, concept_code: str | None = None) -> dict:
    return {
        "concept_code": concept_code or f"{name}.TI",
        "name": name,
        "member_count": member_count,
    }


def _moneyflow(*names: str) -> list[dict]:
    return [
        {"name": name, "net_amount_yi": 100 - index, "company_num": 10 + index}
        for index, name in enumerate(names)
    ]


class _Registry:
    def __init__(self, stocks: dict, moneyflow=None, *, member_error: str = ""):
        self.calls: list[tuple[str, tuple]] = []
        self.memberships = DataResult(
            data=None if member_error else {"stocks": stocks},
            source="tushare:ths_member:by_stock",
            fetched_at="2026-07-14T14:40:00",
            error=member_error,
        )
        self.moneyflow = moneyflow

    def call(self, capability, *args):
        self.calls.append((capability, args))
        if capability == "get_stock_concept_memberships":
            return self.memberships
        if capability == "get_concept_moneyflow_ths":
            if isinstance(self.moneyflow, DataResult):
                return self.moneyflow
            return DataResult(
                data=self.moneyflow,
                source="tushare:moneyflow_cnt_ths",
            )
        raise AssertionError(f"unexpected capability: {capability}")


def test_container_limit_aliases_shared_concept_tag_truth_source():
    assert C.CONCEPT_CONTAINER_MAX_MEMBERS == concept_tags.CONTAINER_MAX_MEMBERS


def test_hot_concepts_filter_containers_before_filling_top8():
    rows = [
        {"name": "AI智能体", "net_amount_yi": 100, "company_num": 449},
        {"name": "百度概念", "net_amount_yi": 99, "company_num": 234},
        {"name": "DeepSeek概念", "net_amount_yi": 98, "company_num": 773},
        *[
            {"name": f"窄概念{i}", "net_amount_yi": 90 - i, "company_num": 20 + i}
            for i in range(1, 8)
        ],
    ]

    names, status = concept_context.select_hot_concepts(rows, top_m=8)

    assert names == ["百度概念", *[f"窄概念{i}" for i in range(1, 8)]]
    assert status == "ok"


def test_hot_concepts_filter_container_before_deduping_same_name():
    rows = [
        {"name": "同名概念", "net_amount_yi": 100, "company_num": 301},
        {"name": "同名概念", "net_amount_yi": 99, "company_num": 120},
        {"name": "补足概念", "net_amount_yi": 98, "company_num": 80},
    ]

    names, status = concept_context.select_hot_concepts(rows, top_m=2)

    assert names == ["同名概念", "补足概念"]
    assert status == "ok"


def test_hot_concepts_use_stable_finite_net_inflow_order_and_keep_negatives():
    rows = [
        {"name": "同额甲", "net_amount_yi": "2", "company_num": "10"},
        {"name": "脏净流入", "net_amount_yi": float("nan"), "company_num": 1},
        {"name": "同额乙", "net_amount": 2.0, "company_num": 11},
        {"name": "负流入", "net_amount_yi": -3, "company_num": 12},
        {"name": "无穷流入", "net_amount_yi": float("inf"), "company_num": 1},
    ]

    names, status = concept_context.select_hot_concepts(rows, top_m=3)

    assert names == ["同额甲", "同额乙", "负流入"]
    assert status == "ok"


@pytest.mark.parametrize(
    "dirty_count",
    [None, "", float("nan"), float("inf"), -1, 0, 1.5, True],
    ids=["none", "empty", "nan", "inf", "negative", "zero", "fraction", "bool"],
)
def test_hot_concepts_fail_closed_on_dirty_width_while_top_m_is_unfilled(dirty_count):
    rows = [
        {"name": "窄概念", "net_amount_yi": 2, "company_num": 10},
        {"name": "宽度未知", "net_amount_yi": 1, "company_num": dirty_count},
    ]

    assert concept_context.select_hot_concepts(rows, top_m=2) == (
        [],
        "coverage_failed",
    )


def test_hot_concepts_ignore_dirty_tail_after_top_m_is_filled():
    rows = [
        {"name": "第一", "net_amount_yi": 3, "company_num": 10},
        {"name": "第二", "net_amount_yi": 2, "company_num": 20},
        {"name": "脏尾部", "net_amount_yi": 1, "company_num": None},
    ]

    assert concept_context.select_hot_concepts(rows, top_m=2) == (
        ["第一", "第二"],
        "ok",
    )


def test_hot_concepts_distinguish_empty_source_from_insufficient_coverage():
    assert concept_context.select_hot_concepts([], top_m=2) == ([], "source_failed")
    assert concept_context.select_hot_concepts(
        [{"name": "唯一", "net_amount_yi": 1, "company_num": 10}],
        top_m=2,
    ) == ([], "coverage_failed")


def test_context_separates_filtered_memberships_from_hot_hits_and_propagates_snapshot():
    stocks = {
        "688106.SH": {
            "status": "ok",
            "concepts": [
                _concept("存储芯片", 180, "885001.TI"),
                _concept("第三代半导体", 160, "885002.TI"),
                _concept("融资融券", 3800, "885003.TI"),
                _concept("存储芯片别名", 100, "885001.TI"),
                _concept("存储芯片", 90, "885004.TI"),
            ],
        }
    }
    registry = _Registry(stocks, _moneyflow("AI眼镜", "无线耳机"))

    result = concept_context.build_concept_context(
        registry,
        ["688106.SH"],
        concept_date="2026-07-10",
        top_m=2,
    )

    row = result["688106.SH"]
    assert registry.calls == [
        ("get_stock_concept_memberships", (["688106.SH"],)),
        ("get_concept_moneyflow_ths", ("2026-07-10",)),
    ]
    assert row["stock_concept_names"] == ["第三代半导体", "存储芯片"]
    assert row["stock_concept_total"] == 2
    assert row["stock_concept_status"] == "ok"
    assert row["stock_concept_source"] == "tushare:ths_member:by_stock"
    assert row["stock_concept_snapshot_at"] == "2026-07-14T14:40:00"
    assert row["concept_names"] == []
    assert row["concept_status"] == "ok"
    assert row["in_hot_concept"] is False
    assert row["stock_concept_memberships"] == [
        _concept("第三代半导体", 160, "885002.TI"),
        _concept("存储芯片", 180, "885001.TI"),
    ]


def test_context_keeps_member_count_300_and_filters_301_or_nonpositive_counts():
    stocks = {
        "600001.SH": {
            "status": "ok",
            "concepts": [
                _concept("边界保留", 300),
                _concept("边界剔除", 301),
                _concept("宽度缺失", 0),
            ],
        }
    }
    registry = _Registry(stocks, _moneyflow("边界保留"))

    row = concept_context.build_concept_context(
        registry, ["600001.SH"], concept_date="2026-07-10", top_m=1
    )["600001.SH"]

    assert row["stock_concept_names"] == ["边界保留"]
    assert row["concept_names"] == ["边界保留"]
    assert row["in_hot_concept"] is True


def test_concept_date_none_still_fetches_current_memberships_once():
    registry = _Registry(
        {"600001.SH": {"status": "ok", "concepts": [_concept("当前归属", 10)]}},
        moneyflow=None,
    )

    row = concept_context.build_concept_context(
        registry, ["600001.SH"], concept_date=None, top_m=8
    )["600001.SH"]

    assert registry.calls == [
        ("get_stock_concept_memberships", (["600001.SH"],))
    ]
    assert row["stock_concept_names"] == ["当前归属"]
    assert row["stock_concept_status"] == "ok"
    assert row["concept_status"] == "source_failed"


def test_empty_candidates_short_circuit_without_registry_calls():
    class _Bomb:
        def __getattr__(self, name):
            raise AssertionError(name)

    assert concept_context.build_concept_context(
        _Bomb(), [], concept_date=None, top_m=8
    ) == {}


@pytest.mark.parametrize("member_status", ["ok", "missing", "source_failed"])
def test_global_source_failed_takes_priority_over_every_member_status(member_status):
    concepts = [_concept("窄概念", 10)] if member_status == "ok" else []
    registry = _Registry(
        {"600001.SH": {"status": member_status, "concepts": concepts}},
        DataResult(data=None, source="tushare", error="moneyflow failed"),
    )

    row = concept_context.build_concept_context(
        registry, ["600001.SH"], concept_date="2026-07-10", top_m=1
    )["600001.SH"]

    assert row["concept_status"] == "source_failed"


@pytest.mark.parametrize("member_status", ["ok", "missing", "source_failed"])
def test_global_coverage_failed_takes_priority_over_every_member_status(member_status):
    concepts = [_concept("窄概念", 10)] if member_status == "ok" else []
    registry = _Registry(
        {"600001.SH": {"status": member_status, "concepts": concepts}},
        _moneyflow("唯一概念"),
    )

    row = concept_context.build_concept_context(
        registry, ["600001.SH"], concept_date="2026-07-10", top_m=2
    )["600001.SH"]

    assert row["concept_status"] == "coverage_failed"


@pytest.mark.parametrize(
    ("member_status", "concepts", "expected_stock_status", "expected_hot_status"),
    [
        ("source_failed", [], "source_failed", "member_failed"),
        ("missing", [], "missing", "ok"),
        ("ok", [_concept("容器概念", 301)], "missing", "ok"),
        ("ok", [_concept("其它窄概念", 10)], "ok", "ok"),
    ],
)
def test_global_ok_maps_per_stock_membership_truth_table(
    member_status, concepts, expected_stock_status, expected_hot_status
):
    registry = _Registry(
        {"600001.SH": {"status": member_status, "concepts": concepts}},
        _moneyflow("热概念"),
    )

    row = concept_context.build_concept_context(
        registry, ["600001.SH"], concept_date="2026-07-10", top_m=1
    )["600001.SH"]

    assert row["stock_concept_status"] == expected_stock_status
    assert row["concept_status"] == expected_hot_status
    assert row["concept_names"] == []
    assert row["in_hot_concept"] is False


def test_batch_membership_failure_marks_each_stock_source_failed_but_keeps_metadata():
    registry = _Registry({}, _moneyflow("热概念"), member_error="catalog failed")

    result = concept_context.build_concept_context(
        registry,
        ["600001.SH", "600002.SH"],
        concept_date="2026-07-10",
        top_m=1,
    )

    assert set(result) == {"600001.SH", "600002.SH"}
    assert all(row["stock_concept_status"] == "source_failed" for row in result.values())
    assert all(row["concept_status"] == "member_failed" for row in result.values())
    assert all(
        row["stock_concept_source"] == "tushare:ths_member:by_stock"
        for row in result.values()
    )
    assert all(
        row["stock_concept_snapshot_at"] == "2026-07-14T14:40:00"
        for row in result.values()
    )


def test_rank_stock_concepts_prefers_hot_then_semantic_relevance_then_narrowness():
    memberships = [
        _concept("科技概念", 5, "885001.TI"),
        _concept("电子特气概念", 120, "885002.TI"),
        _concept("CPO概念", 80, "885003.TI"),
        _concept("AI概念", 2, "885004.TI"),
        _concept("存储芯片", 200, "885005.TI"),
        _concept("第三代半导体", 160, "885006.TI"),
    ]

    ranked = concept_context.rank_stock_concepts(
        memberships,
        hot_names=["第三代半导体", "存储芯片"],
        semantic_texts=["电子化学品", "主营电子特气研发", "CPO光模块"],
    )

    assert ranked == [
        "第三代半导体",
        "存储芯片",
        "电子特气概念",
        "CPO概念",
        "AI概念",
        "科技概念",
    ]
    assert set(ranked) == {item["name"] for item in memberships}
    assert all(math.isfinite(item["member_count"]) for item in memberships)


def test_rank_stock_concepts_ignores_single_chinese_or_short_alnum_overlap():
    memberships = [
        _concept("算力租赁", 100),
        _concept("AI概念", 10),
        _concept("云服务", 20),
    ]

    ranked = concept_context.rank_stock_concepts(
        memberships,
        hot_names=[],
        semantic_texts=["算", "AI业务"],
    )

    assert ranked == ["AI概念", "云服务", "算力租赁"]
