"""共享 concept_tags util 测试：个股→同花顺概念反查 + 容器过滤。"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from providers.base import DataResult
from services import concept_tags


class _FakeRegistry:
    def __init__(self, resp):
        self.resp = resp

    def call(self, method, *a, **k):
        return self.resp


def test_clean_code():
    assert concept_tags._clean_code("600552.SH") == "600552.SH"
    assert concept_tags._clean_code(None) is None
    assert concept_tags._clean_code("nan") is None
    assert concept_tags._clean_code(600552) == "600552"  # 非字符串归一


def test_build_stock_concept_map_reverse():
    rows = [
        {"con_code": "300570.SZ", "index_name": "共封装光学(CPO)"},
        {"con_code": "300570.SZ", "index_name": "光纤概念"},
        {"con_code": "300570.SZ", "index_name": "共封装光学(CPO)"},  # 重复,去重不重复计数
        {"con_code": "688498.SH", "index_name": "共封装光学(CPO)"},
        "garbage",                                                   # 脏行跳过
        {"con_code": None, "index_name": "X"},                       # 缺码跳过
    ]
    m, mc, ok = concept_tags.build_stock_concept_map(
        _FakeRegistry(DataResult(data=rows, source="tushare:ths_member")), "2026-06-15")
    assert ok is True
    assert m["300570"] == {"共封装光学(CPO)", "光纤概念"}   # 裸码键
    assert m["688498"] == {"共封装光学(CPO)"}
    assert mc["共封装光学(CPO)"] == 2                       # 去重成员数
    assert mc["光纤概念"] == 1


def test_build_stock_concept_map_failure():
    m, mc, ok = concept_tags.build_stock_concept_map(
        _FakeRegistry(DataResult(data=None, source="t", error="boom")), "2026-06-15")
    assert (m, mc, ok) == ({}, {}, False)


def test_stock_real_concepts_filters_container():
    cmap = {"300570": {"共封装光学(CPO)", "融资融券", "光纤概念"}}
    mc = {"共封装光学(CPO)": 120, "光纤概念": 80, "融资融券": 3845}  # 融资融券=容器
    out = concept_tags.stock_real_concepts("300570", cmap, mc)
    # 容器(融资融券)剔除 + 按名 Unicode 升序(光 U+5149 < 共 U+5171)
    assert out == ["光纤概念", "共封装光学(CPO)"]
    # 阈值边界:正好 300 保留,301 剔
    assert concept_tags.stock_real_concepts(
        "X", {"X": {"A", "B"}}, {"A": 300, "B": 301}) == ["A"]


def test_stock_real_concepts_empty():
    assert concept_tags.stock_real_concepts("999", {}, {}) == []
