from copy import deepcopy

import pytest

from services.intraday_summary.archive import parse_report, read_previous_report
from services.intraday_summary.formatter import render_report


def _payload(slot="15:00"):
    previous_slot = "14:30" if slot == "15:00" else "14:00"
    return dict(
        trade_date="2026-11-16", slot=slot, previous_slot=previous_slot,
        status="complete", scanned=100, quote_time=f"{slot}:09",
        current_breadth=dict(up=20, down=70, flat=10, strong_5pct=5, weak_5pct=30),
        interval=dict(status="complete", up=20, down=70, flat=10, up_ratio_pct=20.0,
                      median_pct=-0.25, up_1pct=3, down_1pct=20, amount_yi=123.0, amount_coverage_pct=100.0,
                      indices=[dict(code=code, interval_pct=0.01, daily_pct=-1.0) for code in ("000001.SH", "399001.SZ", "399006.SZ", "000688.SH")],
                      sectors={key: [dict(name=f"{key}{i}", median_pct=sign * 0.2, up_ratio_pct=50.0, amount_yi=10.0) for i in range(5)] for key, sign in (("strongest", 1), ("weakest", -1))},
                      rising=[dict(name=f"涨{i}", code=f"00000{i}.SZ", interval_pct=2.0, daily_pct=3.0) for i in range(5)],
                      falling=[dict(name=f"跌{i}", code=f"60000{i}.SH", interval_pct=-2.0, daily_pct=-3.0) for i in range(5)]),
    )


def test_conclusion_precedes_metadata_and_comparison_uses_correct_units():
    current, previous = _payload(), _payload("14:30")
    previous["interval"].update(up=40, down=50, up_ratio_pct=40.0, median_pct=0.0, down_1pct=10)
    previous["current_breadth"]["weak_5pct"] = 20
    text = render_report(current, previous)
    assert text.index("多数个股走弱，上证基本持平") < text.index("数据状态")
    assert "| 区间上涨占比 | 40.0% | 20.0% | -20.0个百分点 |" in text
    assert "| 当日跌幅≥5% | 20只 | 30只 | +10只 |" in text
    assert "14:00—14:30 对照 14:30—15:00" in text
    assert "未核验逐股固定样本" in text
    assert "最终收盘数据尚未" in text


def test_ranking_continuity_is_direction_specific_and_does_not_infer_reversal():
    current, previous = _payload(), _payload("14:30")
    previous["interval"]["sectors"]["strongest"][0]["name"] = "此前强榜"
    # A name from the opposite-direction list is still new to this direction.
    previous["interval"]["sectors"]["weakest"][0]["name"] = "strongest0"
    text = render_report(current, previous)
    assert "strongest0：中位 +0.20%，上涨占比 50.0%，区间成交额 10.0亿｜本期新上榜" in text
    assert "strongest1：中位 +0.20%，上涨占比 50.0%，区间成交额 10.0亿｜连续两期上榜" in text
    assert "退出本榜：此前强榜。退出不代表转强或转弱。" in text
    assert "不代表连续上涨/下跌" in text


@pytest.mark.parametrize("case", ["missing", "partial_current", "partial_previous", "wrong_date", "wrong_slot", "lunch"])
def test_unavailable_comparison_never_makes_new_or_exit_claims(case):
    current, previous = _payload(), _payload("14:30")
    if case == "missing":
        previous = None
    elif case == "partial_current":
        current["status"] = "partial"
    elif case == "partial_previous":
        previous["status"] = "partial"
    elif case == "wrong_date":
        previous["trade_date"] = "2026-11-13"
    elif case == "wrong_slot":
        previous["slot"] = "14:00"
    elif case == "lunch":
        current.update(slot="13:30", previous_slot="13:00")
        previous["slot"] = "11:30"
    text = render_report(current, previous)
    assert "板块持续性未计算" in text
    assert "｜连续两期上榜" not in text
    assert "｜本期新上榜" not in text
    assert "退出本榜：" not in text
    assert "| 区间上涨占比 |" not in text


def test_archive_roundtrip_ignores_comparison_text_and_tags():
    current, previous = _payload(), _payload("14:30")
    text = render_report(current, previous)
    parsed = parse_report(text, day=current["trade_date"], slot=current["slot"])
    assert parsed == current
    legacy = text[text.index("###"):].replace("｜连续两期上榜", "")
    assert parse_report(legacy, day=current["trade_date"], slot=current["slot"]) == current


