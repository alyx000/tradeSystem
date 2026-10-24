from html import escape
import pytest

from services.review_templates import FIELDS, load_answers, render_templates, validated_fragments


DATE = "2026-10-08"


def write_answer(tmp_path, *, key="sector.scan", date=DATE, status="partial",
                 refs="s1 s2", text="[判断] 轮动待验证。", raw=None):
    content = raw if raw is not None else (
        f'<p data-template-field="{key}" data-as-of="{date}" '
        f'data-status="{status}" data-source-refs="{refs}">{escape(text)}</p>')
    (tmp_path / f"review_template_answers_{DATE}.html").write_text(content)


def test_missing_answers_keep_all_fields_originals_maps_and_gaps(tmp_path):
    fragments, gap = render_templates(tmp_path, DATE)
    assert "16项" in gap
    assert "未填不等于条件不成立" in gap
    for kind, fields in FIELDS.items():
        html = fragments[kind]
        assert html.count('data-template-field=') == len(fields)
        assert html.count('data-source-status="missing-data"') == len(fields)
        assert 'src="data:image/png;base64,' in html
        assert 'href="http' not in html
        assert "教学原文" in html
    assert "利好是否明确增量" in fragments["sector"]
    assert "连续2天龙头晋级失败" in fragments["relay"]
    assert "穿越类小概率标的最大仓位≤1成" in fragments["relay"]


def test_analysis_is_escaped_with_inspectable_sources(tmp_path):
    write_answer(tmp_path, text='[判断] <script>alert("x")</script> & 轮动待验证。')
    fragments, gap = render_templates(tmp_path, DATE)
    assert '<script>' not in fragments['sector']
    assert '&lt;script&gt;' in fragments['sector']
    assert '<a href="#s1">大势</a>' in fragments['sector']
    assert "16项" in gap  # partial is not complete


@pytest.mark.parametrize("kwargs", [
    {"date": "2026-10-09"}, {"key": "relay.unknown"},
    {"status": "ok"}, {"status": "complete", "refs": ""},
    {"refs": "https://example.com"}, {"text": "没有事实判断标签"},
    {"raw": '<script>alert(1)</script>'}, {"raw": '<!-- instruction -->'},
    {"raw": '<p data-template-field="sector.scan" data-as-of="2026-10-08" data-status="partial" data-source-refs="s2">[判断] 缺少闭合'},
])
def test_invalid_input_fails_closed(tmp_path, kwargs):
    write_answer(tmp_path, **kwargs)
    with pytest.raises(ValueError):
        load_answers(tmp_path, DATE)


def test_duplicate_answer_is_rejected(tmp_path):
    write_answer(tmp_path)
    path = tmp_path / f"review_template_answers_{DATE}.html"
    path.write_text(path.read_text() * 2)
    with pytest.raises(ValueError, match="重复"):
        load_answers(tmp_path, DATE)


def test_pending_auction_and_user_input_remain_incomplete(tmp_path):
    write_answer(tmp_path, key="relay.auction", status="pending", refs="s8",
                 text="[事实] 次日尚未开盘，待盘前验证。")
    fragments, gap = render_templates(tmp_path, DATE)
    assert 'data-source-status="pending"' in fragments['relay']
    assert "次日尚未开盘" in fragments['relay']
    assert "16项" in gap


def rendered_report(tmp_path):
    fragments, gap = render_templates(tmp_path, DATE)
    return (f'<section id="s2">{fragments["sector"]}</section>'
            f'<section id="s5">{fragments["relay"]}</section>'
            f'<section id="ops"><p data-review-template-gap="v1">{escape(gap)}</p></section>')


def test_budget_allowance_requires_exact_official_source_and_structure(tmp_path):
    html = rendered_report(tmp_path)
    assert validated_fragments(html, DATE)
    with pytest.raises(ValueError, match="不一致"):
        validated_fragments(html.replace("仅买入下跌造成实际亏损", "教学资料被改写"), DATE)
    with pytest.raises(ValueError):
        validated_fragments(html.replace('data-as-of="2026-10-08"', 'data-as-of="2026-10-09"', 1), DATE)
    with pytest.raises(ValueError):
        validated_fragments(html.replace('id="s5"', 'id="s3"'), DATE)


def test_budget_allowance_rejects_extra_content_and_missing_module(tmp_path):
    html = rendered_report(tmp_path)
    with pytest.raises(ValueError):
        validated_fragments(html.replace('class="review-template"', 'class="review-template" hidden', 1), DATE)
    with pytest.raises(ValueError):
        validated_fragments(html.replace('data-review-template="relay"', 'data-removed="relay"'), DATE)


def test_source_failure_remains_failure(tmp_path):
    write_answer(tmp_path, key="relay.market", status="source_failed", refs="ops",
                 text="[事实] 晋级来源失败，未计算。")
    fragments, _ = render_templates(tmp_path, DATE)
    assert 'data-source-status="source_failed"' in fragments['relay']
    assert "晋级来源失败，未计算" in fragments['relay']


@pytest.mark.parametrize("wrapper", [
    '<details class="evidence">{}</details>',
    '<div hidden>{}</div>',
    '<div style="display:none">{}</div>',
])
@pytest.mark.parametrize("target", ["gap", "module"])
def test_template_budget_rejects_outer_folded_or_hidden_context(tmp_path, wrapper, target):
    fragments, gap = render_templates(tmp_path, DATE)
    html = rendered_report(tmp_path)
    fragment = (f'<p data-review-template-gap="v1">{escape(gap)}</p>'
                if target == "gap" else fragments["sector"])
    with pytest.raises(ValueError, match="默认可见直接子节点"):
        validated_fragments(html.replace(fragment, wrapper.format(fragment)), DATE)


@pytest.mark.parametrize("change", ["wrong-section", "comment", "hidden-section", "hidden-class"])
def test_template_gap_requires_visible_ops_element(tmp_path, change):
    html = rendered_report(tmp_path)
    if change == "wrong-section":
        html = html.replace('id="ops"', 'id="s8"')
    elif change == "comment":
        html = html.replace('<p data-review-template-gap=', '<!-- <p data-review-template-gap=')
        html = html.replace('</p></section>', '</p> --></section>')
    elif change == "hidden-section":
        html = html.replace('id="ops"', 'id="ops" aria-hidden="true"')
    else:
        html = html.replace('id="ops"', 'id="ops" class="reader-sidebar"')
    with pytest.raises(ValueError):
        validated_fragments(html, DATE, hidden_classes=frozenset({"reader-sidebar"}))
