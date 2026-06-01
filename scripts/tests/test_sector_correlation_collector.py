"""collector 单测（阶段3）：Tushare pro 用 _FakePro 注入隔离真网。

覆盖：pct_chg/pct_change 归一、多日均成交额/换手率排名、单标的失败跳过、
panel 列对齐、build_record 的 None 兜底（无指数 / 板块不足）与端到端组装。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from services.sector_correlation import collector

DATES = list(pd.bdate_range("2026-02-02", periods=70).strftime("%Y%m%d"))

# ts_code -> (name, 成交额万元, 对 base 载荷)
L2 = {
    "801081.SI": ("半导体", 3_000_000, 1.4),
    "801193.SI": ("证券", 2_800_000, 1.2),
    "801123.SI": ("白酒", 2_000_000, 0.7),
    "801161.SI": ("电力", 1_500_000, 0.2),
    "801053.SI": ("黄金", 900_000, -0.5),
    "801770.SI": ("通信", 600_000, 1.1),
    "801030.SI": ("化工", 500_000, 0.6),
    "801040.SI": ("钢铁", 300_000, 0.3),
}
CONCEPTS = {
    "885001.TI": ("算力租赁", 6.8, 1.5),
    "885002.TI": ("数据要素", 5.1, 1.0),
    "885003.TI": ("低空经济", 4.3, 0.8),
    "885004.TI": ("光伏", 2.0, 0.4),
    "885005.TI": ("白马", 1.5, 0.5),
}
INDICES = {"000001.SH": 1.0, "399006.SZ": 1.3, "000300.SH": 0.95, "000688.SH": 1.6}
CONCEPT_MAP = {c: nm for c, (nm, *_rest) in CONCEPTS.items()}


class _FakePro:
    """模拟 tushare pro：index_daily / sw_daily / ths_daily 的快照与区间两种调用。"""

    def __init__(self, fail_codes: set | None = None):
        self.fail_codes = fail_codes or set()
        rng = np.random.RandomState(42)
        n = len(DATES)
        self.base = rng.normal(0, 1.0, n)
        self._idx = {c: self.base * sc + rng.normal(0, 0.2, n) for c, sc in INDICES.items()}
        self._sec = {}
        for c, (nm, _amt, load) in L2.items():
            self._sec[c] = self.base * load + rng.normal(0, 0.4, n)
        for c, (nm, _tov, load) in CONCEPTS.items():
            self._sec[c] = self.base * load + rng.normal(0, 0.4, n)

    def index_daily(self, ts_code, start_date, end_date):
        arr = self._idx[ts_code]
        return pd.DataFrame({"trade_date": DATES, "pct_chg": arr, "amount": [1e6] * len(DATES)})

    def sw_daily(self, ts_code=None, trade_date=None, start_date=None, end_date=None):
        if trade_date is not None:  # 快照：全 L2 + 一个非 L2 干扰行
            i = DATES.index(trade_date)
            rows = [{"ts_code": c, "name": nm, "pct_change": self._sec[c][i], "amount": amt}
                    for c, (nm, amt, _l) in L2.items()]
            rows.append({"ts_code": "801010.SI", "name": "申万一级农业", "pct_change": 0.1, "amount": 9e9})
            return pd.DataFrame(rows)
        if ts_code in self.fail_codes:
            raise RuntimeError("模拟拉取失败")
        nm = L2[ts_code][0]
        return pd.DataFrame({"trade_date": DATES, "name": nm, "pct_change": self._sec[ts_code]})

    def ths_daily(self, ts_code=None, trade_date=None, start_date=None, end_date=None):
        if trade_date is not None:
            i = DATES.index(trade_date)
            rows = [{"ts_code": c, "pct_change": self._sec[c][i], "turnover_rate": tov, "vol": 1e8}
                    for c, (nm, tov, _l) in CONCEPTS.items()]
            return pd.DataFrame(rows)
        if ts_code in self.fail_codes:
            raise RuntimeError("模拟拉取失败")
        return pd.DataFrame({"trade_date": DATES, "pct_change": self._sec[ts_code]})


def test_returns_series_normalizes():
    df = pd.DataFrame({"trade_date": ["20260103", "20260102"], "pct_chg": [1.0, -2.0]})
    s = collector._returns_series(df, "pct_chg")
    assert list(s.index) == ["20260102", "20260103"]  # 已排序
    assert s.loc["20260102"] == -2.0
    assert collector._returns_series(pd.DataFrame(), "pct_chg") is None  # 空 → None


def test_rank_industries_by_avg_amount_filters_non_l2():
    pro = _FakePro()
    ranked = collector.rank_industries(pro, DATES[-10:], set(L2), top_n=3)
    assert [r["name"] for r in ranked] == ["半导体", "证券", "白酒"]  # 成交额前三
    assert all(r["ts_code"] in L2 for r in ranked)  # 非 L2 干扰行(申万一级)被过滤


def test_rank_concepts_by_turnover():
    pro = _FakePro()
    ranked = collector.rank_concepts(pro, DATES[-10:], CONCEPT_MAP, top_m=2)
    assert [r["name"] for r in ranked] == ["算力租赁", "数据要素"]  # 换手率前二


def test_fetch_sector_series_failure_skips():
    pro = _FakePro(fail_codes={"801053.SI"})
    items = [{"ts_code": "801081.SI", "name": "半导体"}, {"ts_code": "801053.SI", "name": "黄金"}]
    out, fails = collector.fetch_sector_series(pro, items, "20260202", "20260511", "sw")
    assert "半导体" in out and "黄金" not in out
    assert fails == ["黄金"]


def _build(pro=None, **over):
    kw = dict(
        date="2026-05-29", windows=[20, 60], top_industries=5, top_concepts=3,
        indices=list(INDICES), base_index="000001.SH", activity_days=10,
        l2_codes=set(L2), concept_map=CONCEPT_MAP, min_sample_by_window={20: 15, 60: 20},
    )
    kw.update(over)
    return collector.build_record(pro or _FakePro(), **kw)


def test_build_record_end_to_end():
    rec = _build()
    assert rec is not None
    assert rec["top_n"] == 8  # 5 行业 + 3 概念
    assert set(rec["windows"]) == {20, 60}
    assert "20" in rec["sector_index"] and "60" in rec["sector_index"]
    names = [s["name"] for s in rec["sectors"]]
    assert "半导体" in names and "算力租赁" in names
    # 半导体(载荷1.4) 对上证应强同向
    assert rec["sector_index"]["60"]["半导体"]["000001.SH"]["label"] in ("强同向", "弱同向")
    # 黄金(载荷-0.5) 对上证 beta 应为负
    assert rec["sector_index"]["60"]["黄金"]["000001.SH"]["beta"] < 0
    assert rec["sample_days"]["60"] == 60


def test_build_record_none_when_no_index():
    assert _build(indices=[]) is None


def test_build_record_none_when_too_few_sectors():
    assert _build(top_industries=2, top_concepts=2) is None  # 4 < MIN_SECTORS(5)


def test_build_record_disambiguates_colliding_concept_name():
    """概念名撞行业名 → 概念加(概念)后缀，两者都进矩阵不丢数据（review M1）。"""
    cmap = dict(CONCEPT_MAP)
    cmap["885002.TI"] = "半导体"  # 让一个概念与申万行业"半导体"撞名
    rec = _build(concept_map=cmap, top_industries=5, top_concepts=3)
    names = [s["name"] for s in rec["sectors"]]
    assert "半导体" in names and "半导体(概念)" in names  # 两者并存


def test_build_record_small_window_with_clamped_min_sample():
    """小窗 [10] + min_sample<=window：不被全剔，正常出矩阵（review M3 钳制后的可行性）。"""
    rec = _build(windows=[10], min_sample_by_window={10: 8})
    assert rec is not None
    assert rec["windows"] == [10]
    assert rec["sample_days"]["10"] == 10