@pytest.mark.parametrize("edit", [
    lambda text: text.replace("2026-11-16", "2026-11-13"),
    lambda text: text.replace("区间：14:30 →", "区间：14:00 →"),
    lambda text: text.replace("上涨 20 / 下跌 70 / 平盘 10，上涨占比 20.0%", "上涨 20 / 下跌 70 / 平盘 10，上涨占比 90.0%"),
    lambda text: text.replace("扫描 100 只", "扫描 50 只"),
    lambda text: text + "\n- [事实] 个股半小时涨幅中位数 -0.25%，≥+1% 3 只，≤-1% 20 只\n",
    lambda text: text.replace("- strongest0：", "- strongest1："),
])
def test_corrupt_archives_fail_closed(tmp_path, edit):
    folder = tmp_path / "2026-11-16"
    folder.mkdir()
    (folder / "1500.md").write_text(edit(render_report(_payload())), encoding="utf-8")
    assert read_previous_report(tmp_path, day="2026-11-16", slot="15:00") is None


def test_missing_baseline_does_not_make_market_conclusion():
    current = _payload()
    current.update(status="partial", interval=None, gap="缺少14:30基线")
    text = render_report(current)
    assert "半小时变化未计算，当前快照仅供查看" in text
    assert "多数个股走弱" not in text


def test_negative_top_rank_is_not_called_positive_strength():
    current = deepcopy(_payload())
    for item in current["interval"]["sectors"]["strongest"]:
        item["median_pct"] = -0.1
    text = render_report(current)
    assert "strongest0区间涨幅居前" not in text


def test_missing_sector_amount_does_not_discard_valid_comparison():
    previous = _payload("14:30")
    previous["interval"]["sectors"]["strongest"][0]["amount_yi"] = None
    parsed = parse_report(render_report(previous), day="2026-11-16", slot="14:30")
    assert parsed["interval"]["sectors"]["strongest"][0]["amount_yi"] is None
    text = render_report(_payload(), parsed)
    assert "| 区间上涨占比 |" in text
    assert "｜连续两期上榜" in text


@pytest.mark.parametrize("median,ratio,index", [
    (0.149, 60.0, 0.1), (-0.149, 40.0, -0.1),
    (0.25, 59.96, 0.1), (-0.25, 40.04, -0.1),
    (-0.25, 20.0, -0.054), (0.25, 80.0, 0.054),
    (-0.25, 20.0, 0.054), (0.25, 80.0, -0.054),
])
def test_archived_threshold_rounding_does_not_change_original_judgment(median, ratio, index):
    current = _payload()
    current["scanned"] = 10000
    up = round(ratio * 100)
    current["interval"].update(up=up, down=10000-up, flat=0, median_pct=median, up_ratio_pct=ratio)
    current["interval"]["indices"][0]["interval_pct"] = index
    original = render_report(current)
    parsed = parse_report(original, day="2026-11-16", slot="15:00")
    preview = render_report(parsed)
    headline = next(line for line in original.splitlines() if line.startswith("**[判断"))
    assert headline in preview


def test_ambiguous_archived_threshold_without_judgment_does_not_guess():
    current = _payload()
    current["interval"]["median_pct"] = -0.15
    text = "\n".join(line for line in render_report(current).splitlines() if not line.startswith("**[判断"))
    parsed = parse_report(text, day="2026-11-16", slot="15:00")
    assert "本次不重判方向" in render_report(parsed)


def _preview_visible_text(text):
    from html.parser import HTMLParser
    import markdown

    class Visible(HTMLParser):
        def __init__(self):
            super().__init__()
            self.depth = 0
            self.text = []
            self.folds = 0
            self.open_folds = 0

        def handle_starttag(self, tag, attrs):
            if tag == "details":
                self.depth += 1
                self.folds += 1
                self.open_folds += int("open" in dict(attrs))

        def handle_endtag(self, tag):
            if tag == "details":
                self.depth -= 1

        def handle_data(self, data):
            if not self.depth:
                self.text.append(data)

    parser = Visible()
    parser.feed(markdown.markdown(text, extensions=["tables", "md_in_html"]))
    assert parser.depth == 0
    assert parser.open_folds == 0
    return "".join(parser.text), parser.folds


