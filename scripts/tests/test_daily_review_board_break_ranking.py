"""断板反包双榜独立于次日反馈：来源解析、缺口语义与正式 HTML 硬门。"""
from __future__ import annotations

import re
import subprocess
import sys

import pytest

from tests.test_daily_review_html_report import ASSEMBLER, DATE, _load_assembler, _write_chunks


SOURCE = f"""# 断板反包观察清单 · {DATE}  [判断]
> 上一交易日（T-1）：2026-07-15
## 候选清单（按加权分降序）
| 代码 | 名称 | 连板 | 断板日涨幅 | 收盘 | 6%参考位 | 行业 | 加权分及排名[判断] | PK胜场及排名[判断] | 分歧⚠ |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 002084 | 海鸥住工 | 7 | 1.42% | 7.12 | 7.55 | 家居用品 | 3.0（第1名） | 0胜（第2名） |  |
| 600371 | 万向德农 | 6 | 3.30% | 14.42 | 15.29 | 种植业 | 3.0（第2名） | 1胜（第1名） |  |
## 打分依据明细
## PK 理由
## 脚注二：数据完整性
- 上一交易日（prev_trade_date）：2026-07-15
  - prev_limit_up：成功（源=tushare:limit_list_d）
  - today_limit_up：成功（源=tushare:limit_list_d）
  - today_limit_down：成功（源=tushare:limit_list_d）
- 维度缺失/源失败：无
- PK：状态=ok，无效场=0，已打场次=1/1
"""


@pytest.fixture(scope="module")
def assembler():
    return _load_assembler()


def _render(assembler, tmp_path, source=SOURCE):
    _write_chunks(tmp_path)
    return assembler.render_report(tmp_path, DATE, board_break_report=source)


def test_dual_ranks_are_preserved_and_independent(assembler, tmp_path):
    payload = assembler._parse_board_break_ranking(SOURCE, DATE)
    assert payload["state"] == "v1"
    assert payload["status"] == "ok"
    assert payload["pk_status"] == "ok"
    assert [r["code"] for r in payload["rows"]] == ["002084", "600371"]
    html = _render(assembler, tmp_path)
    assembler.validate_report(html, board_break_report=SOURCE)
    assert html.count('data-board-break-ranking="v1"') == 1
    assert html.count('data-board-break-feedback="v1"') == 1
    assert 'data-candidate-count="2"' in html
    assert "0胜（第2名）" in html and "1胜（第1名）" in html
    assert "6%参考位" not in html  # 不把机械价位/交易动作搬进复盘榜单。


@pytest.mark.parametrize("replacement", [
    (DATE, "2026-07-17"),
    ("600371 | 万向德农", "002084 | 万向德农"),
    ("3.0（第1名）", "NaN（第1名）"),
    ("3.0（第2名）", "3.0（第3名）"),
    ("1胜（第1名）", "9胜（第1名）"),
    ("today_limit_up：成功", "today_limit_up：失败"),
    ("已打场次=1/1", "已打场次=0/1"),
    ("0胜（第2名）", "—"),
    ("1.42%", "nan%"),
    ("| 002084 |", "| 002084.SZ |"),
])
def test_corrupt_source_is_not_empty_or_complete(assembler, replacement):
    source = SOURCE.replace(*replacement)
    payload = assembler._parse_board_break_ranking(source, DATE)
    assert payload["state"] == "missing-data"
    assert payload["status"] == "source_failed"


@pytest.mark.parametrize("source", [None, "", "# 断板反包 数据失败 · " + DATE])
def test_missing_source_is_visible_in_ops(assembler, tmp_path, source):
    html = _render(assembler, tmp_path, source)
    assembler.validate_report(html, board_break_report=source)
    assert 'data-board-break-ranking="missing-data"' in html
    ops = html.split('id="ops"', 1)[1]
    assert "断板反包排名数据不完整" in ops


