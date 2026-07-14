from __future__ import annotations

from collections import Counter
import importlib

import pytest


def _selection():
    return importlib.import_module("services.daily_leaders.selection")


def test_models_export_role_board_and_candidate_limits():
    models = importlib.import_module("services.daily_leaders.models")

    assert getattr(models, "LEADER_ROLES", None) == {
        "趋势中军",
        "连板核心",
        "前排活跃",
        "弹性前排",
    }
    assert getattr(models, "STATUS_ROLES", None) == {"备选", "剔除"}
    assert getattr(models, "BOARD_TYPES", None) == {"10cm", "20cm", "30cm", "非涨停"}
    assert getattr(models, "MAX_CONFIRMATION_CANDIDATES", None) == 15
    assert getattr(models, "MAX_LLM_REVIEW_CANDIDATES", None) == 30
    assert models.CLARITY_HIGH == "高"


def test_normalize_stock_display_only_collapses_identical_whitespace_tokens():
    selection = _selection()

    assert selection.normalize_stock_display(" 华大九天\t华大九天 ") == "华大九天"
    assert selection.normalize_stock_display("688041 海光信息 688041 海光信息") == "688041 海光信息"
    assert selection.normalize_stock_display(" 601138   工业富联 ") == "601138 工业富联"
    assert selection.normalize_stock_display("688041 海光信息 688041 算力") == (
        "688041 海光信息 688041 算力"
    )
    assert selection.stock_identity_key({"stock": "华大九天 华大九天"}) == selection.stock_identity_key(
        {"stock": "华大九天"}
    )
    assert selection.stock_identity_key({"stock": "601138 工业富联"}) != selection.stock_identity_key(
        {"stock": "工业富联"}
    )
    assert selection.stock_identity_key({"stock_code": " 688041.SH ", "stock": "海光信息"}) == (
        selection.stock_identity_key({"code": "688041", "stock": "不同展示名"})
    )


def test_stock_identity_key_uses_first_nonblank_normalized_code_then_name():
    selection = _selection()

    assert selection.stock_identity_key({"stock_code": "   ", "code": "688041.SH"}) == "688041"
    assert selection.stock_identity_key(
        {"stock": " \t ", "stock_name": " 海光信息 ", "name": "备用名称"}
    ) == selection.stock_identity_key({"name": "海光信息"})


def test_assign_fallback_roles_covers_four_roles_without_mutating_input():
    selection = _selection()
    items = [
        {
            "stock": "连板股",
            "sector": "机器人",
            "limit_height": 2,
            "amount_yi": 100,
            "board_type": "10cm",
        },
        {"stock": "容量股", "sector": "半导体", "amount_yi": 80, "board_type": "10cm"},
        {"stock": "弹性股", "sector": "半导体", "amount_yi": 30, "board_type": "20cm"},
        {"stock": "活跃股", "sector": "消费", "board_type": "10cm"},
    ]

    assigned = selection.assign_fallback_roles(items)

    assert [item["fallback_role"] for item in assigned] == [
        "连板核心",
        "趋势中军",
        "弹性前排",
        "前排活跃",
    ]
    assert all("fallback_role" not in item for item in items)


@pytest.mark.parametrize(
    ("follower_board_type", "expected_follower_role"),
    [("10cm", "前排活跃"), ("20cm", "弹性前排")],
)
def test_assign_fallback_roles_does_not_promote_runner_up_when_amount_winner_is_limit_core(
    follower_board_type,
    expected_follower_role,
):
    selection = _selection()
    items = [
        {
            "stock": "百亿连板股",
            "sector": "机器人",
            "limit_height": 2,
            "amount_yi": 100,
            "board_type": "10cm",
        },
        {
            "stock": "二十亿跟随股",
            "sector": "机器人",
            "amount_yi": 20,
            "board_type": follower_board_type,
        },
    ]

    assigned = selection.assign_fallback_roles(items)

    assert [item["fallback_role"] for item in assigned] == [
        "连板核心",
        expected_follower_role,
    ]


