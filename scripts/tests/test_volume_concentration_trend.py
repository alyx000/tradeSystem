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


# ---- v2 趋势分析:板块热度 / CR3 序列+连升降 / 头部均值 / 新陈代谢 ----

def test_sector_heat_rising_and_falling():
    """板块热度:各行业占比 最新日 vs lookback 日前,按 delta 降序(升温在前),排除未分类。"""
    records = [
        _rec_share("2026-05-22", [("半导体", 0.38), ("通信", 0.22), ("未分类", 0.40)]),  # base
        _rec_share("2026-05-29", [("半导体", 0.30), ("通信", 0.30), ("未分类", 0.40)]),
    ]
    heat = trend.compute_trend(records, heat_lookback=5)["sector_heat"]
    by = {h["industry"]: h for h in heat}
    assert by["通信"]["delta_pp"] == 8.0       # 30 - 22 升温
    assert by["半导体"]["delta_pp"] == -8.0     # 降温
    assert heat[0]["industry"] == "通信"        # delta 降序:升温在前
    assert "未分类" not in by                    # 排除


def test_cr3_trend_series_and_streak():
    """CR3 趋势序列 + 连升/连降天数。"""
    records = [
        _rec_share("2026-05-27", [("半导体", 0.40), ("通信", 0.25), ("电池", 0.12)]),  # CR3=77
        _rec_share("2026-05-28", [("半导体", 0.38), ("通信", 0.25), ("电池", 0.12)]),  # CR3=75
        _rec_share("2026-05-29", [("半导体", 0.37), ("通信", 0.25), ("电池", 0.12)]),  # CR3=74
    ]
    ct = trend.compute_trend(records)["cr3_trend"]
    assert ct["series"] == [77.0, 75.0, 74.0]
    assert ct["streak_dir"] == "down"
    assert ct["streak_days"] == 2   # 连降 2 个变动


def test_amount_trend_avg_and_vs_avg():
    """头部量级:近 N 日均值 + 今日 vs 均值。"""
    records = [
        _rec("2026-05-27", [], ["电池"], 100.0),
        _rec("2026-05-28", [], ["电池"], 120.0),
        _rec("2026-05-29", [], ["电池"], 140.0),
    ]
    at = trend.compute_trend(records, heat_lookback=5)["amount_trend"]
    assert at["avg"] == 120.0                       # (100+120+140)/3
    assert at["vs_avg_pct"] == 16.7                 # (140-120)/120*100


def test_metabolism_core_fresh_and_new_flow():
    """新陈代谢:核心(streak≥阈) / 新晋(streak==1) 计数 + 今日新进资金流向行业。"""
    r1 = {"date": "2026-05-28", "total_amount_billion": 100.0, "sector_summary": [],
          "stocks": [{"code": "A", "name": "甲", "industry": "半导体"},
                     {"code": "B", "name": "乙", "industry": "电池"}]}
    r2 = {"date": "2026-05-29", "total_amount_billion": 110.0, "sector_summary": [],
          "stocks": [{"code": "A", "name": "甲", "industry": "半导体"},   # streak 2
                     {"code": "C", "name": "丙", "industry": "通信"}]}     # 新进 streak 1
    m = trend.compute_trend([r1, r2], core_threshold=2)["metabolism"]
    assert m["core"] == 1                       # A streak 2 ≥ 2
    assert m["fresh"] == 1                       # C streak 1
    assert m["new_by_sector"] == [("通信", 1)]   # C 新进属通信


def test_streak_reversal_resets_count():
    """CR3 反转(审查中-6):[77,75,76] → 最新为升,连升 1 日(不计入之前的降)。"""
    records = [
        _rec_share("2026-05-27", [("半导体", 0.40), ("通信", 0.25), ("电池", 0.12)]),  # 77
        _rec_share("2026-05-28", [("半导体", 0.38), ("通信", 0.25), ("电池", 0.12)]),  # 75
        _rec_share("2026-05-29", [("半导体", 0.39), ("通信", 0.25), ("电池", 0.12)]),  # 76
    ]
    ct = trend.compute_trend(records)["cr3_trend"]
    assert ct["streak_dir"] == "up" and ct["streak_days"] == 1


def test_metabolism_empty_when_no_new():
    """无今日新进 → new_by_sector 空(审查低-12 补缺口)。"""
    r1 = {"date": "2026-05-28", "total_amount_billion": 100.0, "sector_summary": [],
          "stocks": [{"code": "A", "name": "甲", "industry": "电池"}]}
    r2 = {"date": "2026-05-29", "total_amount_billion": 110.0, "sector_summary": [],
          "stocks": [{"code": "A", "name": "甲", "industry": "电池"}]}   # 无新进
    m = trend.compute_trend([r1, r2], core_threshold=2)["metabolism"]
    assert m["new_by_sector"] == []


def test_amount_trend_single_day():
    """仅 1 日:avg=当日值、vs_avg=0、无环比(审查低-12 补缺口)。"""
    at = trend.compute_trend([_rec("2026-05-29", [], ["电池"], 140.0)])["amount_trend"]
    assert at["avg"] == 140.0 and at["vs_avg_pct"] == 0.0
    assert at["previous"] is None and at["change_pct"] is None


def test_compute_trend_clamps_nonpositive_lookback():
    """heat_lookback ≤0 钳到 1,不触发 IndexError(codex 轻微:防御参数误用)。"""
    records = [_rec("2026-05-28", [], ["电池"], 100.0), _rec("2026-05-29", [], ["电池"], 120.0)]
    out = trend.compute_trend(records, heat_lookback=-1)   # 负数原会 IndexError
    assert out["sufficient"] is True
    assert out["amount_trend"]["avg"] is not None
