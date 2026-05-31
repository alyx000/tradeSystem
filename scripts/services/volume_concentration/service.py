"""volume_concentration 编排:daily(采集→落库→趋势→渲染)/ trend(只读渲染)。"""
from __future__ import annotations

from . import collector, formatter, repo, trend

TREND_DAYS = 30


def run_daily(conn, registry, date: str, trend_days: int = TREND_DAYS,
              persist: bool = True, refetch: bool = False) -> str | None:
    """daily 模式:read-through 采集 + 申万打标 → (落库) → 读最近 N 日算趋势 → 渲染 Markdown。

    无 top20 数据(非交易日/源全挂)→ collector 返 None,本函数直接返 None,
    上层据此不写库不推送(dec-8 非交易日兜底)。
    persist=False(CLI --dry-run 预览):不落库;趋势用「库里历史 + 内存中今日 record」拼,
    保证预览含当日而不污染真实库。
    refetch=True(CLI --refetch 回填历史):强制重拉 top20,绕过 daily_market 陈旧缓存。
    """
    record = collector.build_record(conn, registry, date, refetch=refetch)
    if record is None:
        return None

    if persist:
        repo.save_concentration(conn, record)
        recent = repo.get_recent_concentration(conn, date, trend_days)
    else:
        recent = repo.get_recent_concentration(conn, date, trend_days)
        recent = [r for r in recent if r["date"] != date] + [record]  # 拼入内存今日

    trend_result = trend.compute_trend(recent)
    return formatter.format_daily_report(record, trend_result)


def run_trend(conn, date: str, days: int = TREND_DAYS) -> str:
    """trend 模式:只读最近 N 日已落库快照,渲染趋势(不采集、不落库、不推送)。"""
    recent = repo.get_recent_concentration(conn, date, days)
    if not recent:
        return "暂无集中度数据(需先跑 volume-watch daily 累积)。"
    record = recent[-1]  # 最新一日作报告主体
    trend_result = trend.compute_trend(recent)
    return formatter.format_daily_report(record, trend_result)