def test_compact_preview_preserves_facts_and_hides_repetition():
    from services.intraday_summary.preview import render_compact_preview
    current, previous = _payload(), _payload("14:30")
    text = render_compact_preview(current, previous, "- 请求者：USER")
    visible, folds = _preview_visible_text(text)
    assert folds == 5
    assert "多数个股走弱，上证基本持平" in visible
    assert "区间涨幅居前" not in visible
    assert visible.count("区间涨幅中位数") == 1
    assert "连续两期位列涨跌幅排名前五" in visible
    assert "涨0" not in visible
    assert "本期市场事实" not in visible
    assert "退出涨跌幅排名前五：" not in visible
    assert "- 请求者：USER" in text
    assert parse_report(text, day="2026-11-16", slot="15:00") == current


@pytest.mark.parametrize("case", ["partial", "source_gaps", "index_error", "amount_error", "error"])
def test_compact_preview_keeps_gaps_visible(case):
    from services.intraday_summary.preview import render_compact_preview
    current = _payload()
    if case == "partial":
        current.update(status="partial", source_gaps=["核验缺口"])
    elif case == "source_gaps":
        current["source_gaps"] = ["核验缺口"]
    else:
        current["interval"][case] = "核验缺口"
        if case == "error":
            current["interval"]["sectors"] = {}
    visible, folds = _preview_visible_text(render_compact_preview(current, None, "来源"))
    assert "核验缺口" in visible
    assert folds == 1


def test_compact_preview_missing_previous_never_claims_new_or_continuous():
    from services.intraday_summary.preview import render_compact_preview
    visible, _ = _preview_visible_text(render_compact_preview(_payload(), None, "来源"))
    assert "上一期完整对照缺失" in visible
    assert "持续性：未计算" in visible
    assert "连续两期在" not in visible
    assert "本期新入" not in visible


def test_default_channel_is_compact_without_html_or_tables():
    from services.intraday_summary.preview import render_compact_preview, render_channel_summary
    current = _payload()
    report = render_compact_preview(current, _payload("14:30"), "- 私有来源路径")
    text = render_channel_summary(report, interval=current["interval"])
    assert "- 区间上涨占比：本期 20.0%｜较上一期 +0.0个百分点" in text
    assert "- strongest0：区间中位 +0.20%｜上涨占比 50.0%｜榜单变化 连续两期" in text
    assert text.count("#### 个股波动榜") == 1
    up, down = text.split("#### 个股波动榜", 1)[1].split("**下行**", 1)
    for i in range(5):
        assert f"涨{i}（00000{i}.SZ）：半小时 +2.00%，当日 +3.00%" in up
        assert f"跌{i}（60000{i}.SH）：半小时 -2.00%，当日 -3.00%" in down
    for omitted in ("<details", "<summary", "| 指标 |", "私有来源路径", "本期退出榜单"):
        assert omitted not in text


def test_partial_channel_keeps_gap_visible():
    from services.intraday_summary.preview import render_compact_preview, render_channel_summary
    current = _payload()
    current.update(status="partial", interval=None, gap="缺14:30基线")
    text = render_channel_summary(render_compact_preview(current, None, "来源"), interval=current["interval"])
    assert "缺14:30基线" in text
    assert "半小时变化未计算" in text
    assert "半小时波动未计算" in text


def test_partial_channel_keeps_available_stock_ranking_once():
    from services.intraday_summary.preview import render_compact_preview, render_channel_summary
    current = _payload()
    current.update(status="partial", source_gaps=["指数覆盖缺口"])
    current["interval"]["status"] = "partial"
    text = render_channel_summary(render_compact_preview(current, None, "来源"), interval=current["interval"])
    assert "指数覆盖缺口" in text
    assert text.count("涨0（000000.SZ）") == 1
    assert text.count("跌4（600004.SH）") == 1


def test_all_rising_sectors_still_use_relative_rank_labels_for_local_and_push():
    from services.intraday_summary.preview import render_channel_summary, render_compact_preview

    current = _payload()
    for row in current["interval"]["sectors"]["weakest"]:
        row["median_pct"] = 0.82
    text = render_compact_preview(current, _payload("14:30"), "来源")
    for view in (text, render_channel_summary(text, interval=current["interval"])):
        assert "涨跌幅排名后五" in view and "+0.82%" in view and "走弱前五" not in view
    detail = render_report(current)
    for version in (detail, detail.replace("涨跌幅排名前五（按成分股半小时涨跌幅中位数）",
                                          "走强居前（按成分股半小时涨幅中位数）")
                                  .replace("涨跌幅排名后五（同口径）", "走弱居前（同口径）")):
        parsed = parse_report(version, day=current["trade_date"], slot=current["slot"])
        assert parsed["interval"]["sectors"]["weakest"][0]["median_pct"] == 0.82
