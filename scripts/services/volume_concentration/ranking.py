"""成交额前50 板块区间涨幅排名（纯函数派生层，formatter 与 API 共用）。

输入 universe：成交额前 50 个股，每股含 code/name/industry/gain_5d/gain_10d/gain_20d。
对 5/10/20 日各产出一份独立排名：

- 板块按「板块内涨幅最大个股」降序；最大相同则比次大个股（对板块内个股涨幅向量做降序后
  按字典序比较，reverse=True）—— 有可比次大股的板块胜过更短向量的板块；
- 「未分类」不进排名（与板块集中度对未分类的处理一致）；
- 个股 gain=None 剔出该板块该周期向量；某周期全 None → 板块向量为空、排末位；
- 板块内个股按该周期 gain 降序。

全为客观区间涨幅（真实收盘价算得），不含价位目标/不给买卖建议。
"""
from __future__ import annotations

from .aggregator import UNCLASSIFIED

# (输出周期键, universe 中的字段名)
_PERIODS: list[tuple[str, str]] = [
    ("5d", "gain_5d"),
    ("10d", "gain_10d"),
    ("20d", "gain_20d"),
]


def _rank_groups(groups: dict, field: str) -> list[dict]:
    """给定 {组名: [stocks]} → 组内 gain 降序 + 组间向量字典序降序。组名放 `industry` 字段
    (申万板块榜与同花顺题材榜共用同一展示契约/前端表格,题材榜的 `industry` 即概念名)。"""
    out: list[dict] = []
    for name, stocks in groups.items():
        valid = [
            {"name": s.get("name") or s.get("code"), "code": s.get("code"), "gain": s[field]}
            for s in stocks
            if s.get(field) is not None
        ]
        valid.sort(key=lambda x: x["gain"], reverse=True)
        vec = [x["gain"] for x in valid]
        out.append({"industry": name, "max_gain": vec[0] if vec else None, "stocks": valid, "_vec": vec})

    # 确定性：先按组名升序（稳定排序保证向量全等时可复现），再按向量字典序降序。
    # 空向量（该周期全 None）的 tuple ()< 任何非空 tuple，reverse=True 后自然落末位。
    out.sort(key=lambda s: s["industry"])
    out.sort(key=lambda s: tuple(s["_vec"]), reverse=True)
    for s in out:
        del s["_vec"]
    return out


def _industry_groups(universe: list[dict]) -> dict:
    """按申万二级单标签分组（剔未分类）。"""
    by_ind: dict[str, list[dict]] = {}
    for s in universe:
        ind = s.get("industry") or UNCLASSIFIED
        if ind == UNCLASSIFIED:
            continue
        by_ind.setdefault(ind, []).append(s)
    return by_ind


def _concept_groups(universe: list[dict], min_members: int) -> dict:
    """按同花顺概念**多标签**分组（一只票进它的每个概念）；只保留 universe 内成员数 ≥ min_members
    的概念（单票不成"题材"）。concepts 已在采集时做过容器过滤(≤300)。"""
    by_c: dict[str, list[dict]] = {}
    for s in universe:
        for c in (s.get("concepts") or []):
            by_c.setdefault(c, []).append(s)
    return {c: stocks for c, stocks in by_c.items() if len(stocks) >= min_members}


def build_sector_gain_ranking(universe: list[dict]) -> dict:
    """申万二级板块榜：{"5d": [...], "10d": [...], "20d": [...]}，每档为已排好序的板块列表。"""
    groups = _industry_groups(universe or [])
    return {key: _rank_groups(groups, field) for key, field in _PERIODS}


def build_concept_gain_ranking(universe: list[dict], min_members: int = 2) -> dict:
    """同花顺题材榜（多标签）：结构同 build_sector_gain_ranking，组=概念（≥min_members 头部票）。"""
    groups = _concept_groups(universe or [], min_members)
    return {key: _rank_groups(groups, field) for key, field in _PERIODS}
