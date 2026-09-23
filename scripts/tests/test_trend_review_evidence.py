from copy import deepcopy
import json

import pytest

from services.trend_leader import review_evidence as R, research_evidence as E, observations as O
from tests.test_trend_leader_observations import bars

DAY = "2026-07-30"


def summary():
    holders = E.holder_summary([
        dict(ts_code="600000.SH", end_date="20260331", ann_date="20260430", holder_num=100),
        dict(ts_code="600000.SH", end_date="20260630", ann_date="20260720", holder_num=80),
    ], "600000", DAY)
    income = E.income_summary([
        dict(ts_code="600000.SH", end_date="20250630", ann_date="20250720", report_type=1, n_income_attr_p=100),
        dict(ts_code="600000.SH", end_date="20260630", ann_date="20260720", report_type=1, n_income_attr_p=130),
    ], "600000", DAY)
    support = dict(status="complete", supported=True, trade_date=DAY, industry="银行", delta_yi=10, share_delta_pp=1)
    return dict(date=DAY, launch_pullbacks=[dict(code="600000", name="浦发银行", **O.analyze(bars(), DAY, support))],
                research_cards=[dict(code="600000", trade_date=DAY, status="complete", holders=holders, income=income)],
                research_coverage=dict(eligible=1, collected=1, not_collected=[]))


def payload(): return R.build(summary(), "original")


def test_dated_snapshot_bound_to_report_and_never_falls_back(tmp_path):
    p=payload(); assert p["status"] == "complete"
    (tmp_path/f"{DAY}.md").write_text("original")
    R.write(tmp_path,DAY,p)
    assert R.load(tmp_path,DAY)["status"] == "complete"
    assert R.load(tmp_path,"2026-07-31")["status"] == "missing-data"
    (tmp_path/f"{DAY}.md").write_text("different")
    assert R.load(tmp_path,DAY)["status"] == "source_failed"
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("mutate", [
    lambda p:p.update(trade_date="2026-07-29"),
    lambda p:p["observations"].append(deepcopy(p["observations"][0])),
    lambda p:p["observations"][0].update(trade_date="2026-07-31"),
    lambda p:p["observations"][0].update(launch_date="2026-07-31"),
    lambda p:p["cards"][0]["income"]["latest"].update(ann_date="2026-08-01"),
    lambda p:p["cards"][0]["income"].update(profit_change_pct=999),
    lambda p:p["cards"][0]["holders"]["latest"].update(value=0),
    lambda p:p["cards"][0].update(code="000001"),
    lambda p:p["coverage"].update(collected=0),
    lambda p:p["coverage"].update(eligible=True),
    lambda p:p["observations"][0].update(sector_support={"supported":None}),
    lambda p:p["observations"][0]["sector_support"].update(trade_date="2026-07-29"),
    lambda p:p["observations"][0]["sector_support"].update(supported=1),
    lambda p:p["observations"][0]["sector_support"].update(delta_yi=-1),
    lambda p:p["observations"][0].update(state="support_weakened"),
    lambda p:(p["observations"][0].update(state="pullback_observed"),
              p["observations"][0]["sector_support"].update(supported=False, delta_yi=-1)),
    lambda p:p["cards"][0]["income"]["latest"].update(report_type=2),
    lambda p:p["cards"][0]["income"]["prior_year"].update(report_type=2),
])
def test_invalid_snapshot_fails_closed(mutate):
    p=payload(); mutate(p)
    assert R.validate(p,DAY)["status"] == "source_failed"


def test_partial_coverage_and_empty_are_distinct():
    s=summary(); s['research_cards']=[]
    s['research_coverage'].update(collected=0,not_collected=['600000'])
    p=R.build(s,'original')
    assert p['status']=='partial' and any('未覆盖' in g for g in p['gaps'])
    html,gaps=R.render(p,DAY)
    assert '证据卡0/1' in html and '600000' in html and gaps
    s.update(launch_pullbacks=[],research_coverage=dict(eligible=0,collected=0,not_collected=[]))
    assert R.build(s,'original')['status']=='complete'
    assert R.build(dict(date=DAY),'original')['status']=='source_failed'


def test_safe_html_complete_rows_and_source_gaps():
    s=summary(); s['launch_pullbacks'][0]['name']='<script>bad</script>'
    s['source_errors']=['mainline_llm']
    p=R.build(s,'original'); text,gaps=R.render(p,DAY)
    assert p['status']=='partial' and '<script>' not in text
    assert '<details class="evidence"' in text and 'mainline_llm' in gaps


def test_assembler_injects_once_and_reconciles_source(tmp_path):
    from tests.test_daily_review_html_report import _load_assembler, _write_chunks
    module=_load_assembler(); _write_chunks(tmp_path,DAY)
    p=payload(); text=module.render_report(tmp_path,DAY,trend_review_evidence=p)
    assert text.count('data-trend-review-evidence=')==1
    module.validate_report(text,trend_review_evidence=p)
    with pytest.raises(module.ReportValidationError,match='trend_review_evidence_source_mismatch'):
        module.validate_report(text.replace('同比+30.00%', '同比+99.00%'),trend_review_evidence=p)
    chunk=tmp_path/f'b{DAY}_s456.html'
    chunk.write_text(chunk.read_text()+f'<div data-trend-review-evidence="{DAY}">伪造</div>')
    with pytest.raises(module.ReportValidationError,match='duplicate_trend_review_evidence'):
        module.render_report(tmp_path,DAY,trend_review_evidence=p)


def test_missing_snapshot_is_visible_and_in_ops(tmp_path):
    from tests.test_daily_review_html_report import _load_assembler, _write_chunks
    module=_load_assembler(); _write_chunks(tmp_path,DAY)
    p=R.load(tmp_path,DAY); text=module.render_report(tmp_path,DAY,trend_review_evidence=p)
    module.validate_report(text,trend_review_evidence=p)
    assert text.count('同日趋势扫描证据尚未生成') == 2


@pytest.mark.parametrize("mutation", ["date", "items", "kind", "unfold", "remove"])
def test_assembler_rejects_trend_structure_drift(tmp_path, mutation):
    from tests.test_daily_review_html_report import _load_assembler, _write_chunks
    module=_load_assembler(); _write_chunks(tmp_path,DAY)
    p=payload(); fragment=R.render(p,DAY)[0]
    text=module.render_report(tmp_path,DAY,trend_review_evidence=p)
    if mutation == "date": changed=fragment.replace(f'data-as-of="{DAY}"', 'data-as-of="2026-07-29"')
    elif mutation == "items": changed=fragment.replace('data-items="2"', 'data-items="3"')
    elif mutation == "kind": changed=fragment.replace('data-evidence-kind="trend-review"', 'data-evidence-kind="other"')
    elif mutation == "unfold": changed=fragment.replace('<details ', '<details open ')
    else: changed=fragment.replace('<details ', '<div ').replace('</details>', '</div>')
    assert changed != fragment and fragment in text
    with pytest.raises(module.ReportValidationError):
        module.validate_report(text.replace(fragment,changed),trend_review_evidence=p)


def test_omitted_source_is_missing_not_failed(tmp_path):
    from tests.test_daily_review_html_report import _load_assembler, _write_chunks
    module=_load_assembler(); _write_chunks(tmp_path,DAY)
    text=module.render_report(tmp_path,DAY)
    assert 'missing-data：同日趋势扫描证据尚未提供' in text
    assert 'source_failed：同日趋势' not in text
    module.validate_report(text)
