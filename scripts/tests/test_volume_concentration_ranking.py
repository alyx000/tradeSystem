"""成交额前50 板块区间涨幅排名纯函数测试。

build_sector_gain_ranking(universe) → {"5d":[...], "10d":[...], "20d":[...]}：
- 板块按「板块内涨幅最大个股」降序；平手比次大个股（字典序降序）；
- 剔「未分类」；个股 gain=None 剔出该板块该周期向量；某周期全 None → 板块排末位；
- 板块内个股按该周期 gain 降序。
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from services.volume_concentration.ranking import build_sector_gain_ranking


def _stock(code, name, industry, g5, g10, g20):
    return {"code": code, "name": name, "industry": industry,
            "gain_5d": g5, "gain_10d": g10, "gain_20d": g20}


def test_sectors_sorted_by_max_gain_descending():
    universe = [
        _stock("A.SZ", "甲", "电子", 5.0, 5.0, 5.0),
        _stock("B.SZ", "乙", "银行", 12.0, 12.0, 12.0),
        _stock("C.SZ", "丙", "医药", 8.0, 8.0, 8.0),
    ]
    out = build_sector_gain_ranking(universe)
    assert [s["industry"] for s in out["5d"]] == ["银行", "医药", "电子"]
    assert out["5d"][0]["max_gain"] == 12.0


def test_tiebreak_by_second_highest_stock():
    # 两板块最大涨幅都是 10.0，比次大：甲板块次大 6.0 > 乙板块次大 3.0 → 甲在前
    universe = [
        _stock("A1", "a1", "甲", 10.0, None, None),
        _stock("A2", "a2", "甲", 6.0, None, None),
        _stock("B1", "b1", "乙", 10.0, None, None),
        _stock("B2", "b2", "乙", 3.0, None, None),
    ]
    out = build_sector_gain_ranking(universe)
    assert [s["industry"] for s in out["5d"]] == ["甲", "乙"]


def test_sector_with_more_stocks_beats_shorter_on_tie():
    # 最大涨幅相同(10.0)，甲有次大股、乙只有一只 → 甲胜(有可比的次大)
    universe = [
        _stock("A1", "a1", "甲", 10.0, None, None),
        _stock("A2", "a2", "甲", 4.0, None, None),
        _stock("B1", "b1", "乙", 10.0, None, None),
    ]
    out = build_sector_gain_ranking(universe)
    assert [s["industry"] for s in out["5d"]] == ["甲", "乙"]


def test_stocks_within_sector_sorted_desc():
    universe = [
        _stock("A1", "低", "甲", 3.0, None, None),
        _stock("A2", "高", "甲", 9.0, None, None),
        _stock("A3", "中", "甲", 6.0, None, None),
    ]
    out = build_sector_gain_ranking(universe)
    stocks = out["5d"][0]["stocks"]
    assert [s["name"] for s in stocks] == ["高", "中", "低"]
    assert [s["gain"] for s in stocks] == [9.0, 6.0, 3.0]


def test_unclassified_excluded():
    universe = [
        _stock("A1", "a1", "未分类", 99.0, 99.0, 99.0),
        _stock("B1", "b1", "电子", 5.0, 5.0, 5.0),
    ]
    out = build_sector_gain_ranking(universe)
    assert [s["industry"] for s in out["5d"]] == ["电子"]


def test_none_gain_dropped_from_vector():
    # 甲只有一只有效 5日涨幅(其余 None)，乙两只有效；甲最大 20 > 乙最大 8 → 甲在前
    universe = [
        _stock("A1", "a1", "甲", 20.0, None, None),
        _stock("A2", "a2", "甲", None, None, None),
        _stock("B1", "b1", "乙", 8.0, None, None),
        _stock("B2", "b2", "乙", 7.0, None, None),
    ]
    out = build_sector_gain_ranking(universe)
    assert [s["industry"] for s in out["5d"]] == ["甲", "乙"]
    # 甲板块 5日向量只含有效那只
    assert [s["name"] for s in out["5d"][0]["stocks"]] == ["a1"]


def test_all_none_period_sector_sorted_last():
    universe = [
        _stock("A1", "a1", "甲", None, None, None),   # 5日全 None
        _stock("B1", "b1", "乙", 1.0, None, None),
    ]
    out = build_sector_gain_ranking(universe)
    inds = [s["industry"] for s in out["5d"]]
    assert inds[-1] == "甲"
    assert inds[0] == "乙"
    # 全 None 板块 max_gain=None、stocks 为空
    jia = next(s for s in out["5d"] if s["industry"] == "甲")
    assert jia["max_gain"] is None
    assert jia["stocks"] == []


def test_three_periods_independent():
    # 同一票不同周期排名不同：甲 5日强 20日弱，乙反之
    universe = [
        _stock("A1", "a1", "甲", 20.0, 5.0, 2.0),
        _stock("B1", "b1", "乙", 3.0, 5.0, 18.0),
    ]
    out = build_sector_gain_ranking(universe)
    assert [s["industry"] for s in out["5d"]] == ["甲", "乙"]
    assert [s["industry"] for s in out["20d"]] == ["乙", "甲"]


def test_empty_universe():
    out = build_sector_gain_ranking([])
    assert out == {"5d": [], "10d": [], "20d": []}
