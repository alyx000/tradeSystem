"""研报速读编排：collect_cn / collect_us → 受控 narrate → Top3 → 渲染 MD。

- 取 registry（CLI 负责 setup_providers + initialize_all 后注入，codex M-2）。
- A股采集失败不致命，继续美股（M1）；两市全空 → 渲染显式空报告（不崩、不推空壳由 CLI 决定）。
- narration 受控：默认 A股关、美股开（决策）；no_llm / llm_runner 缺失 → 全关走纯结构化。
- 不写 SQLite（研报速读不入库，仅文件 + 推送）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from . import collector, narrator, ranker, renderer, universe

logger = logging.getLogger(__name__)


@dataclass
class RenderedDigest:
    title: str
    markdown: str
    cn: list[dict] = field(default_factory=list)
    us: list[dict] = field(default_factory=list)
    top3: list[dict] = field(default_factory=list)
    cn_industry: list[dict] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.cn and not self.us


def run_daily_digest(
    registry,
    date: str,
    *,
    no_llm: bool = False,
    us_tickers=None,
    us_lookback_days: int = 5,
    cn_narrate: bool = False,   # 决策：A股 narrator 默认关（纯结构化）
    us_narrate: bool = True,    # 美股 narrator 默认开
    llm_runner=None,
) -> RenderedDigest:
    """date = A股目标交易日（北京，CLI 已 resolve）；美股窗口按美东日历独立计算（H2）。"""
    # A股（失败不致命）
    cn = collector.collect_cn(registry, date)

    # 美股（独立美东窗口 + 精选池逐只）
    tickers = universe.us_universe(us_tickers)
    window = collector.us_date_window(lookback_days=us_lookback_days)
    us = collector.collect_us(registry, tickers, window)

    # 受控叙事
    if not no_llm and llm_runner is not None:
        if cn_narrate:
            cn = narrator.narrate(cn, llm_runner=llm_runner, market="cn")
        if us_narrate:
            us = narrator.narrate(us, llm_runner=llm_runner, market="us")

    top3 = ranker.pick_top3(cn, us)
    # 行业覆盖热度：复用 label_industry（此处对 sw map 的唯一额外网络调用，digest job 有凭据）+ aggregate；cn 空跳过
    cn_industry = collector.aggregate_by_industry(collector.label_industry(cn, registry)) if cn else []
    title, markdown = renderer.render_md(date, cn, us, top3, cn_industry=cn_industry or None)

    if not cn and not us:
        logger.info("[research-digest] %s 两市均无符合条件评级变动（A股空 + 美股空）", date)

    return RenderedDigest(title=title, markdown=markdown, cn=cn, us=us, top3=top3, cn_industry=cn_industry)
