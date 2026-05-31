"""volume_concentration trend 纯函数单测:三段趋势 + 不足N天兜底 + 映射补全≠轮动。"""
from __future__ import annotations

import pytest

from services.volume_concentration import trend


def _rec(date, stocks, sector_inds, total):
    """造一条精简 record:stocks=[(code,name)],sector_inds=[行业名],total=合计额。"""
    return {
        "date": date,
        "total_amount_billion": total,
        "stocks": [{"code": c, "name": n} for c, n in stocks],
        "sector_summary": [{"industry": i} for i in sector_inds],
    }


def test_insufficient_data_flagged():
    """仅 1 天 → sufficient=False(formatter 据此出兜底文案)。"""
    records = [_rec("2026-05-29", [("A", "甲")], ["电池"], 100.0)]

    result = trend.compute_trend(records)

    assert result["days"] == 1
    assert result["sufficient"] is False


def test_stock_retention_counts_consecutive_streak():
    """个股连续在榜:从最新日往前数连续天数,只统计最新日在榜的票。"""
    records = [
        _rec("2026-05-27", [("A", "甲"), ("B", "乙")], ["电池"], 100.0),
        _rec("2026-05-28", [("A", "甲"), ("C", "丙")], ["电池"], 120.0),
        _rec("2026-05-29", [("A", "甲"), ("C", "丙")], ["电池"], 150.0),
    ]

    result = trend.compute_trend(records)
    ret = {r["code"]: r["streak"] for r in result["stock_retention"]}

    assert ret["A"] == 3   # 连续 3 天
    assert ret["C"] == 2   # 05-28、05-29 连续 2 天
    assert "B" not in ret  # 最新日不在榜,不计留存


def test_sector_rotation_excludes_unclassified():
    """板块轮动 = 最新日 vs 前一日;「未分类」不计入轮动(映射补全≠轮动,codex 中5)。"""
    records = [
        _rec("2026-05-28", [], ["电池", "白酒Ⅱ", "未分类"], 100.0),
        _rec("2026-05-29", [], ["电池", "证券Ⅱ", "未分类"], 120.0),
    ]

    result = trend.compute_trend(records)
    rot = result["sector_rotation"]

    assert rot["new"] == ["证券Ⅱ"]       # 新进
    assert rot["dropped"] == ["白酒Ⅱ"]    # 退出
    assert rot["持续"] == ["电池"]         # 持续
    assert "未分类" not in rot["new"] + rot["dropped"] + rot["持续"]


def test_amount_trend_change_pct():
    """头部量级环比:最新 vs 前一日总额。"""
    records = [
        _rec("2026-05-28", [], ["电池"], 100.0),
        _rec("2026-05-29", [], ["电池"], 120.0),
    ]

    result = trend.compute_trend(records)

    assert result["amount_trend"]["latest"] == 120.0
    assert result["amount_trend"]["previous"] == 100.0
    assert result["amount_trend"]["change_pct"] == pytest.approx(20.0)


def test_handles_unordered_records_defensively():
    """乱序传入(最新在前)也能正确算 —— compute_trend 内部按日期正序防御。"""
    newer = _rec("2026-05-29", [("A", "甲")], ["证券Ⅱ", "电池"], 120.0)
    older = _rec("2026-05-28", [("A", "甲")], ["白酒Ⅱ", "电池"], 100.0)

    result = trend.compute_trend([newer, older])  # 故意乱序

    assert result["amount_trend"]["latest"] == 120.0      # 05-29 才是最新
    assert result["amount_trend"]["previous"] == 100.0
    assert result["sector_rotation"]["new"] == ["证券Ⅱ"]  # 05-29 相对 05-28 新进


def test_stock_rotation_new_and_dropped():
    """个股级轮动(#2):最新日 vs 前一日的 Top20 成员新进 / 退出,带 name。"""
    records = [
        _rec("2026-05-28", [("A", "甲"), ("B", "乙")], ["电池"], 100.0),
        _rec("2026-05-29", [("A", "甲"), ("C", "丙")], ["电池"], 120.0),
    ]

    rot = trend.compute_trend(records)["stock_rotation"]

    assert [x["code"] for x in rot["new"]] == ["C"]       # C 今日新进
    assert rot["new"][0]["name"] == "丙"
    assert [x["code"] for x in rot["dropped"]] == ["B"]   # B 退出


def test_stock_rotation_empty_when_single_day():
    """仅 1 天 → 无前一日可比,new/dropped 均空。"""
    records = [_rec("2026-05-29", [("A", "甲")], ["电池"], 100.0)]
    assert trend.compute_trend(records)["stock_rotation"] == {"new": [], "dropped": []}


def _rec_share(date, sectors_with_share):
    """造带 share_in_top_n 的精简 record(供 CR3 计算)。"""
    return {
        "date": date, "total_amount_billion": 100.0, "stocks": [],
        "sector_summary": [{"industry": i, "share_in_top_n": sh} for i, sh in sectors_with_share],
    }


def test_cr3_trend_delta_and_rank():
    """CR3 环比(pp)+ 窗口分位(第几高);未分类不计入 CR3(与 formatter 同口径)。"""
    records = [
        _rec_share("2026-05-27", [("半导体", 0.30), ("通信", 0.20), ("电池", 0.10), ("未分类", 0.40)]),  # CR3=60
        _rec_share("2026-05-28", [("半导体", 0.40), ("通信", 0.25), ("电池", 0.10)]),  # CR3=75
        _rec_share("2026-05-29", [("半导体", 0.35), ("通信", 0.20), ("电池", 0.10)]),  # CR3=65
    ]

    ct = trend.compute_trend(records)["cr3_trend"]

    assert ct["current"] == 65.0
    assert ct["previous"] == 75.0
    assert ct["delta_pp"] == -10.0   # 65 - 75
    assert ct["rank"] == 2           # 75 > 65 → 今天第 2 高
    assert ct["window"] == 3


def test_cr3_trend_rank_with_ties():
    """CR3 并列(审查高-3):rank=严格更高的天数+1,并列最高仍算第1(语义明确)。"""
    records = [
        _rec_share("2026-05-27", [("半导体", 0.40), ("通信", 0.20), ("电池", 0.15)]),  # CR3=75
        _rec_share("2026-05-28", [("半导体", 0.35), ("通信", 0.20), ("电池", 0.10)]),  # CR3=65
        _rec_share("2026-05-29", [("半导体", 0.40), ("通信", 0.20), ("电池", 0.15)]),  # CR3=75 并列最高
    ]
    ct = trend.compute_trend(records)["cr3_trend"]
    assert ct["current"] == 75.0
    assert ct["rank"] == 1   # 无人严格更高 → 第1高


def test_stock_rotation_new_preserves_amount_order():
    """新进个股保留成交额降序(审查中-1:dict 推导在 3.7+ 保插入序,锁定行为)。"""
    records = [
        _rec("2026-05-28", [("A", "甲")], ["电池"], 100.0),
        _rec("2026-05-29", [("A", "甲"), ("B", "乙"), ("C", "丙")], ["电池"], 120.0),  # B、C 新进(额降序)
    ]
    rot = trend.compute_trend(records)["stock_rotation"]
    assert [x["code"] for x in rot["new"]] == ["B", "C"]   # 保留 stocks 顺序
