"""增量资金推断的证据门、失败语义与组装集成。数值均为测试合成。"""
from datetime import date

import pytest

from tests.test_daily_review_html_report import DATE, _load_assembler, _write_chunks


@pytest.fixture(scope="module")
def assembler():
    return _load_assembler()


def row(role, source, label, fact, status="complete", day=DATE):
    return (f'<tr data-role="{role}" data-source="{source}" data-source-status="{status}" '
            f'data-window-start="{day}" data-as-of="{day}">'
            f'<td>{label}</td><td>{source}</td><td>{day} 至 {day}</td>'
            f'<td>[事实] {fact} {status}</td><td>[判断] 仅作渠道线索，不能识别最终投资者。</td></tr>')


def fragment():
    return (f'<p data-incremental-capital="verdict" data-as-of="{DATE}" '
            f'data-reviewed-through="{DATE}" data-source-status="complete" '
            'data-candidate="etf" data-confidence="medium">'
            '[判断] 增量资金较可能经ETF申赎渠道体现，中可信度；若转净赎回则证伪。</p>'
            f'<details class="evidence" data-as-of="{DATE}" data-items="4">'
            '<summary>增量资金具体依据（4条合成测试数据）</summary><div class="evidence-body">'
            f'<table data-incremental-capital="evidence" data-as-of="{DATE}">'
            '<thead><tr><th>作用</th><th>来源</th><th>日期窗口</th><th>具体依据</th><th>推断与局限</th></tr></thead><tbody>'
            + row("support", "etf", "支持", "ETF fund_share 样本20只净增10亿份，估算额20亿元")
            + row("support", "turnover", "支持", "成交额10000亿元，较前日增加500亿元")
            + row("counter", "margin", "反证", "融资余额较前日减少5亿元")
            + row("gap", "institution", "缺口", "机构席位仅覆盖龙虎榜样本，最终投资者身份不可识别")
            + '</tbody></table></div></details>')


def validate(assembler, body):
    parser = assembler._ReportParser()
    parser.feed('<article id="report-document"><section id="s1">' + body + '</section></article>')
    parser._validate_incremental_capital(date.fromisoformat(DATE))


def test_candidate_evidence_and_integration(assembler, tmp_path):
    validate(assembler, fragment())
    paths = _write_chunks(tmp_path)
    text = paths["s1"].read_text()
    paths["s1"].write_text(text.replace('</section>', fragment() + '</section>'))
    html = assembler.render_report(tmp_path, DATE)
    assembler.validate_report(html)
    assert html.count('data-incremental-capital="verdict"') == 1
    assert 'data-incremental-capital="missing-data"' not in html


@pytest.mark.parametrize("comment", ["", '<!-- TODO data-incremental-capital="verdict" -->'])
def test_old_material_becomes_explicit_missing_not_zero(assembler, tmp_path, comment):
    paths = _write_chunks(tmp_path)
    paths["s1"].write_text(paths["s1"].read_text().replace('</section>', comment + '</section>'))
    html = assembler.render_report(tmp_path, DATE)
    assembler.validate_report(html)
    assert html.count('data-incremental-capital="missing-data"') == 1
    assert "增量资金：无法识别" in html
    assert "不代表没有增量资金" in html


@pytest.mark.parametrize("old,new", [
    ('data-confidence="medium"', 'data-confidence="high"'),
    ('data-candidate="etf"', 'data-candidate="margin"'),
    ('data-role="counter"', 'data-role="support"'),
    ('data-role="gap"', 'data-role="counter"'),
    ('data-source="turnover"', 'data-source="etf"'),
    ('data-source="etf"', 'data-source="northbound"'),
    ('data-source-status="complete"', 'data-source-status="source_failed"'),
    ('data-as-of="2026-07-16"', 'data-as-of="2026-07-17"'),
    ('data-reviewed-through="2026-07-16"', 'data-reviewed-through="2026-07-15"'),
    ('[事实]', ''),
    ('[判断]', ''),
    ('成交额10000亿元，较前日增加500亿元', '成交额明显放大'),
    ('<p data-incremental-capital', '<p hidden data-incremental-capital'),
    ('<details class="evidence"', '<details open class="evidence"'),
])
def test_invalid_evidence_rejected(assembler, old, new):
    with pytest.raises(assembler.ReportValidationError, match="invalid_incremental_capital"):
        validate(assembler, fragment().replace(old, new))


def test_lagged_direct_evidence_cannot_identify_current_channel(assembler):
    body = fragment().replace(
        row("support", "etf", "支持", "ETF fund_share 样本20只净增10亿份，估算额20亿元"),
        row("support", "etf", "支持", "ETF fund_share 样本20只净增10亿份，估算额20亿元", day="2026-07-15"))
    with pytest.raises(assembler.ReportValidationError, match="两类当日完整支持"):
        validate(assembler, body)


def test_partial_can_retain_known_facts_with_unknown_conclusion(assembler):
    body = fragment().replace('data-candidate="etf"', 'data-candidate="unknown"').replace(
        'data-confidence="medium"', 'data-confidence="low"').replace('中可信度', '低可信度').replace(
        '较可能经ETF申赎渠道体现', '无法识别').replace('data-source-status="complete"', 'data-source-status="partial"', 1)
    body = body.replace(row("support", "etf", "支持", "ETF fund_share 样本20只净增10亿份，估算额20亿元"),
                        row("gap", "etf", "缺口", "ETF份额源失败", status="source_failed"))
    parser = assembler._ReportParser()
    parser.feed('<article id="report-document"><section id="s1">' + body + '</section><section id="ops">增量资金推断：ETF source_failed</section></article>')
    parser._validate_incremental_capital(date.fromisoformat(DATE))
    parser.section_text["ops"] = []
    with pytest.raises(assembler.ReportValidationError, match="登记数据缺口"):
        parser._validate_incremental_capital(date.fromisoformat(DATE))


def test_duplicate_summary_is_rejected(assembler):
    with pytest.raises(assembler.ReportValidationError, match="唯一摘要"):
        validate(assembler, fragment() + fragment())