def test_prepare_llm_review_pool_caps_each_sector_role_at_two_and_global_at_thirty():
    selection = _selection()
    items = [
        {
            "stock": f"候选-{sector_index:02d}-{stock_index}",
            "sector": f"板块-{sector_index:02d}",
            "limit_height": 2,
            "board_type": "10cm",
            "_selection_score": 1_000 - sector_index,
        }
        for sector_index in range(20)
        for stock_index in range(3)
    ]

    pool = selection.prepare_llm_review_pool(items)

    groups = Counter((item["sector"], item["fallback_role"]) for item in pool)
    assert len(pool) == 30
    assert max(groups.values()) == 2
    assert list(groups.values()) == [2] * 15
    assert [item["stock"] for item in pool[:2]] == ["候选-00-0", "候选-00-1"]


def test_prepare_llm_review_pool_deduplicates_stock_globally_before_spending_capacity():
    selection = _selection()
    items = [
        {
            "stock": "海光信息",
            "stock_code": "688041.SH",
            "sector": "半导体",
            "limit_height": 2,
            "_selection_score": 100,
        },
        {
            "stock": "688041 海光信息",
            "code": "688041",
            "sector": "算力",
            "limit_height": 2,
            "_selection_score": 99,
        },
        {
            "stock": "中芯国际",
            "stock_code": "688981.SH",
            "sector": "半导体",
            "limit_height": 2,
            "_selection_score": 98,
        },
        {
            "stock": "工业富联",
            "stock_code": "601138.SH",
            "sector": "算力",
            "limit_height": 2,
            "_selection_score": 97,
        },
    ]

    pool = selection.prepare_llm_review_pool(items, limit=3)

    assert [item["stock"] for item in pool] == ["海光信息", "中芯国际", "工业富联"]
    assert len({selection.stock_identity_key(item) for item in pool}) == 3


def test_select_confirmation_candidates_preserves_fallback_role_from_full_review_pool():
    selection = _selection()
    complete_items = [
        {
            "stock": "成交额中军",
            "sector": "机器人",
            "amount_yi": 100,
            "board_type": "10cm",
            "_selection_score": 10,
        },
        {
            "stock": "20cm弹性票",
            "sector": "机器人",
            "amount_yi": 20,
            "board_type": "20cm",
            "_selection_score": 100,
        },
    ]

    assigned = selection.assign_fallback_roles(complete_items)
    pool = selection.prepare_llm_review_pool(assigned, limit=1)
    selected, _ = selection.select_confirmation_candidates(pool)

    assert [item["fallback_role"] for item in assigned] == ["趋势中军", "弹性前排"]
    assert [item["stock"] for item in pool] == ["20cm弹性票"]
    assert selected[0]["fallback_role"] == "弹性前排"
    assert selected[0]["leader_role"] == "弹性前排"


@pytest.mark.parametrize("fallback_role", [None, "", "容量中军", "备选"])
def test_select_confirmation_candidates_recomputes_missing_or_invalid_fallback_role(fallback_role):
    selection = _selection()
    item = {
        "stock": "直接调用弹性票",
        "sector": "机器人",
        "board_type": "20cm",
        "_selection_score": 100,
    }
    if fallback_role is not None:
        item["fallback_role"] = fallback_role

    selected, _ = selection.select_confirmation_candidates([item])

    assert selected[0]["fallback_role"] == "弹性前排"
    assert selected[0]["leader_role"] == "弹性前排"


@pytest.mark.parametrize(
    ("limit", "expected_count"),
    [(1, 1), (30, 30), (31, 30)],
)
def test_prepare_llm_review_pool_accepts_integer_limits_and_clamps_above_thirty(
    limit,
    expected_count,
):
    selection = _selection()
    items = [
        {
            "stock": f"候选-{index:02d}",
            "sector": f"板块-{index:02d}",
            "limit_height": 2,
            "_selection_score": 100 - index,
        }
        for index in range(40)
    ]

    assert len(selection.prepare_llm_review_pool(items, limit=limit)) == expected_count


@pytest.mark.parametrize("limit", [0, -1, True, False, 1.0, "1", None])
def test_prepare_llm_review_pool_rejects_nonpositive_or_noninteger_limits(limit):
    selection = _selection()

    with pytest.raises(ValueError, match="limit"):
        selection.prepare_llm_review_pool([], limit=limit)


