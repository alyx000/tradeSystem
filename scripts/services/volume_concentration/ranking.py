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


def _rank_one_period(universe: list[dict], field: str) -> list[dict]:
    """单周期排名：按板块分组（剔未分类）→ 板块内 gain 降序 → 板块间向量字典序降序。"""
    by_ind: dict[str, list[dict]] = {}
    for s in universe:
        ind = s.get("industry") or UNCLASSIFIED
        if ind == UNCLASSIFIED:
            continue
        by_ind.setdefault(ind, []).append(s)

    sectors: list[dict] = []
    for ind, stocks in by_ind.items():
        valid = [
            {"name": s.get("name") or s.get("code"), "code": s.get("code"), "gain": s[field]}
            for s in stocks
            if s.get(field) is not None
        ]
        valid.sort(key=lambda x: x["gain"], reverse=True)
        vec = [x["gain"] for x in valid]
        sectors.append({
            "industry": ind,
            "max_gain": vec[0] if vec else None,
            "stocks": valid,
            "_vec": vec,
        })

    # 确定性：先按板块名升序（稳定排序保证向量全等时输出可复现），再按向量字典序降序。
    # 空向量（该周期全 None）的 tuple ()< 任何非空 tuple，reverse=True 后自然落末位。
    sectors.sort(key=lambda s: s["industry"])
    sectors.sort(key=lambda s: tuple(s["_vec"]), reverse=True)
    for s in sectors:
        del s["_vec"]
    return sectors


def build_sector_gain_ranking(universe: list[dict]) -> dict:
    """产出 {"5d": [...], "10d": [...], "20d": [...]}，每档为已排好序的板块列表。"""
    return {key: _rank_one_period(universe or [], field) for key, field in _PERIODS}
