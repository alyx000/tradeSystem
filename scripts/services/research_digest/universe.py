"""美股精选股票池（universe）：config/env 维护，零 schema 改动（决策 M6/F7）。

来源优先级：显式传入 raw（测试/调用方）> env RESEARCH_DIGEST_US_TICKERS > 内置默认池。
逐只循环喂 get_us_rating_changes；永久冻结标的（如 META 仅到 2024）的剔除由 provider 侧
冻结日志 + 后续池子体检负责，本模块只管"配置了哪些"。
"""
from __future__ import annotations

import os

# 内置默认池：美股科技/半导体/AI 算力龙头 + 主流中概（覆盖度高、分析师活跃，时效新鲜概率大）。
_DEFAULT_US_TICKERS = [
    "NVDA", "AMD", "AVGO", "TSM", "MU", "ARM", "SMCI",
    "MSFT", "AAPL", "GOOGL", "AMZN", "META", "TSLA", "NFLX",
    "PLTR", "COIN", "MSTR",
    "LLY", "NVO",
    "BABA", "PDD", "JD", "BIDU",
]


def us_universe(raw=None) -> list[str]:
    """返回去重、大写、保序的美股精选 ticker 列表。

    raw: None → 读 env RESEARCH_DIGEST_US_TICKERS（逗号/中文逗号分隔）；空 → 内置默认池。
         也接受 list/tuple（测试注入）。
    """
    if raw is None:
        raw = os.getenv("RESEARCH_DIGEST_US_TICKERS", "")
    if isinstance(raw, str):
        parts = raw.replace("，", ",").split(",")
    else:
        parts = list(raw)
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        sym = str(p).strip().upper()
        if sym and sym not in seen:
            seen.add(sym)
            out.append(sym)
    return out or list(_DEFAULT_US_TICKERS)