def test_select_confirmation_candidates_uses_llm_rank_within_same_sector_role():
    selection = _selection()
    items = [
        {
            "stock": "高分但第二",
            "sector": "半导体",
            "board_type": "10cm",
            "_selection_score": 100,
            "llm_role": "前排活跃",
            "llm_rank": 2,
        },
        {
            "stock": "低分但第一",
            "sector": "半导体",
            "board_type": "10cm",
            "_selection_score": 10,
            "llm_role": "前排活跃",
            "llm_rank": 1,
        },
    ]

    selected, stats = selection.select_confirmation_candidates(items, llm_ok=True)

    assert [item["stock"] for item in selected] == ["低分但第一"]
    assert selected[0]["leader_role"] == "前排活跃"
    assert selected[0]["attribute_type"] == "前排活跃"
    assert selected[0]["selection_basis"] == "llm"
    assert stats["sector_role_trimmed_count"] == 1


def test_select_confirmation_candidates_ignores_llm_when_run_failed():
    selection = _selection()
    items = [
        {
            "stock": "规则高分",
            "sector": "半导体",
            "board_type": "10cm",
            "_selection_score": 100,
            "llm_role": "前排活跃",
            "llm_rank": 2,
        },
        {
            "stock": "LLM第一",
            "sector": "半导体",
            "board_type": "10cm",
            "_selection_score": 10,
            "llm_role": "前排活跃",
            "llm_rank": 1,
        },
    ]

    selected, _ = selection.select_confirmation_candidates(items, llm_ok=False)

    assert [item["stock"] for item in selected] == ["规则高分"]
    assert selected[0]["selection_basis"] == "deterministic_fallback"


def test_select_confirmation_candidates_falls_back_from_illegal_llm_role():
    selection = _selection()
    selected, _ = selection.select_confirmation_candidates(
        [
            {
                "stock": "非法角色票",
                "sector": "机器人",
                "board_type": "20cm",
                "_selection_score": 50,
                "llm_role": "容量中军",
                "llm_rank": 1,
            }
        ],
        llm_ok=True,
    )

    assert selected[0]["fallback_role"] == "弹性前排"
    assert selected[0]["leader_role"] == "弹性前排"
    assert selected[0]["attribute_type"] == "弹性前排"
    assert selected[0]["selection_basis"] == "deterministic_fallback"


def test_select_confirmation_candidates_ignores_rank_from_illegal_llm_role():
    selection = _selection()
    items = [
        {
            "stock": "非法角色低分",
            "sector": "机器人",
            "board_type": "10cm",
            "_selection_score": 10,
            "llm_role": "容量中军",
            "llm_rank": 1,
        },
        {
            "stock": "规则高分",
            "sector": "机器人",
            "board_type": "10cm",
            "_selection_score": 100,
        },
    ]

    selected, _ = selection.select_confirmation_candidates(items, llm_ok=True)

    assert [item["stock"] for item in selected] == ["规则高分"]
    assert selected[0]["selection_basis"] == "deterministic_fallback"


def test_select_confirmation_candidates_ignores_rank_between_backup_candidates():
    selection = _selection()
    items = [
        {
            "stock": "备选低分",
            "sector": "机器人",
            "board_type": "10cm",
            "_selection_score": 10,
            "llm_role": "备选",
            "llm_rank": 1,
        },
        {
            "stock": "备选高分",
            "sector": "机器人",
            "board_type": "10cm",
            "_selection_score": 100,
            "llm_role": "备选",
            "llm_rank": 9,
        },
    ]

    selected, _ = selection.select_confirmation_candidates(items, llm_ok=True)

    assert [item["stock"] for item in selected] == ["备选高分"]
    assert selected[0]["selection_basis"] == "deterministic_fallback"