def test_true_empty_needs_explicit_healthy_receipt(assembler, tmp_path):
    source = re.sub(r"\| 代码.*?## 打分", "今日无断板反包候选。\n核心数据源全部成功，本次为真实空候选（非采集故障）。\n## 打分", SOURCE, flags=re.S)
    source = source.replace("状态=ok，无效场=0，已打场次=1/1", "状态=skipped，无效场=0，已打场次=0/0")
    payload = assembler._parse_board_break_ranking(source, DATE)
    assert payload["state"] == "none"
    html = _render(assembler, tmp_path, source)
    assembler.validate_report(html, board_break_report=source)
    assert 'data-board-break-ranking="none"' in html
    broken = source.replace("核心数据源全部成功，本次为真实空候选（非采集故障）。", "")
    assert assembler._parse_board_break_ranking(broken, DATE)["state"] == "missing-data"


@pytest.mark.parametrize("pk_footer,expected", [
    ("未运行", "not_run"),
    ("状态=melted，无效场=0，已打场次=0/1", "melted"),
])
def test_unfinished_pk_never_invents_ranks(assembler, tmp_path, pk_footer, expected):
    source = re.sub(r"\d+胜（第\d+名）", "—", SOURCE)
    source = source.replace("状态=ok，无效场=0，已打场次=1/1", pk_footer)
    payload = assembler._parse_board_break_ranking(source, DATE)
    assert payload["state"] == "v1"
    assert payload["pk_status"] == expected
    html = _render(assembler, tmp_path, source)
    assembler.validate_report(html, board_break_report=source)
    assert "PK未计算" in html
    assert "1胜（第1名）" not in html


def test_partial_dimensions_keep_rank_and_gap(assembler, tmp_path):
    source = SOURCE.replace("维度缺失/源失败：无", "维度缺失/源失败：业绩 × 1 只")
    assert assembler._parse_board_break_ranking(source, DATE)["status"] == "partial"
    html = _render(assembler, tmp_path, source)
    assembler.validate_report(html, board_break_report=source)
    assert "业绩 × 1 只" in html
    assert "断板反包排名数据不完整" in html.split('id="ops"', 1)[1]


