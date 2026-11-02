"""Pure style charts preserve the owning contracts, denominators and missing states."""
from copy import deepcopy
from html.parser import HTMLParser

from tests.test_daily_review_html_report import assembler, _style_market_effect_payload, DATE


class Visible(HTMLParser):
    def __init__(self):
        super().__init__(); self.depth = 0; self.parts = []
    def handle_starttag(self, tag, attrs):
        if tag == 'details': self.depth += 1
    def handle_endtag(self, tag):
        if tag == 'details': self.depth -= 1
    def handle_data(self, text):
        if not self.depth: self.parts.append(text)


def visible(html):
    p = Visible(); p.feed(html); return ''.join(p.parts)


def test_style_charts_keep_explicit_board_history_and_window_denominators(assembler):
    p = _style_market_effect_payload(); before = deepcopy(p)
    html = assembler._render_style_market_effect(p, DATE)
    text = visible(html)
    assert 'data-style-chart="board-width"' in html
    assert 'data-style-chart="board-history"' in html
    for w in (10, 20, 30):
        assert f'data-style-board="{w}"' in html
        assert f'data-history-board="{w}"' in html
    for h in (1, 3, 5, 10):
        item = p['shock']['horizons'][str(h)]
        assert f'data-style-horizon="{h}"' in html
        assert f'正收益{item["positive_count"]}/{item["sample_count"]}' in text
    assert '分母不同' in text
    assert '总涨停/首板/连板/10cm/20cm/30cm' not in text
    assert p == before


def test_low_price_compares_separate_bands_to_market_without_zero_filling(assembler):
    p = _style_market_effect_payload(); before = deepcopy(p)
    html = assembler._render_style_low_price_effect(p, DATE); text = visible(html)
    assert html.count('data-style-metric="median"') == 3
    assert html.count('data-style-metric="advance"') == 3
    for label in ('≤5元', '5～10元', '全市场'): assert label in text
    for item in [*p['low_price']['bands'].values(), p['low_price']['market']]:
        assert f'n={item["sample_count"]}' in text
    assert '个股等权' not in text and '个股等权' in html
    p['low_price']['market']['advance_rate'] = None
    missing = assembler._render_style_low_price_effect(p, DATE)
    assert 'data-value="unavailable"' in missing and '未计算' in visible(missing)
    assert before['low_price']['market']['advance_rate'] is not None


def test_missing_payload_does_not_render_charts_or_zero_denominators(assembler):
    for fn in (assembler._render_style_low_price_effect, assembler._render_style_market_effect):
        html = fn(None, DATE)
        assert 'missing-data' in html and 'data-style-chart=' not in html


def test_zero_bse_is_only_source_sample_and_history_dates_remain_exact(assembler):
    p = _style_market_effect_payload()
    p['market']['current']['bse'] = []
    html = assembler._render_style_market_effect(p, DATE)
    assert '本日无北交所涨停' not in html
    assert '不能判断北交所全市场' in visible(html)
    for row in p['market']['history']:
        assert f'data-date="{row["date"]}"' in html


def test_low_price_partial_is_visible_and_all_contract_attributes_survive(assembler):
    p = _style_market_effect_payload(); p['low_price']['status'] = 'partial'
    html = assembler._render_style_low_price_effect(p, DATE)
    assert '部分覆盖' in visible(html)
    assert 'data-low-sample-count=' in html and 'data-market-median-pct=' in html


def test_closed_low_price_reuses_flat_evidence_and_keeps_historical_date(assembler):
    from services.review_style_charts import low_price
    p = _style_market_effect_payload()
    # The owning closed-day path provides an existing evidence container.
    legacy = '<div><h3>低价</h3><details class="evidence" data-as-of="'+DATE+'" data-items="4"><summary>原依据</summary><p>历史事实和原口径</p></details></div>'
    html = low_price(legacy, p['low_price'], '2026-07-18')
    assert html.count('<details') == 1
    assert '历史事实和原口径' in html
    assert DATE in visible(html) and '2026-07-18' in visible(html)


def test_nonzero_bse_uses_scope_note_without_claiming_zero(assembler):
    html = assembler._render_style_market_effect(_style_market_effect_payload(), DATE)
    assert '30cm · 1只' in visible(html)
    assert '30cm为0' not in visible(html)
    assert '30cm统计仅限本来源样本' in visible(html)
