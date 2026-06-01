"""_filter_us_rating_window 纯函数：窗口/方向/tz/陈旧 边界穷举（实施计划 H3）。

把"防陈旧"核心防线下沉到纯函数层穷举，对齐仓库 _overnight_from_hist 正例，
避免只在方法级 mock 测出"本就没放窗口内行→空也算过"的假绿。
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from providers.akshare_provider import _filter_us_rating_window, _most_recent_grade_date

WIN_S, WIN_E = date(2026, 5, 28), date(2026, 5, 29)


def _ud(rows):
    """rows: (date_str, firm, from, to, action, cur_pt, prior_pt) → upgrades_downgrades 式 DataFrame。"""
    idx = pd.to_datetime([r[0] for r in rows])
    return pd.DataFrame({
        "Firm": [r[1] for r in rows],
        "FromGrade": [r[2] for r in rows],
        "ToGrade": [r[3] for r in rows],
        "Action": [r[4] for r in rows],
        "currentPriceTarget": [r[5] for r in rows],
        "priorPriceTarget": [r[6] for r in rows],
    }, index=idx)


def test_keeps_window_inner_direction_change():
    df = _ud([("2026-05-29", "Morgan Stanley", "Equal-Weight", "Overweight", "up", 300.0, 250.0)])
    out = _filter_us_rating_window(df, WIN_S, WIN_E)
    assert len(out) == 1
    assert out[0]["firm"] == "Morgan Stanley"
    assert out[0]["action"] == "up"
    assert out[0]["grade_date"] == "2026-05-29"
    assert out[0]["current_pt"] == 300.0
    assert out[0]["prior_pt"] == 250.0


def test_drops_stale_2024_meta_like():
    """META 式：末行停在 2024 → 窗口过滤为空（设计内，非 bug）。"""
    df = _ud([
        ("2024-09-30", "Cantor Fitzgerald", "Overweight", "Overweight", "reit", None, None),
        ("2024-08-01", "X", "Buy", "Buy", "up", None, None),
    ])
    assert _filter_us_rating_window(df, WIN_S, WIN_E) == []


def test_boundary_inclusive_both_ends():
    """闭区间 [start, end]：下界与上界当日都保留。"""
    df = _ud([("2026-05-28", "A", "Hold", "Buy", "up", 1, 1),
              ("2026-05-29", "B", "Hold", "Buy", "up", 1, 1)])
    out = _filter_us_rating_window(df, WIN_S, WIN_E)
    assert {o["grade_date"] for o in out} == {"2026-05-28", "2026-05-29"}


def test_drops_future_date():
    df = _ud([("2026-06-05", "A", "Hold", "Buy", "up", 1, 1)])
    assert _filter_us_rating_window(df, WIN_S, WIN_E) == []


def test_filters_maintain_and_reiterate():
    """main=维持 / reit=重申 不算评级变动，过滤。"""
    df = _ud([("2026-05-29", "A", "Buy", "Buy", "main", 1, 1),
              ("2026-05-29", "B", "Buy", "Buy", "reit", 1, 1)])
    assert _filter_us_rating_window(df, WIN_S, WIN_E) == []


def test_keeps_init_and_reinit():
    """codex 中4：init/reinit（新覆盖）属真方向信号，必须保留。"""
    df = _ud([("2026-05-29", "A", "", "Buy", "init", 1, 1),
              ("2026-05-29", "B", "Buy", "Buy", "reinit", 1, 1)])
    out = _filter_us_rating_window(df, WIN_S, WIN_E)
    assert {o["action"] for o in out} == {"init", "reinit"}


def test_tz_aware_index_normalized_to_eastern():
    """GradeDate tz-aware 美东 → 按美东日历日比较（H2 防时区错位）。"""
    idx = pd.to_datetime(["2026-05-29 20:00"]).tz_localize("America/New_York")
    df = pd.DataFrame({
        "Firm": ["A"], "FromGrade": ["Hold"], "ToGrade": ["Buy"], "Action": ["up"],
        "currentPriceTarget": [1.0], "priorPriceTarget": [1.0],
    }, index=idx)
    out = _filter_us_rating_window(df, WIN_S, WIN_E)
    assert len(out) == 1 and out[0]["grade_date"] == "2026-05-29"


def test_tz_naive_index_is_standard_yfinance_case():
    """yfinance 实际常返 tz-naive 索引（本文件所有 _ud 用例即 tz-naive）→ 直接取 .date()。

    显式命名一条，文档化 naive 路径（与上面 tz-aware 用例互补；naive 早被全部 _ud 用例覆盖）。
    """
    df = _ud([("2026-05-29", "A", "Hold", "Buy", "up", 1, 1)])
    assert df.index.tz is None  # 坐实 _ud 产出 tz-naive
    out = _filter_us_rating_window(df, WIN_S, WIN_E)
    assert len(out) == 1 and out[0]["grade_date"] == "2026-05-29"


def test_direction_only_false_keeps_maintain():
    """direction_only=False 时 main/reit 也保留（纯函数 toggle 路径）。"""
    df = _ud([("2026-05-29", "A", "Buy", "Buy", "main", 1, 1)])
    out = _filter_us_rating_window(df, WIN_S, WIN_E, direction_only=False)
    assert len(out) == 1 and out[0]["action"] == "main"


def test_empty_and_none_df():
    assert _filter_us_rating_window(pd.DataFrame(), WIN_S, WIN_E) == []
    assert _filter_us_rating_window(None, WIN_S, WIN_E) == []


def test_reverse_assertion_fresh_not_false_dropped():
    """反向断言：窗口内昨夜真实评级不能被误删（防过滤过度）。"""
    df = _ud([("2026-05-29", "A", "Hold", "Buy", "up", 1, 1)])
    assert len(_filter_us_rating_window(df, WIN_S, WIN_E)) == 1


def test_most_recent_grade_date():
    df = _ud([("2024-09-30", "A", "B", "B", "reit", None, None),
              ("2026-05-29", "C", "D", "E", "up", 1, 1)])
    assert _most_recent_grade_date(df) == date(2026, 5, 29)
    assert _most_recent_grade_date(pd.DataFrame()) is None
    assert _most_recent_grade_date(None) is None