@pytest.mark.parametrize("branch_status", ["complete", "partial", "source_failed"])
@pytest.mark.parametrize("context_status", ["complete", "partial"])
def test_real_renderer_new_branch_preserves_identity_and_coverage(assembler,tmp_path,branch_status,context_status):
    from services.board_break import scorer,renderer
    cand=dict(code="601188",name="龙江交通",limit_times=1,branch="rebreak",
              pattern_evidence=dict(prior_limit_times=3,dates=["2026-07-13","2026-07-14","2026-07-15",DATE]),
              close=4.3,pct_chg=-8.8983,ref_price=4.56,bars=[])
    card=scorer.build_fact_card(cand,main_sectors=set(),ann_result=None,holder_result=None,earnings_rows=[],adj_factors=None)
    card.update(board_path=dict(status=context_status, rows=[], expected_count=4,
                                one_price_count=0, traded_count=0, gaps=[]),
                sector_context=dict(status=context_status, leadership=dict(status="not_computed")))
    result=dict(date=DATE,prev_trade_date="2026-07-15",status="ok" if branch_status=="complete" else "partial",
                sources={k:dict(ok=True,source="tushare:limit_list_d") for k in ["prev_limit_up","today_limit_up","today_limit_down"]},
                rebreak=dict(status=branch_status,dates=cand["pattern_evidence"]["dates"],
                             sources={key:dict(date=day,source="tushare:limit_list_d",ok=True)
                                      for key,day in [("prior_limit_up","2026-07-13"),
                                                      ("first_break_limit_up","2026-07-14"),
                                                      ("first_break_limit_down","2026-07-14"),
                                                      ("rebound_limit_up","2026-07-15"),
                                                      ("today_limit_up",DATE),("today_limit_down",DATE)]},
                             gaps=[] if branch_status=="complete" else ["另一只股票日线缺失"]))
    source=renderer.render_daily(result,scorer.score_all([card]),None)
    payload=assembler._parse_board_break_ranking(source,DATE)
    assert payload["state"]=="v1", payload["notes"]
    assert payload["rows"][0]["height"]=="1"
    assert payload["rows"][0]["branch"]=="反包后再断板（前段3板）"
    assert any("PK附加证据覆盖" in note for note in payload["notes"]) == (context_status != "complete")
    if branch_status!="complete":
        assert any("新分支缺口" in note for note in payload["notes"])
    html=_render(assembler,tmp_path,source)
    assembler.validate_report(html,board_break_report=source)
    assert "昨日1板；反包后再断板（前段3板）" in html
    with pytest.raises(assembler.ReportValidationError):
        assembler.validate_report(html.replace('data-branch="反包后再断板（前段3板）"','data-branch="连续板断板"'),board_break_report=source)
    # 收据必须来自真实新格式：缺失、重复、错日、失败均不得假完整。
    mutations = [
        re.sub(r"^- 反包后再断板分支：.*\n", "", source, flags=re.M),
        source.replace(f"- 反包后再断板分支：{branch_status}", "- 反包后再断板分支：unknown"),
        source + f"\n- 反包后再断板分支：{branch_status}；日期链：重复",
        source.replace("2026-07-13 → 2026-07-14 → 2026-07-15", "2026-07-14 → 2026-07-14 → 2026-07-15"),
        re.sub(r"^  - prior_limit_up：.*\n", "", source, flags=re.M),
        source.replace("prior_limit_up：2026-07-13", "prior_limit_up：2026-07-12"),
        source.replace("prior_limit_up：2026-07-13 / tushare:limit_list_d / 成功", "prior_limit_up：2026-07-13 / tushare:limit_list_d / 失败"),
    ]
    for broken in mutations:
        assert assembler._parse_board_break_ranking(broken,DATE)["state"]=="missing-data"
    # 新10列表本身必须触发分支收据门，不依赖可删的规则说明段。
    no_rule = re.sub(r"^> 用户既定打法转述：.*\n", "", source, flags=re.M)
    no_receipt = re.sub(r"^- 反包后再断板分支：.*\n", "", no_rule, flags=re.M)
    assert assembler._parse_board_break_ranking(no_receipt, DATE)["state"] == "missing-data"


@pytest.mark.parametrize("version", ["sector-first-traded-board-v2", "industry-bounds-sector-priority-v3"])
def test_v2_context_receipt_required_and_preserved(assembler, version):
    source = SOURCE + f"\n评价口径：{version}；新口径\n"
    assert assembler._parse_board_break_ranking(source, DATE)["state"] == "missing-data"
    source += "\n- PK附加证据覆盖：板块强弱部分覆盖/缺失 1只（600001）\n"
    parsed = assembler._parse_board_break_ranking(source, DATE)
    assert parsed["state"] == "v1" and parsed["status"] == "partial"
    assert any("PK附加证据覆盖" in n for n in parsed["notes"])
    assert assembler._parse_board_break_ranking(source + "\n- PK附加证据覆盖：完整\n", DATE)["state"] == "missing-data"