def test_select_confirmation_candidates_drops_excluded_and_downweights_backup():
    selection = _selection()
    items = [
        {
            "stock": "剔除票",
            "sector": "机器人",
            "board_type": "10cm",
            "_selection_score": 1_000,
            "llm_role": "剔除",
            "llm_rank": 1,
        },
        {
            "stock": "备选票",
            "sector": "半导体",
            "board_type": "20cm",
            "_selection_score": 100,
            "llm_role": "备选",
            "llm_rank": 1,
        },
        {
            "stock": "正常票",
            "sector": "消费",
            "board_type": "10cm",
            "_selection_score": 1,
            "llm_role": "前排活跃",
            "llm_rank": 9,
        },
    ]

    selected, stats = selection.select_confirmation_candidates(items, llm_ok=True)

    assert [item["stock"] for item in selected] == ["正常票", "备选票"]
    assert selected[1]["leader_role"] == "弹性前排"
    assert selected[1]["selection_basis"] == "deterministic_fallback"
    assert stats["review_pool_count"] == 3
    assert stats["trimmed_count"] == 1


def test_select_confirmation_candidates_keeps_one_stock_per_sector_role():
    selection = _selection()
    items = [
        {
            "stock": "第一票",
            "sector": "半导体",
            "board_type": "10cm",
            "_selection_score": 100,
        },
        {
            "stock": "第二票",
            "sector": "半导体",
            "board_type": "10cm",
            "_selection_score": 90,
        },
    ]

    selected, stats = selection.select_confirmation_candidates(items)

    assert [item["stock"] for item in selected] == ["第一票"]
    assert stats["sector_role_trimmed_count"] == 1
    assert stats["stock_duplicate_trimmed_count"] == 0


def test_select_confirmation_candidates_keeps_stock_globally_unique_across_sectors():
    selection = _selection()
    items = [
        {
            "stock": "海光信息",
            "stock_code": "688041.SH",
            "sector": "半导体",
            "board_type": "10cm",
            "_selection_score": 100,
        },
        {
            "stock": "688041 海光信息",
            "code": "688041",
            "sector": "算力",
            "board_type": "20cm",
            "_selection_score": 90,
        },
    ]

    selected, stats = selection.select_confirmation_candidates(items)

    assert [item["sector"] for item in selected] == ["半导体"]
    assert stats["sector_role_trimmed_count"] == 0
    assert stats["stock_duplicate_trimmed_count"] == 1


def test_select_confirmation_candidates_caps_final_list_at_fifteen_and_reports_stats():
    selection = _selection()
    items = [
        {
            "stock": f"候选-{index:02d}",
            "sector": f"板块-{index:02d}",
            "board_type": "10cm",
            "_selection_score": 100 - index,
        }
        for index in range(20)
    ]

    selected, stats = selection.select_confirmation_candidates(items)

    assert len(selected) == 15
    assert [item["stock"] for item in selected] == [f"候选-{index:02d}" for index in range(15)]
    assert stats == {
        "original_count": 20,
        "review_pool_count": 20,
        "final_count": 15,
        "sector_role_trimmed_count": 0,
        "stock_duplicate_trimmed_count": 0,
        "trimmed_count": 5,
    }


@pytest.mark.parametrize("max_candidates", [1, 15])
def test_select_confirmation_candidates_accepts_integer_caps_at_boundaries(max_candidates):
    selection = _selection()

    selected, stats = selection.select_confirmation_candidates([], max_candidates=max_candidates)

    assert selected == []
    assert stats["final_count"] == 0


@pytest.mark.parametrize("max_candidates", [0, 16, True, False, 1.0, 15.0, "1", None])
def test_select_confirmation_candidates_rejects_noninteger_or_out_of_range_caps(max_candidates):
    selection = _selection()

    with pytest.raises(ValueError, match="max_candidates"):
        selection.select_confirmation_candidates([], max_candidates=max_candidates)


@pytest.mark.parametrize("invalid_rank", [True, False, 0, -1, 1.0, 1.5, "1", "not-a-rank"])
def test_select_confirmation_candidates_treats_invalid_llm_rank_as_missing(invalid_rank):
    selection = _selection()
    items = [
        {
            "stock": "无效排名低分",
            "sector": "半导体",
            "board_type": "10cm",
            "_selection_score": 10,
            "llm_role": "前排活跃",
            "llm_rank": invalid_rank,
        },
        {
            "stock": "规则高分",
            "sector": "半导体",
            "board_type": "10cm",
            "_selection_score": 100,
            "llm_role": "前排活跃",
        },
    ]

    selected, _ = selection.select_confirmation_candidates(items, llm_ok=True)

    assert [item["stock"] for item in selected] == ["规则高分"]
