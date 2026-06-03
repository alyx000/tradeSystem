"""get_stock_sw_industry_map 单测:个股→申万二级映射(index_member_all 分页)。

核心回归:index_member_all 默认单页 2000 行静默截断,必须 offset/limit 分页拉满,
否则映射缺一半(参见 memory reference_tushare_index_member_all_pagination)。
"""
from __future__ import annotations

import pandas as pd

from providers.tushare_provider import TushareProvider


class _MemberAllPro:
    """index_member_all 桩:按 offset/limit 分页吐 total 行,记录每次 offset。

    真实 l2_name 形如「电池」「白酒Ⅱ」(与 sw_daily 行业名逐字对齐),此处用「行业N」
    仅验证分页/映射机制,不模拟真实命名。
    """

    def __init__(self, total: int):
        self.total = total
        self.offsets: list[int] = []

    def index_member_all(self, is_new=None, fields=None, offset=0, limit=2000):
        self.offsets.append(offset)
        start, end = offset, min(offset + limit, self.total)
        if start >= self.total:
            return pd.DataFrame()
        rows = [
            {
                "ts_code": f"{i:06d}.SZ",
                "name": f"股{i}",
                "l1_name": "L1",
                "l2_name": f"行业{i % 131}",
                "l2_code": f"8010{i % 131}.SI",
                "is_new": "Y",
            }
            for i in range(start, end)
        ]
        return pd.DataFrame(rows)


def _make_provider(pro) -> TushareProvider:
    p = TushareProvider.__new__(TushareProvider)
    p.name = "tushare"
    p.config = {}
    p.pro = pro
    p._initialized = True
    p._sw_l2_codes = None
    p._ths_concept_map = None
    p._sw_member_map = None
    return p


def test_get_stock_sw_industry_map_paginates_beyond_2000():
    """4001 只 → 必须分 3 页(offset 0/2000/4000)拉满,不被 2000 截断。"""
    provider = _make_provider(_MemberAllPro(4001))

    result = provider.get_stock_sw_industry_map()

    assert result.success
    assert len(result.data) == 4001
    assert provider.pro.offsets == [0, 2000, 4000]


def test_maps_ts_code_to_name_and_sw_l2():
    """映射语义:l1_name → sw_l1、l2_name → sw_l2,name 一并带回。"""
    provider = _make_provider(_MemberAllPro(3))

    result = provider.get_stock_sw_industry_map()

    assert result.success
    assert result.source == "tushare:index_member_all"
    assert result.data["000001.SZ"] == {"name": "股1", "sw_l1": "L1", "sw_l2": "行业1"}


def test_caches_map_and_does_not_requery():
    """惰性缓存:第二次调用命中缓存,不再打 index_member_all。"""
    provider = _make_provider(_MemberAllPro(3))

    provider.get_stock_sw_industry_map()
    provider.get_stock_sw_industry_map()

    assert provider.pro.offsets == [0]  # 仅第一次拉取


def test_uninitialized_returns_clear_error():
    """未初始化时返回明确错误,不抛异常。"""
    provider = _make_provider(_MemberAllPro(3))
    provider.pro = None
    provider._initialized = False

    result = provider.get_stock_sw_industry_map()

    assert not result.success
    assert result.error == "provider_not_initialized: get_stock_sw_industry_map"


def test_capability_is_declared():
    """capability 必须声明,否则 registry 静默跳过(memory project_registry_capability_must_declare)。"""
    provider = _make_provider(_MemberAllPro(0))
    assert "get_stock_sw_industry_map" in provider.get_capabilities()


class _FailingPro:
    """index_member_all 抛异常的桩。"""

    def index_member_all(self, **kwargs):
        raise RuntimeError("boom")


def test_api_failure_surfaces_as_error_not_empty_success():
    """接口异常 → success=False(可区分'源全挂'与'全部未分类'),不静默伪装成空 map。"""
    provider = _make_provider(_FailingPro())

    result = provider.get_stock_sw_industry_map()

    assert not result.success
    assert "index_member_all" in (result.error or "")


def test_exact_multiple_of_2000_terminates_with_one_empty_probe():
    """total 恰为 2000 整数倍:多一次空 offset 探测后正常终止,不漏页不死循环。"""
    provider = _make_provider(_MemberAllPro(4000))

    result = provider.get_stock_sw_industry_map()

    assert result.success
    assert len(result.data) == 4000
    assert provider.pro.offsets == [0, 2000, 4000]  # offset=4000 得空 → break


def test_empty_member_list_returns_success_empty_map():
    """0 条成分 → success + 空 map(异常才报错;空由上层 coverage 标注)。"""
    provider = _make_provider(_MemberAllPro(0))

    result = provider.get_stock_sw_industry_map()

    assert result.success
    assert result.data == {}