def test_pk_only_report_shared_ranks_and_html(assembler, tmp_path):
    from services.board_break import scorer, renderer, pk
    cards = [dict(code=f"60000{i}", name=f"票{i}", limit_times=2, close=10, pct_chg=1, ref_price=10.6,
                  total=100-i, rank_score=i+1, evidences=[], bias60=1, bias120=2,
                  board_path=dict(status="complete"), sector_context=dict(status="complete")) for i in range(3)]
    seq = iter(['{"winner":"A","reason":"r"}', '{"winner":"B","reason":"r"}', '{"winner":"A","reason":"r"}'])
    # A>B、C>A、B>C，三票各一胜；总分完全不同仍并列。
    p = pk.run_pk(cards, cards, lambda *args: next(seq))
    result = dict(date=DATE, prev_trade_date="2026-07-15", status="ok",
                  sources={k:dict(ok=True, source="tushare:limit_list_d") for k in ["prev_limit_up","today_limit_up","today_limit_down"]},
                  rebreak=dict(status="complete", dates=[], sources={}, gaps=[]))
    source = renderer.render_daily(result, list(reversed(cards)), p)
    parsed = assembler._parse_board_break_ranking(source, DATE)
    assert parsed["state"] == "v1", parsed["notes"]
    assert parsed["pk_only"] and [r["pk_rank"] for r in parsed["rows"]] == [1,1,1]
    assert all(r["score_rank"] is None for r in parsed["rows"])
    assert "加权分及排名" not in source and "分歧⚠" not in source
    html = _render(assembler, tmp_path, source)
    assembler.validate_report(html, board_break_report=source)
    assert "加权分[参考]" in html and "PK排名；加权分仅参考" in html
    corrupted = source.replace("1胜（第1名）", "1胜（第2名）", 1)
    assert assembler._parse_board_break_ranking(corrupted, DATE)["state"] == "missing-data"
    without_matches = re.sub(r"^- \d{6} vs .*\n", "", source, flags=re.M)
    assert assembler._parse_board_break_ranking(without_matches, DATE)["state"] == "missing-data"


def test_unlabelled_first_board_still_rejected(assembler):
    assert assembler._parse_board_break_ranking(SOURCE.replace("| 7 |","| 1 |"),DATE)["state"]=="missing-data"


@pytest.mark.parametrize("mutation", ["remove", "date", "duplicate", "rank", "count", "hidden"])
def test_html_gate_rejects_missing_or_tampered_ranking(assembler, tmp_path, mutation):
    html = _render(assembler, tmp_path)
    pattern = r'<details class="evidence" data-as-of="[^"]+" data-items="\d+"><summary>断板反包.*?</details>'
    block = re.search(pattern, html, re.S).group(0)
    changed = {
        "remove": "",
        "date": block.replace(f'data-as-of="{DATE}"', 'data-as-of="2026-07-15"'),
        "duplicate": block + block,
        "rank": block.replace("0胜（第2名）", "0胜（第1名）"),
        "count": block.replace('data-candidate-count="2"', 'data-candidate-count="1"'),
        "hidden": block.replace("万向德农", '<span hidden>万向德农</span>'),
    }[mutation]
    with pytest.raises(assembler.ReportValidationError):
        assembler.validate_report(html.replace(block, changed), board_break_report=SOURCE)


def test_chunk_cannot_supply_its_own_ranking(assembler, tmp_path):
    paths = _write_chunks(tmp_path)
    path = paths["s456"]
    path.write_text(path.read_text() + "<p data-board-break-ranking = 'none'>假空榜</p>")
    with pytest.raises(assembler.ReportValidationError, match="duplicate_board_break_ranking"):
        assembler.render_report(tmp_path, DATE, board_break_report=SOURCE)


def test_source_is_escaped_not_interpreted(assembler, tmp_path):
    source = SOURCE.replace("海鸥住工", '<script>alert(1)</script>')
    html = _render(assembler, tmp_path, source)
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assembler.validate_report(html, board_break_report=source)


def test_official_cli_reads_same_day_source(assembler, tmp_path):
    _write_chunks(tmp_path)
    source = tmp_path / "board-break.md"
    source.write_text(SOURCE)
    output = tmp_path / "review.html"
    result = subprocess.run([
        sys.executable, str(ASSEMBLER), str(tmp_path), DATE,
        "--board-break-report", str(source), "--output", str(output),
        "--trade-db", str(tmp_path / "no-live-db.sqlite"),
        "--daily-dir", str(tmp_path / "no-live-daily"),
    ], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'data-board-break-ranking="v1"' in output.read_text()
