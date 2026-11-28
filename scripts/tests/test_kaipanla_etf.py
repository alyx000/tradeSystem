"""Boundary cases for changes between verified ETF snapshots."""

import csv
from pathlib import Path

from automations.kaipanla_etf import analyze_etf


def write_csv(directory, name, rows):
    with (directory / name).open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def snapshot(path: Path, fact, flow_day, flows, motion, scope="宽基、行业"):
    path.mkdir()
    write_csv(path, "etf-market.csv", [
        dict(date=fact, bucket="涨幅高", name="科创芯片ETF甲", code="590001",
             price_raw="1.1", change_pct_raw="1.00%", turnover_raw="3亿", scope=scope),
    ])
    write_csv(path, "etf-flows.csv", [
        dict(date=fact, flow_date=flow_day, view="方向汇总", bucket="比较补录", name=name, code="",
             constituent_count=count, flow_amount_raw=amount, flow_shares_raw="", return_pct_raw="",
             size_raw="", scope=scope, disclosure_status="境内已到披露窗口，跨境按已显示值")
        for name, count, amount in flows
    ])
    write_csv(path, "etf-trends.csv", [
        dict(date=fact, updated_at=f"{fact} 21:00", bucket=bucket, name=name, constituent_count=count,
             duration_days="3", interval_change_pct_raw="-2.00%", interval_flow_raw=amount)
        for name, bucket, count, amount in motion
    ])
    write_csv(path, "etf-meta.csv", [
        dict(date=fact, view="动向", scope=scope, boundary="已核对两榜至尾，区间结束日未显示"),
    ])


def test_report_separates_retreat_from_verified_daily_turn(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    snapshot(old, "2026-11-26", "2026-11-25",
             [("芯片ETF", "4", "1亿"), ("电池ETF", "2", "-2亿"), ("恒生科技ETF", "4", "3亿")],
             [("芯片ETF", "连续净申购", "4", "4亿"), ("电池ETF", "连续净赎回", "2", "-3亿"),
              ("恒生科技ETF", "连续净申购", "4", "6亿")])
    snapshot(new, "2026-11-27", "2026-11-26",
             [("芯片ETF", "4", "-1亿"), ("电池ETF", "3", "3亿"), ("恒生科技ETF", "4", "-4亿")],
             [("医药ETF", "连续净申购", "1", "1亿"), ("电池ETF", "连续净赎回", "3", "-3亿")])

    analysis = analyze_etf(new, old)
    changes = {row["name"]: row for row in analysis["changes"]}
    assert changes["医药ETF"]["classification"] == "新进连续净申购榜"
    assert changes["芯片ETF"]["classification"] == "退出连续净申购榜"
    assert changes["芯片ETF"]["verified_daily_turn"] == "净申购转净赎回"
    assert changes["恒生科技ETF"]["verified_daily_turn"] == ""
    assert changes["电池ETF"]["scope_note"] == "只数变化或未核实"
    summary = analysis["summary"]
    assert "11/25→11/26申赎芯片1亿→-1亿" in summary
    assert "最近两期净申购转净赎回" in summary
    assert "连续净申购榜新进医药，退出芯片、恒生科技" in summary
    assert "区间结束日未显示" in analysis["details"]


def test_scope_change_and_missing_baseline_suppress_cross_day_judgments(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    snapshot(old, "2026-11-26", "2026-11-25", [("芯片ETF", "4", "1亿")],
             [("芯片ETF", "连续净申购", "4", "4亿")], "宽基")
    snapshot(new, "2026-11-27", "2026-11-26", [("芯片ETF", "4", "-1亿")],
             [("芯片ETF", "连续净赎回", "4", "-4亿")], "行业")

    analysis = analyze_etf(new, old)
    assert analysis["changes"][0]["classification"] == "当日榜单，缺可比基线"
    assert analysis["changes"][0]["verified_daily_turn"] == ""
    assert analysis["flow_comparisons"][0]["status"] == "筛选范围变化"
    assert analyze_etf(new, None)["flow_comparisons"] == []
